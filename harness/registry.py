"""Reads `tools/<tool>.yaml` and builds each setup's adapter from `harness/adapters/<tool>.py`.

An adapter module turns the tool name into a module name (`people-data-labs` -> `people_data_labs`) and defines
`build(setup, spec, env) -> Adapter`.
"""
from __future__ import annotations

import dataclasses
from functools import cache
from importlib import import_module
from pathlib import Path
from typing import Any

import yaml

from .tool_base import Adapter

TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


@dataclasses.dataclass(frozen=True)
class SetupSpec:
    tool: str
    name: str
    spec: dict[str, Any]
    env: list[str]

    @property
    def runnable(self) -> bool:
        return bool(self.spec.get("endpoint"))


@cache
def setups() -> dict[str, SetupSpec]:
    out: dict[str, SetupSpec] = {}
    for path in sorted(TOOLS_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text()) or {}
        tool = doc.get("tool", path.stem)
        for name, spec in (doc.get("setups") or {}).items():
            if name in out:
                raise RuntimeError(f"setup {name!r} is defined in both {out[name].tool} and {tool}")
            out[name] = SetupSpec(tool, name, spec or {}, list(doc.get("env") or []))
    return out


def main_setups() -> list[str]:
    return [name for name, s in setups().items() if s.spec.get("class") == "main" and s.runnable]


def build(name: str) -> Adapter:
    spec = setups().get(name)
    if spec is None:
        raise KeyError(f"unknown setup {name!r}; see `python -m harness.run list-tools`")
    if not spec.runnable:
        raise ValueError(f"setup {name!r} has no endpoint in tools/{spec.tool}.yaml, so it cannot be run")
    module = import_module(f"harness.adapters.{spec.tool.replace('-', '_')}")
    return module.build(name, spec.spec, spec.env)
