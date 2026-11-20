# harness

Runs the benchmark and calls each tool. No grading logic lives here.

- `run.py` — Command-line runner: `selftest`, `run`, `grade` and `list-tools`.
- `runner.py` — Asks each setup about every company, saves the answers, and skips companies a setup already answered, so a stopped run can resume.
- `registry.py` — Reads `tools/<tool>.yaml` and builds each setup's adapter from `adapters/<tool>.py`.
- `tool_base.py` — The base class every adapter extends.
- `adapters/` — The request and response code for each tool, one file per tool.
- `models.py` — The shapes of a company and of one setup's answer.
- `company.py` — The company record every adapter returns, and how it turns into the graded fields.
- `web_research.py` — The question, output schema and response parsing that Exa, Parallel, Seltz and Nimble share.
- `vendor_values.py` — Helpers for reading values out of responses whose field names vary.
- `http_client.py` — Sends HTTP requests with retries and timing, and removes API keys before anything is saved.
- `llm.py` — Reads search results into the output schema with an OpenAI model, for the Nimble search setups.
- `config.yaml` — Fields being measured (`hq`, `industry`, `employee_band`, `founded_year`, `linkedin_url`) and board groups.

## What a run writes

```text
runs/<run>/
├── <setup>.jsonl           one answer per company, in the format `grade` reads
└── raw/<setup>/<domain>.json   the tool's response, with API keys removed
```

`python -m harness.run run --tasks <file>` prints the plan and stops. Add `--execute` to call the tools, which costs money. Pass the same `--out` folder again to resume a run.
