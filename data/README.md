# data

This folder holds a 4-company public sample (`sample.jsonl`) for offline verification (`selftest`) and schema reference.

## Public vs. held-out evaluation sets

- **Public split:** 10 companies published on Hugging Face at [`openbenchmarks/OB-Company-Enrichment`](https://huggingface.co/datasets/openbenchmarks/OB-Company-Enrichment).
- **Public sample (`sample.jsonl`):** 4 well-known companies (`stripe.com`, `figma.com`, `vercel.com`, `linear.app`) used to verify the CLI and grader without API keys.
- **Held-out split (private):** The evaluation cohort used for the published leaderboard stays private so providers cannot train on it. Pass your own JSONL file with `--tasks <path>`.

## Files and schema

- `sample.jsonl` — One JSON object per line.

| Field | Type | Description |
|---|---|---|
| `domain` | string | Canonical company domain (for example, `stripe.com`). |
| `company` | string | Company name. |
| `hq` | string | Headquarters city and country (for example, `San Francisco, United States`). |
| `industry` | string | Ground-truth industry label. |
| `employee_band` | string | Standardized employee count band (for example, `501-1000`). |
| `founded_year` | integer | Year the company was founded. |
| `linkedin_url` | string | Canonical company LinkedIn URL. |
