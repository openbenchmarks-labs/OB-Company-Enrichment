"""Apollo: find the organization by domain, then fetch its full record."""
from __future__ import annotations

from typing import Any

from harness import http_client
from harness.company import CompanyRecord
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.vendor_values import as_list, first_value, parse_employee_range

SEARCH_URL = "https://api.apollo.io/api/v1/mixed_companies/search"
DETAIL_URL = "https://api.apollo.io/api/v1/organizations/{id}"
COST_UNIT = "credit_event"
TIMEOUT = 45


def parse(organization: dict[str, Any]) -> CompanyRecord:
    headcount = first_value(organization, "estimated_num_employees", "employee_count", "employees")
    low, high = parse_employee_range(headcount)
    industries = as_list(first_value(organization, "industries", "industry"))
    return CompanyRecord(
        legal_name=first_value(organization, "name", "organization_name", "legal_name"),
        primary_domain=first_value(organization, "primary_domain", "website_url", "domain"),
        aliases=[str(v) for v in as_list(first_value(organization, "names", "aliases")) if v],
        domains=[str(v) for v in as_list(first_value(organization, "domains", "website_url")) if v],
        hq_country=first_value(organization, "country", "country_name"),
        hq_city=first_value(organization, "city"),
        founded_year=first_value(organization, "founded_year", "founded"),
        industry=str(industries[0]) if industries else None,
        industries=[str(v) for v in industries if v],
        linkedin_url=first_value(organization, "linkedin_url", "linkedin_company_url"),
        headcount_min=low,
        headcount_max=high,
    )


class Apollo(Adapter):
    def lookup(self, company: Company) -> Answer:
        headers = {"x-api-key": self.key("APOLLO_API_KEY"), "Content-Type": "application/json"}
        per_page = int((self.settings.get("search") or {}).get("per_page") or 1)
        search = http_client.request_json(
            "POST", SEARCH_URL, headers=headers, timeout=TIMEOUT,
            json={"q_organization_domains_list": [company.domain], "page": 1, "per_page": per_page},
        )
        organizations = search.body.get("organizations") if isinstance(search.body, dict) else None
        organizations = organizations if isinstance(organizations, list) else []
        raw: dict[str, Any] = {"search": search.body}
        if not organizations:
            return self.answer(company, "not_found", latency_ms=search.latency_ms, cost_units=1, cost_unit=COST_UNIT, raw=raw)
        organization_id = organizations[0].get("id")
        if not organization_id:
            return self.answer(company, "error", latency_ms=search.latency_ms, cost_units=1, cost_unit=COST_UNIT,
                               error="search result had no organization id", raw=raw)
        detail = http_client.request_json("GET", DETAIL_URL.format(id=organization_id), headers=headers, timeout=TIMEOUT)
        raw["detail"] = detail.body
        organization = detail.body.get("organization") if isinstance(detail.body, dict) else None
        if not isinstance(organization, dict):
            organization = detail.body if isinstance(detail.body, dict) else {}
        return self.answer(
            company, "ok", parse(organization).fields(), latency_ms=search.latency_ms + detail.latency_ms,
            cost_units=2, cost_unit=COST_UNIT, raw=raw,
        )


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return Apollo(setup, spec, env)
