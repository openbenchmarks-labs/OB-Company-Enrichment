# OB-Company-Enrichment

Open, independent benchmark of company enrichment APIs. Each provider gets a company and returns its firmographics. The board scores five fields against a reviewed answer key: headquarters, founded year, industry, LinkedIn URL and headcount band.

**Live leaderboard:** [openbenchmarks.com/company-enrichment](https://openbenchmarks.com/company-enrichment) · **Public dataset:** [`openbenchmarks/OB-Company-Enrichment-Firmographics`](https://huggingface.co/datasets/openbenchmarks/OB-Company-Enrichment-Firmographics)

Published and maintained by **[OpenBenchmarks Labs](https://openbenchmarks.com)**.

## How it works

1. **Run.** Each provider is called once per company and its answer is normalized to one company record (`harness/common.py`, `NormalizedCompany`). Every response is saved per provider and company under `runs/`.
2. **Judge.** One model call per provider and field compares the provider's values with the answer key (`judge/judge.py`, prompts in `judge/field_judge_prompts.py`). Headquarters, founded year, industry and LinkedIn URL use GPT-5.6 Terra at medium effort. Headcount uses GPT-5.6 Sol with the numeric headcount prompt. A field with no answer-key value is left out.
3. **Board.** For each company, correct fields over fields with an answer-key value; the board averages that over companies and ranks by it (`recompute_leaderboard` in `harness/common.py`).

## Providers

| Kind | Providers | Code |
|---|---|---|
| Data APIs | `apollo`, `company-enrich`, `explorium`, `fiber`, `ocean`, `people-data-labs`, `predictleads` | `harness/run_data_apis.py` |
| Research APIs | `parallel-research`, `exa-research`, `exa-research-v2` | `harness/run_research.py`, `harness/web_research.py` |
| Answer, search and agent APIs | `seltz-companies`, `seltz-news`, `nimble-lite`, `nimble-standard`, `nimble-agents`, `company-enrich-v2` | `harness/run_extension.py` |
| Batch CLI | `zoominfo` | `harness/run_zoominfo.py` |

One adapter per provider lives in `harness/providers/`.

## Dataset

- `data/sample.json`: the 10 public companies from Hugging Face, in the cases format the runners and the judge read.
- `data/identity-redirects.json`: audited domain and LinkedIn redirects. Empty for the public companies.
- The companies behind the published board stay private, so providers cannot train on them.

See [`data/README.md`](data/README.md) for the format.

## Run it

```sh
uv venv && uv pip install -r requirements.txt
cp .env.example .env   # add the keys for the providers you run
```

Calls below are billed by each provider. `--dry-run` shows the plan and calls nothing.

```sh
.venv/bin/python -m harness.run_data_apis --only apollo --dry-run
.venv/bin/python -m harness.run_data_apis --only apollo --confirm-paid
.venv/bin/python -m harness.run_research --only parallel-research --confirm-paid
.venv/bin/python -m harness.run_extension --only seltz-companies --confirm-paid
.venv/bin/python -m harness.run_zoominfo --run      # needs the ZoomInfo gtm CLI
.venv/bin/python -m judge.judge --only apollo,parallel-research --run --confirm-paid --build   # needs OPENAI_API_KEY
```

## Related benchmarks

- [OB-Company-Enrichment-Funding](https://github.com/openbenchmarks-labs/OB-Company-Enrichment-Funding): latest funding round
- [OB-Company-Search-Lookalikes](https://github.com/openbenchmarks-labs/OB-Company-Search-Lookalikes): company lookalike discovery
- [OB-Company-Search-Multi-Turn](https://github.com/openbenchmarks-labs/OB-Company-Search-Multi-Turn): multi-hop company research
- [All OpenBenchmarks leaderboards](https://openbenchmarks.com)

## License

MIT. See [LICENSE](LICENSE).
