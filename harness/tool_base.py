"""The base class every tool adapter in `harness/adapters/` extends."""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

from .models import Answer, Company


class Adapter(ABC):
    """Builds one setup's requests from its `settings` in `tools/<tool>.yaml` and reads the answers."""

    # Companies per request. An adapter that sends several companies in one request sets this above 1.
    batch_size = 1

    def __init__(self, setup: str, spec: dict[str, Any], env: list[str]) -> None:
        self.setup = setup
        self.spec = spec
        self.settings: dict[str, Any] = dict(spec.get("settings") or {})
        self.env = list(spec.get("env") or env or [])
        # Pacing the runner enforces across its workers, from the setup's settings.
        self.seconds_between_requests = float(self.settings.get("seconds_between_requests") or 0)
        self.max_workers = int(self.settings.get("max_workers") or 0) or None

    def missing_env(self) -> list[str]:
        return [name for name in self.env if not os.getenv(name)]

    def key(self, name: str) -> str:
        value = os.getenv(name)
        if not value:
            raise RuntimeError(f"{self.setup}: {name} is not set")
        return value

    def answer(self, company: Company, status: str, fields: dict[str, Any] | None = None, **kwargs: Any) -> Answer:
        return Answer(setup=self.setup, domain=company.domain, status=status, fields=fields or {}, **kwargs)

    @abstractmethod
    def lookup(self, company: Company) -> Answer:
        """Ask the tool about one company. Return an Answer; raise only for a failure the runner should record."""

    def lookup_many(self, companies: list[Company]) -> list[Answer]:
        return [self.lookup(company) for company in companies]
