"""Offline tests for the SpecStream Web Search adapter. No request leaves the machine."""
from __future__ import annotations

import unittest
from unittest import mock

import requests

from harness import http_client, registry
from harness.adapters import specstream
from harness.models import Company

# Trimmed from a real compact reply of POST /v1/web-search (2026-11-26).
REPLY = {"data": {"request_id": "eefbc888ed314636aa7bcd32e8d8f91e",
                  "took_ms": 2937,
                  "price_usd": 0.005,
                  "sources": [{"title": "Salesforce Products List 2026: Clouds, Agentforce, and Acquisitions",
                               "url": "https://martech.zone/salesforce-product-landscape/",
                               "published": "2026-11-03",
                               "snippet": "Informatica: Data integration, quality, governance, catalog, and master data "
                                          "management ( MDM ), acquired for "},
                              {"title": "Salesforce Agentforce ARR: Separating Bought From Built",
                               "url": "https://zandigital.in/salesforce-agentforce-arr-informatica-organic",
                               "published": "2026-10-15",
                               "snippet": "How much did Salesforce pay for Informatica?\n"
                                          "Salesforce agreed in May 2025 to pay $25 per share in cash, an eq"},
                              {"title": "Why Salesforce keeps buying AI companies (2026) | Arcsen",
                               "url": "https://arcsen.com/salesforce-ai-acquisitions-2026/",
                               "published": "2026-11-03",
                               "modified": "2026-11-14",
                               "snippet": "Understanding the customer is Listen Labs for research and simulation, sitting on "
                                          "Informatica (about $8 billio"},
                              {"title": "Explaining martech",
                               "url": "https://ppc.land/martech/",
                               "published": "2026-09-25",
                               "snippet": "Salesforce agreed to buy Informatica for $8 billion on May 27, 2025 and completed "
                                          "the deal on November 18, 202"}]}}
URLS = [source["url"] for source in REPLY["data"]["sources"]]
EXTRACTED = {"legal_name": "Salesforce, Inc.", "primary_domain": "salesforce.com", "hq_country": "United States",
             "hq_city": "San Francisco", "founded_year": 1999, "industry": "Software", "industries": ["Software"],
             "linkedin_url": None, "headcount_min": None, "headcount_max": None, "sources": [URLS[0]]}


def response(body):
    return http_client.Response(status=200, body=body, latency_ms=5, attempts=1, headers={})


class SpecStreamTest(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.dict("os.environ", {"SPECSTREAM_API_KEY": "test-key"}),
                        mock.patch.object(requests, "request", side_effect=AssertionError("network call"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.adapter = registry.build("specstream-search-web")
        self.company = Company(domain="salesforce.com", name="Salesforce")

    def lookup(self, body):
        with mock.patch.object(specstream.http_client, "request_json", return_value=response(body)) as post, \
                mock.patch.object(specstream, "extract_from_results", return_value=(dict(EXTRACTED), {"model": "m"})) as extract:
            return self.adapter.lookup(self.company), post, extract

    def test_registered_as_main_setup(self):
        self.assertIsInstance(self.adapter, specstream.SpecStreamSearch)
        self.assertIn("specstream-search-web", registry.main_setups())
        self.assertEqual(self.adapter.env, ["SPECSTREAM_API_KEY", "OPENAI_API_KEY"])

    def test_request(self):
        answer, post, _ = self.lookup(REPLY)
        (method, url), kwargs = post.call_args
        self.assertEqual((method, url), ("POST", "https://api.specstream.ai/v1/web-search"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(set(kwargs["json"]), {"q", "num", "compact"})
        self.assertEqual((kwargs["json"]["num"], kwargs["json"]["compact"]), (10, True))
        self.assertIn('"Salesforce" (salesforce.com)', kwargs["json"]["q"])
        self.assertEqual(answer.raw["request"], kwargs["json"])

    def test_sources_go_to_the_shared_extractor(self):
        answer, _, extract = self.lookup(REPLY)
        name, domain, hits, extractor = extract.call_args.args
        self.assertEqual((name, domain), ("Salesforce", "salesforce.com"))
        self.assertEqual([hit["url"] for hit in hits], URLS)
        self.assertEqual(set(hits[0]), {"url", "title", "snippet"})
        self.assertEqual(hits[0]["snippet"], REPLY["data"]["sources"][0]["snippet"])
        self.assertEqual(extractor["model"], "gpt-5.6-terra")
        self.assertEqual(answer.status, "ok")
        self.assertEqual(answer.fields["sources"], [URLS[0]])
        self.assertEqual((answer.cost_units, answer.cost_unit), (1, "specstream_search_request"))

    def test_hits_are_unique_and_capped(self):
        sources = REPLY["data"]["sources"]
        hits = specstream.search_hits([sources[0], sources[0], {"title": "no url"}, *sources[1:]], 2)
        self.assertEqual([hit["url"] for hit in hits], URLS[:2])

    def test_no_sources_is_not_found_and_not_billed(self):
        answer, _, extract = self.lookup({"data": {"request_id": "r", "took_ms": 1, "price_usd": 0, "sources": []}})
        extract.assert_not_called()
        self.assertEqual((answer.status, answer.cost_units), ("not_found", 0))

    def test_unwrapped_reply_is_an_error(self):
        answer, _, extract = self.lookup({"sources": REPLY["data"]["sources"]})
        extract.assert_not_called()
        self.assertEqual(answer.status, "error")
        self.assertIn("data.sources", answer.error)


if __name__ == "__main__":
    unittest.main()
