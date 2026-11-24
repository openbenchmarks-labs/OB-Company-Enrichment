"""Shared contract, cohort loading, reference scoring, and snapshot I/O.

Cases come from a cases file (see data/README.md). Available reference fields
are used as null-aware ground truth.
"""
from __future__ import annotations

import dataclasses
import functools
import json
import math
import re
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import urlparse

import pycountry


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "runs" / "snapshot.json"
RUNS_DIR = ROOT / "runs"
IDENTITY_REDIRECTS_PATH = ROOT / "data" / "identity-redirects.json"

LEGACY_DATASET_SLUG = "company-firmographic-stable-pilot-2026-q3-v1"
DATASET_SLUG = "company-firmographic-linkedin-gt-2026-q3-v2"
DATASET_NAME = "Company Firmographic Enrichment"
LINKEDIN_REFERENCE_STATUS = "linkedin_live_ground_truth_v1"

SCORED_ATTRIBUTES = (
    "hq_location",
    "founded_year",
    "industry",
    "linkedin_url",
    "headcount_band",
)
DEFERRED_ATTRIBUTES = ("funding_stage",)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _year(value: Any) -> int | None:
    if value in (None, ""):
        return None
    match = re.search(r"(?:17|18|19|20)\d{2}", str(value))
    return int(match.group(0)) if match else None


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


@functools.lru_cache(maxsize=1)
def _identity_redirects() -> dict[str, dict[str, str]]:
    """Load audited redirect and inactive-domain observations.

    This is deliberately a versioned, checked-in cache rather than a live HTTP
    lookup during scoring: benchmark results remain reproducible while the
    original input domain stays available in every case record.
    """
    payload = json.loads(IDENTITY_REDIRECTS_PATH.read_text(encoding="utf-8"))
    return {
        key: {str(source): str(target) for source, target in (payload.get(key) or {}).items()}
        for key in ("domain_redirects", "linkedin_redirects", "inactive_domains")
    }


def _resolve_redirect(value: str | None, redirects: dict[str, str]) -> str | None:
    current = value
    seen: set[str] = set()
    while current and current in redirects and current not in seen:
        seen.add(current)
        current = redirects[current]
    return current


def resolve_domain_identity(value: Any) -> str | None:
    """Return the audited current domain while retaining callers' original value."""
    domain = normalize_domain(value)
    return _resolve_redirect(domain, _identity_redirects()["domain_redirects"])


def resolve_linkedin_identity(value: Any) -> str | None:
    url = normalize_linkedin(value)
    return _resolve_redirect(url, _identity_redirects()["linkedin_redirects"])


def inactive_domain_reason(value: Any) -> str | None:
    domain = normalize_domain(value)
    return _identity_redirects()["inactive_domains"].get(domain or "")


def primary_domain_match_basis(reference: dict[str, Any], company: "NormalizedCompany") -> str:
    """Compare domains after audited redirects, or surface inactive inputs."""
    reference_domain = normalize_domain(reference.get("primary_domain"))
    if inactive_domain_reason(reference_domain):
        return "inactive_input_excluded"
    reference_domains = [reference_domain, *[normalize_domain(value) for value in reference.get("alternate_primary_domains", [])]]
    canonical_references = {resolve_domain_identity(value) for value in reference_domains if value}
    canonical_domains = {resolve_domain_identity(domain) for domain in company.domains}
    if canonical_references & canonical_domains:
        return (
            "exact_domain"
            if any(value in company.domains for value in reference_domains if value)
            else "verified_domain_redirect"
        )
    return "mismatch"


def linkedin_match_basis(reference: dict[str, Any], company: "NormalizedCompany") -> str:
    reference_url = normalize_linkedin(reference.get("linkedin_url"))
    provider_url = normalize_linkedin(company.linkedin_url)
    if not reference_url or not provider_url:
        return "mismatch"
    if resolve_linkedin_identity(reference_url) == resolve_linkedin_identity(provider_url):
        return "exact_linkedin" if reference_url == provider_url else "verified_linkedin_redirect"
    return "mismatch"


def normalize_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()
    return re.sub(r"\s+", " ", text) or None


def normalize_country(value: Any) -> str | None:
    """Return an ISO-backed comparison value for names and alpha-2/3 codes."""
    if value in (None, ""):
        return None
    raw = str(value).strip()
    aliases = {
        "turkey": "Türkiye",
        "uk": "United Kingdom",
        "u.k.": "United Kingdom",
        "usa": "United States",
        "u.s.": "United States",
        "united states of america": "United States",
    }
    candidate = aliases.get(raw.lower(), raw)
    try:
        return pycountry.countries.lookup(candidate).alpha_2.lower()
    except LookupError:
        return normalize_text(candidate)


def normalize_funding_stage(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    aliases = {
        "no funding yet": "unfunded",
        "no funding": "unfunded",
        "not funded": "unfunded",
        "pre seed": "pre_seed",
        "series unknown": "venture_unknown",
        "venture unknown": "venture_unknown",
        "private equity": "private_equity",
    }
    return aliases.get(text, text.replace(" ", "_"))


def _clean_int(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


HEADCOUNT_BANDS: tuple[tuple[int, int | None], ...] = (
    (1, 1),
    (2, 10),
    (11, 50),
    (51, 200),
    (201, 500),
    (501, 1_000),
    (1_001, 5_000),
    (5_001, 10_000),
    (10_001, None),
)
HEADCOUNT_EXACT_TOLERANCE_PCT = 0.05


def _parse_headcount_band(value: Any) -> tuple[int | None, int | None]:
    if value in (None, ""):
        return None, None
    text = str(value).strip().lower().replace("employees", "").strip()
    if text == "self-employed":
        return 1, 1
    numbers = [int(number.replace(",", "")) for number in re.findall(r"\d[\d,]*", text)]
    if "+" in text and numbers:
        return numbers[0], None
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return None, None


def _canonical_headcount_band(
    minimum: int | None, maximum: int | None
) -> tuple[int, int | None] | None:
    if minimum is None and maximum is None:
        return None
    candidate = (minimum, maximum)
    # Several providers use 1-10 for the same small-company band that
    # LinkedIn labels 2-10. It is not an exact count, but is semantically the
    # same inclusive band for benchmark purposes.
    if candidate == (1, 10):
        return (2, 10)
    if candidate in HEADCOUNT_BANDS:
        return candidate
    if minimum is not None and maximum == minimum:
        for lower, upper in HEADCOUNT_BANDS:
            if minimum >= lower and (upper is None or minimum <= upper):
                return lower, upper
    return None


@dataclasses.dataclass(frozen=True)
class CompanyCase:
    case_slug: str
    company_id: str
    slice: str
    input_name: str | None
    input_domain: str
    reference: dict[str, Any]
    source_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class NormalizedCompany:
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
    funding_stage: str | None = None

    def __post_init__(self) -> None:
        self.primary_domain = normalize_domain(self.primary_domain)
        self.domains = _unique(
            [d for value in [self.primary_domain, *self.domains] if (d := normalize_domain(value))]
        )
        self.aliases = _unique([str(v).strip() for v in self.aliases if str(v).strip()])
        self.linkedin_url = normalize_linkedin(self.linkedin_url)
        self.founded_year = _year(self.founded_year)
        self.headcount_min = _clean_int(self.headcount_min)
        self.headcount_max = _clean_int(self.headcount_max)
        if self.headcount_min is not None and self.headcount_min <= 0:
            self.headcount_min = None
        if self.headcount_max is not None and self.headcount_max <= 0:
            self.headcount_max = None
        self.funding_stage = normalize_funding_stage(self.funding_stage)
        self.industries = _unique(
            [str(v).strip() for v in [self.industry, *self.industries] if v and str(v).strip()]
        )
        if not self.industry and self.industries:
            self.industry = self.industries[0]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class ProviderResult:
    provider_slug: str
    provider_name: str
    case_slug: str
    status: str
    latency_ms: int
    normalized: NormalizedCompany | None = None
    cost_units: float | None = None
    cost_unit: str | None = None
    ambiguity_count: int = 0
    audit: dict[str, Any] = dataclasses.field(default_factory=dict)
    error: str | None = None
    queried_at: str = dataclasses.field(default_factory=now_iso)
    metrics: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = dataclasses.asdict(self)
        value["normalized"] = self.normalized.to_dict() if self.normalized else None
        return value


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def load_cases_file(path: Path) -> tuple[list[CompanyCase], dict[str, Any]]:
    """Read a cases file: {"reference_status": ..., "cases": [CompanyCase fields, ...]}."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = [CompanyCase(**row) for row in payload["cases"]]
    if len({case.case_slug for case in cases}) != len(cases):
        raise RuntimeError(f"{path} has duplicate case slugs")
    return cases, {key: value for key, value in payload.items() if key != "cases"}


def _present(company: NormalizedCompany, attribute: str) -> bool:
    if attribute == "hq_location":
        return bool(company.hq_country or company.hq_city)
    if attribute == "headcount_band":
        return company.headcount_min is not None or company.headcount_max is not None
    if attribute == "industry":
        return bool(company.industry or company.industries)
    return bool(getattr(company, attribute))


def _range_overlap(a_min: int | None, a_max: int | None, b_min: int | None, b_max: int | None) -> bool | None:
    if a_min is None and a_max is None:
        return None
    if b_min is None and b_max is None:
        return None
    a_lo, a_hi = a_min or 0, a_max if a_max is not None else math.inf
    b_lo, b_hi = b_min or 0, b_max if b_max is not None else math.inf
    return max(a_lo, b_lo) <= min(a_hi, b_hi)


def _industry_agrees(reference: list[str], actual: list[str]) -> bool | None:
    ref = [normalize_text(v) for v in reference if normalize_text(v)]
    got = [normalize_text(v) for v in actual if normalize_text(v)]
    if not ref or not got:
        return None
    return any(r == g or (len(r) >= 5 and (r in g or g in r)) for r in ref for g in got)


def _reference_available(reference: dict[str, Any], attribute: str) -> bool:
    if attribute == "hq_location":
        return bool(reference.get("hq_country") or reference.get("hq_city"))
    if attribute == "headcount_band":
        return reference.get("headcount_min") is not None or reference.get("headcount_max") is not None
    if attribute == "industry":
        return bool(reference.get("industry") or reference.get("industries"))
    return reference.get(attribute) not in (None, "")


def headcount_match_basis(reference: dict[str, Any], company: NormalizedCompany) -> str:
    """Accept matching bands or a supported exact-count/range containment."""
    reference_band = _canonical_headcount_band(
        reference.get("headcount_min"), reference.get("headcount_max")
    )
    provider_band = _canonical_headcount_band(
        company.headcount_min, company.headcount_max
    )
    if reference_band is not None and reference_band == provider_band:
        return "canonical_linkedin_band"

    reference_exact = _clean_int(reference.get("headcount_exact"))
    provider_exact = (
        company.headcount_min
        if company.headcount_min is not None
        and company.headcount_min == company.headcount_max
        else None
    )
    reference_min = _clean_int(reference.get("headcount_min")); reference_max = _clean_int(reference.get("headcount_max"))
    if provider_exact is not None and reference_min is not None and reference_max is not None and reference_min <= provider_exact <= reference_max:
        return "provider_exact_within_reference_range"
    if reference_exact is not None and company.headcount_min is not None and company.headcount_max is not None and company.headcount_min <= reference_exact <= company.headcount_max:
        return "reference_exact_within_provider_range"
    if reference_exact is not None and provider_exact is not None and reference_exact > 0 and abs(provider_exact-reference_exact)/reference_exact <= HEADCOUNT_EXACT_TOLERANCE_PCT:
        return "exact_count_within_5_pct"
    return "mismatch"


def _headcount_matches(reference: dict[str, Any], company: NormalizedCompany) -> bool:
    return headcount_match_basis(reference, company) != "mismatch"


def reference_metrics(case: CompanyCase, result: ProviderResult) -> list[dict[str, Any]]:
    """Score a provider result only against reference fields available per case."""
    company = result.normalized
    status_detail = {
        "reference_status": case.source_metadata.get("reference_status")
        or "unknown_reference"
    }
    metrics: list[dict[str, Any]] = [
        {
            "metric_name": "resolved",
            "metric_value": 1 if company else 0,
            "detail": status_detail,
        }
    ]
    present = {
        attribute: bool(company and _present(company, attribute))
        for attribute in SCORED_ATTRIBUTES
    }
    for attribute, value in present.items():
        metrics.append(
            {
                "metric_name": f"coverage_{attribute}",
                "metric_value": 1 if value else 0,
                "detail": status_detail,
            }
        )
    metrics.append(
        {
            "metric_name": "attribute_coverage_pct",
            "metric_value": round(100 * sum(present.values()) / len(SCORED_ATTRIBUTES), 2),
            "detail": {**status_detail, "attribute_count": len(SCORED_ATTRIBUTES)},
        }
    )

    reference = case.reference
    evaluable = [
        attribute
        for attribute in SCORED_ATTRIBUTES
        if _reference_available(reference, attribute)
        # An unreachable input has no authoritative current primary domain.
        # It remains in the cohort and audit trail, but is excluded from the
        # primary-domain accuracy denominator until a verified successor exists.
        and not (
            attribute == "primary_domain" and inactive_domain_reason(case.input_domain)
        )
    ]
    if not evaluable:
        return metrics

    comparisons: dict[str, bool] = {}
    if company:
        if "legal_name" in evaluable:
            comparisons["legal_name"] = bool(
                company.legal_name
                and normalize_text(reference.get("legal_name")) == normalize_text(company.legal_name)
            )
        if "primary_domain" in evaluable:
            comparisons["primary_domain"] = (
                primary_domain_match_basis(reference, company) != "mismatch"
            )
        if "hq_location" in evaluable:
            country_matches = (
                normalize_country(reference.get("hq_country")) == normalize_country(company.hq_country)
                if reference.get("hq_country")
                else True
            )
            city_matches = (
                normalize_text(reference.get("hq_city")) == normalize_text(company.hq_city)
                if reference.get("hq_city")
                else True
            )
            comparisons["hq_location"] = bool(
                present["hq_location"] and country_matches and city_matches
            )
        if "founded_year" in evaluable:
            comparisons["founded_year"] = bool(
                company.founded_year is not None
                and reference.get("founded_year") == company.founded_year
            )
        if "industry" in evaluable:
            comparisons["industry"] = bool(
                _industry_agrees(
                    reference.get("industries")
                    or ([reference["industry"]] if reference.get("industry") else []),
                    company.industries or ([company.industry] if company.industry else []),
                )
            )
        if "linkedin_url" in evaluable:
            comparisons["linkedin_url"] = linkedin_match_basis(reference, company) != "mismatch"
        if "headcount_band" in evaluable:
            comparisons["headcount_band"] = _headcount_matches(reference, company)
        if "funding_stage" in evaluable:
            comparisons["funding_stage"] = bool(
                company.funding_stage
                and normalize_funding_stage(reference.get("funding_stage")) == company.funding_stage
            )
    for attribute in evaluable:
        correct = comparisons.get(attribute, False)
        metrics.append(
            {
                "metric_name": f"reference_correct_{attribute}",
                "metric_value": 1 if correct else 0,
                "detail": status_detail,
            }
        )

    returned = [attribute for attribute in evaluable if present[attribute]]
    correct_count = sum(1 for attribute in evaluable if comparisons.get(attribute, False))
    if returned:
        metrics.append(
            {
                "metric_name": "reference_accuracy_when_present_pct",
                "metric_value": round(100 * correct_count / len(returned), 2),
                "detail": {
                    **status_detail,
                    "truth_fields_evaluable": len(evaluable),
                    "truth_fields_returned": len(returned),
                },
            }
        )
    metrics.append(
        {
            "metric_name": "correct_field_yield_pct",
            "metric_value": round(100 * correct_count / len(evaluable), 2),
            "detail": {
                **status_detail,
                "truth_fields_evaluable": len(evaluable),
                "truth_fields_returned": len(returned),
            },
        }
    )
    return metrics


def benchmark_metrics(case: CompanyCase, result: ProviderResult) -> list[dict[str, Any]]:
    if case.source_metadata.get("reference_status") in {
        "clay_reference_v1",
        LINKEDIN_REFERENCE_STATUS,
    }:
        return reference_metrics(case, result)
    return provisional_metrics(case, result)


def provisional_metrics(case: CompanyCase, result: ProviderResult) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = [
        {"metric_name": "resolved", "metric_value": 1 if result.normalized else 0, "detail": {}}
    ]
    company = result.normalized
    if not company:
        for attribute in SCORED_ATTRIBUTES:
            metrics.append(
                {
                    "metric_name": f"coverage_{attribute}",
                    "metric_value": 0,
                    "detail": {"reference_status": "provisional_candidate_hints"},
                }
            )
        metrics.append(
            {
                "metric_name": "attribute_coverage_pct",
                "metric_value": 0,
                "detail": {"attribute_count": len(SCORED_ATTRIBUTES)},
            }
        )
        return metrics

    present = {attribute: _present(company, attribute) for attribute in SCORED_ATTRIBUTES}
    for attribute, value in present.items():
        metrics.append(
            {
                "metric_name": f"coverage_{attribute}",
                "metric_value": 1 if value else 0,
                "detail": {"reference_status": "provisional_candidate_hints"},
            }
        )
    metrics.append(
        {
            "metric_name": "attribute_coverage_pct",
            "metric_value": round(100 * sum(present.values()) / len(SCORED_ATTRIBUTES), 2),
            "detail": {"attribute_count": len(SCORED_ATTRIBUTES)},
        }
    )

    ref = case.reference
    comparisons: dict[str, bool | None] = {
        "legal_name": (
            normalize_text(ref.get("legal_name")) == normalize_text(company.legal_name)
            if ref.get("legal_name") and company.legal_name
            else None
        ),
        "primary_domain": (
            normalize_domain(ref.get("primary_domain")) in company.domains
            if ref.get("primary_domain") and company.domains
            else None
        ),
        "hq_country": (
            normalize_country(ref.get("hq_country")) == normalize_country(company.hq_country)
            if ref.get("hq_country") and company.hq_country
            else None
        ),
        "hq_city": (
            normalize_text(ref.get("hq_city")) == normalize_text(company.hq_city)
            if ref.get("hq_city") and company.hq_city
            else None
        ),
        "founded_year": (
            int(ref["founded_year"]) == company.founded_year
            if ref.get("founded_year") and company.founded_year
            else None
        ),
        "industry": _industry_agrees(
            ref.get("industries") or ([ref["industry"]] if ref.get("industry") else []),
            company.industries or ([company.industry] if company.industry else []),
        ),
        "linkedin_url": (
            normalize_linkedin(ref.get("linkedin_url")) == company.linkedin_url
            if ref.get("linkedin_url") and company.linkedin_url
            else None
        ),
        "headcount_band": _range_overlap(
            ref.get("headcount_min"), ref.get("headcount_max"),
            company.headcount_min, company.headcount_max,
        ),
        "funding_stage": (
            normalize_funding_stage(ref.get("funding_stage")) == company.funding_stage
            if ref.get("funding_stage") and company.funding_stage
            else None
        ),
    }
    available = [value for value in comparisons.values() if value is not None]
    for attribute, agrees in comparisons.items():
        if agrees is not None:
            metrics.append(
                {
                    "metric_name": f"provisional_agreement_{attribute}",
                    "metric_value": 1 if agrees else 0,
                    "detail": {"reference_status": "provisional_candidate_hints"},
                }
            )
    if available:
        metrics.append(
            {
                "metric_name": "provisional_agreement_pct",
                "metric_value": round(100 * sum(available) / len(available), 2),
                "detail": {
                    "comparisons_available": len(available),
                    "reference_status": "provisional_candidate_hints",
                    "not_accuracy": True,
                },
            }
        )
    return metrics


def metric_value(result: dict[str, Any], name: str) -> float | None:
    for metric in result.get("metrics") or []:
        if metric.get("metric_name") == name:
            value = metric.get("metric_value")
            return float(value) if value is not None else None
    return None


def _sql_round(value: float, places: int) -> float:
    """Match PostgreSQL numeric round (half away from zero)."""
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def recompute_leaderboard(runs: list[dict[str, Any]], case_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for provider_slug in sorted({run["provider_slug"] for run in runs}):
        cells = [run for run in runs if run["provider_slug"] == provider_slug]
        coverage = [v for run in cells if (v := metric_value(run, "attribute_coverage_pct")) is not None]
        agreement = [v for run in cells if (v := metric_value(run, "provisional_agreement_pct")) is not None]
        reference_accuracy = [
            v
            for run in cells
            if (v := metric_value(run, "reference_accuracy_when_present_pct")) is not None
        ]
        correct_yield = [
            v for run in cells if (v := metric_value(run, "correct_field_yield_pct")) is not None
        ]
        latencies = [int(run["latency_ms"]) for run in cells if run.get("latency_ms") is not None]
        costs: dict[str, float] = {}
        for run in cells:
            if run.get("cost_unit") and run.get("cost_units") is not None:
                unit = run["cost_unit"]
                costs[unit] = round(costs.get(unit, 0) + float(run["cost_units"]), 4)
        rows.append(
            {
                "provider_slug": provider_slug,
                "provider_name": cells[0]["provider_name"],
                "cases_attempted": len(cells),
                "total_cases": case_count,
                "cases_resolved": sum(1 for run in cells if metric_value(run, "resolved") == 1),
                "resolution_rate_pct": _sql_round(
                    100 * sum(1 for run in cells if metric_value(run, "resolved") == 1) / case_count, 2
                ) if case_count else None,
                "avg_attribute_coverage_pct": _sql_round(sum(coverage) / len(coverage), 2) if coverage else None,
                "avg_provisional_agreement_pct": _sql_round(sum(agreement) / len(agreement), 2) if agreement else None,
                "avg_reference_accuracy_when_present_pct": (
                    _sql_round(sum(reference_accuracy) / len(reference_accuracy), 2)
                    if reference_accuracy
                    else None
                ),
                "avg_correct_field_yield_pct": (
                    _sql_round(sum(correct_yield) / len(correct_yield), 2) if correct_yield else None
                ),
                "median_latency_ms": _sql_round(median(latencies), 1) if latencies else None,
                "usage_by_unit": costs,
            }
        )
    rows.sort(
        key=lambda row: (
            -(
                row["avg_correct_field_yield_pct"]
                if row["avg_correct_field_yield_pct"] is not None
                else row["avg_attribute_coverage_pct"] or -1
            ),
            -(row["resolution_rate_pct"] or -1),
            -(row["avg_attribute_coverage_pct"] or -1),
            row["provider_slug"],
        )
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


def write_snapshot(snapshot: dict[str, Any]) -> None:
    temporary = SNAPSHOT_PATH.with_suffix(SNAPSHOT_PATH.suffix + ".tmp")
    temporary.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(SNAPSHOT_PATH)


def read_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
