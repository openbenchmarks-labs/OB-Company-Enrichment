"""Deterministic grader for Company Enrichment: hq, industry, employee_band, founded_year, linkedin_url."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

HERE = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((HERE / "config.yaml").read_text())
FIELDS = CONFIG["judge"]["fields"]
PLACES = json.loads((HERE / "places.json").read_text())
METROS = json.loads((HERE / "metros.json").read_text())
INDUSTRIES = json.loads((HERE / "industries.json").read_text())


def _norm_text(raw: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(raw or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.strip().lower())


def _compact(text: str) -> str:
    """Letters and digits only, so "Mc Kinney" and "McKinney" read alike."""
    return re.sub(r"[^a-z0-9]", "", text)


def _build_country_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for row in PLACES.get("countries") or []:
        iso2 = str(row["iso2"]).upper()
        for alias in [iso2, row.get("iso3", ""), *(row.get("names") or [])]:
            if alias:
                out[_norm_text(alias)] = iso2
    for uk in ("uk", "england", "great britain", "united kingdom"):
        out[uk] = "GB"
    for us in ("us", "usa", "u.s.", "u.s.a.", "united states", "united states of america"):
        out[us] = "US"
    return out


def _build_city_map() -> dict[str, str]:
    """City name or alias -> canonical city. A locality listed with `in` reads as its parent city."""
    rows = PLACES.get("cities") or []
    canonical = {row["name"]: row.get("in") or row["name"] for row in rows}
    out: dict[str, str] = {}
    for row in rows:
        target = _norm_text(canonical.get(row["name"], row["name"]))
        for name in [row["name"], *(row.get("aliases") or [])]:
            out[_norm_text(name)] = target
    return out


COUNTRY_MAP = _build_country_map()
CITY_MAP = _build_city_map()
COMPACT_CITY_MAP: dict[str, str] = {}
for _name, _city in CITY_MAP.items():
    COMPACT_CITY_MAP.setdefault(_compact(_name), _city)
HQ_RULES = FIELDS["hq"]["metro"]["rules"]
# US metro areas of each city's principal Census place (`metros`, not `also`), plus the rules' readings of places the
# Census lists under another name (a borough is a county, not a place).
CITY_METROS = {_norm_text(row["name"]): set(row.get("metros") or []) for row in METROS.get("cities") or []}
CITY_METROS.update({_norm_text(name): {code} for name, code in (HQ_RULES.get("city_metros") or {}).items()})
CBSA = {code: row["cbsa"] for code, row in METROS["metros"].items()}
NO_PLACE = {_norm_text(name) for name in HQ_RULES.get("key_no_place") or []}
SAME_AREA = [tuple(pair) for pair in HQ_RULES.get("same_area") or []]
DIFFERENT_AREA = [tuple(pair) for pair in HQ_RULES.get("different_area") or []]
PLACE_SPLIT = re.compile(r"\s+or\s+|\s+[-–]\s+|[,/()]")
CITY_NAMES = set(CITY_MAP.values())
# Countries that are one city: any city named inside them is the city.
CITY_STATES = {"SG", "HK", "MO", "MC", "VA"}
# US state names and postal codes, which vendors write between the city and the country.
US_STATES = {_norm_text(s) for s in (
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
    "District of Columbia", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas",
    "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri",
    "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington", "West Virginia", "Wisconsin", "Wyoming", "AL",
    "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
)}


def _city(piece: str) -> str:
    norm = _norm_text(piece)
    return CITY_MAP.get(norm) or COMPACT_CITY_MAP.get(_compact(norm)) or norm


def _metros(city: str) -> set[str]:
    return CITY_METROS.get(city) or CITY_METROS.get(_norm_text(city)) or set()


def _one_name(a: str, b: str) -> bool:
    """One place written two ways: "Frankfurt am Main" and "Frankfurt", "Alresford" and "New Alresford": the shorter
    name's words all appear in the longer one, unless both are cities the table knows apart ("York", "New York")."""
    if a in CITY_NAMES and b in CITY_NAMES:
        return False
    wa, wb = set(re.findall(r"[a-z0-9]+", a)), set(re.findall(r"[a-z0-9]+", b))
    return bool(wa and wb) and (wa <= wb or wb <= wa)


def _pair_in(pairs: list[tuple[str, str]], a: set[str], b: set[str]) -> bool:
    """True when one metro of a pair is in `a` and the other is in `b`."""
    return any((x in a and y in b) or (y in a and x in b) for x, y in pairs)


def _same_metro(a: set[str], b: set[str]) -> bool:
    """One Census metropolitan area (the CBSA, so Tampa and St. Petersburg match), less the rules' different areas,
    plus their same areas."""
    if _pair_in(DIFFERENT_AREA, a, b):
        return False
    return bool({CBSA[m] for m in a} & {CBSA[m] for m in b}) or _pair_in(SAME_AREA, a, b)


def _parse_hq(raw: str | None) -> tuple[list[str], set[str]]:
    """City readings and country codes. "Sandton, Johannesburg, ZA" and "Palo Alto or San Francisco, US" give every
    city they name; "RO or Romania" and "England/GB" every country."""
    parts = [p.strip() for p in str(raw or "").split(",") if p.strip()]
    if not parts:
        return [], set()
    countries = {COUNTRY_MAP.get(_norm_text(c), _norm_text(c)) for c in re.split(r"\s+or\s+|/", parts[-1]) if c.strip()}
    cities = []
    for i, part in enumerate(parts[:-1]):
        for piece in PLACE_SPLIT.split(part):
            norm = _norm_text(piece)
            # After the first part, keep only known cities: "Buffalo, New York" names the state, not New York City.
            if norm and (i == 0 or (norm not in US_STATES and _city(piece) in CITY_NAMES)):
                cities.append(_city(piece))
    return cities, countries


def grade_hq(truth: str | None, pred: str | None) -> str:
    if not truth:
        return "not_scoreable"
    if not pred:
        return "wrong"
    t_cities, t_countries = _parse_hq(truth)
    if t_cities and set(t_cities) <= NO_PLACE:
        return "not_scoreable"
    p_cities, p_countries = _parse_hq(pred)
    if not t_countries & p_countries:
        return "wrong"
    if not t_cities or set(t_cities) & set(p_cities) or t_countries <= CITY_STATES:
        return "correct"
    if any(_one_name(t, p) for t in t_cities for p in p_cities):
        return "correct"
    if "US" not in t_countries:
        return "wrong"
    t_metros = set().union(*(_metros(c) for c in t_cities))
    p_metros = set().union(*(_metros(c) for c in p_cities))
    return "correct" if t_metros and p_metros and _same_metro(t_metros, p_metros) else "wrong"


def _industry_norm(text: str) -> str:
    text = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower().replace("&", " and ")).strip()


INDUSTRY_BY_NAME = {_industry_norm(v["label"]): code for code, v in INDUSTRIES["industries"].items()}
INDUSTRY_BY_NAME.update({name: code for name, code in INDUSTRIES["names"].items() if name not in INDUSTRY_BY_NAME})
INDUSTRY_BY_NAME.update(
    {_industry_norm(a): INDUSTRY_BY_NAME[_industry_norm(t)] for a, t in (FIELDS["industry"].get("aliases") or {}).items()}
)


def _industry(label: str) -> str | None:
    """The LinkedIn industry a label names, read whole or by its first comma part ("Software Development, Marketing")."""
    norm = _industry_norm(label)
    return INDUSTRY_BY_NAME.get(norm) or INDUSTRY_BY_NAME.get(_industry_norm(label.split(",")[0]))


def _lineage(code: str) -> list[str]:
    out = [code]
    while (parent := INDUSTRIES["industries"][out[-1]]["parent"]) is not None:
        out.append(parent)
    return out


def grade_industry(truth: str | None, pred: str | None, others: list[str] | None = None) -> str:
    """Right when any answered industry is the key's, or a broader or narrower industry on the same branch of
    LinkedIn's hierarchy; a label no table reads is right only as the key's own words."""
    if not truth:
        return "not_scoreable"
    answers = [a for a in [pred, *(others or [])] if a and str(a).strip()]
    if not answers:
        return "wrong"
    key = _industry(str(truth))
    for answer in answers:
        if _industry_norm(str(answer)) == _industry_norm(str(truth)):
            return "correct"
        code = _industry(str(answer))
        if key and code and (key in _lineage(code) or code in _lineage(key)):
            return "correct"
    return "wrong"


def _parse_band(raw: Any) -> tuple[int, int | None] | None:
    """(min, max) of "51-200", (n, n) of "52", (10001, None) of "10001+"."""
    if raw is None:
        return None
    s = str(raw).replace(",", "").strip()
    if s.lower() == "myself only":  # LinkedIn's size label for a one-person company
        return (1, 1)
    if m := re.match(r"^(\d+)\s*[-–]\s*(\d+)$", s):
        return (int(m.group(1)), int(m.group(2)))
    if m := re.match(r"^(\d+)\+$", s):
        return (int(m.group(1)), None)
    if m := re.match(r"^(\d+)$", s):
        return (int(m.group(1)), int(m.group(1)))
    return None


def _lower_two(band: tuple[int, int | None]) -> tuple[int, int | None]:
    return (2 if band[0] == 1 else band[0], band[1])


def _in_band(value: int, band: tuple[int, int | None], open_band: bool) -> bool:
    low, high = band
    if high is None and not open_band:
        return False
    return low <= value and (high is None or value <= high)


def grade_employee_band(truth: Any, pred: Any, truth_count: Any = None) -> str:
    """The same band (a lower bound of 1 read as 2); else an exact count inside the other side's band; else two exact
    counts within 5%; else bounds each within 1. An open band "10001+" holds any count of at least 10,001. The key's
    exact count is `truth_count` when the set gives one (LinkedIn's member count beside its size band)."""
    t_band, p_band = _parse_band(truth), _parse_band(pred)
    if t_band is None:
        return "not_scoreable"
    if p_band is None:
        return "wrong"
    if _lower_two(t_band) == _lower_two(p_band):
        return "correct"
    p_exact = p_band[0] if p_band[0] == p_band[1] else None
    t_exact = int(truth_count) if truth_count is not None else t_band[0] if t_band[0] == t_band[1] else None
    if p_exact is not None and _in_band(p_exact, t_band, FIELDS["employee_band"]["open_band"]):
        return "correct"
    if t_exact is not None and _in_band(t_exact, p_band, True):
        return "correct"
    if p_exact is not None and t_exact is not None:
        return "correct" if abs(p_exact - t_exact) * 100 <= t_exact * 5 else "wrong"
    if None not in (*t_band, *p_band) and abs(t_band[0] - p_band[0]) <= 1 and abs(t_band[1] - p_band[1]) <= 1:
        return "correct"
    return "wrong"


def grade_founded_year(truth: Any, pred: Any) -> str:
    if truth is None or str(truth).strip() == "":
        return "not_scoreable"
    if pred is None or str(pred).strip() == "":
        return "wrong"
    try:
        return "correct" if int(truth) == int(pred) else "wrong"
    except (TypeError, ValueError):
        return "wrong"


def _linkedin_slug(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip()
    if "://" not in s:
        s = "https://" + s
    path = unquote(urlsplit(s).path).strip("/").rstrip(".").lower()
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] in ("company", "school", "showcase"):
        return parts[1]
    return parts[-1] if parts else ""


def grade_linkedin_url(truth: str | None, pred: str | None) -> str:
    t_slug = _linkedin_slug(truth)
    if not t_slug:
        return "not_scoreable"
    p_slug = _linkedin_slug(pred)
    if not p_slug:
        return "wrong"
    return "correct" if t_slug == p_slug else "wrong"


def grade_row(truth: dict[str, Any], pred: dict[str, Any]) -> dict[str, str]:
    others = pred.get("industries") or (pred.get("extra") or {}).get("industries")
    return {
        "hq": grade_hq(truth.get("hq"), pred.get("hq")),
        "industry": grade_industry(truth.get("industry"), pred.get("industry"), others),
        "employee_band": grade_employee_band(truth.get("employee_band"), pred.get("employee_band"), truth.get("employee_count")),
        "founded_year": grade_founded_year(truth.get("founded_year"), pred.get("founded_year")),
        "linkedin_url": grade_linkedin_url(truth.get("linkedin_url"), pred.get("linkedin_url")),
    }


def wilson95(correct: int, total: int) -> tuple[float, float, float]:
    if total <= 0:
        return (0.0, 0.0, 0.0)
    z = 1.959963984540054
    p = correct / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = (z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)) / denom
    return (p, max(0.0, center - half), min(1.0, center + half))
