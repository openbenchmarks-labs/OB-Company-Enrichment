"""The company record every adapter returns, and its conversion into the fields `judge/grader.py` reads."""
from __future__ import annotations

import dataclasses
import re
from typing import Any
from urllib.parse import urlparse


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _year(value: Any) -> int | None:
    if value in (None, ""):
        return None
    match = re.search(r"(?:17|18|19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None


def _clean_int(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def normalize_domain(value: Any) -> str | None:
    if not value:
        return None
    raw = str(value).strip().lower()
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.") or None


def normalize_linkedin(value: Any) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    if "://" not in raw:
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if "linkedin.com" not in host:
        return raw.rstrip("/")
    path = re.sub(r"/+", "/", parsed.path).rstrip("/").lower()
    return f"https://www.linkedin.com{path}" if path else None


@dataclasses.dataclass
class CompanyRecord:
    """What a tool said about one company, cleaned the same way for every tool."""

    legal_name: str | None = None
    primary_domain: str | None = None
    aliases: list[str] = dataclasses.field(default_factory=list)
    domains: list[str] = dataclasses.field(default_factory=list)
    hq_country: str | None = None
    hq_city: str | None = None
    founded_year: int | None = None
    industry: str | None = None
    industries: list[str] = dataclasses.field(default_factory=list)
    linkedin_url: str | None = None
    headcount_min: int | None = None
    headcount_max: int | None = None

    def __post_init__(self) -> None:
        self.primary_domain = normalize_domain(self.primary_domain)
        self.domains = _unique([d for value in [self.primary_domain, *self.domains] if (d := normalize_domain(value))])
        self.aliases = _unique([str(v).strip() for v in self.aliases if str(v).strip()])
        self.linkedin_url = normalize_linkedin(self.linkedin_url)
        self.founded_year = _year(self.founded_year)
        self.headcount_min = _clean_int(self.headcount_min)
        self.headcount_max = _clean_int(self.headcount_max)
        if self.headcount_min is not None and self.headcount_min <= 0:
            self.headcount_min = None
        if self.headcount_max is not None and self.headcount_max <= 0:
            self.headcount_max = None
        self.industries = _unique([str(v).strip() for v in [self.industry, *self.industries] if v and str(v).strip()])
        if not self.industry and self.industries:
            self.industry = self.industries[0]

    def fields(self) -> dict[str, Any]:
        """The graded fields, plus the rest of the record as extra keys."""
        city, country = (str(v).strip() if v else "" for v in (self.hq_city, self.hq_country))
        return {
            "hq": ", ".join(v for v in (city, country) if v) or None,
            "industry": self.industry,
            "employee_band": employee_band(self.headcount_min, self.headcount_max),
            "founded_year": self.founded_year,
            "linkedin_url": self.linkedin_url,
            "legal_name": self.legal_name,
            "primary_domain": self.primary_domain,
            "domains": self.domains,
            "aliases": self.aliases,
            "industries": self.industries,
            "headcount_min": self.headcount_min,
            "headcount_max": self.headcount_max,
        }


def employee_band(minimum: int | None, maximum: int | None) -> str | None:
    """"51-200" for a range, "10001+" when only the lower bound is known, None without a lower bound."""
    if minimum is None:
        return None
    if maximum is None:
        return f"{minimum}+"
    return f"{minimum}-{maximum}"
