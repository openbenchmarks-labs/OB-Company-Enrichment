"""CompanyEnrich: one enrichment call per domain."""
from __future__ import annotations

from typing import Any

from harness import http_client
from harness.company import CompanyRecord
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.vendor_values import as_list

URL = "https://api.companyenrich.com/companies/enrich"
COST_UNIT = "company_enrich_credit"
TIMEOUT = 45
EMPLOYEE_RANGES: dict[str, tuple[int | None, int | None]] = {
    "1-10": (1, 10),
    "11-50": (11, 50),
    "51-200": (51, 200),
    "201-500": (201, 500),
    "501-1K": (501, 1_000),
    "1K-5K": (1_001, 5_000),
    "5K-10K": (5_001, 10_000),
    "over-10K": (10_001, None),
}


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse(body: dict[str, Any]) -> CompanyRecord:
    location = _object(body.get("location"))
    country = _object(location.get("country"))
    city = _object(location.get("city"))
    low, high = EMPLOYEE_RANGES.get(str(body.get("employees")), (None, None))
    industries = [str(value) for value in as_list(body.get("industries")) if value]
    if body.get("industry") and body["industry"] not in industries:
        industries.insert(0, str(body["industry"]))
    return CompanyRecord(
        legal_name=body.get("name"),
        primary_domain=body.get("domain") or body.get("website"),
        hq_country=country.get("name") or country.get("code"),
        hq_city=city.get("name"),
        founded_year=body.get("founded_year"),
        industry=industries[0] if industries else None,
        industries=industries,
        linkedin_url=_object(body.get("socials")).get("linkedin_url"),
        headcount_min=low,
        headcount_max=high,
    )


class CompanyEnrich(Adapter):
    def lookup(self, company: Company) -> Answer:
        params = {"domain": company.domain}
        headers = {"Authorization": f"Bearer {self.key('COMPANY_ENRICH_API_KEY')}", "Accept": "application/json"}
        try:
            response = http_client.request_json("GET", URL, headers=headers, params=params, timeout=TIMEOUT)
        except http_client.HTTPError as error:
            if error.status == 404:
                return self.answer(company, "not_found", latency_ms=error.response.latency_ms, cost_units=1.0,
                                   cost_unit=COST_UNIT, raw={"status": 404, "body": error.response.body})
            raise
        body = response.body
        if not isinstance(body, dict) or not body.get("id"):
            return self.answer(company, "not_found", latency_ms=response.latency_ms, cost_units=1.0,
                               cost_unit=COST_UNIT, raw=body)
        return self.answer(company, "ok", parse(body).fields(), latency_ms=response.latency_ms, cost_units=1.0,
                           cost_unit=COST_UNIT, raw=body)


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return CompanyEnrich(setup, spec, env)
