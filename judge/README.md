# judge

Grades predicted company fields against ground truth.

- `config.yaml` — Matching rules for each field and leaderboard scoring.
- `grader.py` — Decides pass or fail for each field and computes 95% confidence intervals.
- `places.json` — ISO country names and codes, and city names with their alternate spellings (every city above 15,000 people in [GeoNames](https://www.geonames.org), CC BY 4.0), so spelling variants of one city match.
- `metros.json` — US metro-area lookup built from US Census Bureau data (CBSA delineation, places by county, ACS population), so suburbs and core cities in the same metro match. Both files list the sources and sha256 of their inputs.
- `industries.json` — LinkedIn's 434 active industries with their parents, plus older LinkedIn (V1) names and NAICS titles read as the LinkedIn industry they map to, from LinkedIn's tables on Microsoft Learn (sources and sha256 in the file).
