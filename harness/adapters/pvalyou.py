"""Pvalyou: one company read per domain and name. A company not on file answers as a job, polled until done."""
from __future__ import annotations

import os
import re
import time
from typing import Any

from harness import http_client
from harness.company import CompanyRecord
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.vendor_values import parse_employee_range

BASE = os.environ.get("PVALYOU_API_BASE", "https://company-file-api-559961100092.europe-west1.run.app/v1")
TIMEOUT = 60
POLL_S = 10
MAX_WAIT_S = int(os.environ.get("PVALYOU_MAX_WAIT_S", "900"))
LINKEDIN = re.compile(r"linkedin\.com/(company|school|showcase)/([^/?#\s]+)", re.I)


def _linkedin(url: Any) -> str | None:
    match = LINKEDIN.search(str(url or ""))
    return f"https://www.linkedin.com/{match.group(1).lower()}/{match.group(2).lower()}" if match else None


def _hq_city(record: dict[str, Any]) -> str | None:
    entities = record.get("legal_entities") or []
    for entity in entities:
        if str(entity.get("entity_hierarchy") or "").lower() == "headquarters" and entity.get("city"):
            return entity["city"]
    return next((entity["city"] for entity in entities if entity.get("city")), None)


def parse(record: dict[str, Any], company: Company) -> CompanyRecord:
    identity = record.get("identity") or {}
    classification = record.get("classification") or {}
    low, high = parse_employee_range(identity.get("employee_range"))
    exact = identity.get("employees")
    if isinstance(exact, int) and exact > 0:
        low = high = exact
    industry = classification.get("industry")
    return CompanyRecord(
        legal_name=identity.get("legal_name") or identity.get("name"),
        primary_domain=identity.get("domain") or company.domain,
        aliases=[a for a in (identity.get("other_legal_names") or []) if isinstance(a, str)],
        domains=[d for d in {identity.get("domain"), company.domain} if d],
        hq_country=identity.get("country"),
        hq_city=_hq_city(record),
        founded_year=identity.get("founding_year"),
        industry=industry,
        industries=[i for i in [industry, classification.get("sub_industry")] if i],
        linkedin_url=_linkedin((record.get("public_profiles") or {}).get("linkedin")),
        headcount_min=low,
        headcount_max=high,
    )


class Pvalyou(Adapter):
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.key("PVALYOU_API_KEY"), "Accept": "application/json",
                "Content-Type": "application/json", "X-Platform": "openbenchmarks"}

    def lookup(self, company: Company) -> Answer:
        started = time.monotonic()
        try:
            response = http_client.request_json(
                "POST", f"{BASE}/company", headers=self.headers(), timeout=TIMEOUT, attempts=1,
                json={"company": company.domain, "name": company.name, "tier": self.settings.get("tier", "basic")},
            )
        except http_client.HTTPError as error:
            if error.status in (404, 422):
                return self.answer(company, "not_found", latency_ms=error.response.latency_ms,
                                   raw={"status": error.status, "body": error.response.body})
            raise
        body = response.body or {}
        job_id = body.get("job_id")
        while not body.get("record") and job_id:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if time.monotonic() - started > MAX_WAIT_S:
                return self.answer(company, "error", latency_ms=elapsed_ms, error=f"no answer within {MAX_WAIT_S}s",
                                   raw={"job_id": job_id})
            if body.get("job_status") == "failed":
                return self.answer(company, "not_found", latency_ms=elapsed_ms,
                                   raw={"job_id": job_id, "outcome": body.get("error"), "message": body.get("message")})
            time.sleep(POLL_S)
            body = http_client.request_json("GET", f"{BASE}/company/jobs/{job_id}", headers=self.headers(),
                                            timeout=TIMEOUT).body or {}
        record = body.get("record")
        latency_ms = int((time.monotonic() - started) * 1000)
        if not record:
            return self.answer(company, "not_found", latency_ms=latency_ms, raw={"job_id": job_id})
        billing = body.get("billing") or {}
        usd = billing.get("gross_usd")
        return self.answer(
            company, "ok", parse(record, company).fields(), latency_ms=latency_ms, cost_units=usd,
            cost_unit="usd" if usd is not None else None,
            raw={"job_id": job_id, "first_response_ms": response.latency_ms, "response": body},
        )


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return Pvalyou(setup, spec, env)
