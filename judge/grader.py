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
PLACES = json.loads((HERE / "places.json").read_text())
METROS = json.loads((HERE / "metros.json").read_text())


def _build_country_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for row in PLACES.get("countries") or []:
        iso2 = str(row["iso2"]).upper()
        for alias in [iso2, row.get("iso3", ""), *(row.get("names") or [])]:
            if alias:
                out[str(alias).strip().lower()] = iso2
    for uk in ("uk", "england", "great britain", "united kingdom"):
        out[uk] = "GB"
    for us in ("us", "usa", "u.s.", "u.s.a.", "united states", "united states of america"):
        out[us] = "US"
    return out


COUNTRY_MAP = _build_country_map()
HQ_RULES = (CONFIG["judge"]["fields"]["hq"].get("metro") or {}).get("rules") or {}


def _norm_text(raw: str | None) -> str:
    text = unicodedata.normalize("NFKD", str(raw or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.strip().lower())


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


CITY_MAP = _build_city_map()
EQUIVALENT_CITIES = [{CITY_MAP.get(_norm_text(n), _norm_text(n)) for n in pair} for pair in PLACES.get("equivalent_cities") or []]
# US metro areas of each city's principal Census place (`metros`, not `also`).
CITY_METROS = {_norm_text(row["name"]): set(row.get("metros") or []) for row in METROS.get("cities") or []}
NO_PLACE = {_norm_text(name) for name in HQ_RULES.get("key_no_place") or []}
SAME_AREA = [tuple(pair) for pair in HQ_RULES.get("same_area") or []]
DIFFERENT_AREA = [tuple(pair) for pair in HQ_RULES.get("different_area") or []]


def _canonical_city(city: str) -> str:
    return CITY_MAP.get(city, city)


def _metros(city: str) -> set[str]:
    return CITY_METROS.get(city) or CITY_METROS.get(_canonical_city(city)) or set()


def _pair_in(pairs: list[tuple[str, str]], a: set[str], b: set[str]) -> bool:
    """True when one metro of a pair is in `a` and the other is in `b`."""
    return any((x in a and y in b) or (y in a and x in b) for x, y in pairs)


def _parse_hq(raw: str | None) -> tuple[str, str]:
    parts = [p.strip() for p in str(raw or "").split(",") if p.strip()]
    if not parts:
        return ("", "")
    country = COUNTRY_MAP.get(parts[-1].lower(), parts[-1].lower())
    city = _norm_text(parts[0]) if len(parts) > 1 else ""
    return (city, country)


def grade_hq(truth: str | None, pred: str | None) -> str:
    if not truth:
        return "not_scoreable"
    t_city, t_country = _parse_hq(truth)
    if t_city in NO_PLACE:
        return "not_scoreable"
    if not pred:
        return "wrong"
    p_city, p_country = _parse_hq(pred)
    if not t_country or t_country != p_country:
        return "wrong"
    if not t_city:
        return "correct"
    t_can, p_can = _canonical_city(t_city), _canonical_city(p_city)
    if t_can == p_can or any({t_can, p_can} <= pair for pair in EQUIVALENT_CITIES):
        return "correct"
    if t_country != "US":
        return "wrong"
    t_metros, p_metros = _metros(t_city), _metros(p_city)
    if not (t_metros and p_metros):
        return "wrong"
    if _pair_in(DIFFERENT_AREA, t_metros, p_metros):
        return "wrong"
    if t_metros & p_metros or _pair_in(SAME_AREA, t_metros, p_metros):
        return "correct"
    return "wrong"


def grade_industry(truth: str | None, pred: str | None) -> str:
    if not truth:
        return "not_scoreable"
    if not pred:
        return "wrong"
    return "correct" if _norm_text(truth) == _norm_text(pred) else "wrong"


def _parse_band(raw: Any) -> tuple[int, int] | None:
    if raw is None:
        return None
    s = str(raw).replace(",", "").strip()
    m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", s)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    m_plus = re.match(r"^(\d+)\+$", s)
    if m_plus:
        return (int(m_plus.group(1)), 10_000_000)
    m_num = re.match(r"^(\d+)$", s)
    if m_num:
        n = int(m_num.group(1))
        return (n, n)
    return None


def grade_employee_band(truth: Any, pred: Any) -> str:
    t_band = _parse_band(truth)
    if t_band is None:
        return "not_scoreable"
    p_band = _parse_band(pred)
    if p_band is None:
        return "wrong"
    return "correct" if max(t_band[0], p_band[0]) <= min(t_band[1], p_band[1]) else "wrong"


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
    return {
        "hq": grade_hq(truth.get("hq"), pred.get("hq")),
        "industry": grade_industry(truth.get("industry"), pred.get("industry")),
        "employee_band": grade_employee_band(truth.get("employee_band"), pred.get("employee_band")),
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
