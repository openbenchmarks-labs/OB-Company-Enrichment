"""Offline unit tests for cost-vs-accuracy, latency, and failure-mode analytics."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness.analytics import (
    analyze_predictions,
    compare_run_dir,
    compute_row_cost_usd,
    format_comparison_table,
    format_single_analytics,
    load_pricing,
    percentile,
)

ROOT = Path(__file__).resolve().parent.parent


def _truth_rows() -> list[dict]:
    return [
        {
            "domain": "stripe.com",
            "company": "Stripe",
            "hq": "South San Francisco, United States",
            "industry": "Financial Services",
            "employee_band": "5001-10000",
            "founded_year": 2010,
            "linkedin_url": "https://www.linkedin.com/company/stripe",
        },
        {
            "domain": "figma.com",
            "company": "Figma",
            "hq": "San Francisco, United States",
            "industry": "Software Development",
            "employee_band": "1001-5000",
            "founded_year": 2012,
            "linkedin_url": "https://www.linkedin.com/company/figma",
        },
        {
            "domain": "vercel.com",
            "company": "Vercel",
            "hq": "San Francisco, United States",
            "industry": "Software Development",
            "employee_band": "501-1000",
            "founded_year": 2015,
            "linkedin_url": "https://www.linkedin.com/company/vercel",
        },
    ]


class AnalyticsTests(unittest.TestCase):
    def test_load_pricing_reads_tools_yaml(self) -> None:
        pricing = load_pricing()
        self.assertIn("apollo", pricing)
        self.assertAlmostEqual(pricing["apollo"]["usd"], 0.0196)
        self.assertEqual(pricing["apollo"]["unit"], "credit")

    def test_percentile_interpolation(self) -> None:
        self.assertIsNone(percentile([], 50))
        self.assertEqual(percentile([100.0], 95), 100.0)
        self.assertEqual(percentile([100.0, 200.0, 300.0, 400.0], 50), 250.0)
        self.assertAlmostEqual(percentile([100.0, 200.0, 300.0, 400.0], 95), 385.0)

    def test_compute_row_cost_usd_variants(self) -> None:
        self.assertAlmostEqual(
            compute_row_cost_usd({"cost_units": 2}, {"usd": 0.01, "observed_usd_per_company": None}),
            0.02,
        )
        self.assertAlmostEqual(
            compute_row_cost_usd({"cost_units": None}, {"usd": None, "observed_usd_per_company": 0.05}),
            0.05,
        )
        self.assertIsNone(compute_row_cost_usd({"cost_units": 1}, {"usd": None, "observed_usd_per_company": None}))

    @mock.patch("judge.grader._model_grade", return_value="wrong")
    def test_analyze_predictions_metrics_and_failures(self, _mock_model: mock.MagicMock) -> None:
        truth = _truth_rows()
        preds = [
            {
                "domain": "stripe.com",
                "hq": "San Francisco, United States",
                "industry": "Financial Services",
                "employee_band": "5001-10000",
                "founded_year": 2010,
                "linkedin_url": "https://www.linkedin.com/company/stripe",
                "status": "ok",
                "latency_ms": 120,
                "cost_units": 1,
                "setup": "apollo",
            },
            {
                "domain": "figma.com",
                "status": "error",
                "error": "HTTPError: 429 Too Many Requests",
                "latency_ms": 300,
                "cost_units": 0,
                "setup": "apollo",
            },
            # vercel.com intentionally omitted -> counted as missing
        ]
        summary = analyze_predictions(
            truth,
            preds,
            pricing_by_setup={"apollo": {"usd": 0.02, "unit": "credit", "observed_usd_per_company": None}},
        )
        self.assertEqual(summary["setup"], "apollo")
        self.assertEqual(summary["companies"], 3)
        self.assertEqual(summary["status_counts"], {"ok": 1, "not_found": 0, "error": 1, "missing": 1})
        self.assertAlmostEqual(summary["coverage_rate"], 1 / 3)
        self.assertEqual(summary["overall"]["correct"], 5)
        self.assertEqual(summary["overall"]["total"], 15)
        self.assertAlmostEqual(summary["overall"]["accuracy"], 5 / 15)
        self.assertAlmostEqual(summary["cost"]["total_usd"], 0.02)
        self.assertAlmostEqual(summary["cost"]["usd_per_correct_field"], 0.02 / 5)
        self.assertEqual(summary["latency_ms"]["p50"], 210.0)
        self.assertEqual(summary["top_errors"], [{"message": "HTTPError: 429 Too Many Requests", "count": 1}])
        rendered = format_single_analytics(summary)
        self.assertIn("Setup: apollo", rendered)
        self.assertIn("429 Too Many Requests", rendered)

    @mock.patch("judge.grader._model_grade", return_value="wrong")
    def test_compare_run_dir_and_cli_report(self, _mock_model: mock.MagicMock) -> None:
        truth = _truth_rows()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tasks_file = tmp_path / "tasks.jsonl"
            tasks_file.write_text("\n".join(json.dumps(r) for r in truth) + "\n")

            run_dir = tmp_path / "run"
            run_dir.mkdir()
            # Setup A: 100% accurate on all 3 companies, $0.05/company
            (run_dir / "parallel-research.jsonl").write_text(
                "\n".join(json.dumps({**r, "status": "ok", "latency_ms": 500, "cost_units": 1}) for r in truth) + "\n"
            )
            # Setup B: only 1 company ok, 2 not_found
            (run_dir / "apollo.jsonl").write_text(
                "\n".join([
                    json.dumps({**truth[0], "status": "ok", "latency_ms": 100, "cost_units": 1}),
                    json.dumps({"domain": "figma.com", "status": "not_found", "latency_ms": 80, "cost_units": 0}),
                    json.dumps({"domain": "vercel.com", "status": "not_found", "latency_ms": 90, "cost_units": 0}),
                ]) + "\n"
            )

            summaries = compare_run_dir(truth, run_dir)
            self.assertEqual([s["setup"] for s in summaries], ["parallel-research", "apollo"])
            table = format_comparison_table(summaries)
            self.assertIn("parallel-research", table)
            self.assertIn("apollo", table)

            # End-to-end CLI check for `report` and `grade --analytics`
            json_out = tmp_path / "report.json"
            proc = subprocess.run(
                [sys.executable, "-B", "-m", "harness.run", "report", "--tasks", str(tasks_file), "--run-dir", str(run_dir), "--json", str(json_out)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("parallel-research", proc.stdout)
            exported = json.loads(json_out.read_text())
            self.assertEqual(len(exported), 2)

            proc_grade = subprocess.run(
                [
                    sys.executable, "-B", "-m", "harness.run", "grade",
                    "--tasks", str(tasks_file),
                    "--predictions", str(run_dir / "apollo.jsonl"),
                    "--analytics",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("Status:", proc_grade.stdout)
            self.assertIn("Cost:", proc_grade.stdout)


if __name__ == "__main__":
    unittest.main()
