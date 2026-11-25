"""Riveter: one AI agent per row fills the enrichment columns (setup riveter-enrich).

The recipe (task prompt, the ten typed output columns of the shared schema, the per-row credit budget, the tools) is
saved once per process with POST /v1/enrichments (free). Each company then runs as one row through POST /v1/enrich,
and the filled cells and their source URLs are read back from the run's dataset. API reference:
https://docs.riveterhq.com
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

import requests

from harness import http_client
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.web_research import OUTPUT_SCHEMA, SCORING_FIELDS, company_record, has_scored_value, parse_structured_value

API_BASE = "https://api.riveterhq.com/v1"
USER_AGENT = "openbenchmarks-company-enrichment/1.0"
INPUT_NAME_COLUMN = "Company Name"
INPUT_DOMAIN_COLUMN = "Company Domain"
TIMEOUT = 90
POLL_WAIT_S = 50
POLL_FLOOR_S = 10
# A run given up on keeps billing while the rerun starts another; the agent's own bound is the credit budget.
POLL_DEADLINE_S = 3600
TERMINAL_STATUSES = {"success", "stopped", "failed", "error"}
# Riveter admits 30 kickoffs per minute per account and counts refused attempts; stay under it across threads.
KICKOFFS_PER_MINUTE = 28
COST_UNIT = "riveter_credit"

# web_research.instruction(), written for a saved recipe: the company's name and domain are the row's input columns.
TASK_PROMPT = f"""Research the company identified by the website in the "{INPUT_DOMAIN_COLUMN}" column.
The supplied company name is in the "{INPUT_NAME_COLUMN}" column. Fill in a company-enrichment
record for that exact company. Do not substitute a parent, subsidiary, similarly named
company, or brand owner unless the supplied website itself identifies that entity.

Use reliable first-party or LinkedIn sources where available. Leave a column not found when
no source supports a value; do not guess. For headcount, return a range only when the source
supports a range. Record the URL you used for each value."""

COLUMN_PROMPTS = {
    "legal_name": (
        "The company's official legal or registered name, as its own website, legal pages, or LinkedIn "
        'page states it (for example "Example, Inc."). The entity at the input domain, not a parent or subsidiary.'
    ),
    "primary_domain": (
        'The company\'s canonical public website domain, lowercase, with no scheme, "www.", or path '
        '(for example "example.com"): the input domain, or the domain it now redirects to.'
    ),
    "hq_country": 'The country of the company\'s headquarters, as a country name in English (for example "United States").',
    "hq_city": "The city of the company's headquarters, the city name only, without state, region, or country.",
    "founded_year": "The four-digit year the company was founded, as an integer, only when a source states it.",
    "industry": (
        "The company's primary industry or business category as one short label, as its LinkedIn company page "
        'or website describes it (for example "Software Development").'
    ),
    "industries": (
        "Up to four industry labels that describe the company, the primary one first, each a short label as on "
        "its LinkedIn company page or website."
    ),
    "linkedin_url": (
        "The canonical LinkedIn company-page URL of this exact company, in the form "
        "https://www.linkedin.com/company/<slug>. Not a person, post, school, or showcase page, and not a "
        "parent's or a similarly named company's page: the page must belong to the company at the input domain."
    ),
    "headcount_min": (
        "The lower bound of the company's current employee count, as an integer: the low end of a supported "
        'range (for example 51 from "51-200 employees"), or the exact count when a source gives one.'
    ),
    "headcount_max": (
        "The upper bound of the company's current employee count, as an integer: the high end of a supported "
        'range (for example 200 from "51-200 employees"), or the exact count when a source gives one. '
        'Not found for an open-ended range such as "10,001+".'
    ),
}

_SAVED_ENRICHMENT: dict[str, Any] = {}
_SAVED_ENRICHMENT_LOCK = threading.Lock()
_KICKOFF_TIMES: deque[float] = deque()
_KICKOFF_LOCK = threading.Lock()


def column_schema(key: str) -> dict[str, Any]:
    """The schema's property for one column, without null: Riveter has a not-found state per cell."""
    contract = OUTPUT_SCHEMA["properties"][key]
    if contract["type"] == "array":
        return {"type": "array", "items": contract["items"]}
    return {"type": next(name for name in contract["type"] if name != "null")}


def recipe(additional_tools: list[str], budget: float) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "OpenBenchmarks company enrichment (web research v1.0)",
        "prompt": TASK_PROMPT,
        "input_columns": [INPUT_NAME_COLUMN, INPUT_DOMAIN_COLUMN],
        "output": [
            {"name": key, "prompt": COLUMN_PROMPTS[key], "output_schema": column_schema(key)} for key in SCORING_FIELDS
        ],
        "budget": budget,
        "budget_locked": True,
    }
    if additional_tools:
        body["additional_tools"] = additional_tools
    return body


def kickoff_body(company: Company, enrichment_id: str) -> dict[str, Any]:
    return {
        "enrichment_id": enrichment_id,
        "input": {INPUT_NAME_COLUMN: [company.name or ""], INPUT_DOMAIN_COLUMN: [company.domain]},
    }


def wait_for_kickoff_slot() -> None:
    """At most KICKOFFS_PER_MINUTE kickoffs in any 60 s window across the process's threads."""
    with _KICKOFF_LOCK:
        _drop_kickoffs_older_than_a_minute()
        if len(_KICKOFF_TIMES) >= KICKOFFS_PER_MINUTE:
            time.sleep(max(0.0, 60.0 - (time.monotonic() - _KICKOFF_TIMES[0])))
            _drop_kickoffs_older_than_a_minute()
        _KICKOFF_TIMES.append(time.monotonic())


def _drop_kickoffs_older_than_a_minute() -> None:
    now = time.monotonic()
    while _KICKOFF_TIMES and now - _KICKOFF_TIMES[0] >= 60.0:
        _KICKOFF_TIMES.popleft()


def parse_row(row: dict[str, Any]) -> dict[str, Any]:
    """The schema object from the row's cells: an `ok` cell is the value, anything else null; sources from the cells."""
    cells = row.get("cells")
    if not isinstance(cells, dict):
        raise ValueError("Riveter row had no cells")
    failed = sorted(key for key in SCORING_FIELDS if isinstance(cells.get(key), dict) and cells[key].get("state") == "failed")
    if failed:
        raise ValueError(f"Riveter left cells failed: {', '.join(failed)}")
    value: dict[str, Any] = {}
    sources: list[str] = []
    for key in SCORING_FIELDS:
        cell = cells.get(key)
        if not isinstance(cell, dict):
            raise ValueError(f"Riveter row had no cell for {key}")
        value[key] = cell.get("value") if cell.get("state") == "ok" else None
        sources.extend(url for url in cell.get("sources") or [] if isinstance(url, str) and url)
    industries = value.get("industries")
    value["industries"] = industries if isinstance(industries, list) else [industries] if industries not in (None, "") else []
    return parse_structured_value({**value, "sources": list(dict.fromkeys(sources))})


class Riveter(Adapter):
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key('RIVETER_API_KEY')}", "Content-Type": "application/json",
                "User-Agent": USER_AGENT}

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        return http_client.request_json(method, f"{API_BASE}{path}", headers=self.headers(), timeout=TIMEOUT, **kwargs).body

    def enabled_additional_tools(self) -> list[str]:
        body = self.call("GET", "/tools")
        tools = body.get("tools") if isinstance(body, dict) else None
        enabled = {tool["name"] for tool in tools or [] if isinstance(tool, dict) and tool.get("enabled")}
        return [name for name in self.settings.get("additional_tools", []) if name in enabled]

    def saved_enrichment(self) -> dict[str, Any]:
        """The recipe saved on the account, created on the first call of the process (free, no run)."""
        with _SAVED_ENRICHMENT_LOCK:
            if not _SAVED_ENRICHMENT:
                body = self.call("POST", "/enrichments", attempts=1,
                                 json=recipe(self.enabled_additional_tools(), self.settings["budget_credits_per_row"]))
                if not isinstance(body, dict) or not isinstance(body.get("id"), str):
                    raise ValueError("Riveter did not return a saved enrichment")
                _SAVED_ENRICHMENT.update(body)
            return dict(_SAVED_ENRICHMENT)

    def poll_once(self, run_id: str) -> Any:
        try:
            return self.call("GET", f"/runs/{run_id}", attempts=1, params={"wait": POLL_WAIT_S})
        except http_client.HTTPError as error:
            if error.status < 500 and error.status != 429:
                raise
        except requests.RequestException:
            pass
        return None

    def poll_run(self, run_id: str) -> dict[str, Any]:
        """Long-poll the run to a terminal status, by wall clock: a queued run answers at once."""
        deadline = time.monotonic() + POLL_DEADLINE_S
        while True:
            started = time.monotonic()
            body = self.poll_once(run_id)
            if isinstance(body, dict) and body.get("status") in TERMINAL_STATUSES:
                return body
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Riveter run {run_id} was not finished after {POLL_DEADLINE_S}s")
            time.sleep(max(0.0, POLL_FLOOR_S - (time.monotonic() - started)))

    def lookup(self, company: Company) -> Answer:
        enrichment = self.saved_enrichment()
        request_body = kickoff_body(company, enrichment["id"])
        wait_for_kickoff_slot()
        started = time.perf_counter()
        kickoff = self.call("POST", "/enrich", attempts=1, json=request_body)
        if not isinstance(kickoff, dict) or not isinstance(kickoff.get("id"), str):
            raise ValueError("Riveter did not return a run")
        if kickoff.get("legacy"):
            raise ValueError(f"Riveter ran the legacy pipeline: {kickoff['legacy']}")
        run = self.poll_run(kickoff["id"])
        latency_ms = round((time.perf_counter() - started) * 1000)
        if run.get("status") != "success":
            error = run.get("error") or {}
            reason = run.get("stopped_reason") or (error.get("message") if isinstance(error, dict) else None)
            raise ValueError(f"Riveter run {kickoff['id']} ended {run.get('status')}: {reason}")
        dataset_id = run.get("dataset_id") or kickoff.get("dataset_id")
        if not isinstance(dataset_id, str) or not dataset_id:
            raise ValueError(f"Riveter run {run.get('id')} named no dataset")
        rows = self.call("GET", f"/datasets/{dataset_id}/rows", params={"limit": 1})
        rows = rows.get("rows") if isinstance(rows, dict) else None
        if not rows or not isinstance(rows[0], dict):
            raise ValueError(f"Riveter dataset {dataset_id} returned no row")
        value = parse_row(rows[0])
        credits = float(run.get("credits_used") or 0)
        raw = {
            "enrichment": {key: enrichment.get(key) for key in ("id", "name", "tools", "budget_credits_per_row", "budget_locked", "version")},
            "request": {"method": "POST", "url": f"{API_BASE}/enrich", "body": request_body},
            "run": run,
            "row": rows[0],
            "sources": value["sources"],
        }
        if not has_scored_value(value):
            return self.answer(company, "not_found", latency_ms=latency_ms, cost_units=credits, cost_unit=COST_UNIT, raw=raw)
        return self.answer(company, "ok", {**company_record(value).fields(), "sources": value["sources"]},
                           latency_ms=latency_ms, cost_units=credits, cost_unit=COST_UNIT, raw=raw)


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return Riveter(setup, spec, env)
