"""Exa search with a structured company-enrichment answer (setup exa-research-v2)."""
from __future__ import annotations

from typing import Any

from harness import http_client
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.web_research import (
    EXA_V2_OUTPUT_SCHEMA, company_record, exa_v2_query, exa_v2_system_prompt, has_scored_value, parse_exa_v2_response,
)

URL = "https://api.exa.ai/search"
TIMEOUT = 180
HIGHLIGHTS_QUERY = "official company name, website, headquarters, founding year, LinkedIn company page, employee count, industry"


class Exa(Adapter):
    def request_body(self, company: Company) -> dict[str, Any]:
        name = company.name or company.domain
        return {
            "query": exa_v2_query(company.name, company.domain),
            "type": self.settings.get("type", "deep-reasoning"),
            "category": self.settings.get("category", "company"),
            "numResults": int(self.settings.get("num_results", 10)),
            "additionalQueries": [f"site:{company.domain}", f'site:linkedin.com/company "{name}"'],
            "contents": {"highlights": {
                "query": HIGHLIGHTS_QUERY, "maxCharacters": int(self.settings.get("highlights_max_characters", 6000)),
            }},
            "systemPrompt": exa_v2_system_prompt(company.name, company.domain),
            "outputSchema": EXA_V2_OUTPUT_SCHEMA,
        }

    def lookup(self, company: Company) -> Answer:
        headers = {"x-api-key": self.key("EXA_API_KEY"), "Content-Type": "application/json"}
        response = http_client.request_json("POST", URL, headers=headers, json=self.request_body(company), timeout=TIMEOUT)
        body = response.body
        if not isinstance(body, dict):
            raise ValueError("Exa returned a non-object response")
        value = parse_exa_v2_response(body, input_domain=company.domain)
        raw = {"response_id": body.get("requestId"), "sources": value["sources"], "response": body}
        if not has_scored_value(value):
            return self.answer(company, "not_found", latency_ms=response.latency_ms, raw=raw)
        return self.answer(
            company, "ok", {**company_record(value).fields(), "sources": value["sources"]},
            latency_ms=response.latency_ms, cost_units=1, cost_unit="exa_deep_reasoning_request", raw=raw,
        )


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return Exa(setup, spec, env)
