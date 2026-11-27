"""Cost-vs-accuracy, latency, and failure-mode analytics for benchmark runs."""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from judge.grader import grade_row, wilson95

from .models import FIELDS

ROOT = Path(__file__).resolve().parent.parent


def load_pricing(tools_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Load per-setup pricing metadata from `tools/*.yaml`."""
    directory = tools_dir or (ROOT / "tools")
    pricing: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        for setup_name, setup_cfg in (doc.get("setups") or {}).items():
            raw = (setup_cfg or {}).get("pricing") or {}
            pricing[setup_name] = {
                "usd": raw.get("usd"),
                "unit": raw.get("unit"),
                "observed_usd_per_company": raw.get("observed_usd_per_company"),
            }
    return pricing


def percentile(values: list[float], pct: float) -> float | None:
    """Compute linear-interpolated percentile (0-100) for a non-empty list."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (pct / 100.0) * (len(ordered) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return float(ordered[lo])
    frac = pos - lo
    return float(ordered[lo] * (1.0 - frac) + ordered[hi] * frac)


def compute_row_cost_usd(pred_row: dict[str, Any], setup_pricing: dict[str, Any] | None) -> float | None:
    """Estimate USD cost for a single prediction row using `cost_units` or `observed_usd_per_company`."""
    if not setup_pricing:
        return None
    unit_usd = setup_pricing.get("usd")
    cost_units = pred_row.get("cost_units")
    if cost_units is not None and unit_usd is not None:
        return float(cost_units) * float(unit_usd)
    observed = setup_pricing.get("observed_usd_per_company")
    if observed is not None:
        return float(observed)
    return None


def _infer_setup_name(pred_rows: list[dict[str, Any]], explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for row in pred_rows:
        if row.get("setup"):
            return str(row["setup"])
    return None


def analyze_predictions(
    truth_rows: list[dict[str, Any]],
    pred_rows: list[dict[str, Any]],
    setup_name: str | None = None,
    pricing_by_setup: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute accuracy, Wilson CIs, coverage, latency percentiles, cost efficiency, and failure modes."""
    truth_by_domain = {str(r["domain"]).strip().lower(): r for r in truth_rows}
    preds_by_domain = {str(r["domain"]).strip().lower(): r for r in pred_rows}

    resolved_setup = _infer_setup_name(pred_rows, setup_name)
    pricing_map = pricing_by_setup if pricing_by_setup is not None else load_pricing()
    setup_pricing = pricing_map.get(resolved_setup or "")

    field_counts: dict[str, list[int]] = {f: [0, 0, 0] for f in FIELDS}  # [correct, scored, not_scoreable]
    status_counts: Counter[str] = Counter()
    error_counts: Counter[str] = Counter()
    latencies: list[float] = []
    known_costs: list[float] = []
    total_units = 0.0
    has_units = False

    for domain, truth in truth_by_domain.items():
        pred = preds_by_domain.get(domain)
        if pred is None:
            status_counts["missing"] += 1
            pred_for_grade: dict[str, Any] = {}
        else:
            status = str(pred.get("status") or "ok")
            status_counts[status] += 1
            if status == "error":
                msg = str(pred.get("error") or "unknown error").strip().splitlines()[0][:120]
                error_counts[msg] += 1
            if pred.get("latency_ms") is not None:
                latencies.append(float(pred["latency_ms"]))
            if pred.get("cost_units") is not None:
                total_units += float(pred["cost_units"])
                has_units = True
            row_cost = compute_row_cost_usd(pred, setup_pricing)
            if row_cost is not None:
                known_costs.append(row_cost)
            pred_for_grade = pred

        verdicts = grade_row(truth, pred_for_grade)
        for field, verdict in verdicts.items():
            if verdict == "correct":
                field_counts[field][0] += 1
                field_counts[field][1] += 1
            elif verdict == "wrong":
                field_counts[field][1] += 1
            elif verdict == "not_scoreable":
                field_counts[field][2] += 1

    fields_summary: dict[str, dict[str, Any]] = {}
    total_correct = 0
    total_scored = 0
    for field, (c, n, ns) in field_counts.items():
        p, lo, hi = wilson95(c, n)
        fields_summary[field] = {
            "correct": c,
            "total": n,
            "not_scoreable": ns,
            "accuracy": p,
            "ci_low": lo,
            "ci_high": hi,
        }
        total_correct += c
        total_scored += n

    ov_p, ov_lo, ov_hi = wilson95(total_correct, total_scored)
    companies = len(truth_by_domain)
    ok_count = status_counts.get("ok", 0)
    total_usd = sum(known_costs) if known_costs else None
    usd_per_company = (total_usd / companies) if (total_usd is not None and companies > 0) else None

    return {
        "setup": resolved_setup or "unknown",
        "companies": companies,
        "coverage_rate": (ok_count / companies) if companies > 0 else 0.0,
        "status_counts": {
            "ok": status_counts.get("ok", 0),
            "not_found": status_counts.get("not_found", 0),
            "error": status_counts.get("error", 0),
            "missing": status_counts.get("missing", 0),
        },
        "overall": {
            "correct": total_correct,
            "total": total_scored,
            "accuracy": ov_p,
            "ci_low": ov_lo,
            "ci_high": ov_hi,
        },
        "fields": fields_summary,
        "latency_ms": {
            "count": len(latencies),
            "mean": (sum(latencies) / len(latencies)) if latencies else None,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
        },
        "cost": {
            "total_usd": total_usd,
            "usd_per_company": usd_per_company,
            "usd_per_1k_companies": (usd_per_company * 1000.0) if usd_per_company is not None else None,
            "usd_per_correct_field": (total_usd / total_correct) if (total_usd is not None and total_correct > 0) else None,
            "total_units": total_units if has_units else None,
            "unit": (setup_pricing or {}).get("unit"),
        },
        "top_errors": [{"message": msg, "count": cnt} for msg, cnt in error_counts.most_common(5)],
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def compare_run_dir(
    truth_rows: list[dict[str, Any]],
    run_dir: Path,
    pricing_by_setup: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Analyze every `<setup>.jsonl` in `run_dir` and sort by accuracy desc, then cost/correct asc."""
    pricing_map = pricing_by_setup if pricing_by_setup is not None else load_pricing()
    summaries = []
    for pred_file in sorted(run_dir.glob("*.jsonl")):
        pred_rows = _load_jsonl(pred_file)
        summaries.append(analyze_predictions(truth_rows, pred_rows, setup_name=pred_file.stem, pricing_by_setup=pricing_map))

    def sort_key(item: dict[str, Any]) -> tuple[float, float]:
        acc = item["overall"]["accuracy"]
        cpf = item["cost"]["usd_per_correct_field"]
        return (-acc, cpf if cpf is not None else float("inf"))

    summaries.sort(key=sort_key)
    return summaries


def _fmt_ms(val: float | None) -> str:
    return f"{val:.0f}ms" if val is not None else "-"


def _fmt_usd(val: float | None, decimals: int = 4) -> str:
    return f"${val:.{decimals}f}" if val is not None else "-"


def format_single_analytics(summary: dict[str, Any]) -> str:
    """Format detailed analytics for a single setup."""
    lines = [f"Setup: {summary['setup']} ({summary['companies']} companies)"]
    for field, stats in summary["fields"].items():
        lines.append(
            f"{field:14s} {stats['correct']}/{stats['total']} = {stats['accuracy']*100:5.1f}%  "
            f"(95% CI {stats['ci_low']*100:4.1f}%-{stats['ci_high']*100:4.1f}%)"
        )
    ov = summary["overall"]
    st = summary["status_counts"]
    lat = summary["latency_ms"]
    cost = summary["cost"]
    lines.append("-" * 64)
    lines.append(
        f"{'overall':14s} {ov['correct']}/{ov['total']} = {ov['accuracy']*100:5.1f}%  "
        f"(95% CI {ov['ci_low']*100:4.1f}%-{ov['ci_high']*100:4.1f}%)"
    )
    lines.append(
        f"Status:        ok={st['ok']} ({summary['coverage_rate']*100:.1f}%), "
        f"not_found={st['not_found']}, error={st['error']}, missing={st['missing']}"
    )
    lines.append(
        f"Latency:       p50={_fmt_ms(lat['p50'])}, p95={_fmt_ms(lat['p95'])}, mean={_fmt_ms(lat['mean'])}"
    )
    lines.append(
        f"Cost:          total={_fmt_usd(cost['total_usd'])}, "
        f"$/1k_co={_fmt_usd(cost['usd_per_1k_companies'], 2)}, "
        f"$/correct_field={_fmt_usd(cost['usd_per_correct_field'], 5)}"
    )
    if summary["top_errors"]:
        lines.append("Top errors:")
        for err in summary["top_errors"]:
            lines.append(f"  [{err['count']}x] {err['message']}")
    return "\n".join(lines)


def format_comparison_table(summaries: list[dict[str, Any]]) -> str:
    """Format a multi-setup Pareto comparison table for terminal display."""
    if not summaries:
        return "No prediction JSONL files found."
    header = (
        f"{'Setup':24s} {'Acc':>6s} {'Cov':>6s} {'p50':>7s} {'p95':>7s} "
        f"{'Total$':>8s} {'$/1k':>8s} {'$/Hit':>9s} {'ok/nf/err/mis':>15s}"
    )
    lines = [header, "-" * len(header)]
    for s in summaries:
        st = s["status_counts"]
        st_str = f"{st['ok']}/{st['not_found']}/{st['error']}/{st['missing']}"
        lines.append(
            f"{s['setup'][:24]:24s} "
            f"{s['overall']['accuracy']*100:5.1f}% "
            f"{s['coverage_rate']*100:5.1f}% "
            f"{_fmt_ms(s['latency_ms']['p50']):>7s} "
            f"{_fmt_ms(s['latency_ms']['p95']):>7s} "
            f"{_fmt_usd(s['cost']['total_usd'], 3):>8s} "
            f"{_fmt_usd(s['cost']['usd_per_1k_companies'], 2):>8s} "
            f"{_fmt_usd(s['cost']['usd_per_correct_field'], 4):>9s} "
            f"{st_str:>15s}"
        )
    errors_present = [s for s in summaries if s["top_errors"]]
    if errors_present:
        lines.append("")
        lines.append("Failure breakdown:")
        for s in errors_present:
            top = "; ".join(f"{e['count']}x {e['message']}" for e in s["top_errors"][:3])
            lines.append(f"  {s['setup']}: {top}")
    return "\n".join(lines)
