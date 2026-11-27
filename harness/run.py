#!/usr/bin/env python3
"""CLI runner for the Company Enrichment benchmark (selftest, run, grade, list-tools)."""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from judge.grader import CONFIG, grade_row, wilson95

from . import runner

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ("hq", "industry", "employee_band", "founded_year", "linkedin_url")
# Field names in graded.json, as the board's packager reads them.
GRADED_NAMES = {"hq": "hq_location", "industry": "industry", "employee_band": "headcount_band",
                "founded_year": "founded_year", "linkedin_url": "linkedin_url"}


def load_tools() -> dict[str, dict[str, Any]]:
    tools = {}
    for p in sorted((ROOT / "tools").glob("*.yaml")):
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        tools[doc.get("tool", p.stem)] = doc.get("setups") or {}
    return tools


def cmd_selftest(tasks_path: Path) -> int:
    rows = runner.load_rows(tasks_path)
    tools = load_tools()
    failed = 0
    for row in rows:
        truth, domain = runner.truth_of(row), runner.company_of(row).domain
        gold, empty = grade_row(truth, truth), grade_row(truth, {})
        # A field the key leaves empty is not scored; every other field is right for the key itself and wrong for no answer.
        if any(g != ("not_scoreable" if empty[f] == "not_scoreable" else "correct") for f, g in gold.items()):
            print(f"FAIL gold {domain}: {gold}")
            failed += 1
        if not all(v in ("wrong", "not_scoreable") for v in empty.values()):
            print(f"FAIL empty {domain}: {empty}")
            failed += 1
    print(f"judge gold/empty ok={len(rows) - failed} failed={failed}")
    print(f"tools={len(tools)} setups={sum(len(v) for v in tools.values())} tasks={len(rows)}")
    return 1 if failed else 0


def _present(value: Any) -> bool:
    return value not in (None, "", [])


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 2) if whole else None


def _graded_run(setup: str, spec: dict[str, Any], case_slug: str, answer: dict[str, Any], verdicts: dict[str, str],
                raw: Any) -> dict[str, Any]:
    """One setup's answer for one company, with each field's verdict and the company's counts."""
    ok = answer.get("status") == "ok"
    present = {f: ok and _present(answer.get(f)) for f in FIELDS}
    scored = [f for f in FIELDS if verdicts[f] in ("correct", "wrong")]
    correct = sum(verdicts[f] == "correct" for f in scored)
    returned = sum(present[f] for f in scored)
    counts = {"truth_fields_evaluable": len(scored), "truth_fields_returned": returned}
    metrics = [{"metric_name": "resolved", "metric_value": int(ok), "detail": {}}]
    metrics += [{"metric_name": f"coverage_{GRADED_NAMES[f]}", "metric_value": int(present[f]), "detail": {}} for f in FIELDS]
    metrics.append({"metric_name": "attribute_coverage_pct", "metric_value": _pct(sum(present.values()), len(FIELDS)),
                    "detail": {"attribute_count": len(FIELDS)}})
    for f in scored:
        rationale = getattr(verdicts[f], "rationale", None)
        model = CONFIG["judge"]["fields"][f].get("model") or {}
        detail = ({"judge_method": "model", "judge_model": model.get("name"), "judge_reasoning_effort": model.get("effort"),
                   "rationale": rationale} if rationale is not None else {"judge_method": "code", "rationale": None})
        metrics.append({"metric_name": f"reference_correct_{GRADED_NAMES[f]}", "metric_value": int(verdicts[f] == "correct"),
                        "detail": detail})
    metrics.append({"metric_name": "reference_accuracy_when_present_pct", "metric_value": _pct(correct, returned), "detail": counts})
    metrics.append({"metric_name": "correct_field_yield_pct", "metric_value": _pct(correct, len(scored)), "detail": counts})
    normalized = ({"industry": answer.get("industry"), "founded_year": answer.get("founded_year"),
                   "linkedin_url": answer.get("linkedin_url"), **(answer.get("extra") or {})} if ok else None)
    return {
        "provider_slug": setup, "provider_name": setup, "case_slug": case_slug, "status": answer.get("status"),
        "latency_ms": answer.get("latency_ms"), "normalized": normalized, "cost_units": answer.get("cost_units"),
        "cost_unit": answer.get("cost_unit"), "error": answer.get("error"), "queried_at": answer.get("queried_at"),
        "audit": {"request": {"endpoint": spec.get("endpoint"), "settings": spec.get("settings")}, "raw_response": raw},
        "metrics": metrics,
    }


def cmd_grade(tasks_path: Path, run_dir: Path, setup_names: list[str] | None, workers: int) -> int:
    from dotenv import load_dotenv

    from . import registry

    load_dotenv(ROOT / ".env")
    rows = runner.load_rows(tasks_path)
    tasks = [(row, runner.company_of(row), runner.truth_of(row)) for row in rows]
    setups = setup_names or sorted(p.stem for p in run_dir.glob("*.jsonl"))
    specs = registry.setups()
    jobs = []
    for setup in setups:
        # A resumed run appends; the last answer for a company is the one that counts. A company with no answer or an
        # error is graded wrong, as on the board; `run` asks again for it.
        answers = {a["domain"]: a for a in runner.load_rows(runner.answers_path(run_dir, setup))}
        missing = [company.domain for _, company, _ in tasks if answers.get(company.domain, {}).get("status") in (None, "error")]
        if missing:
            print(f"{setup}: no answer for {len(missing)} of {len(tasks)} companies (missing or error), graded wrong; run it again to fill them")
        jobs += [(setup, row, company, truth, answers.get(company.domain, {})) for row, company, truth in tasks]

    def grade(job: tuple) -> dict[str, Any]:
        setup, row, company, truth, answer = job
        raw_path = run_dir / "raw" / setup / f"{company.domain}.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else None
        spec = specs[setup].spec if setup in specs else {}
        return _graded_run(setup, spec, row.get("case_id") or company.domain, answer, grade_row(truth, answer), raw)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        graded = list(pool.map(grade, jobs))

    skip = {"case_id", "slice", "input_domain", "input_name", "domain", "company"}
    cases = [{"case_slug": row.get("case_id") or company.domain, "slice": row.get("slice"), "input_name": company.name,
              "input_domain": company.domain, "reference": {k: v for k, v in row.items() if k not in skip}}
             for row, company, _ in tasks]
    out = run_dir / "graded.json"
    out.write_text(json.dumps({
        "dataset": tasks_path.name,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "providers": setups,
        "cases": cases,
        "runs": graded,
    }, indent=1, ensure_ascii=False, default=str) + "\n",encoding="utf-8")

    for setup in setups:
        print(setup)
        for field in FIELDS:
            name = f"reference_correct_{GRADED_NAMES[field]}"
            values = [m["metric_value"] for run in graded if run["provider_slug"] == setup
                      for m in run["metrics"] if m["metric_name"] == name]
            p, lo, hi = wilson95(sum(values), len(values))
            print(f"  {field:14s} {sum(values)}/{len(values)} = {p*100:5.1f}%  (95% CI {lo*100:4.1f}%-{hi*100:4.1f}%)")
    print(f"\nVerdicts are in {out}")
    return 0


def cmd_run(tasks_path: Path, setup_names: list[str] | None, out: Path, execute: bool, workers: int) -> int:
    from dotenv import load_dotenv

    from . import registry

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
    print(f"\nAnswers are in {out}/<setup>.jsonl. Grade them with: python -m harness.run grade --tasks {tasks_path} --run {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Company Enrichment benchmark CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_self = sub.add_parser("selftest", help="Offline check of grader, configs, and sample data")
    p_self.add_argument("--tasks", type=Path, default=ROOT / "data/sample.jsonl")
    p_run = sub.add_parser("run", help="Ask each setup about every company and save the answers")
    p_run.add_argument("--tasks", type=Path, required=True,
                       help="JSONL of companies: a Hugging Face dataset file, or rows with a `domain` and `company`")
    p_run.add_argument("--setups", help="Comma-separated setup names (default: every main setup)")
    p_run.add_argument("--out", type=Path, default=None, help="Run folder (default: runs/<UTC time>). Reuse it to resume.")
    p_run.add_argument("--execute", action="store_true", help="Call the tools. Without it, only print the plan.")
    p_run.add_argument("--workers", type=int, default=4, help="Parallel requests per setup")
    p_grade = sub.add_parser("grade", help="Grade a run's answers against ground truth and save the verdicts")
    p_grade.add_argument("--tasks", type=Path, required=True, help="The ground truth the run asked about")
    p_grade.add_argument("--run", type=Path, required=True, help="Run folder; graded.json is written into it")
    p_grade.add_argument("--setups", help="Comma-separated setup names (default: every setup in the run folder)")
    p_grade.add_argument("--workers", type=int, default=8, help="Companies graded at once (model judge calls)")
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
        names = [s.strip() for s in args.setups.split(",") if s.strip()] if args.setups else None
        return cmd_grade(args.tasks, args.run, names, args.workers)
    if args.cmd == "list-tools":
        for tool, setups in load_tools().items():
            print(f"{tool}: {', '.join(setups)}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
