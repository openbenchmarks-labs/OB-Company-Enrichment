"""Parallel Responses with the shared web-research question and schema (setup parallel-research)."""
from __future__ import annotations

from typing import Any

from harness import http_client
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.web_research import OUTPUT_SCHEMA, company_record, has_scored_value, instruction, parse_response

URL = "https://api.parallel.ai/v1/responses"
TIMEOUT = 180


class Parallel(Adapter):
    def request_body(self, company: Company) -> dict[str, Any]:
        return {
            "model": self.settings.get("model", "parallel"),
            "input": instruction(company.name, company.domain),
            "reasoning": {"effort": self.settings.get("reasoning_effort", "medium")},
            "text": {"format": {"type": "json_schema", "name": "company_enrichment", "schema": OUTPUT_SCHEMA}},
        }

    def lookup(self, company: Company) -> Answer:
        headers = {"Authorization": f"Bearer {self.key('PARALLEL_API_KEY')}", "Content-Type": "application/json"}
        response = http_client.request_json("POST", URL, headers=headers, json=self.request_body(company), timeout=TIMEOUT)
        body = response.body
        if not isinstance(body, dict):
            raise ValueError("Parallel returned a non-object response")
        value = parse_response(body)
        cost_unit = f"parallel_responses_{self.settings.get('reasoning_effort', 'medium')}_request"
        raw = {"response_id": body.get("id"), "sources": value["sources"], "response": body}
        if not has_scored_value(value):
            return self.answer(company, "not_found", latency_ms=response.latency_ms, cost_units=1, cost_unit=cost_unit, raw=raw)
        return self.answer(
            company, "ok", {**company_record(value).fields(), "sources": value["sources"]},
            latency_ms=response.latency_ms, cost_units=1, cost_unit=cost_unit, raw=raw,
        )


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return Parallel(setup, spec, env)
