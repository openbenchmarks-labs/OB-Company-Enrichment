"""Coresignal: one multi-source company enrichment call per website, core bundle only.

The core bundle is the cheapest company record (10 credits). Coresignal charges successful requests only, so a 404
costs nothing.
"""
from __future__ import annotations

from typing import Any

from harness import http_client
from harness.company import CompanyRecord
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.vendor_values import as_list, parse_employee_range

URL = "https://api.coresignal.com/cdapi/v2/company_multi_source/enrich"
BUNDLE = "core_company_data"
CREDITS_PER_RECORD = 10
COST_UNIT = "coresignal_credit"
TIMEOUT = 45


def _city(body: dict[str, Any]) -> str | None:
    """`hq_city`, else the first part of `hq_location` ("Gardabaer, Iceland") when it names more than the country."""
    if body.get("hq_city"):
        return body["hq_city"]
    parts = [part.strip() for part in str(body.get("hq_location") or "").split(",") if part.strip()]
    if len(parts) < 2 or parts[0] == body.get("hq_country"):
        return None
    return parts[0]


def parse(body: dict[str, Any]) -> CompanyRecord:
    low, high = parse_employee_range(body.get("size_range"))
    if low is None and high is None:
        low, high = parse_employee_range(body.get("employees_count"))
    industry = body.get("industry")
    return CompanyRecord(
        legal_name=body.get("company_legal_name") or body.get("company_name"),
        primary_domain=body.get("website_domain") or body.get("website"),
        aliases=[v for v in [body.get("company_name"), *as_list(body.get("company_name_alias"))] if isinstance(v, str)],
        domains=[v for v in as_list(body.get("website_alias")) if isinstance(v, str)],
        hq_country=body.get("hq_country"),
        hq_city=_city(body),
        founded_year=body.get("founded_year"),
        industry=industry,
        industries=[industry] if industry else [],
        linkedin_url=body.get("canonical_linkedin_url") or body.get("linkedin_url"),
        headcount_min=low,
        headcount_max=high,
    )


class Coresignal(Adapter):
    def lookup(self, company: Company) -> Answer:
        headers = {"apikey": self.key("CORESIGNAL_API_KEY"), "Accept": "application/json"}
        params = {"website": company.domain, "bundle": BUNDLE}
        try:
            response = http_client.request_json("GET", URL, headers=headers, params=params, timeout=TIMEOUT)
        except http_client.HTTPError as error:
            if error.status == 404:
                return self.answer(company, "not_found", latency_ms=error.response.latency_ms,
                                   raw={"status": 404, "body": error.response.body})
            raise
        body = response.body
        if not isinstance(body, dict) or not body.get("id"):
            return self.answer(company, "not_found", latency_ms=response.latency_ms, raw=body)
        return self.answer(
            company, "ok", parse(body).fields(), latency_ms=response.latency_ms,
            cost_units=CREDITS_PER_RECORD, cost_unit=COST_UNIT, raw=body,
        )


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return Coresignal(setup, spec, env)
