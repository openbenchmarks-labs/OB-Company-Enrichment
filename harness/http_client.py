"""HTTP requests with retries and timing, and removal of API keys before anything is saved."""
from __future__ import annotations

import dataclasses
import re
import time
from typing import Any

import requests

RETRY_STATUSES = {429, 500, 502, 503, 504}
# A field name that ends in a credential word, such as `apiKey`, `accessToken`, `X-Api-Token` or `Authorization`.
# Matching only the end of the name keeps counts like `input_tokens`.
SECRET_KEY = re.compile(r"(api[_-]?key|apikey|access[_-]?key|secret[_-]?key|token|secret|authorization|password)$", re.I)
REDACTED = "***REDACTED***"


class HTTPError(RuntimeError):
    """A non-2xx response, raised by `request_json`."""

    def __init__(self, response: "Response", url: str) -> None:
        self.response = response
        self.status = response.status
        self.retry_after = response.headers.get("retry-after")
        body = response.body
        message = ""
        if isinstance(body, dict):
            message = str(body.get("message") or body.get("error") or body.get("detail") or body.get("non_json_body") or "")
        super().__init__(f"HTTP {response.status} from {url}: {message[:300]}")


@dataclasses.dataclass
class Response:
    status: int
    body: Any
    latency_ms: int  # Time of the attempt that produced this response. Earlier failed attempts are not counted.
    attempts: int
    headers: dict[str, str]

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json: Any = None,
    timeout: float = 60,
    attempts: int = 3,
) -> Response:
    """Send one request. Retries connection errors, 429 and 5xx with backoff, and honours Retry-After."""
    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            response = requests.request(method, url, headers=headers, params=params, json=json, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout):
            if attempt == attempts:
                raise
            time.sleep(2 ** attempt)
            continue
        latency_ms = round((time.perf_counter() - started) * 1000)
        try:
            body: Any = response.json()
        except ValueError:
            body = {"non_json_body": response.text[:2000]}
        if response.status_code in RETRY_STATUSES and attempt < attempts:
            time.sleep(_retry_after(response) or 2 ** attempt)
            continue
        kept = {k.lower(): v for k, v in response.headers.items()
                if k.lower().startswith(("retry-after", "x-ratelimit", "ratelimit", "x-call-credits"))}
        return Response(response.status_code, body, latency_ms, attempt, kept)
    raise AssertionError("unreachable")


def request_json(method: str, url: str, **kwargs: Any) -> Response:
    """`request`, but raise HTTPError for a non-2xx response."""
    response = request(method, url, **kwargs)
    if not response.ok:
        raise HTTPError(response, url)
    return response


def _retry_after(response: requests.Response) -> float | None:
    try:
        return min(float(response.headers.get("retry-after", "")), 60.0)
    except ValueError:
        return None


def redact(value: Any) -> Any:
    """Copy of `value` with every field whose name looks like a credential replaced by a placeholder."""
    if isinstance(value, dict):
        return {k: REDACTED if SECRET_KEY.search(str(k)) and v else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
