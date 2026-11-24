"""Coresignal: one multi-source company enrichment call per website, core bundle only.

The core bundle is the cheapest company record (10 credits). Coresignal charges successful requests only, so a 404
costs nothing.
"""
from __future__ import annotations

import os
from typing import Any

from harness.common import CompanyCase, NormalizedCompany, ProviderResult

from .base import ProviderHTTPError, as_list, parse_employee_range, request_json


VENDOR_SLUG = "coresignal"
VENDOR_NAME = "Coresignal"
REQUIRED_ENV = ("CORESIGNAL_API_KEY",)
PAID_CALLS_PER_CASE = 1

URL = "https://api.coresignal.com/cdapi/v2/company_multi_source/enrich"
BUNDLE = "core_company_data"
CREDITS_PER_RECORD = 10
COST_UNIT = "coresignal_credit"
TIMEOUT = 45


def _employee_range(value: Any) -> tuple[int | None, int | None]:
    if isinstance(value, str) and value.replace(",", "").strip().lower() == "myself only":  # LinkedIn's size label for a one-person company
        return 1, 1
    return parse_employee_range(value)


def _city(body: dict[str, Any]) -> str | None:
    """`hq_city`, else the first part of `hq_location` ("Gardabaer, Iceland") when it names more than the country."""
    if body.get("hq_city"):
        return body["hq_city"]
    parts = [part.strip() for part in str(body.get("hq_location") or "").split(",") if part.strip()]
    if len(parts) < 2 or parts[0] == body.get("hq_country"):
        return None
    return parts[0]


def parse(body: dict[str, Any]) -> NormalizedCompany:
    low, high = _employee_range(body.get("size_range"))
    if low is None and high is None:
        low, high = _employee_range(body.get("employees_count"))
    industry = body.get("industry")
    return NormalizedCompany(
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


def run(case: CompanyCase) -> ProviderResult:
    headers = {"apikey": os.environ["CORESIGNAL_API_KEY"], "Accept": "application/json"}
    params = {"website": case.input_domain, "bundle": BUNDLE}
    try:
        body, latency_ms = request_json("GET", URL, headers=headers, params=params, timeout=TIMEOUT)
    except ProviderHTTPError as exc:
        if exc.status_code == 404:
            return ProviderResult(VENDOR_SLUG, VENDOR_NAME, case.case_slug, "not_found", exc.elapsed_ms or 0,
                                  audit={"vendor_status_code": 404})
        raise
    if not isinstance(body, dict) or not body.get("id"):
        return ProviderResult(VENDOR_SLUG, VENDOR_NAME, case.case_slug, "not_found", latency_ms)
    return ProviderResult(
        VENDOR_SLUG, VENDOR_NAME, case.case_slug, "ok", latency_ms, normalized=parse(body),
        cost_units=CREDITS_PER_RECORD, cost_unit=COST_UNIT, audit={"record_id": body.get("id")},
    )
