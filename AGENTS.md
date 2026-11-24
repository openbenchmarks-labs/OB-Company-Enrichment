# AGENTS.md: OB-Company-Enrichment

| Folder | Owns |
|---|---|
| `data/` | The public sample in the cases format (`sample.json`) and the redirect table. |
| `harness/` | Provider adapters (`providers/`), the shared company contract and scoring helpers (`common.py`), and the runners. Saved cells go to `runs/`. |
| `judge/` | The field judge prompts and the judge that builds the board. |

## Commands

```sh
uv venv && uv pip install -r requirements.txt
.venv/bin/python -m harness.run_extension --only seltz-companies --dry-run
```

## Rules

- This code is the benchmark the board runs. A change to a judge prompt, a provider request or the normalization changes published scores: it goes in its own pull request with the reason.
- Keep API keys in `.env` and run outputs in `runs/`. Both are gitignored.
