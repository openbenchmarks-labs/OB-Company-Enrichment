"""Extruct Deep Research with the shared web-research instruction plus Extruct's own notes (setup extruct-deep-research)."""
from __future__ import annotations

import time
from typing import Any

from harness import http_client
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.web_research import company_record, has_scored_value, instruction

URL = "https://api.extruct.ai/v1/deep_research_tasks"
TIMEOUT = 60
ATTEMPTS = 4
POLL_S = 15
DEADLINE_S = 1500
COST_UNIT = "extruct_deep_research_credits"
# Extruct rejects ["string","null"] unions. Omit unknown fields instead of null.
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "legal_name": {"type": "string", "description": "Official company or legal name."},
        "primary_domain": {"type": "string", "description": "Canonical public website domain only, without a URL path."},
        "hq_country": {"type": "string", "description": "Country of headquarters."},
        "hq_city": {"type": "string", "description": "City of headquarters."},
        "founded_year": {"type": "integer", "description": "Four-digit founding year."},
        "industry": {"type": "string", "description": "Primary industry label."},
        "industries": {"type": "array", "items": {"type": "string"}, "description": "Up to four relevant industry labels."},
        "linkedin_url": {"type": "string", "description": "Canonical LinkedIn company or school page, not a person or post."},
        "headcount_min": {"type": "integer", "description": "Lower bound of the LinkedIn company-size band, not members-on-LinkedIn."},
        "headcount_max": {"type": "integer", "description": "Upper bound of the LinkedIn company-size band."},
        "sources": {"type": "array", "items": {"type": "string"}},
    },
}


def brief(company: Company) -> str:
    return (
        instruction(company.name, company.domain)
        + "\n\nOmit any field you cannot verify; do not guess. "
        "LinkedIn URL must be the main /company/ or /school/ page of THIS "
        "identity — never a person, post, department, library, alumni, or "
        "division page. Headcount: LinkedIn company-size BAND (for example "
        "11-50 or 51-200), not the employees-on-LinkedIn member count. "
        "Industry: copy the LinkedIn company-page industry string as industry "
        "when it exists; put adjacent labels in industries. Founded year: the "
        "brand/organization year on the website About page or LinkedIn, not a "
        "later holding-company or GmbH/Ltd incorporation. HQ: use the LinkedIn "
        "HQ locality even when it is a suburb, district, or nationwide label; "
        "do not upgrade a suburb to the nearest famous city. If the supplied "
        "website is a subdomain or section of a larger organization's site, "
        "the target IS that organization."
    )


def _unwrap(value: Any) -> Any:
    if isinstance(value, dict) and "value" in value and ("sources" in value or "reasoning" in value):
        return value.get("value")
    return value


def _text(value: Any) -> str | None:
    value = _unwrap(value)
    if value in (None, "", [], {}):
        return None
    return str(value).strip() or None


def _int(value: Any) -> int | None:
    value = _unwrap(value)
    if value in (None, "", [], {}) or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(float(str(value).replace(",", "").replace("$", "").strip()))
    except (TypeError, ValueError):
        return None


def _list(value: Any) -> list[str]:
    value = _unwrap(value)
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


class Extruct(Adapter):
    def request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.key('EXTRUCT_API_TOKEN').strip()}",
                   "Content-Type": "application/json", "Accept": "application/json"}
        return http_client.request_json(method, url, headers=headers, json=payload, timeout=TIMEOUT, attempts=ATTEMPTS).body

    def lookup(self, company: Company) -> Answer:
        depth = self.settings.get("depth", "high")
        started = time.perf_counter()
        created = self.request("POST", URL, {"brief": brief(company), "depth": depth, "output_schema": OUTPUT_SCHEMA})
        task_id = created.get("id")
        if not task_id:
            raise RuntimeError(f"create returned no id: {created!r}"[:400])
        deadline = time.time() + DEADLINE_S
        latest = created
        while latest.get("status") != "done":
            if latest.get("status") == "failed":
                raise RuntimeError(f"task {task_id} failed: {latest.get('failure_reason') or 'no reason'}")
            if time.time() >= deadline:
                raise TimeoutError(f"task {task_id} timed out (status={latest.get('status')})")
            time.sleep(POLL_S)
            latest = self.request("GET", f"{URL}/{task_id}")
        report = latest.get("report") or {}
        fields = report.get("fields") if isinstance(report, dict) else None
        if not isinstance(fields, dict):
            raise RuntimeError(f"task {task_id} done without schema fields")
        agents = latest.get("agents")
        cost_units = (agents if isinstance(agents, int) else len(agents or [])) * 2
        latency_ms = round((time.perf_counter() - started) * 1000)
        value = {
            "legal_name": _text(fields.get("legal_name")),
            "primary_domain": _text(fields.get("primary_domain")) or company.domain,
            "hq_country": _text(fields.get("hq_country")),
            "hq_city": _text(fields.get("hq_city")),
            "founded_year": _int(fields.get("founded_year")),
            "industry": _text(fields.get("industry")),
            "industries": _list(fields.get("industries")),
            "linkedin_url": _text(fields.get("linkedin_url")),
            "headcount_min": _int(fields.get("headcount_min")),
            "headcount_max": _int(fields.get("headcount_max")),
            "sources": _list(fields.get("sources")),
        }
        raw = {"task_id": task_id, "depth": depth, "sources": value["sources"],
               "degradation_reasons": report.get("degradation_reasons") or [], "response": latest}
        if not has_scored_value(value):
            return self.answer(company, "not_found", latency_ms=latency_ms, cost_units=cost_units, cost_unit=COST_UNIT, raw=raw)
        return self.answer(company, "ok", {**company_record(value).fields(), "sources": value["sources"]},
                           latency_ms=latency_ms, cost_units=cost_units, cost_unit=COST_UNIT, raw=raw)


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return Extruct(setup, spec, env)
