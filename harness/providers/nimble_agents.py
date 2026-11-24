"""Nimble Agents enrichment, with one isolated asynchronous run per company."""

from __future__ import annotations

import os
import random
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import quote

import requests

from harness.common import CompanyCase, ProviderResult
from harness.web_research import (
    OUTPUT_SCHEMA, has_scored_value, instruction, normalized_company,
    parse_structured_value, request_contract,
)
from .base import ProviderHTTPError, request_json

VENDOR_SLUG = "nimble-agents"
VENDOR_NAME = "Nimble Agents"
REQUIRED_ENV = ("NIMBLE_API_KEY",)
PAID_CALLS_PER_CASE = 1  # One agent run; polling is separate.
RUNS_URL = "https://sdk.nimbleway.com/v2/agents/runs"
EFFORT = "high"  # Documented default, fixed for reproducibility.
RUN_TIMEOUT_S = 1800
POLL_INTERVAL_S = 10
MAX_REQUEST_ATTEMPTS = 7
_RATE_LOCK = threading.Lock()
_COOLDOWN_UNTIL = 0.0


def request_with_backoff(method: str, url: str, *, deadline: float,
                         retries: list[dict[str, Any]], **kwargs: Any) -> tuple[Any, int]:
    """Retry rejected creates on 429; only GETs retry ambiguous transport failures."""
    global _COOLDOWN_UNTIL
    for attempt in range(MAX_REQUEST_ATTEMPTS):
        while True:
            with _RATE_LOCK:
                wait = max(0, _COOLDOWN_UNTIL - time.monotonic())
            remaining = deadline - time.monotonic()
            if remaining <= 0 or wait >= remaining:
                raise TimeoutError("Nimble request deadline exceeded during backoff")
            if not wait:
                break
            time.sleep(wait)
        try:
            return request_json(method, url, **{
                **kwargs, "timeout": max(1, min(kwargs.get("timeout", 60), int(remaining))),
            })
        except (ProviderHTTPError, requests.Timeout, requests.ConnectionError) as exc:
            status = getattr(exc, "status_code", None)
            retryable = status == 429 or (method == "GET" and (
                status in {403, 408, 500, 502, 503, 504}
                or isinstance(exc, (requests.Timeout, requests.ConnectionError))))
            if not retryable or attempt == MAX_REQUEST_ATTEMPTS - 1:
                raise
            delay = min(120, 5 * 2 ** attempt) + random.uniform(0, 1)
            retry_after = getattr(exc, "retry_after", None)
            if retry_after:
                try:
                    seconds = float(retry_after)
                except ValueError:
                    try:
                        seconds = parsedate_to_datetime(retry_after).timestamp() - time.time()
                    except (ValueError, TypeError, OverflowError):
                        seconds = 0
                delay = max(delay, seconds)
            retries.append({"method": method, "http_status": status,
                            "attempt": attempt + 1, "delay_s": round(delay, 2)})
            print(f"Nimble {method} retry after HTTP {status}: wait {delay:.1f}s", flush=True)
            if status == 429:
                # Pause all five workers before further requests to the same account.
                with _RATE_LOCK:
                    _COOLDOWN_UNTIL = max(_COOLDOWN_UNTIL, time.monotonic() + delay)
            else:
                if time.monotonic() + delay >= deadline:
                    raise TimeoutError("Nimble request deadline exceeded during backoff") from exc
                time.sleep(delay)


def parse_result(payload: Any) -> dict[str, Any]:
    output = payload.get("output") if isinstance(payload, dict) else None
    if not isinstance(output, dict) or output.get("type") != "json":
        raise ValueError("Nimble Agents returned no JSON output")
    value = parse_structured_value(output.get("content"))
    citations = []
    for claim in (output.get("trust") or {}).get("claims", []) or []:
        for citation in claim.get("citations", []) or []:
            url = citation.get("url")
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                citations.append(url)
    return {**value, "sources": list(dict.fromkeys([*value["sources"], *citations]))}


def run(case: CompanyCase, *, checkpoint: Callable[[dict[str, Any]], None] | None = None) -> ProviderResult:
    started = time.monotonic()
    headers = {"Authorization": f"Bearer {os.environ['NIMBLE_API_KEY']}",
               "Content-Type": "application/json"}
    body = {
        "use_case": "enrichment", "effort": EFFORT,
        "input": instruction(case.input_name, case.input_domain),
        # Identity only. Do not send reference fields or reuse agent memory.
        "input_data": {"primary_domain": case.input_domain},
        "output_schema": OUTPUT_SCHEMA,
    }
    audit: dict[str, Any] = {
        "web_research_contract": request_contract(case),
        "request": {"method": "POST", "url": RUNS_URL, "body": body},
        "use_case": "enrichment", "effort": EFFORT,
        "cost_scope": "Nimble agent run; USD pricing not configured",
        "polls": [],
        "retries": [],
    }
    value = None
    status, error = "error", None
    accepted = False
    try:
        deadline = started + RUN_TIMEOUT_S
        # Keep the create timeout separate so even a zero polling budget retains IDs.
        state, _ = request_with_backoff("POST", RUNS_URL, headers=headers, json_body=body,
                                       timeout=60, deadline=started + max(60, RUN_TIMEOUT_S), retries=audit["retries"])
        audit["create_response"] = state
        accepted = True
        agent_id, run_id = state["web_search_agent_id"], state["id"]
        audit.update(agent_id=agent_id, run_id=run_id)
        if checkpoint:
            checkpoint(audit)
        url = ("https://sdk.nimbleway.com/v2/agents/" + quote(agent_id, safe="")
               + "/runs/" + quote(run_id, safe=""))
        while state.get("status") not in {"completed", "failed", "cancelled"}:
            remaining = RUN_TIMEOUT_S - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("Nimble agent polling timed out; remote run may still be active; see audit run_id")
            time.sleep(min(POLL_INTERVAL_S, remaining))
            remaining = RUN_TIMEOUT_S - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("Nimble agent polling timed out; remote run may still be active; see audit run_id")
            state, _ = request_with_backoff("GET", url, headers=headers, timeout=60,
                                           deadline=deadline, retries=audit["retries"])
            audit["polls"].append(state)
        if state["status"] != "completed":
            raise RuntimeError(f"Nimble agent run {state['status']}: {state.get('error') or state.get('error_message') or ''}")
        result, _ = request_with_backoff("GET", url + "/result", headers=headers, timeout=60,
                                        deadline=time.monotonic() + 600, retries=audit["retries"])
        audit["raw_response"] = result
        value = parse_result(result)
        audit["sources"] = value["sources"]
        status = "ok" if has_scored_value(value) else "not_found"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    return ProviderResult(
        VENDOR_SLUG, VENDOR_NAME, case.case_slug, status,
        round((time.monotonic() - started) * 1000),
        normalized=normalized_company(value) if status == "ok" else None,
        cost_units=1 if accepted else None, cost_unit="nimble_agent_run",
        audit=audit, error=error,
    )
