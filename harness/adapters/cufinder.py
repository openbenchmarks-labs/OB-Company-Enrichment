"""CUFinder: one company enrichment call per domain."""
from __future__ import annotations

from typing import Any

from harness import http_client
from harness.company import CompanyRecord
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.vendor_values import parse_employee_range

URL = "https://api.cufinder.io/v3/companies/enrich"
COST_UNIT = "cufinder_credit"
TIMEOUT = 45


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _credits(body: Any) -> float:
    charged = _object(_object(_object(body).get("meta")).get("credits")).get("charged")
    try:
        return float(charged)
    except (TypeError, ValueError):
        return 0.0


def parse(company: dict[str, Any]) -> CompanyRecord:
    location = _object(company.get("location"))
    size = _object(company.get("size"))
    low, high = parse_employee_range(size.get("employee_range"))
    exact = size.get("employee_count")
    if low is None and high is None and isinstance(exact, (int, float)) and exact > 0:
        low = high = int(exact)
    industry = _object(company.get("industry")).get("name")
    return CompanyRecord(
        legal_name=company.get("name"),
        primary_domain=company.get("domain") or company.get("website_url"),
        hq_country=location.get("country_code") or location.get("country"),
        hq_city=location.get("city"),
        founded_year=company.get("founded_year"),
        industry=industry,
        industries=[industry] if industry else [],
        linkedin_url=_object(company.get("social")).get("linkedin"),
        headcount_min=low,
        headcount_max=high,
    )


class CUFinder(Adapter):
    def lookup(self, company: Company) -> Answer:
        headers = {"x-api-key": self.key("CUFINDER_API_KEY"), "Content-Type": "application/json", "Accept": "application/json"}
        try:
            response = http_client.request_json("POST", URL, headers=headers, json={"query": company.domain}, timeout=TIMEOUT)
        except http_client.HTTPError as error:
            if error.status in (404, 422):
                return self.answer(company, "not_found", latency_ms=error.response.latency_ms, cost_units=0.0,
                                   cost_unit=COST_UNIT, raw={"status": error.status, "body": error.response.body})
            raise
        body = response.body
        found = _object(_object(_object(body).get("data")).get("company"))
        credits = _credits(body)
        if not found or not (found.get("name") or found.get("domain")):
            return self.answer(company, "not_found", latency_ms=response.latency_ms, cost_units=credits,
                               cost_unit=COST_UNIT, raw=body)
        return self.answer(company, "ok", parse(found).fields(), latency_ms=response.latency_ms, cost_units=credits,
                           cost_unit=COST_UNIT, raw=body)


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return CUFinder(setup, spec, env)
