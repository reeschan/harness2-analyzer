"""harness2 traces: reading `trace.jsonl` without harness2, and units from its spans.

`read_traces` merges the start and end events of each span the same way as
`harness2.core.graph.build`, so `units_from_spans` gets the same input from the files as the
evaluator gets from a `harness2.core.graph.Graph` (its nodes have the same attributes).
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .ids import hash12, norm_error, read_jsonl, safe_id, sig_id_of
from .units import Split, System, make_unit

TRACE_NAME = "trace.jsonl"
SUMMARY_NAME = "summary.json"


class Kind:
    """The harness2 trace kinds the analyzer reads (`harness2.core.trace.Kind`)."""

    TASK = "task"
    MODE = "mode"
    ROLLOUT = "rollout"
    STAGE = "stage"
    TREE_DIFF = "tree.diff"
    MODEL_ROUTE = "model.route"
    MODEL_SWITCH = "model.switch"
    MODEL_FENCE = "model.fence"
    ERROR = "error"


class SpanLike(Protocol):
    """What the analyzer reads from a span: `harness2.core.graph.Node` and `Span` both fit."""

    kind: str
    run_id: str
    span_id: str
    attrs: dict[str, Any]
    start_ts: str | None
    status: str | None
    dur_s: float | None
    source: Path


@dataclass
class Span:
    """One span or point event of a trace, with its start and end merged."""

    kind: str
    run_id: str
    span_id: str
    parent_id: str | None
    attrs: dict[str, Any] = field(default_factory=dict)
    start_ts: str | None = None
    end_ts: str | None = None
    status: str | None = None
    dur_s: float | None = None
    source: Path = Path()


def read_traces(paths: Iterable[Path]) -> list[Span]:
    """The spans of these trace files; the end event's attrs win, `phase` is dropped."""
    spans: dict[tuple[str, str], Span] = {}
    for path in paths:
        for _, event in read_jsonl(Path(path)):
            try:
                run_id, span_id, kind, ts = (
                    event["run_id"],
                    event["span_id"],
                    event["kind"],
                    event["ts"],
                )
            except KeyError:
                continue
            attrs = event.get("attrs") or {}
            if not isinstance(attrs, Mapping):
                continue
            key = (run_id, span_id)
            span = spans.get(key)
            if span is None:
                span = Span(kind, run_id, span_id, event.get("parent_id"), source=Path(path))
                spans[key] = span
            phase = attrs.get("phase")
            ended = span.end_ts is not None
            if phase == "end":
                span.end_ts = ts
                span.status = attrs.get("status")
                span.dur_s = attrs.get("dur_s")
            elif span.start_ts is None:
                span.start_ts = ts
            for name, value in attrs.items():
                if name == "phase":
                    continue
                if phase != "end" and ended:
                    span.attrs.setdefault(name, value)
                else:
                    span.attrs[name] = value
    return list(spans.values())


def find_runs(root: Path) -> tuple[list[Path], dict[Path, dict]]:
    """Every `trace.jsonl` under `root` and the `summary.json` objects next to them."""
    paths = sorted(path for path in Path(root).rglob(TRACE_NAME) if path.is_file())
    summaries: dict[Path, dict] = {}
    for path in paths:
        summary_path = path.parent / SUMMARY_NAME
        if not summary_path.is_file():
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(summary, dict):
            summaries[path.parent] = summary
    return paths, summaries


def attempts_stage(value: Any) -> str:
    """Stage attempts rounded to `0`, `1`, `2` or `3-up`."""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        return "0"
    return str(value) if value <= 2 else "3-up"


def diff_type(path: str) -> str:
    """The shape of a changed path: its top folder and its extension, as `top/.ext`.

    A file at the root has the top `.`; a file without an extension has the extension `none`.
    """
    pure = PurePosixPath(str(path).replace("\\", "/"))
    top = pure.parts[0] if len(pure.parts) > 1 else "."
    return f"{top}/{pure.suffix or 'none'}"


def _outcome(span: SpanLike) -> str:
    """`error` for a failed span or one with an error, `fail` when not ok or not delivered."""
    if span.status == "error" or span.attrs.get("error"):
        return "error"
    if span.attrs.get("ok") is False or span.attrs.get("delivered") is False:
        return "fail"
    return "ok"


def _first_candidate(routes: list[SpanLike], role: str) -> Mapping[str, Any]:
    """The first model candidate of the first `model.route` of `role`, or an empty mapping."""
    for route in routes:
        if route.attrs.get("role") != role:
            continue
        candidates = route.attrs.get("candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], Mapping):
            return candidates[0]
    return {}


def units_from_spans(
    spans: Iterable[SpanLike],
    *,
    split_index: Mapping[str, str] | None = None,
    split_set_id: str | None = None,
    default_change: str | None = None,
) -> list[dict]:
    """One `harness.unit/1` row per rollout span, each with its stage 1 signature.

    `split_index` maps a task id to its split; a task the TASK span and the index do not name is
    `unsplit`. `default_change` is the change id of a run whose TASK span has none.
    """
    by_run: dict[str, dict[str, list[SpanLike]]] = defaultdict(lambda: defaultdict(list))
    for span in spans:
        by_run[span.run_id][span.kind].append(span)
    units: list[tuple[str, dict]] = []
    for run_id in sorted(by_run):
        kinds = by_run[run_id]
        tasks = kinds.get(Kind.TASK) or []
        if not tasks:
            continue
        task = tasks[0]
        units.extend(
            _run_units(run_id, task, kinds, split_index or {}, split_set_id, default_change)
        )
    units.sort(key=lambda pair: (pair[0], pair[1]["unit_id"]))
    _number_trials([unit for _, unit in units])
    return [unit for _, unit in units]


def _run_units(
    run_id: str,
    task: SpanLike,
    kinds: Mapping[str, list[SpanLike]],
    split_index: Mapping[str, str],
    split_set_id: str | None,
    default_change: str | None,
) -> list[tuple[str, dict]]:
    """The units of one trace run, each with the start time used to order them."""
    attrs = task.attrs
    task_id = safe_id(attrs.get("task_id") or run_id)
    split = attrs.get("split") or split_index.get(str(attrs.get("task_id")), Split.UNSPLIT)
    if split == "test":
        split = Split.HELDOUT
    if split not in Split.ALL:
        split = Split.UNSPLIT
    change_id = safe_id(attrs.get("change_id") or default_change)
    executor = _first_candidate(kinds.get(Kind.MODEL_ROUTE, []), "executor")
    switched = bool(kinds.get(Kind.MODEL_SWITCH))
    fenced = any(span.attrs.get("fenced") is False for span in kinds.get(Kind.MODEL_FENCE, []))
    errors = sorted(kinds.get(Kind.ERROR, []), key=lambda span: span.start_ts or "")
    stages: dict[str, list[SpanLike]] = defaultdict(list)
    for stage in kinds.get(Kind.STAGE, []):
        stages[str(stage.attrs.get("mode") or "")].append(stage)
    diffs: dict[str, set[str]] = defaultdict(set)
    for diff in kinds.get(Kind.TREE_DIFF, []):
        for item in diff.attrs.get("files") or []:
            if isinstance(item, Mapping) and item.get("path"):
                diffs[str(diff.attrs.get("label") or "")].add(diff_type(item["path"]))

    out = []
    for rollout in kinds.get(Kind.ROLLOUT, []):
        ra = rollout.attrs
        role = str(ra.get("role") or "")
        mode = str(ra.get("mode") or "")
        label = str(ra.get("label") or "")
        outcome = _outcome(rollout)
        where, error = "", ra.get("error")
        if outcome != "ok":
            where = f"rollout:{role}"
            for event in errors:
                place = str(event.attrs.get("where") or "")
                if (role == "base" and place == "base") or (
                    role != "base" and mode and place.endswith(f":{mode}")
                ):
                    where = place
                    error = error or event.attrs.get("message")
                    break
        mode_stages = stages.get(mode, [])
        parts = {
            "outcome": outcome,
            "role": role,
            "where": safe_id(where) or "",
            "err": hash12(norm_error(error)) if error else "",
            "fell_back": any(bool(stage.attrs.get("fell_back")) for stage in mode_stages),
            "attempts": attempts_stage(max(_attempts(mode_stages), default=0)),
            "switched": switched,
            "fenced": fenced,
            "diff_types": sorted(safe_id(item) for item in diffs.get(label, set()))[:8],
        }
        unit = make_unit(
            unit_id=f"h2:{run_id}:{rollout.span_id}",
            system=System.HARNESS2,
            run_id=safe_id(run_id),
            task_id=task_id,
            group=safe_id(attrs.get("domain")) or "unknown",
            split=split,
            split_set_id=safe_id(attrs.get("split_set_id") or split_set_id),
            change_id=change_id,
            condition={
                "harness_fp": safe_id(attrs.get("harness_fp")),
                "runner": safe_id(executor.get("runner")),
                "model": safe_id(executor.get("model")),
                "effort": safe_id(executor.get("effort")),
                "mode": safe_id(mode) or "",
                "role": safe_id(role) or "",
                "label": safe_id(label) or "",
                "k": attrs.get("k") if isinstance(attrs.get("k"), int) else None,
                "seed": attrs.get("seed") if isinstance(attrs.get("seed"), int) else None,
            },
            outcome={
                "status": outcome,
                "passed": outcome == "ok",
                "verify": None,
                "usd": ra.get("usd") if isinstance(ra.get("usd"), (int, float)) else None,
                "seconds": rollout.dur_s if isinstance(rollout.dur_s, (int, float)) else None,
                "tokens": None,
            },
            signature={"sig_id": sig_id_of(parts), "parts": parts},
        )
        out.append((rollout.start_ts or "", unit))
    return out


def _attempts(stages: list[SpanLike]) -> list[int]:
    """The integer `attempts` the stages recorded."""
    values = (stage.attrs.get("attempts") for stage in stages)
    return [v for v in values if isinstance(v, int) and not isinstance(v, bool)]


def _number_trials(units: list[dict]) -> None:
    """Number the units of one task, change, mode, role and label as trials 1, 2, ..."""
    seen: dict[tuple, int] = defaultdict(int)
    for unit in units:
        cond = unit["condition"]
        key = (unit["task_id"], unit["change_id"], cond["mode"], cond["role"], cond["label"])
        seen[key] += 1
        unit["trial"] = seen[key]
