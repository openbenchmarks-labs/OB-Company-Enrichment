"""Calls each setup for every company and saves the answers, resuming a run that stopped part way."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .http_client import redact
from .models import Answer, Company
from .tool_base import Adapter


def load_companies(path: Path) -> list[Company]:
    companies = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            companies.append(Company(domain=str(row["domain"]).strip().lower(), name=row.get("company")))
    return companies


def answers_path(out: Path, setup: str) -> Path:
    return out / f"{setup}.jsonl"


def done_domains(out: Path, setup: str) -> set[str]:
    """Domains this setup already has an answer for. An "error" answer is retried on the next run."""
    path = answers_path(out, setup)
    if not path.exists():
        return set()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
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
            with answers_path(out, adapter.setup).open("a", encoding="utf-8") as handle:
                for a in answers:
                    if a.raw is not None:
                        (raw_dir / f"{a.domain}.json").write_text(
                            json.dumps(redact(a.raw), indent=1, default=str),
                            encoding="utf-8",
                        )
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
