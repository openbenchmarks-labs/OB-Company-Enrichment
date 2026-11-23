# Status

Owner: Aktan

State: runs through obkit 0.5.1. The public set is `data/sample.jsonl` (4 companies). Each company is scored per field (hq, industry, employee_band, founded_year, linkedin_url); the board unit is fields. Paid runs are off: `budget_usd` is 0 and no setup has `pricing.answer_usd` yet.

Next step: name the validation and private sets with `obkit add-set`, once the held-out set for v2 is chosen.

Open decisions:
- Raise `budget_usd` above 0 and source `answer_usd` (Fenil).
- The public set has 4 companies. The obkit contract asks for at least 10. Adding questions changes the public set, so it needs a decision.
- Board unit: fields. Five per company, 20 graded fields in the public set. The README already describes per-field grading; it is not changed here.
- A field whose ground truth cannot be scored returns None in `judge/obkit.py` and is left out of the board.
- Industry is graded by LinkedIn's hierarchy in code. On the 5,288 saved answers it agrees with the old model judge on 88.0%; the rest are mostly labels from other taxonomies ("Business Services") that the model judge accepted. Outside the US, a suburb named instead of the key's city is wrong: there is no metro table outside the US.
- Cost of failed calls: a vendor error row records 0 cost. Spend on those calls is not counted.
