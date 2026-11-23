"""obkit's grade hook: the repo's own field grader. Each graded field is one verdict: True, False, or None when the grader
says the ground truth cannot be scored for it."""
from __future__ import annotations

import json
from typing import Any

from harness.models import FIELDS

from .grader import grade_row

VERDICT = {"correct": True, "wrong": False, "not_scoreable": None}


def grade(question: dict[str, Any], answer: str) -> dict[str, bool | None]:
    verdicts = grade_row(question, json.loads(answer))
    return {field: VERDICT[verdicts[field]] for field in FIELDS}
