"""People Data Labs: one company enrichment call per website."""
from __future__ import annotations

from typing import Any

from harness import http_client
from harness.company import CompanyRecord
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.vendor_values import as_list, parse_employee_range

URL = "https://api.peopledatalabs.com/v5/company/enrich"
COST_UNIT = "pdl_company_enrichment_credit"
TIMEOUT = 45


def parse(body: dict[str, Any]) -> CompanyRecord:
    location = body.get("location") if isinstance(body.get("location"), dict) else {}
    low, high = parse_employee_range(body.get("size"))
    if low is None and high is None:
        low, high = parse_employee_range(body.get("employee_count"))
    return CompanyRecord(
        legal_name=body.get("legal_name") or body.get("display_name") or body.get("name"),
        primary_domain=body.get("website") or body.get("website_url"),
        aliases=[v for v in as_list(body.get("alternative_names")) if isinstance(v, str)],
        domains=[v for v in as_list(body.get("alternative_domains")) if isinstance(v, str)],
        hq_country=location.get("country") or location.get("country_name"),
        hq_city=location.get("locality") or location.get("city") or location.get("name"),
        founded_year=body.get("founded"),
        industry=body.get("industry"),
        industries=[body["industry"]] if body.get("industry") else [],
        linkedin_url=body.get("linkedin_url"),
        headcount_min=low,
        headcount_max=high,
    )


def _credits(headers: dict[str, str]) -> float | None:
    raw = headers.get("x-call-credits-spent")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


class PeopleDataLabs(Adapter):
    def lookup(self, company: Company) -> Answer:
        headers = {"X-Api-Key": self.key("PEOPLE_DATA_LABS_API_KEY"), "Accept": "application/json"}
        try:
            response = http_client.request_json("GET", URL, headers=headers, params={"website": company.domain}, timeout=TIMEOUT)
        except http_client.HTTPError as error:
            if error.status == 404:
                return self.answer(company, "not_found", latency_ms=error.response.latency_ms,
                                   raw={"status": 404, "body": error.response.body})
            raise
        body = response.body
        if not isinstance(body, dict) or not body.get("id"):
            return self.answer(company, "not_found", latency_ms=response.latency_ms, raw=body)
        credits = _credits(response.headers)
        return self.answer(
            company, "ok", parse(body).fields(), latency_ms=response.latency_ms, cost_units=credits,
            cost_unit=COST_UNIT if credits is not None else None, raw=body,
        )


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return PeopleDataLabs(setup, spec, env)
