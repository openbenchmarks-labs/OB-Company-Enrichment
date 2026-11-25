"""Calls each setup for every company and saves the answers, resuming a run that stopped part way."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .company import employee_band
from .http_client import redact
from .models import Answer, Company
from .tool_base import Adapter


def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def company_of(row: dict[str, Any]) -> Company:
    """A task row is either a Hugging Face dataset row (`input_domain`, `input_name`) or a flat row (`domain`, `company`)."""
    domain = row.get("input_domain") or row["domain"]
    return Company(domain=str(domain).strip().lower(), name=row.get("input_name") or row.get("company"))


def truth_of(row: dict[str, Any]) -> dict[str, Any]:
    """The ground truth in the fields `judge/grader.py` reads. A flat row has them already; a dataset row keeps the
    headquarters and the employee band in parts, and some rows give the alternate LinkedIn pages as one comma-separated
    string."""
    if "input_domain" not in row:
        return row
    alternates = row.get("linkedin_url_alternates") or []
    if isinstance(alternates, str):
        alternates = [url.strip() for url in alternates.split(",") if url.strip()]
    return {
        "domain": company_of(row).domain,
        "hq": ", ".join(str(v).strip() for v in (row.get("hq_city"), row.get("hq_country")) if v) or None,
        "industry": row.get("industry"),
        "employee_band": employee_band(row.get("headcount_min"), row.get("headcount_max")),
        "employee_count": row.get("headcount_exact"),
        "founded_year": row.get("founded_year"),
        "linkedin_url": row.get("linkedin_url"),
        "linkedin_url_alternates": alternates,
    }


def load_companies(path: Path) -> list[Company]:
    return [company_of(row) for row in load_rows(path)]


def answers_path(out: Path, setup: str) -> Path:
    return out / f"{setup}.jsonl"


def done_domains(out: Path, setup: str) -> set[str]:
    """Domains this setup already has an answer for. An "error" answer is retried on the next run."""
    path = answers_path(out, setup)
    if not path.exists():
        return set()
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return {row["domain"] for row in rows if row.get("status") != "error"}


class _Pacer:
    """Keeps at least `seconds` between request starts, across all workers of one setup."""

    def __init__(self, seconds: float) -> None:
        self.seconds, self.lock, self.next_start = seconds, threading.Lock(), 0.0

    def wait(self) -> None:
        if self.seconds <= 0:
            return
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_start - now)
            self.next_start = max(now, self.next_start) + self.seconds
        time.sleep(delay)


def run_setup(adapter: Adapter, companies: list[Company], out: Path, workers: int) -> dict[str, int]:
    """Look up every company this setup has no answer for yet. Returns counts by status."""
    done = done_domains(out, adapter.setup)
    pending = [c for c in companies if c.domain not in done]
    batches = [pending[i:i + adapter.batch_size] for i in range(0, len(pending), adapter.batch_size)]
    pacer, write_lock = _Pacer(adapter.seconds_between_requests), threading.Lock()
    raw_dir = out / "raw" / adapter.setup
    counts: dict[str, int] = {"skipped": len(companies) - len(pending)}

    def one(batch: list[Company]) -> list[Answer]:
        pacer.wait()
        try:
            return adapter.lookup_many(batch)
        except Exception as error:  # A failure is saved as an "error" answer and retried on the next run.
            return [adapter.answer(c, "error", error=f"{type(error).__name__}: {error}"[:500]) for c in batch]

    def save(answers: list[Answer]) -> None:
        with write_lock:
            raw_dir.mkdir(parents=True, exist_ok=True)
            with answers_path(out, adapter.setup).open("a") as handle:
                for a in answers:
                    if a.raw is not None:
                        (raw_dir / f"{a.domain}.json").write_text(json.dumps(redact(a.raw), indent=1, default=str))
                    handle.write(json.dumps(a.row(), default=str) + "\n")
                    counts[a.status] = counts.get(a.status, 0) + 1

    limit = min(workers, adapter.max_workers or workers)
    with ThreadPoolExecutor(max_workers=max(1, limit)) as pool:
        for future in as_completed([pool.submit(one, batch) for batch in batches]):
            save(future.result())
    return counts


def plan(adapters: list[Adapter], companies: list[Company], out: Path) -> list[dict[str, Any]]:
    return [{
        "setup": a.setup,
        "pending": len([c for c in companies if c.domain not in done_domains(out, a.setup)]),
        "missing_env": a.missing_env(),
    } for a in adapters]
