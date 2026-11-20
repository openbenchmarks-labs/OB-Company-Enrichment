"""Structured extraction with an OpenAI model, for setups whose tool returns search results instead of answers."""
from __future__ import annotations

import json
import time
from functools import cache
from typing import Any


@cache
def _client() -> Any:
    from openai import OpenAI

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
