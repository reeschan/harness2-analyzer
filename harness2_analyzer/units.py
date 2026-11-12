"""`harness.unit/1`: one trial per row, the analyzer's evaluation unit (design §4.2).

A unit holds only integers, numbers, booleans, enumerated values and short identifiers, the
same value types as atelier's allowlist, so a unit never carries body text. `check_unit`
enforces that before a unit is written.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .ids import is_id

SCHEMA = "harness.unit/1"


class System:
    """Where a unit came from."""

    HARNESS2 = "harness2"
    ATELIER = "atelier"
    ALL = (HARNESS2, ATELIER)


class Split:
    """The split a unit belongs to; `unsplit` when no manifest names it."""

    TRAIN = "train"
    HELDOUT = "heldout"
    REGRESSION = "regression"
    UNSPLIT = "unsplit"
    ALL = (TRAIN, HELDOUT, REGRESSION, UNSPLIT)


class RootCause:
    """The shared root causes (design §4.2, atelier addendum §3.5)."""

    AGENT = "agent"
    HARNESS = "harness"
    GRADER = "grader"
    TASK = "task"
    ENV = "env"
    NOISE = "noise"
    UNKNOWN = "unknown"
    ALL = (AGENT, HARNESS, GRADER, TASK, ENV, NOISE, UNKNOWN)


# atelier's `--cause` values mapped to the shared root causes (atelier addendum §3.5).
CAUSE_ROOT: Mapping[str, str] = {
    "spec": RootCause.TASK,
    "convention": RootCause.HARNESS,
    "scope": RootCause.HARNESS,
    "memory": RootCause.HARNESS,
    "model": RootCause.AGENT,
    "env": RootCause.ENV,
    "grader": RootCause.GRADER,
    "flaky": RootCause.NOISE,
}

FIELDS = (
    "schema",
    "unit_id",
    "system",
    "run_id",
    "task_id",
    "group",
    "split",
    "split_set_id",
    "change_id",
    "condition",
    "trial",
    "outcome",
    "signature",
    "facets_ref",
    "embedding_ref",
    "cluster",
    "root_cause",
    "body_free",
)

# Fields filled by the second stage; stage 1 writes them as null.
STAGE2_FIELDS = ("facets_ref", "embedding_ref", "cluster")


def root_cause_of(causes: Iterable[Any] | None) -> str | None:
    """The one root cause the atelier causes map to; `unknown` when they map to several.

    No causes (a passing attempt) gives None.
    """
    roots = {CAUSE_ROOT.get(str(cause), RootCause.UNKNOWN) for cause in causes or ()}
    if not roots:
        return None
    if len(roots) == 1:
        return roots.pop()
    return RootCause.UNKNOWN


def make_unit(**fields: Any) -> dict:
    """A unit with every field present: the given ones, null for the rest, the fixed ones set."""
    unit = {name: None for name in FIELDS}
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ValueError(f"unknown unit fields: {', '.join(sorted(unknown))}")
    unit.update(fields)
    unit["schema"] = SCHEMA
    unit["body_free"] = True
    return unit


def _check_value(value: Any, where: str) -> None:
    """A value is a scalar, a short identifier, or a list or object of those."""
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if value and not is_id(value):
            raise ValueError(f"{where} is not a short identifier: {value[:40]!r}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_value(item, f"{where}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not is_id(str(key)):
                raise ValueError(f"{where} has a key that is not an identifier: {key!r}")
            _check_value(item, f"{where}.{key}")
        return
    raise ValueError(f"{where} has a value of type {type(value).__name__}")


def check_unit(unit: Mapping[str, Any]) -> None:
    """Raise ValueError when `unit` is not a body-free `harness.unit/1` row."""
    if unit.get("schema") != SCHEMA:
        raise ValueError(f"unit schema must be {SCHEMA}, not {unit.get('schema')!r}")
    missing = [name for name in FIELDS if name not in unit]
    if missing:
        raise ValueError(f"unit is missing {', '.join(missing)}")
    extra = sorted(set(unit) - set(FIELDS))
    if extra:
        raise ValueError(f"unit has unknown fields {', '.join(extra)}")
    if unit["system"] not in System.ALL:
        raise ValueError(f"unknown system {unit['system']!r}")
    if unit["split"] not in Split.ALL:
        raise ValueError(f"unknown split {unit['split']!r}")
    if unit["root_cause"] is not None and unit["root_cause"] not in RootCause.ALL:
        raise ValueError(f"unknown root cause {unit['root_cause']!r}")
    if unit["body_free"] is not True:
        raise ValueError("body_free must be true")
    for name in ("unit_id", "run_id", "task_id"):
        if not is_id(unit[name]):
            raise ValueError(f"{name} must be a short identifier, not {unit[name]!r}")
    for name in FIELDS:
        _check_value(unit[name], name)
