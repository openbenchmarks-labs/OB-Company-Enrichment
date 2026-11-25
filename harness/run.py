#!/usr/bin/env python3
"""CLI runner for the Company Enrichment benchmark (selftest, list-tools, grade)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

from judge.grader import grade_row, wilson95

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("hq", "industry", "employee_band", "founded_year", "linkedin_url")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_tools() -> dict[str, dict[str, Any]]:
    tools = {}
    for p in sorted((ROOT / "tools").glob("*.yaml")):
        doc = yaml.safe_load(p.read_text()) or {}
        tools[doc.get("tool", p.stem)] = doc.get("setups") or {}
    return tools


def cmd_selftest(tasks_path: Path) -> int:
    rows = load_jsonl(tasks_path)
    tools = load_tools()
    failed = 0
    for row in rows:
        gold = grade_row(row, row)
        empty = grade_row(row, {})
        if not all(v == "correct" for v in gold.values()):
            print(f"FAIL gold {row['domain']}: {gold}")
            failed += 1
        if not all(v == "wrong" for v in empty.values()):
            print(f"FAIL empty {row['domain']}: {empty}")
            failed += 1
    print(f"judge gold/empty ok={len(rows) - failed} failed={failed}")
    print(f"tools={len(tools)} setups={sum(len(v) for v in tools.values())} tasks={len(rows)}")
    return 1 if failed else 0


def cmd_grade(tasks_path: Path, preds_path: Path, analytics: bool = False, json_out: Path | None = None) -> int:
    truth_rows = load_jsonl(tasks_path)
    pred_rows = load_jsonl(preds_path)
    if analytics or json_out is not None:
        from .analytics import analyze_predictions, format_single_analytics

        summary = analyze_predictions(truth_rows, pred_rows, setup_name=preds_path.stem)
        print(format_single_analytics(summary))
        if json_out is not None:
            json_out.parent.mkdir(parents=True, exist_ok=True)
            json_out.write_text(json.dumps(summary, indent=2))
        return 0

    truth_by_domain = {r["domain"].lower(): r for r in truth_rows}
    preds_by_domain = {r["domain"].lower(): r for r in pred_rows}
    counts = {f: [0, 0] for f in FIELDS}
    for domain, truth in truth_by_domain.items():
        verdicts = grade_row(truth, preds_by_domain.get(domain, {}))
        for field, verdict in verdicts.items():
            if verdict in ("correct", "wrong"):
                counts[field][1] += 1
                counts[field][0] += int(verdict == "correct")
    for field, (c, n) in counts.items():
        p, lo, hi = wilson95(c, n)
        print(f"{field:14s} {c}/{n} = {p*100:5.1f}%  (95% CI {lo*100:4.1f}%-{hi*100:4.1f}%)")
    return 0


def cmd_report(tasks_path: Path, run_dir: Path, json_out: Path | None = None) -> int:
    from .analytics import compare_run_dir, format_comparison_table

    truth_rows = load_jsonl(tasks_path)
    summaries = compare_run_dir(truth_rows, run_dir)
    print(format_comparison_table(summaries))
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(json.dumps(summaries, indent=2))
    return 0


def cmd_run(tasks_path: Path, setup_names: list[str] | None, out: Path, execute: bool, workers: int) -> int:
    from dotenv import load_dotenv

    from . import registry, runner

    load_dotenv(ROOT / ".env")
    companies = runner.load_companies(tasks_path)
    adapters = [registry.build(name) for name in (setup_names or registry.main_setups())]
    rows = runner.plan(adapters, companies, out)
    for row in rows:
        missing = f"  missing: {', '.join(row['missing_env'])}" if row["missing_env"] else ""
        print(f"{row['setup']:32s} {row['pending']:4d} of {len(companies)} companies to look up{missing}")
    blocked = [row["setup"] for row in rows if row["missing_env"] and row["pending"]]
    if not execute:
        print(f"\nPlan only. Add --execute to call the tools; this spends money. Answers go to {out}/")
        return 0
    if blocked:
        print(f"\nNot running: missing credentials for {', '.join(blocked)}")
        return 1
    for adapter in adapters:
        counts = runner.run_setup(adapter, companies, out, workers)
        print(f"{adapter.setup}: {counts}", flush=True)
    print(f"\nAnswers are in {out}/<setup>.jsonl. Grade one with: python -m harness.run grade --tasks {tasks_path} --predictions {out}/<setup>.jsonl")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Company Enrichment benchmark CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_self = sub.add_parser("selftest", help="Offline check of grader, configs, and sample data")
    p_self.add_argument("--tasks", type=Path, default=ROOT / "data/sample.jsonl")
    p_run = sub.add_parser("run", help="Ask each setup about every company and save the answers")
    p_run.add_argument("--tasks", type=Path, required=True, help="JSONL with a `domain` and `company` per line")
    p_run.add_argument("--setups", help="Comma-separated setup names (default: every main setup)")
    p_run.add_argument("--out", type=Path, default=None, help="Run folder (default: runs/<UTC time>). Reuse it to resume.")
    p_run.add_argument("--execute", action="store_true", help="Call the tools. Without it, only print the plan.")
    p_run.add_argument("--workers", type=int, default=4, help="Parallel requests per setup")
    p_grade = sub.add_parser("grade", help="Grade a predictions JSONL against ground truth")
    p_grade.add_argument("--tasks", type=Path, required=True)
    p_grade.add_argument("--predictions", type=Path, required=True)
    p_grade.add_argument("--analytics", action="store_true", help="Print cost, latency, coverage, and failure breakdown")
    p_grade.add_argument("--json", type=Path, default=None, help="Optional path to write analytics JSON")
    p_report = sub.add_parser("report", help="Compare all setup JSONLs in a run folder (accuracy, cost, latency, errors)")
    p_report.add_argument("--tasks", type=Path, required=True)
    p_report.add_argument("--run-dir", type=Path, required=True)
    p_report.add_argument("--json", type=Path, default=None, help="Optional path to write comparison JSON")
    sub.add_parser("list-tools", help="List configured tools and setups")
    args = parser.parse_args()
    if args.cmd == "selftest":
        return cmd_selftest(args.tasks)
    if args.cmd == "run":
        from datetime import datetime, timezone

        out = args.out or ROOT / "runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        names = [s.strip() for s in args.setups.split(",") if s.strip()] if args.setups else None
        return cmd_run(args.tasks, names, out, args.execute, args.workers)
    if args.cmd == "grade":
        return cmd_grade(args.tasks, args.predictions, analytics=args.analytics, json_out=args.json)
    if args.cmd == "report":
        return cmd_report(args.tasks, args.run_dir, json_out=args.json)
    if args.cmd == "list-tools":
        for tool, setups in load_tools().items():
            print(f"{tool}: {', '.join(setups)}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
