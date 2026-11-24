"""CompanyEnrich arm on a second API key.

Same request and normalization as the original `company-enrich` arm; only the
account behind COMPANY_ENRICH_API_KEY differs, so the two arms isolate the
effect of the key. Results are stored under their own provider slug so the
original arm is never overwritten.
"""
from __future__ import annotations

import dataclasses

from harness.common import CompanyCase, ProviderResult

from . import company_enrich

VENDOR_SLUG = "company-enrich-v2"
VENDOR_NAME = "CompanyEnrich (new key)"
REQUIRED_ENV = company_enrich.REQUIRED_ENV
PAID_CALLS_PER_CASE = company_enrich.PAID_CALLS_PER_CASE


def run(case: CompanyCase) -> ProviderResult:
    result = company_enrich.run(case)
    return dataclasses.replace(result, provider_slug=VENDOR_SLUG, provider_name=VENDOR_NAME)
