# data

`sample.json` holds the 10 public companies from [`openbenchmarks/OB-Company-Enrichment-Firmographics`](https://huggingface.co/datasets/openbenchmarks/OB-Company-Enrichment-Firmographics). Pass another file with `--cases`.

The file is `{"cases": [...]}`. Each case has the fields of `CompanyCase` in `harness/common.py`:

| Field | Meaning |
|---|---|
| `case_slug`, `company_id` | Stable id of the company. |
| `slice` | Sampling slice. |
| `input_name`, `input_domain` | What the providers get. |
| `reference` | The answer key: `hq_country`, `hq_city`, `founded_year`, `industry`, `industries`, `linkedin_url`, `linkedin_url_alternates`, `headcount_min`, `headcount_max`, `headcount_exact`. A missing value is `null` and the field is not scored for that company. |
| `source_metadata.reference_status` | `linkedin_live_ground_truth_v1`: score against `reference`. |

`identity-redirects.json` lists audited domain and LinkedIn redirects and inactive input domains. It is empty for the public companies.
