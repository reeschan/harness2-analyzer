"""atelier events as harness2 traces, so `harness2-evaluate` can point findings at them.

harness2's evaluation keeps only findings whose evidence is a span of the run graph, and the
graph is built from `trace.jsonl` files. `write_traces` therefore writes one trace per atelier
run: a `task` span (`domain: atelier`) holding one point event per atelier event, of kind
`atelier.<type>`. The attrs carry the same identifiers and allowlisted values as the event, so
the traces stay body-free. `events_from_spans` reads the events back from the graph.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .atelier import Event
from .h2trace import TRACE_NAME, Kind, SpanLike
from .ids import hash12, is_id

KIND_PREFIX = "atelier."
TASK_SPAN = "atelier-task"


def _line(run_id: str, span_id: str, parent: str | None, kind: str, ts: str, attrs: dict) -> str:
    """One trace line in harness2's format (sorted keys, ASCII)."""
    event = {
        "run_id": run_id,
        "span_id": span_id,
        "parent_id": parent,
        "kind": kind,
        "ts": ts,
        "attrs": attrs,
    }
    return json.dumps(event, sort_keys=True, ensure_ascii=True)


def _folder(run_id: str) -> str:
    """A folder name for a run: the run id when it is safe, else its hash."""
    if is_id(run_id) and "/" not in run_id and ":" not in run_id and run_id not in (".", ".."):
        return run_id
    return "h-" + hash12(run_id)


def write_traces(events: Iterable[Event], out: Path) -> list[Path]:
    """Write `<out>/<run>/trace.jsonl` for each atelier run; returns the files written."""
    by_run: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        by_run[event.run_id].append(event)
    written = []
    for run_id in sorted(by_run):
        run = sorted(by_run[run_id], key=lambda e: e.order)
        first = min(e.ts for e in run)
        last = max(e.ts for e in run)
        lines = [
            _line(
                run_id,
                TASK_SPAN,
                None,
                Kind.TASK,
                first,
                {"phase": "start", "task_id": f"atelier:{run_id}", "domain": "atelier"},
            )
        ]
        for event in run:
            attrs: dict[str, Any] = {
                "type": event.type,
                "seq": event.seq,
                "stage": event.stage,
                "pbi": event.pbi,
                "data": event.data,
                "links": event.links,
                "line": event.line,
                "file": Path(event.source).name,
            }
            span_id = f"at{event.order:06d}"
            lines.append(
                _line(run_id, span_id, TASK_SPAN, KIND_PREFIX + event.type, event.ts, attrs)
            )
        lines.append(
            _line(run_id, TASK_SPAN, None, Kind.TASK, last, {"phase": "end", "status": "ok"})
        )
        path = Path(out) / _folder(run_id) / TRACE_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(path)
    return written


def events_from_spans(spans: Iterable[SpanLike]) -> list[tuple[Event, SpanLike]]:
    """The atelier events of a graph with the span each came from, in their original order."""
    found = []
    for span in spans:
        if not span.kind.startswith(KIND_PREFIX):
            continue
        attrs = span.attrs
        data = attrs.get("data")
        links = attrs.get("links")
        seq = attrs.get("seq")
        line = attrs.get("line")
        event = Event(
            type=str(attrs.get("type") or span.kind.removeprefix(KIND_PREFIX)),
            run_id=span.run_id,
            ts=span.start_ts or "",
            seq=seq if isinstance(seq, int) else None,
            stage=attrs.get("stage"),
            pbi=attrs.get("pbi"),
            data=dict(data) if isinstance(data, dict) else {},
            links=dict(links) if isinstance(links, dict) else {},
            source=str(attrs.get("file") or ""),
            line=line if isinstance(line, int) else 0,
        )
        found.append((event, span))
    found.sort(key=lambda pair: pair[1].span_id)
    for index, (event, _) in enumerate(found):
        event.order = index
    return found
