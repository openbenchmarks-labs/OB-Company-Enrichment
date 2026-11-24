"""The shapes passed between the runner and the tool adapters."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any

# The fields `harness.run grade` scores. Every adapter fills these keys, with None for a value it did not return.
FIELDS = ("hq", "industry", "employee_band", "founded_year", "linkedin_url")


@dataclasses.dataclass(frozen=True)
class Company:
    domain: str
    name: str | None = None


@dataclasses.dataclass
class Answer:
    """One setup's answer for one company."""

    setup: str
    domain: str
    status: str  # "ok", "not_found" or "error"
    fields: dict[str, Any] = dataclasses.field(default_factory=dict)
    latency_ms: int | None = None
    cost_units: float | None = None
    cost_unit: str | None = None
    error: str | None = None
    raw: Any = None  # The vendor's responses, with API keys removed. Saved apart from the answer row.
    queried_at: str = dataclasses.field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def row(self) -> dict[str, Any]:
        """The line written to `<run>/<setup>.jsonl`, which `harness.run grade` reads."""
        values = {name: self.fields.get(name) for name in FIELDS}
        extra = {k: v for k, v in self.fields.items() if k not in FIELDS}
        return {
            "domain": self.domain, **values, "status": self.status, "error": self.error,
            "latency_ms": self.latency_ms, "cost_units": self.cost_units, "cost_unit": self.cost_unit,
            "queried_at": self.queried_at, "setup": self.setup, **({"extra": extra} if extra else {}),
        }
