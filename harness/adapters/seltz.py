"""Seltz, in two forms.

Answer (seltz-companies): one Answer request with a JSON-schema answer, scoped by the setup's `scope`.
Search (seltz-search-web): one Search request at the setup's `scope`. Its results mix web pages with company profiles
(`properties.company`, from the company's LinkedIn page). When a profile names the input's website, its fields are the
answer as Seltz gives them; a field the profile lacks stays empty. When none does, a model reads the results, as for
Nimble's search setups.
"""
from __future__ import annotations

import json
import time
from typing import Any

from harness import http_client
from harness.models import Answer, Company
from harness.tool_base import Adapter
from harness.adapters.nimble import search_query
from harness.company import normalize_domain
from harness.web_research import (
    EXA_OUTPUT_SCHEMA, OUTPUT_SCHEMA, company_record, extract_from_results, has_scored_value, instruction,
    parse_structured_value,
)

URL = "https://api.seltz.ai/v1/answer"
SEARCH_URL = "https://api.seltz.ai/v1/search"
TIMEOUT = 180
SEARCH_TIMEOUT = 60
COST_UNIT = "seltz_answer_request"
SEARCH_COST_UNIT = "seltz_search_request"
SYSTEM_PROMPT = (
    "Return only the requested JSON object. Use null for unknown scalar values "
    "and [] for unknown lists. Do not add prose or Markdown fences."
)
RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {"name": "company_enrichment", "strict": True, "schema": EXA_OUTPUT_SCHEMA}}


def parse_answer(value: Any, citations: Any) -> dict[str, Any]:
    """Decode Seltz's answer text, which may come wrapped in a Markdown code fence, and add its citation URLs."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Seltz Answer returned no answer text")
    text = value.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: text.rindex("```")]
        text = text.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("Seltz Answer was not valid structured JSON") from error
    sources = [row["url"] for row in citations or [] if isinstance(row, dict) and isinstance(row.get("url"), str) and row["url"]]
    return parse_structured_value({**parsed, "sources": list(dict.fromkeys(sources))})


class Seltz(Adapter):
    def request_body(self, company: Company) -> dict[str, Any]:
        return {
            "query": instruction(company.name, company.domain),
            "scope": self.settings["scope"],
            "system_prompt": SYSTEM_PROMPT,
            "response_format": RESPONSE_FORMAT,
        }

    def lookup(self, company: Company) -> Answer:
        headers = {"x-api-key": self.key("SELTZ_API_KEY"), "Content-Type": "application/json"}
        request_body = self.request_body(company)
        response = http_client.request_json("POST", URL, headers=headers, json=request_body, timeout=TIMEOUT)
        body = response.body
        if not isinstance(body, dict):
            raise ValueError("Seltz Answer returned a non-object response")
        value = parse_answer(body.get("answer"), body.get("citations"))
        raw = {"request": request_body, "sources": value["sources"], "response": body}
        if not has_scored_value(value):
            return self.answer(company, "not_found", latency_ms=response.latency_ms, cost_units=1, cost_unit=COST_UNIT, raw=raw)
        return self.answer(
            company, "ok", {**company_record(value).fields(), "sources": value["sources"]},
            latency_ms=response.latency_ms, cost_units=1, cost_unit=COST_UNIT, raw=raw,
        )


def _int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def profile_for(documents: list[Any], company: Company) -> dict[str, Any] | None:
    """The company profile among the results whose website is the input's domain: the one named like the input company
    first, then the one with the most employees (a group's own page over its divisions'), then Seltz's order."""
    matches = []
    for rank, doc in enumerate(documents):
        profile = (doc.get("properties") or {}).get("company") if isinstance(doc, dict) else None
        if isinstance(profile, dict) and normalize_domain(profile.get("website")) == normalize_domain(company.domain):
            matches.append((rank, doc, profile))
    if not matches:
        return None
    name = (company.name or "").strip().casefold()
    rank, doc, profile = min(matches, key=lambda m: (str(m[2].get("name") or "").strip().casefold() != name,
                                                     -(_int(m[2].get("employee_count")) or 0), m[0]))
    return {"rank": rank, "url": doc.get("url"), "company": profile}


def profile_value(found: dict[str, Any]) -> dict[str, Any]:
    """A Seltz company profile in the output schema. Its LinkedIn page is the document's URL; its headcount is one count."""
    profile, url = found["company"], str(found.get("url") or "")
    hq = profile.get("headquarters") if isinstance(profile.get("headquarters"), dict) else {}
    count = _int(profile.get("employee_count"))
    industry = profile.get("industry") or None
    value = {key: None for key in OUTPUT_SCHEMA["properties"]}
    value.update({
        "legal_name": profile.get("name") or None, "primary_domain": normalize_domain(profile.get("website")) or None,
        "hq_country": hq.get("country") or None, "hq_city": hq.get("city") or None,
        "founded_year": _int(profile.get("founded_year")), "industry": industry, "industries": [industry] if industry else [],
        "linkedin_url": url if "linkedin.com/company/" in url else None, "headcount_min": count, "headcount_max": count,
        "sources": [url] if url else [],
    })
    return value


def search_hits(documents: list[Any], max_results: int) -> list[dict[str, str]]:
    """Unique result URLs with their text, in Seltz's order, for the extraction model."""
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    for doc in documents:
        url = doc.get("url").strip() if isinstance(doc, dict) and isinstance(doc.get("url"), str) else ""
        if not url or url in seen:
            continue
        seen.add(url)
        text = doc.get("content") or "\n".join(
            (s.get("text") if isinstance(s, dict) else str(s)) or "" for s in doc.get("snippets") or [])
        hits.append({"url": url, "title": "", "snippet": str(text or "")})
        if len(hits) >= max_results:
            break
    return hits


class SeltzSearch(Adapter):
    def request_body(self, company: Company) -> dict[str, Any]:
        return {"query": search_query(company), "scope": self.settings["scope"], "max_results": int(self.settings.get("max_results", 10))}

    def lookup(self, company: Company) -> Answer:
        started = time.perf_counter()
        request_body = self.request_body(company)
        headers = {"x-api-key": self.key("SELTZ_API_KEY"), "Content-Type": "application/json"}
        response = http_client.request_json("POST", SEARCH_URL, headers=headers, json=request_body, timeout=SEARCH_TIMEOUT)
        body = response.body
        raw: dict[str, Any] = {"request": request_body, "response": body, "search_latency_ms": response.latency_ms}
        status, value, error = "not_found", None, None
        try:
            documents = body.get("documents") if isinstance(body, dict) else None
            if not isinstance(documents, list):
                raise ValueError("Seltz Search returned no documents array")
            found = profile_for(documents, company)
            if found:
                raw["answered_from"], raw["profile_rank"] = "profile", found["rank"]
                value = profile_value(found)
            else:
                hits = search_hits(documents, request_body["max_results"])
                raw["answered_from"], raw["search_evidence"] = "extraction", hits
                if hits:
                    value, raw["extraction"] = extract_from_results(company.name, company.domain, hits,
                                                                    self.settings.get("extractor") or {})
            status = "ok" if value and has_scored_value(value) else "not_found"
        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {exc}"
        fields = {**company_record(value).fields(), "sources": value["sources"]} if status == "ok" and value else {}
        return self.answer(
            company, status, fields, latency_ms=round((time.perf_counter() - started) * 1000),
            cost_units=1, cost_unit=SEARCH_COST_UNIT, error=error, raw=raw,
        )


def build(setup: str, spec: dict[str, Any], env: list[str]) -> Adapter:
    if str(spec.get("endpoint") or "").endswith("/v1/search"):
        return SeltzSearch(setup, spec, env)
    return Seltz(setup, spec, env)
