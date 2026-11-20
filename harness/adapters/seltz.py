"""Seltz Answer with a JSON-schema answer, scoped by the setup's `scope` (setup seltz-companies)."""
from __future__ import annotations

import json
from typing import Any

from harness import http_client
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.web_research import EXA_OUTPUT_SCHEMA, company_record, has_scored_value, instruction, parse_structured_value

URL = "https://api.seltz.ai/v1/answer"
TIMEOUT = 180
COST_UNIT = "seltz_answer_request"
SYSTEM_PROMPT = (
    "Return only the requested JSON object. Use null for unknown scalar values "
    "and [] for unknown lists. Do not add prose or Markdown fences."
)
RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {"name": "company_enrichment", "strict": True, "schema": EXA_OUTPUT_SCHEMA}}


def parse_answer(value: Any, citations: Any) -> dict[str, Any]:
    """Decode Seltz's answer text, which may come wrapped in a Markdown code fence, and add its citation URLs."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Seltz Answer returned no answer text")
    text = value.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: text.rindex("```")]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("Seltz Answer was not valid structured JSON") from error
    sources = [row["url"] for row in citations or [] if isinstance(row, dict) and isinstance(row.get("url"), str) and row["url"]]
    return parse_structured_value({**parsed, "sources": list(dict.fromkeys(sources))})


class Seltz(Adapter):
    def request_body(self, company: Company) -> dict[str, Any]:
        return {
            "query": instruction(company.name, company.domain),
            "scope": self.settings["scope"],
            "system_prompt": SYSTEM_PROMPT,
            "response_format": RESPONSE_FORMAT,
        }

    def lookup(self, company: Company) -> Answer:
        headers = {"x-api-key": self.key("SELTZ_API_KEY"), "Content-Type": "application/json"}
        request_body = self.request_body(company)
        response = http_client.request_json("POST", URL, headers=headers, json=request_body, timeout=TIMEOUT)
        body = response.body
        if not isinstance(body, dict):
            raise ValueError("Seltz Answer returned a non-object response")
        value = parse_answer(body.get("answer"), body.get("citations"))
        raw = {"request": request_body, "sources": value["sources"], "response": body}
        if not has_scored_value(value):
            return self.answer(company, "not_found", latency_ms=response.latency_ms, cost_units=1, cost_unit=COST_UNIT, raw=raw)
        return self.answer(
            company, "ok", {**company_record(value).fields(), "sources": value["sources"]},
            latency_ms=response.latency_ms, cost_units=1, cost_unit=COST_UNIT, raw=raw,
        )


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return Seltz(setup, spec, env)
