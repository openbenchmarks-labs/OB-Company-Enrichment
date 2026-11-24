"""Judge saved provider cells with the dedicated field judges and build the board.

The provider calls and the ground truth remain separated: raw files are created
without reference values, then this script gives only saved provider values and
reference values to one judge call per provider and field. Headcount uses
GPT-5.6 Sol with the numeric headcount prompt; the other fields use GPT-5.6 Terra.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from harness.common import (
    SCORED_ATTRIBUTES,
    CompanyCase,
    NormalizedCompany,
    ProviderResult,
    benchmark_metrics,
    now_iso,
    recompute_leaderboard,
)
from judge.field_judge_prompts import (
    HEADCOUNT_SOL_PROMPT,
    HEADCOUNT_SOL_PROMPT_VERSION,
    MODEL as TERRA_MODEL,
    PROMPTS,
    PROMPT_VERSION,
    REASONING_EFFORT,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "data" / "sample.json"
DEFAULT_ROOT = ROOT / "runs"
FIELDS = tuple(SCORED_ATTRIBUTES)
SOL_MODEL = "gpt-5.6-sol"


class Verdict(BaseModel):
    case_slug: str
    provider_present: bool
    is_correct: bool
    rationale: str


class JudgeOutput(BaseModel):
    provider_slug: str
    verdicts: list[Verdict]


def reference(case: dict[str, Any]) -> dict[str, Any]:
    return case.get("reference_attributes") or case.get("reference") or {}


def value(record: dict[str, Any] | None, field: str) -> Any:
    record = record or {}
    if field == "hq_location":
        result = {"country": record.get("hq_country"), "city": record.get("hq_city")}
        return result if any(result.values()) else None
    if field == "headcount_band":
        result = {"min": record.get("headcount_min"), "max": record.get("headcount_max")}
        return result if any(item is not None for item in result.values()) else None
    if field == "industry":
        return [item for item in [record.get("industry"), *(record.get("industries") or [])] if item] or None
    return record.get(field)


def reference_value(case: dict[str, Any], field: str) -> Any:
    ref = reference(case)
    if field != "linkedin_url":
        return value(ref, field)
    alternates = ref.get("linkedin_url_alternates") or []
    if isinstance(alternates, str):
        alternates = [item.strip() for item in alternates.replace("\n", ",").split(",") if item.strip()]
    return [item for item in [ref.get("linkedin_url"), *alternates] if item] or None


def load_raw(snapshot: dict[str, Any], root: Path, provider: str) -> list[dict[str, Any]]:
    rows = []
    for case in snapshot["cases"]:
        path = root / "raw" / provider / f"{case['case_slug']}.json"
        if not path.exists():
            path = root / "cells-v1" / f"{case['case_slug']}__{provider}.json"
        if not path.exists():
            raise RuntimeError(f"{provider}: missing raw checkpoint for {case['case_slug']}")
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("case_slug") != case["case_slug"] or row.get("provider_slug") != provider:
            raise RuntimeError(f"{provider}/{case['case_slug']}: raw checkpoint identity mismatch")
        rows.append(row)
    return rows


def judge_rows(cases: list[dict[str, Any]], runs: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    by_slug = {row["case_slug"]: row for row in runs}
    rows = []
    for case in cases:
        expected = reference_value(case, field)
        if expected in (None, [], {}):
            continue
        ref = reference(case)
        rows.append({
            "case_slug": case["case_slug"],
            "reference": expected,
            "provider_value": value(by_slug[case["case_slug"]].get("normalized"), field),
            "alternate_primary_domains": ref.get("alternate_primary_domains") or [],
            "reference_exact_headcount": ref.get("headcount_exact"),
        })
    return rows


def checkpoint(root: Path, provider: str, field: str) -> Path:
    return root / "judges" / provider / f"{field}.json"


def judge_one(root: Path, provider: str, field: str, rows: list[dict[str, Any]], force: bool) -> dict[str, Any]:
    path = checkpoint(root, provider, field)
    if path.exists() and not force:
        return {"provider": provider, "field": field, "status": "checkpoint_reused"}
    model = SOL_MODEL if field == "headcount_band" else TERRA_MODEL
    prompt = HEADCOUNT_SOL_PROMPT if field == "headcount_band" else PROMPTS[field]
    prompt_version = HEADCOUNT_SOL_PROMPT_VERSION if field == "headcount_band" else PROMPT_VERSION
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=900)
    response = client.responses.parse(
        model=model,
        reasoning={"effort": REASONING_EFFORT},
        input=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"provider_slug": provider, "field": field, "cases": rows}, separators=(",", ":"))},
        ],
        text_format=JudgeOutput,
        max_output_tokens=50_000,
        store=False,
    )
    result = response.output_parsed
    expected = {row["case_slug"] for row in rows}
    if not result or result.provider_slug != provider or len(result.verdicts) != len(rows) or {row.case_slug for row in result.verdicts} != expected:
        raise RuntimeError(f"invalid judge output for {provider}/{field}")
    payload = {
        "model": model,
        "reasoning_effort": REASONING_EFFORT,
        "prompt_version": prompt_version,
        "provider_slug": provider,
        "field": field,
        "response_id": response.id,
        "verdicts": [row.model_dump() for row in result.verdicts],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return {"provider": provider, "field": field, "status": "judged", "eligible": len(rows)}


def apply_verdicts(case: dict[str, Any], run: dict[str, Any], root: Path) -> dict[str, Any]:
    company = NormalizedCompany(**run["normalized"]) if run.get("normalized") else None
    case_model = CompanyCase(**case)
    result = ProviderResult(
        provider_slug=run["provider_slug"], provider_name=run["provider_name"],
        case_slug=run["case_slug"], status=run.get("status") or "error",
        latency_ms=run.get("latency_ms") or 0, normalized=company,
        cost_units=run.get("cost_units"), cost_unit=run.get("cost_unit"),
        ambiguity_count=run.get("ambiguity_count") or 0, audit=run.get("audit") or {},
        error=run.get("error"), queried_at=run.get("queried_at") or now_iso(),
    )
    metrics = benchmark_metrics(case_model, result)
    metrics = [row for row in metrics if not row["metric_name"].startswith("reference_correct_") and row["metric_name"] not in {"reference_accuracy_when_present_pct", "correct_field_yield_pct"}]
    for field in FIELDS:
        expected = reference_value(case, field)
        if expected in (None, [], {}):
            continue
        saved = json.loads(checkpoint(root, run["provider_slug"], field).read_text(encoding="utf-8"))
        verdicts = {row["case_slug"]: row for row in saved["verdicts"]}
        verdict = verdicts.get(case["case_slug"])
        if verdict is None:
            raise RuntimeError(f"missing verdict for {run['provider_slug']}/{field}/{case['case_slug']}")
        metrics.append({
            "metric_name": f"reference_correct_{field}",
            "metric_value": int(verdict["is_correct"]),
            "detail": {
                "judge_method": "openai_dedicated_field",
                "judge_model": saved["model"],
                "judge_reasoning_effort": saved["reasoning_effort"],
                "judge_prompt_version": saved["prompt_version"],
                "rationale": verdict["rationale"],
            },
        })
    by_name = {row["metric_name"]: row for row in metrics}
    evaluable = [field for field in FIELDS if f"reference_correct_{field}" in by_name]
    returned = [field for field in evaluable if by_name.get(f"coverage_{field}", {}).get("metric_value") == 1]
    correct = sum(by_name[f"reference_correct_{field}"]["metric_value"] for field in evaluable)
    correct_returned = sum(by_name[f"reference_correct_{field}"]["metric_value"] for field in returned)
    metrics.extend([
        {"metric_name": "reference_accuracy_when_present_pct", "metric_value": round(100 * correct_returned / len(returned), 2) if returned else None, "detail": {"truth_fields_evaluable": len(evaluable), "truth_fields_returned": len(returned)}},
        {"metric_name": "correct_field_yield_pct", "metric_value": round(100 * correct / len(evaluable), 2) if evaluable else None, "detail": {"truth_fields_evaluable": len(evaluable), "truth_fields_returned": len(returned)}},
    ])
    return {**run, "metrics": metrics}


def build_extension(snapshot: dict[str, Any], raw: dict[str, list[dict[str, Any]]], root: Path) -> dict[str, Any]:
    cases = snapshot["cases"]
    case_by_slug = {row["case_slug"]: row for row in cases}
    runs = [apply_verdicts(case_by_slug[row["case_slug"]], row, root) for provider in raw for row in raw[provider]]
    return {
        "artifact_version": "firmographic-judged-v1",
        "generated_at": now_iso(),
        "providers": list(raw),
        "cases": cases,
        "runs": runs,
        "leaderboard": recompute_leaderboard(runs, len(cases)),
        "judge_policy": {"default_model": TERRA_MODEL, "headcount_model": SOL_MODEL, "reasoning_effort": REASONING_EFFORT, "default_prompt_version": PROMPT_VERSION, "headcount_prompt_version": HEADCOUNT_SOL_PROMPT_VERSION},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", required=True, help="comma-separated provider slugs")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--run", action="store_true", help="make missing paid judge calls")
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument("--force", action="store_true", help="overwrite saved judge checkpoints")
    parser.add_argument("--build", action="store_true", help="build the judged board from complete checkpoints")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.run and not args.confirm_paid:
        parser.error("--run requires --confirm-paid")
    from harness.providers import REGISTRY
    from harness.run_extension import PROVIDERS as extension
    from harness.run_research import PROVIDERS as research
    available = {*REGISTRY, *research, *extension, "zoominfo"}
    providers = tuple(dict.fromkeys(x.strip() for x in args.only.split(",") if x.strip()))
    if not providers or set(providers) - available:
        parser.error(f"--only must name ported providers: {', '.join(sorted(available))}")
    snapshot = json.loads(args.cases.read_text(encoding="utf-8"))
    raw = {provider: load_raw(snapshot, args.output_root, provider) for provider in providers}
    jobs = [(provider, field, judge_rows(snapshot["cases"], raw[provider], field)) for provider in providers for field in FIELDS]
    print(json.dumps({"cases_file": str(args.cases), "providers": list(providers), "raw_runs": sum(map(len, raw.values())), "judge_calls": len(jobs), "saved_judge_calls": sum(checkpoint(args.output_root, provider, field).exists() for provider, field, _ in jobs), "run": args.run, "build": args.build}, indent=2), flush=True)
    if args.run:
        load_dotenv(ROOT / ".env", override=False)
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required")
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(judge_one, args.output_root, provider, field, rows, args.force) for provider, field, rows in jobs]
            for future in concurrent.futures.as_completed(futures):
                print(json.dumps(future.result()), flush=True)
    if args.build:
        result = build_extension(snapshot, raw, args.output_root)
        output = args.output_root / "judged.json"
        temporary = output.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)
        print(json.dumps({"output": str(output), "runs": len(result["runs"]), "providers": len(result["leaderboard"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
