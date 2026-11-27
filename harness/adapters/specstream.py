"""SpecStream Web Search (setup specstream-search-web): one compact search per company, then a model reads the results,
as for Nimble's search setups.

The reply is wrapped as `{"data": {..., "sources": [...]}}`; compact mode gives one snippet per source. A search that
returns no sources is not billed.
"""
from __future__ import annotations

import time
from typing import Any

from harness import http_client
from harness.adapters.nimble import search_query
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.web_research import company_record, extract_from_results, has_scored_value

SEARCH_URL = "https://api.specstream.ai/v1/web-search"
SEARCH_TIMEOUT = 60
COST_UNIT = "specstream_search_request"


def search_hits(sources: list[Any], max_results: int) -> list[dict[str, str]]:
    """Unique result URLs with their title and snippet, in SpecStream's order, for the extraction model."""
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    for source in sources:
        url = source.get("url").strip() if isinstance(source, dict) and isinstance(source.get("url"), str) else ""
        if not url or url in seen:
            continue
        seen.add(url)
        hits.append({"url": url, "title": str(source.get("title") or ""), "snippet": str(source.get("snippet") or "")})
        if len(hits) >= max_results:
            break
    return hits


class SpecStreamSearch(Adapter):
    def request_body(self, company: Company) -> dict[str, Any]:
        body = {"q": search_query(company), "num": int(self.settings.get("num", 10)), "compact": True}
        if self.settings.get("source_max_chars"):
            body["source_max_chars"] = int(self.settings["source_max_chars"])
        return body

    def lookup(self, company: Company) -> Answer:
        started = time.perf_counter()
        request_body = self.request_body(company)
        headers = {"Authorization": f"Bearer {self.key('SPECSTREAM_API_KEY')}", "Content-Type": "application/json"}
        response = http_client.request_json("POST", SEARCH_URL, headers=headers, json=request_body, timeout=SEARCH_TIMEOUT)
        body = response.body
        raw: dict[str, Any] = {"request": request_body, "response": body, "search_latency_ms": response.latency_ms}
        status, value, error, billed = "not_found", None, None, 0
        try:
            data = body.get("data") if isinstance(body, dict) else None
            if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
                raise ValueError("SpecStream returned no data.sources array")
            billed = 1 if data["sources"] else 0
            hits = search_hits(data["sources"], request_body["num"])
            raw["search_evidence"] = hits
            if hits:
                value, raw["extraction"] = extract_from_results(company.name, company.domain, hits,
                                                                self.settings.get("extractor") or {})
                status = "ok" if has_scored_value(value) else "not_found"
        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {exc}"
        fields = {**company_record(value).fields(), "sources": value["sources"]} if status == "ok" and value else {}
        return self.answer(
            company, status, fields, latency_ms=round((time.perf_counter() - started) * 1000),
            cost_units=billed, cost_unit=COST_UNIT, error=error, raw=raw,
        )


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    return SpecStreamSearch(setup, spec, env)
