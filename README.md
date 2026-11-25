# OB-Company-Enrichment (Firmographics)

Open, independent benchmark of company enrichment APIs on 282 human-reviewed companies. Every tool receives the same company domain and returns five firmographic fields: headquarters, industry, employee band, founding year and LinkedIn URL. Each field is graded against verified ground truth. Tools measured: Apollo, People Data Labs, ZoomInfo, Explorium, CompanyEnrich, PredictLeads, Nimble, Parallel, Exa and Seltz.

**Live leaderboard:** [openbenchmarks.com/company-enrichment](https://openbenchmarks.com/company-enrichment) · **Public dataset:** [`openbenchmarks/OB-Company-Enrichment-Firmographics`](https://huggingface.co/datasets/openbenchmarks/OB-Company-Enrichment-Firmographics)

Published and maintained by **[OpenBenchmarks Labs](https://openbenchmarks.com)**.

## Methodology

Every tool receives the same company domains and is evaluated on five structured fields:

- `hq`: Headquarters city and country (normalized via `judge/places.json` and US metro-area mappings in `judge/metros.json`; city names come from [GeoNames](https://www.geonames.org) under CC BY 4.0, metros from US Census Bureau data). A city those tables cannot place, such as a district outside the US, goes to the model judge in `judge/config.yaml`.
- `industry`: Right when an answered industry is the key's own words. Otherwise a model judge (`gpt-5.6-terra`, question in `judge/config.yaml`) accepts the same industry, a synonym, a broader or narrower one, or an adjacent label for the same main business. Needs `OPENAI_API_KEY`.
- `employee_band`: Headcount range: the same band, an exact count inside it, or an open band such as `10001+` holding any larger count.
- `founded_year`: Founding year.
- `linkedin_url`: The company's LinkedIn page, matched by its page name (the part after `/company/`).

`judge/grader.py` grades each field against ground truth: code decides the founding year, the LinkedIn page, the employee band and most headquarters, and the model judge decides the industry when the words differ and the headquarters the tables cannot place. It computes per-field accuracy and 95% confidence intervals across all evaluated companies.

## Tools evaluated

11 tools configured in [`tools/`](tools/README.md):

`apollo`, `company-enrich`, `coresignal`, `exa`, `explorium`, `nimble`, `parallel`, `people-data-labs`, `predictleads`, `seltz`, `zoominfo`

## Dataset

- **Public split:** 10 companies published on Hugging Face at [`openbenchmarks/OB-Company-Enrichment-Firmographics`](https://huggingface.co/datasets/openbenchmarks/OB-Company-Enrichment-Firmographics).
- **Public sample:** 4 example companies are committed in [`data/sample.jsonl`](data/sample.jsonl) for offline verification (`selftest`) and schema reference.
- **Held-out split:** The full evaluation cohort used for the published leaderboard (spanning stable-large companies, long-tail companies, subsidiaries, and verified rebrands) is kept private so vendors cannot train on it.

See [`data/README.md`](data/README.md) for the file schema.

## Repository structure

- [`data/`](data/README.md): Input schema documentation and 4-row public sample (`sample.jsonl`) for offline checks.
- [`harness/`](harness/README.md): CLI runner (`selftest`, `run`, `grade`, `list-tools`) and one adapter per tool.
- [`tools/`](tools/README.md): One `<tool>.yaml` config per enrichment provider (11 tools).
- [`judge/`](judge/README.md): Field grader (code rules, plus a model judge for industry and some headquarters), place/metro normalization tables, and confidence interval math.
- [`benchmark.yaml`](benchmark.yaml): Benchmark contract, scope, and refresh cadence.
- [`AGENTS.md`](AGENTS.md): Entry guide for coding agents.

## Quick start

Run the offline check (no API keys required):

```sh
uv venv && uv pip install -r requirements.txt
.venv/bin/python -B -m harness.run selftest --tasks data/sample.jsonl
```

Run the tools on your own companies file (one JSON object per line with `domain` and `company`). Without `--execute` this only prints the plan; with it, it calls the tools and costs money:

```sh
cp .env.example .env   # then add the keys for the tools you run
.venv/bin/python -B -m harness.run run --tasks data/sample.jsonl --setups apollo,parallel-research
.venv/bin/python -B -m harness.run run --tasks data/sample.jsonl --setups apollo,parallel-research --out runs/my-run --execute
```

Each setup's answers land in `runs/my-run/<setup>.jsonl`.

Grade a local predictions file against ground truth:

```sh
.venv/bin/python -B -m harness.run grade --tasks data/sample.jsonl --predictions runs/preds.jsonl
```

List configured tools:

```sh
.venv/bin/python -B -m harness.run list-tools
```

## Adding or updating a tool

1. Add or edit `tools/<tool>.yaml`. A new tool also needs `harness/adapters/<tool>.py` and its API key in `.env.example`.
2. Run the offline `selftest` command above.
3. Open a pull request.

## Links

- [Live leaderboard](https://openbenchmarks.com/company-enrichment)
- [JSON API](https://openbenchmarks.com/api/benchmarks/company-enrichment)
- [Hugging Face dataset](https://huggingface.co/datasets/openbenchmarks/OB-Company-Enrichment-Firmographics)
- [`llms.txt`](https://openbenchmarks.com/llms.txt)

## Related benchmarks

- [OB-Company-Enrichment-Funding](https://github.com/openbenchmarks-labs/OB-Company-Enrichment-Funding): Latest funding round accuracy and freshness ([leaderboard](https://openbenchmarks.com/company-funding))
- [OB-Company-Search-Lookalikes](https://github.com/openbenchmarks-labs/OB-Company-Search-Lookalikes): Company lookalike discovery ([leaderboard](https://openbenchmarks.com/lookalikes))
- [OB-Company-Search-Multi-Turn](https://github.com/openbenchmarks-labs/OB-Company-Search-Multi-Turn): Multi-hop company research agents ([leaderboard](https://openbenchmarks.com/multi-turn-company-search))
- [All OpenBenchmarks leaderboards](https://openbenchmarks.com)

## License

MIT. See [LICENSE](LICENSE).
