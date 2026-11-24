"""Run the answer, search and agent enrichment providers (Seltz, Nimble, CompanyEnrich v2).

Raw cells are checkpointed immediately under runs/raw. Every existing status is
skipped unless its status is explicitly selected with ``--retry-status``; this
prevents a restart from silently repeating paid calls.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from harness.common import CompanyCase, load_cases_file
from harness.providers import seltz_companies, seltz_news, nimble_lite, nimble_standard, nimble_agents, company_enrich_v2


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "data" / "sample.json"
DEFAULT_OUTPUT_ROOT = ROOT / "runs"
DEFAULT_PROVIDERS = (seltz_companies.VENDOR_SLUG, seltz_news.VENDOR_SLUG)
PROVIDERS = {
    company_enrich_v2.VENDOR_SLUG: company_enrich_v2,
    nimble_agents.VENDOR_SLUG: nimble_agents,
    nimble_lite.VENDOR_SLUG: nimble_lite,
    nimble_standard.VENDOR_SLUG: nimble_standard,
    seltz_companies.VENDOR_SLUG: seltz_companies,
    seltz_news.VENDOR_SLUG: seltz_news,
}
CONCURRENCY = {
    company_enrich_v2.VENDOR_SLUG: 4,
    nimble_agents.VENDOR_SLUG: 5,
    nimble_lite.VENDOR_SLUG: 4,
    nimble_standard.VENDOR_SLUG: 4,
    seltz_companies.VENDOR_SLUG: 6,
    seltz_news.VENDOR_SLUG: 6,
}


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def output_path(output_root: Path, provider: str, case_slug: str) -> Path:
    return output_root / "raw" / provider / f"{case_slug}.json"


def load_saved_status(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    value = payload.get("status")
    return str(value) if value else None


def load_cases(
    cases_path: Path,
    pilot_per_slice: int | None,
    requested_case_slugs: list[str],
) -> list[CompanyCase]:
    payload = json.loads(cases_path.read_text(encoding="utf-8"))
    load_cases_file(cases_path)
    rows = payload["cases"]
    if requested_case_slugs:
        by_slug = {row["case_slug"]: row for row in rows}
        missing = sorted(set(requested_case_slugs) - set(by_slug))
        if missing:
            raise ValueError(f"unknown case slug(s): {', '.join(missing)}")
        rows = [by_slug[slug] for slug in requested_case_slugs]
    elif pilot_per_slice is not None:
        selected: list[dict[str, Any]] = []
        for slice_name in sorted({row["slice"] for row in rows}):
            candidates = sorted(
                (row for row in rows if row["slice"] == slice_name),
                key=lambda row: row["case_slug"],
            )
            selected.extend(candidates[:pilot_per_slice])
        rows = selected
    return [CompanyCase(**row) for row in rows]


def run_one(module: Any, case: CompanyCase, output_root: Path) -> dict[str, Any]:
    try:
        row = module.run(case).to_dict()
    except Exception as error:  # preserve failures for explicit, reviewed retry
        row = {
            "provider_slug": module.VENDOR_SLUG,
            "provider_name": module.VENDOR_NAME,
            "case_slug": case.case_slug,
            "status": "error",
            "error": f"{type(error).__name__}: {error}",
        }
    row["input_name"] = case.input_name
    row["input_domain"] = case.input_domain
    path = output_path(output_root, module.VENDOR_SLUG, case.case_slug)
    if path.exists():
        attempts = path.with_suffix(".attempts.jsonl")
        attempts.parent.mkdir(parents=True, exist_ok=True)
        with attempts.open("a", encoding="utf-8") as handle:
            handle.write(path.read_text(encoding="utf-8").strip() + "\n")
    atomic_write(path, row)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--only", default=",".join(DEFAULT_PROVIDERS))
    parser.add_argument(
        "--pilot-per-slice",
        type=int,
        help="select the first N stable case slugs in each cohort slice",
    )
    parser.add_argument(
        "--case-slug",
        action="append",
        default=[],
        help="run an explicit case (repeatable); takes precedence over the pilot selector",
    )
    parser.add_argument(
        "--retry-status",
        action="append",
        default=[],
        choices=("ok", "not_found", "error", "pending"),
        help="explicitly rerun saved cells with this status (repeatable)",
    )
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="validate and print the call plan")
    args = parser.parse_args()
    if not args.dry_run and not args.confirm_paid:
        parser.error("live provider calls require --confirm-paid")
    if args.pilot_per_slice is not None and args.pilot_per_slice < 1:
        parser.error("--pilot-per-slice must be positive")

    requested = [value.strip() for value in args.only.split(",") if value.strip()]
    unknown = sorted(set(requested) - set(PROVIDERS))
    if unknown:
        parser.error(f"unknown provider(s): {', '.join(unknown)}")
    load_dotenv(ROOT / ".env", override=False)
    modules = [PROVIDERS[slug] for slug in requested]
    missing = sorted(
        {
            key
            for module in modules
            for key in module.REQUIRED_ENV
            if not os.environ.get(key)
        }
    )
    if missing and not args.dry_run:
        raise RuntimeError(f"missing required environment variable(s): {', '.join(missing)}")

    cases = load_cases(args.cases, args.pilot_per_slice, args.case_slug)
    retry_statuses = set(args.retry_status)
    summary: dict[str, Any] = {
        "cases_file": str(args.cases),
        "cases_selected": len(cases),
        "providers": {},
    }
    for module in modules:
        pending = []
        status_before: dict[str, int] = {}
        for case in cases:
            saved = load_saved_status(
                output_path(args.output_root, module.VENDOR_SLUG, case.case_slug)
            )
            if saved:
                status_before[saved] = status_before.get(saved, 0) + 1
            if saved is None or saved in retry_statuses:
                pending.append(case)
        print(
            json.dumps(
                {
                    "provider": module.VENDOR_SLUG,
                    "selected": len(cases),
                    "saved_statuses": status_before,
                    "to_run": len(pending),
                    "concurrency": CONCURRENCY[module.VENDOR_SLUG],
                }
            ),
            flush=True,
        )
        if args.dry_run:
            summary["providers"][module.VENDOR_SLUG] = {
                "saved_statuses": status_before,
                "calls_planned": len(pending),
                "missing_environment": [
                    key for key in module.REQUIRED_ENV if not os.environ.get(key)
                ],
            }
            continue
        completed: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=CONCURRENCY[module.VENDOR_SLUG]
        ) as pool:
            futures = {
                pool.submit(run_one, module, case, args.output_root): case
                for case in pending
            }
            for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
                row = future.result()
                completed.append(row)
                print(
                    json.dumps(
                        {
                            "provider": module.VENDOR_SLUG,
                            "completed": index,
                            "of": len(pending),
                            "case_slug": row.get("case_slug"),
                            "status": row.get("status"),
                            "latency_ms": row.get("latency_ms"),
                        }
                    ),
                    flush=True,
                )
        summary["providers"][module.VENDOR_SLUG] = {
            "new_calls": len(completed),
            "ok": sum(row.get("status") == "ok" for row in completed),
            "not_found": sum(row.get("status") == "not_found" for row in completed),
            "error": sum(row.get("status") == "error" for row in completed),
        }
    if not args.dry_run:
        atomic_write(args.output_root / "latest-run-summary.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
