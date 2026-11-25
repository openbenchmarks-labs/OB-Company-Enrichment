"""Structured answers from an OpenAI model: the extraction step of the Nimble search setups and the model judge.

The model is served by Azure OpenAI when `AZURE_OPENAI_BASE_URL` (the deployment's OpenAI v1 URL, ending in
`/openai/v1`) and `AZURE_OPENAI_API_KEY` are set, and by OpenAI with `OPENAI_API_KEY` otherwise. On Azure, the model
names in the configs are deployment names.
"""
from __future__ import annotations

import json
import os
import time
from functools import cache
from typing import Any


def provider() -> str | None:
    """"azure" or "openai", by the credentials set; None when neither is."""
    if os.getenv("AZURE_OPENAI_BASE_URL") and os.getenv("AZURE_OPENAI_API_KEY"):
        return "azure"
    return "openai" if os.getenv("OPENAI_API_KEY") else None


@cache
def _client() -> Any:
    from openai import OpenAI

    if provider() == "azure":
        return OpenAI(base_url=os.environ["AZURE_OPENAI_BASE_URL"].rstrip("/"), api_key=os.environ["AZURE_OPENAI_API_KEY"])
    return OpenAI()


def extract(
    *,
    system_prompt: str,
    user_content: str,
    schema: dict[str, Any],
    schema_name: str,
    model: str,
    reasoning_effort: str,
    max_output_tokens: int = 4000,
    timeout: float = 180,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the parsed JSON answer and an audit record (response id, usage, latency)."""
    started = time.perf_counter()
    response = _client().with_options(timeout=timeout, max_retries=0).responses.create(
        model=model,
        reasoning={"effort": reasoning_effort},
        max_output_tokens=max_output_tokens,
        store=False,
        input=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
        text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
    )
    audit = {
        "model": model, "reasoning_effort": reasoning_effort, "response_id": response.id, "status": response.status,
        "usage": response.usage.model_dump() if response.usage else None,
        "latency_ms": round((time.perf_counter() - started) * 1000),
    }
    if response.status != "completed":
        raise ValueError(f"extraction did not complete: {response.status}")
    return json.loads(response.output_text), audit
