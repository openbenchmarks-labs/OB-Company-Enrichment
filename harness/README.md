# harness

Runs the benchmark and calls each tool. No grading logic lives here.

- `run.py` — Command-line runner: `selftest`, `run`, `grade` and `list-tools`.
- `runner.py` — Asks each setup about every company, saves the answers, and skips companies a setup already answered, so a stopped run can resume.
- `registry.py` — Reads `tools/<tool>.yaml` and builds each setup's adapter from `adapters/<tool>.py`.
- `tool_base.py` — The base class every adapter extends.
- `adapters/` — The request and response code for each tool, one file per tool.
- `models.py` — The shapes of a company and of one setup's answer.
- `company.py` — The company record every adapter returns, and how it turns into the graded fields.
- `web_research.py` — The question, output schema and response parsing that Exa, Parallel, Seltz and Nimble share, and the model step that reads search results for Nimble's and Seltz's search setups.
- `vendor_values.py` — Helpers for reading values out of responses whose field names vary.
- `http_client.py` — Sends HTTP requests with retries and timing, and removes API keys before anything is saved.
- `llm.py` — Structured answers from an OpenAI model, on Azure OpenAI or OpenAI: the Nimble search setups' extraction and the model judge.
- `config.yaml` — Fields being measured (`hq`, `industry`, `employee_band`, `founded_year`, `linkedin_url`) and board groups.

## Tasks

`--tasks` takes a Hugging Face dataset file (`input_domain`, `input_name`, `hq_city`, `hq_country`, `headcount_min`, `headcount_max`, `headcount_exact`, `linkedin_url_alternates`, ...) or flat rows like `data/sample.jsonl`. `runner.truth_of` turns a dataset row into the fields the grader reads.

## What a run writes

```text
runs/<run>/
├── <setup>.jsonl           one answer per company, in the format `grade` reads
├── raw/<setup>/<domain>.json   the tool's response, with API keys removed
└── graded.json             the verdicts, written by `grade`
```

`python -m harness.run run --tasks <file>` prints the plan and stops. Add `--execute` to call the tools, which costs money. Pass the same `--out` folder again to resume a run.

## What `grade` writes

`python -m harness.run grade --tasks <file> --run runs/<run>` grades every `<setup>.jsonl` in the folder (or `--setups`) and writes `graded.json`:

- `cases`: one per company, with its `case_slug` (the dataset's `case_id`, else the domain), name, domain, slice and ground truth (`reference`).
- `runs`: one per setup and company, with the answer (`normalized`), status, latency, cost, the request settings and the raw response, and `metrics`:
  - `resolved`: 1 when the setup found the company.
  - `coverage_<field>` and `attribute_coverage_pct`: which of the five fields came back, and their share.
  - `reference_correct_<field>`: 1 or 0 for each field the ground truth has, with `judge_method` (`code` or `model`) and the model judge's `rationale`.
  - `reference_accuracy_when_present_pct` and `correct_field_yield_pct`: correct fields over fields returned, and over fields in the ground truth.

Field names in `graded.json` are `hq_location`, `industry`, `headcount_band`, `founded_year` and `linkedin_url`. A company with no answer in a setup's file, or only an error, is graded wrong on every field it has ground truth for, as on the board; `grade` prints how many, and `run` asks again for them.
