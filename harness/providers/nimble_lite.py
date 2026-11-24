"""Nimble Lite company enrichment arm."""
from harness.common import CompanyCase, ProviderResult
from .nimble_search import PAID_CALLS_PER_CASE, REQUIRED_ENV, run_depth

VENDOR_SLUG = "nimble-lite"
VENDOR_NAME = "Nimble lite"


def run(case: CompanyCase) -> ProviderResult:
    return run_depth(case, "lite")
