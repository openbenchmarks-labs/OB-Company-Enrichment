"""PredictLeads: the extended company record for a domain, following a redirect to the company it points to."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from harness import http_client
from harness.company import CompanyRecord
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.vendor_values import as_list, first_dict

URL = "https://predictleads.com/api/v3/extended_companies/{domain}"
COST_UNIT = "extended_company_request"
TIMEOUT = 45


def _location(attributes: dict[str, Any]) -> tuple[str | None, str | None]:
    locations = [v for v in as_list(attributes.get("location_data")) if isinstance(v, dict)]
    chosen = next((v for v in locations if v.get("category") == "headquarters"), None) or first_dict(locations)
    if chosen:
        return chosen.get("country"), chosen.get("city")
    linkedin = first_dict(attributes.get("linkedin_location_data"))
    return linkedin.get("country"), linkedin.get("city")


def parse(body: dict[str, Any], record: dict[str, Any]) -> CompanyRecord:
    attributes = record.get("attributes") or {}
    redirect = ((record.get("relationships") or {}).get("redirects_to") or {}).get("data")
    target = next((v for v in body.get("included") or [] if redirect and v.get("id") == redirect.get("id")), None)
    target_attributes = (target or {}).get("attributes") or {}
    country, city = _location(attributes)
    aliases = [attributes.get("company_name"), attributes.get("friendly_company_name"), target_attributes.get("company_name")]
    return CompanyRecord(
        legal_name=target_attributes.get("company_name") or attributes.get("company_name") or attributes.get("friendly_company_name"),
        primary_domain=target_attributes.get("domain") or attributes.get("domain"),
        aliases=[v for v in aliases if v],
        domains=[v for v in [attributes.get("domain"), target_attributes.get("domain")] if v],
        hq_country=country,
        hq_city=city,
        founded_year=attributes.get("linkedin_founded"),
        industry=attributes.get("linkedin_industry"),
        industries=[attributes["linkedin_industry"]] if attributes.get("linkedin_industry") else [],
        linkedin_url=attributes.get("linkedin_url"),
        headcount_min=attributes.get("employees_min"),
        headcount_max=attributes.get("employees_max"),
    )


class PredictLeads(Adapter):
    def lookup(self, company: Company) -> Answer:
        headers = {
            "X-Api-Key": self.key("PREDICTLEADS_API_KEY"),
            "X-Api-Token": self.key("PREDICTLEADS_API_TOKEN"),
            "Accept": "application/json",
        }
        try:
            response = http_client.request_json("GET", URL.format(domain=quote(company.domain)), headers=headers, timeout=TIMEOUT)
        except http_client.HTTPError as error:
            if error.status == 404:
                return self.answer(company, "not_found", latency_ms=error.response.latency_ms, cost_units=1,
                                   cost_unit=COST_UNIT, raw={"status": 404, "body": error.response.body})
            raise
        body = response.body
        records = body.get("data") if isinstance(body, dict) else None
        records = records if isinstance(records, list) else ([records] if isinstance(records, dict) else [])
        if not records:
            return self.answer(company, "not_found", latency_ms=response.latency_ms, raw=body)
        return self.answer(company, "ok", parse(body, records[0]).fields(), latency_ms=response.latency_ms,
                           cost_units=1, cost_unit=COST_UNIT, raw=body)


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return PredictLeads(setup, spec, env)
