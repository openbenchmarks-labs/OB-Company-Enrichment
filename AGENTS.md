# AGENTS.md — OB-Company-Enrichment-Firmographics

Quick navigation guide for coding agents working in `OB-Company-Enrichment-Firmographics`.

## Structure (Zero Overlap)

| Folder | Owns | Files |
|---|---|---|
| **`data/`** | Dataset schema & public sample rows | `data/README.md`, `data/sample.jsonl` |
| **`harness/`** | Execution only: CLI runner, tool adapters, registry, HTTP client, extraction model | `harness/run.py`, `harness/runner.py`, `harness/registry.py`, `harness/adapters/<tool>.py`, `harness/company.py`, `harness/web_research.py`, `harness/http_client.py`, `harness/llm.py`, `harness/config.yaml` |
| **`tools/`** | Tool setups, request settings & prices (one file per tool for clean PRs) | `tools/<tool>.yaml` (10 tools, 13 setups) |
| **`judge/`** | Deterministic grader, rules, board math, place/metro tables | `judge/grader.py`, `judge/config.yaml`, `judge/metros.json`, `judge/places.json` |

Benchmark order & win condition live at the root in `benchmark.yaml`.

## Commands

```sh
uv venv && uv pip install -r requirements.txt
.venv/bin/python -B -m harness.run selftest --tasks data/sample.jsonl
```

## Guidelines

- **Keep folders strictly separated:** Dataset info in `data/`; execution in `harness/`; per-tool configs in `tools/<tool>.yaml`; grading in `judge/`.
- **Adding or tuning a tool:** Create or edit `tools/<tool>.yaml`. A new tool also needs `harness/adapters/<tool>.py`, and its API key in `.env.example`. Then run `selftest`.
- **Paid runs need `--execute`:** `harness.run run` only prints the plan without it.
- **No secrets or raw run logs in git:** Keep API keys in `.env` and run outputs in `runs/` (both gitignored).
- **Consistent terminology:** Use `tool`, `setup`, `question`, `ground truth`, `board`, `main score`.
