"""Parallel Task API (setup parallel-task).

The task asks for the same fields under the same rules as the other web-research tools, written the way Parallel's
Task API documents (docs.parallel.ai/task-api/best-practices): the company as structured input, an instruction for
each field in its description, and the rules for the whole answer in the schema's top-level description. Sources are
not asked for as a field; Parallel cites URLs for every field in the result's `basis`.
"""
from __future__ import annotations

import time
from typing import Any

from harness import http_client
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.web_research import EXA_V2_OUTPUT_SCHEMA, company_record, has_scored_value, parse_structured_value

URL = "https://api.parallel.ai/v1/tasks/runs"
# Each result request waits up to this long on Parallel's side for the run to finish.
RESULT_WAIT_S = 60
# A run still unfinished after this long is recorded as an error. Kept well above core's usual run time, so a slow run is
# not counted as Parallel's failure when the cutoff is ours.
DEADLINE_S = 900
# Run states in which the result is not ready yet.
ACTIVE = {"queued", "action_required", "running", "cancelling"}

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "company_name": {"type": "string", "description": "The name supplied for the company to research."},
        "company_website": {"type": "string", "description": "The website that identifies the company to research."},
    },
    "required": ["company_name", "company_website"],
}
# The ten scored fields with their descriptions (shared with Exa), plus the rules every web-research tool gets.
OUTPUT_SCHEMA: dict[str, Any] = {
    **EXA_V2_OUTPUT_SCHEMA,
    "description": (
        "A company-enrichment record for the exact company identified by company_website. Do not substitute a parent, "
        "subsidiary, similarly named company, or brand owner unless that website itself identifies that entity. Use "
        "reliable first-party or LinkedIn sources where available. Return null for unknown values and [] for unknown "
        "lists; do not guess. For headcount, return a range only when a source supports a range."
    ),
}


def parse_task_output(output: Any) -> dict[str, Any]:
    """The structured answer in a Task API result, with the URLs Parallel cites in `basis` as `sources`."""
    content = output.get("content") if isinstance(output, dict) else None
    if not isinstance(content, dict):
        raise ValueError("Parallel Task result had no structured output content")
    cited = [
        citation["url"]
        for basis in output.get("basis") or [] if isinstance(basis, dict)
        for citation in basis.get("citations") or [] if isinstance(citation, dict) and isinstance(citation.get("url"), str)
    ]
    return parse_structured_value({**content, "sources": list(dict.fromkeys(cited))})


class ParallelTask(Adapter):
    def request_body(self, company: Company) -> dict[str, Any]:
        return {
            "input": {"company_name": company.name or company.domain, "company_website": company.domain},
            "processor": self.settings.get("processor", "core"),
            "task_spec": {
                "input_schema": {"type": "json", "json_schema": INPUT_SCHEMA},
                "output_schema": {"type": "json", "json_schema": OUTPUT_SCHEMA},
            },
        }

    def lookup(self, company: Company) -> Answer:
        headers = {"x-api-key": self.key("PARALLEL_API_KEY"), "Content-Type": "application/json"}
        started = time.monotonic()
        created = http_client.request_json("POST", URL, headers=headers, json=self.request_body(company), timeout=60).body
        if not isinstance(created, dict) or not created.get("run_id"):
            raise ValueError("Parallel Task returned no run_id")
        run_id = created["run_id"]
        result = self._wait_for_result(run_id, headers, started)
        latency_ms = round((time.monotonic() - started) * 1000)
        value = parse_task_output(result.get("output"))
        processor = self.settings.get("processor", "core")
        cost_unit = f"parallel_task_{processor}_run"
        raw = {"run_id": run_id, "sources": value["sources"], "created": created, "result": result}
        if not has_scored_value(value):
            return self.answer(company, "not_found", latency_ms=latency_ms, cost_units=1, cost_unit=cost_unit, raw=raw)
        return self.answer(
            company, "ok", {**company_record(value).fields(), "sources": value["sources"]},
            latency_ms=latency_ms, cost_units=1, cost_unit=cost_unit, raw=raw,
        )

    def _wait_for_result(self, run_id: str, headers: dict[str, str], started: float) -> dict[str, Any]:
        url = f"{URL}/{run_id}/result"
        while time.monotonic() - started < DEADLINE_S:
            response = http_client.request(
                "GET", url, headers=headers, params={"timeout": RESULT_WAIT_S}, timeout=RESULT_WAIT_S + 30)
            if response.status == 408:  # the wait ended and the run is still going
                continue
            if response.status == 404:  # the run failed, or its result is not available yet
                run = http_client.request_json("GET", f"{URL}/{run_id}", headers=headers, timeout=60).body
                state = run.get("status") if isinstance(run, dict) else None
                if state in ACTIVE:
                    time.sleep(5)
                    continue
                raise RuntimeError(f"Parallel Task run {run_id} {state}: {run.get('error') if isinstance(run, dict) else run}")
            if not response.ok:
                raise http_client.HTTPError(response, url)
            body = response.body
            status = (body.get("run") or {}).get("status") if isinstance(body, dict) else None
            if status in ("failed", "cancelled"):
                raise RuntimeError(f"Parallel Task run {run_id} {status}: {(body.get('run') or {}).get('error')}")
            if isinstance(body, dict) and body.get("output"):
                return body
            time.sleep(2)
        raise TimeoutError(f"Parallel Task run {run_id} did not finish within {DEADLINE_S} s")


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return ParallelTask(setup, spec, env)
