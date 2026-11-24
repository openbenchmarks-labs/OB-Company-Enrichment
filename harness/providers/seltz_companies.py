"""Seltz Answer company-scope firmographic adapter."""
from __future__ import annotations

from harness.common import CompanyCase, ProviderResult

from .seltz_answer import PAID_CALLS_PER_CASE, REQUIRED_ENV, run_scope


VENDOR_SLUG = "seltz-companies"
VENDOR_NAME = "Seltz — Companies"
SCOPE = "companies"


def run(case: CompanyCase) -> ProviderResult:
    return run_scope(
        case,
        scope=SCOPE,
        provider_slug=VENDOR_SLUG,
        provider_name=VENDOR_NAME,
    )
