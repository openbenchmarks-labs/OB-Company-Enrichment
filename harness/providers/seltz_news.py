"""Seltz Answer news-scope firmographic adapter."""
from harness.common import CompanyCase, ProviderResult
from .seltz_answer import PAID_CALLS_PER_CASE, REQUIRED_ENV, run_scope

VENDOR_SLUG = "seltz-news"
VENDOR_NAME = "Seltz — News"
SCOPE = "news"


def run(case: CompanyCase) -> ProviderResult:
    return run_scope(case, scope=SCOPE, provider_slug=VENDOR_SLUG, provider_name=VENDOR_NAME)
