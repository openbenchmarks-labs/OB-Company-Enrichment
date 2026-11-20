"""Explorium: match the business by name and domain, then fetch its record."""
from __future__ import annotations

from typing import Any

from harness import http_client
from harness.company import CompanyRecord
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.vendor_values import as_list, find_first_key, first_value, parse_employee_range

MATCH_URL = "https://api.explorium.ai/v1/businesses/match"
FETCH_URL = "https://api.explorium.ai/v1/businesses"
COST_UNIT = "api_request"
TIMEOUT = 45


def _records(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [v for v in body if isinstance(v, dict)]
    if not isinstance(body, dict):
        return []
    for key in ("data", "businesses", "results", "matched_businesses"):
        value = body.get(key)
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
    return []


def parse(selected: dict[str, Any]) -> CompanyRecord:
    headcount = first_value(selected, "number_of_employees_range", "employee_count_range", "employees_range", "number_of_employees")
    low, high = parse_employee_range(headcount)
    industries = as_list(first_value(selected, "industry", "industries", "naics_description", "sic_description"))
    linkedin = first_value(selected, "linkedin", "linkedin_url", "linkedin_profile", "linkedin_company_url")
    if isinstance(linkedin, dict):
        linkedin = first_value(linkedin, "url", "profile_url", "value")
    return CompanyRecord(
        legal_name=first_value(selected, "company_name", "business_name", "name", "legal_name"),
        primary_domain=first_value(selected, "domain", "website", "website_domain"),
        aliases=[str(v) for v in as_list(first_value(selected, "aliases", "company_names")) if v],
        domains=[str(v) for v in as_list(first_value(selected, "domains", "websites")) if v],
        hq_country=first_value(selected, "country", "country_name", "hq_country"),
        hq_city=first_value(selected, "city_name", "city", "hq_city"),
        founded_year=first_value(selected, "founded_year", "year_founded", "founded"),
        industry=str(industries[0]) if industries else None,
        industries=[str(v) for v in industries if v],
        linkedin_url=linkedin,
        headcount_min=low,
        headcount_max=high,
    )


class Explorium(Adapter):
    def lookup(self, company: Company) -> Answer:
        headers = {"api_key": self.key("EXPLORIUM_API_KEY"), "Content-Type": "application/json"}
        match = http_client.request_json(
            "POST", MATCH_URL, headers=headers, timeout=TIMEOUT,
            json={"businesses_to_match": [{"name": company.name, "domain": company.domain}]},
        )
        raw: dict[str, Any] = {"match": match.body}
        business_id = find_first_key(match.body, "business_id") or find_first_key(match.body, "businessId")
        if not business_id:
            return self.answer(company, "not_found", latency_ms=match.latency_ms, cost_units=1, cost_unit=COST_UNIT, raw=raw)
        mode = (self.settings.get("fetch") or {}).get("mode", "full")
        fetch = http_client.request_json(
            "POST", FETCH_URL, headers=headers, timeout=TIMEOUT,
            json={"filters": {"business_id": {"values": [business_id]}}, "mode": mode, "page_size": 10, "size": 10, "page": 1},
        )
        raw["fetch"] = fetch.body
        latency = match.latency_ms + fetch.latency_ms
        records = _records(fetch.body)
        selected = next(
            (v for v in records if str(v.get("business_id") or v.get("businessId")) == str(business_id)),
            records[0] if records else None,
        )
        if not selected:
            return self.answer(company, "not_found", latency_ms=latency, cost_units=2, cost_unit=COST_UNIT, raw=raw)
        return self.answer(company, "ok", parse(selected).fields(), latency_ms=latency, cost_units=2,
                           cost_unit=COST_UNIT, raw=raw)


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return Explorium(setup, spec, env)
