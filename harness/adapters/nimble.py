"""Nimble, in two forms.

Search setups (nimble-lite, nimble-standard) run one Nimble search, then an OpenAI model reads only the returned
titles and snippets into the shared schema. Agent setups (nimble-agents, nimble-agents-medium) start one Nimble agent
run per company at the setup's `effort` and poll it until it finishes.
"""
from __future__ import annotations

import random
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

import requests

from harness import http_client
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.web_research import OUTPUT_SCHEMA, company_record, extract_from_results, has_scored_value, instruction, parse_structured_value

SEARCH_URL = "https://sdk.nimbleway.com/v2/search"
RUNS_URL = "https://sdk.nimbleway.com/v2/agents/runs"
AGENT_URL = "https://sdk.nimbleway.com/v2/agents/{agent_id}/runs/{run_id}"
SEARCH_TIMEOUT = 60
RUN_TIMEOUT_S = 1800
POLL_INTERVAL_S = 10
MAX_REQUEST_ATTEMPTS = 7


def search_query(company: Company) -> str:
    return (
        f'Company profile for "{company.name or company.domain}" ({company.domain}): '
        "official company name, headquarters country and city, founding year, industry, "
        "LinkedIn company page and employee count."
    )


def search_hits(payload: Any, max_results: int) -> list[dict[str, str]]:
    """Unique result URLs with their title and description, in Nimble's order."""
    rows = payload.get("results") if isinstance(payload, dict) else None
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("url"), str):
            continue
        url = row["url"].strip()
        if not url or url in seen:
            continue
        seen.add(url)
        hits.append({"url": url, "title": str(row.get("title") or ""), "snippet": str(row.get("description") or "")})
        if len(hits) >= max_results:
            break
    return hits


class NimbleSearch(Adapter):
    def request_body(self, company: Company) -> dict[str, Any]:
        return {
            "query": search_query(company),
            "search_depth": self.settings["search_depth"],
            "full_content": bool(self.settings.get("full_content", False)),
            "focus": self.settings.get("focus", "general"),
            "max_results": int(self.settings.get("max_results", 10)),
        }

    def extract(self, company: Company, hits: list[dict[str, str]]) -> tuple[dict[str, Any], dict[str, Any]]:
        return extract_from_results(company.name, company.domain, hits, self.settings.get("extractor") or {})

    def lookup(self, company: Company) -> Answer:
        started = time.perf_counter()
        request_body = self.request_body(company)
        headers = {"Authorization": f"Bearer {self.key('NIMBLE_API_KEY')}", "Content-Type": "application/json"}
        response = http_client.request_json("POST", SEARCH_URL, headers=headers, json=request_body, timeout=SEARCH_TIMEOUT)
        body = response.body
        raw: dict[str, Any] = {"request": request_body, "response": body, "search_latency_ms": response.latency_ms}
        status, value, error = "not_found", None, None
        try:
            if not isinstance(body, dict) or not isinstance(body.get("results"), list):
                raise ValueError("Nimble returned no results array")
            hits = search_hits(body, request_body["max_results"])
            raw["search_evidence"] = hits
            if hits:
                value, raw["extraction"] = self.extract(company, hits)
                status = "ok" if has_scored_value(value) else "not_found"
        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {exc}"
        fields = {**company_record(value).fields(), "sources": value["sources"]} if status == "ok" and value else {}
        return self.answer(
            company, status, fields, latency_ms=round((time.perf_counter() - started) * 1000),
            cost_units=1, cost_unit="nimble_search_request", error=error, raw=raw,
        )


_RATE_LOCK = threading.Lock()
_COOLDOWN_UNTIL = 0.0


def _retry_seconds(retry_after: str | None) -> float:
    if not retry_after:
        return 0
    try:
        return float(retry_after)
    except ValueError:
        try:
            return parsedate_to_datetime(retry_after).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return 0


def request_with_backoff(method: str, url: str, *, deadline: float, retries: list[dict[str, Any]], **kwargs: Any) -> Any:
    """Retry a rejected create only on 429; retry polls also on 403, 408, 5xx and transport failures.

    A 429 pauses every worker of the account, not just the one that got it.
    """
    global _COOLDOWN_UNTIL
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        while True:
            with _RATE_LOCK:
                wait = max(0.0, _COOLDOWN_UNTIL - time.monotonic())
            remaining = deadline - time.monotonic()
            if remaining <= 0 or wait >= remaining:
                raise TimeoutError("Nimble request deadline exceeded during backoff")
            if not wait:
                break
            time.sleep(wait)
        try:
            timeout = max(1, min(kwargs.pop("timeout", 60), int(remaining)))
            return http_client.request_json(method, url, attempts=1, timeout=timeout, **kwargs).body
        except (http_client.HTTPError, requests.Timeout, requests.ConnectionError) as exc:
            status = getattr(exc, "status", None)
            retryable = status == 429 or (method == "GET" and (
                status in {403, 408, 500, 502, 503, 504} or isinstance(exc, (requests.Timeout, requests.ConnectionError))))
            if not retryable or attempt == MAX_REQUEST_ATTEMPTS - 1:
                raise
            delay = max(min(120, 5 * 2 ** attempt) + random.uniform(0, 1), _retry_seconds(getattr(exc, "retry_after", None)))
            retries.append({"method": method, "http_status": status, "attempt": attempt + 1, "delay_s": round(delay, 2)})
            if status == 429:
                with _RATE_LOCK:
                    _COOLDOWN_UNTIL = max(_COOLDOWN_UNTIL, time.monotonic() + delay)
            else:
                if time.monotonic() + delay >= deadline:
                    raise TimeoutError("Nimble request deadline exceeded during backoff") from exc
                time.sleep(delay)
    raise AssertionError("unreachable")


def parse_agent_result(payload: Any) -> dict[str, Any]:
    output = payload.get("output") if isinstance(payload, dict) else None
    if not isinstance(output, dict) or output.get("type") != "json":
        raise ValueError("Nimble Agents returned no JSON output")
    value = parse_structured_value(output.get("content"))
    citations = [
        citation.get("url")
        for claim in (output.get("trust") or {}).get("claims", []) or []
        for citation in claim.get("citations", []) or []
        if isinstance(citation.get("url"), str) and citation["url"].startswith(("https://", "http://"))
    ]
    return {**value, "sources": list(dict.fromkeys([*value["sources"], *citations]))}


class NimbleAgents(Adapter):
    def request_body(self, company: Company) -> dict[str, Any]:
        return {
            "use_case": self.settings.get("use_case", "enrichment"),
            "effort": self.settings["effort"],
            "input": instruction(company.name, company.domain),
            # Identity only. Do not send reference fields or reuse agent memory.
            "input_data": {"primary_domain": company.domain},
            "output_schema": OUTPUT_SCHEMA,
        }

    def lookup(self, company: Company) -> Answer:
        started = time.monotonic()
        headers = {"Authorization": f"Bearer {self.key('NIMBLE_API_KEY')}", "Content-Type": "application/json"}
        body = self.request_body(company)
        raw: dict[str, Any] = {"request": body, "polls": [], "retries": []}
        value, status, error, accepted = None, "error", None, False
        try:
            deadline = started + RUN_TIMEOUT_S
            state = request_with_backoff("POST", RUNS_URL, headers=headers, json=body, timeout=60,
                                         deadline=deadline, retries=raw["retries"])
            raw["create_response"] = state
            accepted = True
            raw.update(agent_id=state["web_search_agent_id"], run_id=state["id"])
            url = AGENT_URL.format(agent_id=quote(state["web_search_agent_id"], safe=""), run_id=quote(state["id"], safe=""))
            while state.get("status") not in {"completed", "failed", "cancelled"}:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Nimble agent polling timed out; the remote run may still be active; see run_id")
                time.sleep(min(POLL_INTERVAL_S, remaining))
                state = request_with_backoff("GET", url, headers=headers, timeout=60, deadline=deadline, retries=raw["retries"])
                raw["polls"].append(state)
            if state["status"] != "completed":
                raise RuntimeError(f"Nimble agent run {state['status']}: {state.get('error') or state.get('error_message') or ''}")
            result = request_with_backoff("GET", url + "/result", headers=headers, timeout=60,
                                          deadline=time.monotonic() + 600, retries=raw["retries"])
            raw["response"] = result
            value = parse_agent_result(result)
            raw["sources"] = value["sources"]
            status = "ok" if has_scored_value(value) else "not_found"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        fields = {**company_record(value).fields(), "sources": value["sources"]} if status == "ok" and value else {}
        return self.answer(
            company, status, fields, latency_ms=round((time.monotonic() - started) * 1000),
            cost_units=1 if accepted else None, cost_unit="nimble_agent_run", error=error, raw=raw,
        )


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    settings = spec.get("settings") or {}
    if "search_depth" in settings:
        return NimbleSearch(setup, spec, env)
    if "effort" in settings:
        return NimbleAgents(setup, spec, env)
    raise ValueError(f"{setup}: settings need either search_depth (search) or effort (agents)")
