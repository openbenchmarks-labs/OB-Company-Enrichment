"""ZoomInfo through the `gtm` CLI. One call enriches up to `domains_per_call` domains."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any

from harness.company import CompanyRecord
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.vendor_values import as_list

TIMEOUT = 180
COST_UNIT = "zoominfo_data_credit"


def _int(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", "")) if value not in (None, "") else None
    except ValueError:
        return None


def parse(data: dict[str, Any]) -> CompanyRecord:
    linkedin = next((str(row.get("url")) for row in data.get("socialMediaUrls") or []
                     if isinstance(row, dict) and str(row.get("type", "")).upper() == "LINKED_IN" and row.get("url")), None)
    employee_range = str(data.get("employeeRange") or "").replace(",", "")
    numbers = [int(value) for value in re.findall(r"\d+", employee_range)]
    lowered = employee_range.lower()
    # ZoomInfo's top bucket is "Over 10,000": an open range above 10,000, not the exact count 10,000.
    if numbers and ("over" in lowered or "more than" in lowered):
        minimum, maximum = numbers[0] + 1, None
    elif numbers and "+" in employee_range:
        minimum, maximum = numbers[0], None
    elif len(numbers) >= 2:
        minimum, maximum = numbers[0], numbers[1]
    elif numbers:
        minimum, maximum = numbers[0], numbers[0]
    else:
        minimum, maximum = None, None
    return CompanyRecord(
        legal_name=data.get("name"),
        primary_domain=data.get("domain") or data.get("website"),
        domains=[str(v) for v in as_list(data.get("domainList")) if v],
        hq_country=data.get("country"),
        hq_city=data.get("city"),
        founded_year=_int(data.get("foundedYear")),
        industry=data.get("industry"),
        industries=[str(v) for v in as_list(data.get("industries") or data.get("primaryIndustry")) if v],
        linkedin_url=linkedin,
        headcount_min=minimum,
        headcount_max=maximum,
    )


class ZoomInfo(Adapter):
    def __init__(self, setup: str, spec: dict[str, Any], env: list[str]) -> None:
        super().__init__(setup, spec, env)
        self.batch_size = int(self.settings.get("domains_per_call") or 10)

    def missing_env(self) -> list[str]:
        return [] if shutil.which("gtm") else ["gtm CLI (not installed)"]

    def call(self, companies: list[Company]) -> tuple[dict[str, Any], int]:
        """Run `gtm companies enrich` for these domains. Raise if the CLI fails."""
        started = time.perf_counter()
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as handle:
            json.dump([{"domain": c.domain} for c in companies], handle)
            handle.flush()
            result = subprocess.run(
                ["gtm", "companies", "enrich", "--file", handle.name, "--fields", *self.settings["fields"], "--format", "json"],
                capture_output=True, text=True, check=False, timeout=TIMEOUT,
            )
        latency_ms = round((time.perf_counter() - started) * 1000)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip()[:500] or f"gtm exited {result.returncode}")
        response = json.loads(result.stdout)
        return (response if isinstance(response, dict) else {}), latency_ms

    def lookup_many(self, companies: list[Company]) -> list[Answer]:
        response, latency_ms = self.call(companies)
        answers = []
        # The response keys each company by its position in the request: company_1, company_2, ...
        for index, company in enumerate(companies, start=1):
            item = response.get(f"company_{index}")
            data = item.get("data") if isinstance(item, dict) else None
            if isinstance(item, dict) and item.get("success") and isinstance(data, dict) and data:
                answers.append(self.answer(company, "ok", parse(data).fields(), latency_ms=latency_ms, cost_units=1,
                                           cost_unit=COST_UNIT, raw=item))
            else:
                answers.append(self.answer(company, "not_found", latency_ms=latency_ms, error="ZoomInfo returned no company match",
                                           raw=item))
        return answers

    def lookup(self, company: Company) -> Answer:
        return self.lookup_many([company])[0]


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return ZoomInfo(setup, spec, env)
