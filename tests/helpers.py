"""Small builders for the test inputs: harness2 traces and atelier records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RUN = "0123456789abcdef0123456789abcdef"
AT_RUN = "44571837-61ec-44d5-917f-cbb0ed2d07bd"
CASE = "repo:repo:claude/x:W-001:PBI-1"
SECRET = "SECRET-BODY-TEXT do not leak"


def write_lines(path: Path, rows: list[dict[str, Any]]) -> Path:
    """Write rows as JSON lines, creating the folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), "utf-8")
    return path


def ev(run, span, parent, kind, ts, **attrs):
    """One trace event."""
    return {
        "run_id": run,
        "span_id": span,
        "parent_id": parent,
        "kind": kind,
        "ts": ts,
        "attrs": attrs,
    }


def harness2_run(
    folder: Path, *, run: str = RUN, task_id: str = "lab/t1", fail_message: str = ""
) -> Path:
    """A task run: a base rollout that passes and a candidate that fails with an error."""
    t = "2026-11-10T00:00:0{}+00:00"
    rows = [
        ev(
            run,
            "task",
            None,
            "task",
            t.format(0),
            phase="start",
            task_id=task_id,
            domain="lab",
            k=1,
            seed=7,
            modes=["adapt"],
            resumed=False,
        ),
        ev(
            run,
            "route",
            "task",
            "model.route",
            t.format(0),
            role="executor",
            candidates=[{"runner": "opencode", "model": "m1", "effort": "high"}],
        ),
        ev(
            run,
            "base",
            "task",
            "rollout",
            t.format(1),
            phase="start",
            role="base",
            mode="",
            label="base-1",
            run_id="r1",
        ),
        ev(
            run,
            "base",
            "task",
            "rollout",
            t.format(2),
            phase="end",
            status="ok",
            dur_s=1.5,
            ok=True,
            delivered=True,
            usd=0.1,
            error="",
        ),
        ev(run, "mode", "task", "mode", t.format(2), phase="start", mode="adapt"),
        ev(
            run,
            "stage",
            "mode",
            "stage",
            t.format(3),
            phase="start",
            label="s1",
            dim="INV",
            mode="adapt",
        ),
        ev(
            run,
            "stage",
            "mode",
            "stage",
            t.format(4),
            phase="end",
            status="ok",
            dur_s=1,
            fell_back=True,
            attempts=3,
        ),
        ev(
            run,
            "diff",
            "mode",
            "tree.diff",
            t.format(4),
            label="cand-1",
            files=[{"path": "src/a.py", "sha256": "x", "change": "changed"}],
        ),
        ev(
            run,
            "cand",
            "mode",
            "rollout",
            t.format(5),
            phase="start",
            role="candidate",
            mode="adapt",
            label="cand-1",
            run_id="r2",
        ),
        ev(
            run,
            "cand",
            "mode",
            "rollout",
            t.format(6),
            phase="end",
            status="ok",
            dur_s=2,
            ok=False,
            delivered=False,
            usd=0.2,
            error="",
        ),
        ev(
            run,
            "err",
            "mode",
            "error",
            t.format(6),
            where="mode:adapt",
            message=fail_message or f"run {run} failed at /tmp/x/y.py line 12",
        ),
        ev(run, "mode", "task", "mode", t.format(7), phase="end", status="ok", dur_s=5),
        ev(run, "task", None, "task", t.format(8), phase="end", status="ok", dur_s=8),
    ]
    path = write_lines(folder / "trace.jsonl", rows)
    (folder / "summary.json").write_text(json.dumps({"task_id": task_id, "k": 1}), "utf-8")
    return path


# The parts and sig_id of a real pbi.attempted line (atelier-handoff-json, 2026-11-12).
REAL_PARTS = {
    "outcome": "sent-back",
    "causes": ["model"],
    "verify_failed_n": "0",
    "findings": {"critical": "2-up", "major": "0", "minor": "0"},
    "unchanged": False,
    "memory_violation": False,
    "test_failing": [],
    "fallback_any": False,
    "runner": "claude",
    "tier": "sonnet",
}
REAL_SIG = "3ff2478f3d48"


def at_event(type_: str, ts: str, data: dict, *, seq: int = 1, pbi=None, run=AT_RUN, **links):
    """One local `docs/pbi/events.jsonl` row."""
    row = {
        "schema": "atelier.event/1",
        "ts": ts,
        "run_id": run,
        "seq": seq,
        "stage": None,
        "pbi": pbi,
        "type": type_,
        "data": data,
    }
    row.update(links)
    return row


def atelier_repo(root: Path, *, presentations: list[dict] | None = None) -> Path:
    """An atelier repository with events, runs and cases under `docs/pbi`."""
    base = root / "docs" / "pbi"
    attempted = {
        "attempt": 1,
        "step": 1,
        "rework": 0,
        "runner": "claude",
        "model": "sonnet",
        "tier": "sonnet",
        "effort": "high",
        "outcome": "sent-back",
        "causes": ["model"],
        "findings": {"critical": 2, "major": 0, "minor": 0},
        "unchanged": False,
        "seconds": 135,
        "input": 28,
        "output": 100,
        "cache_read": 10,
        "cache_write": 2,
        "usd": 0.43,
        "case_id": CASE,
        "signature": {"sig_id": REAL_SIG, "parts": REAL_PARTS},
    }
    events = [
        at_event("episode.started", "2026-11-12T16:00:00Z", {"episode": "build", "wave": 1}),
        at_event(
            "question.answered",
            "2026-11-12T16:01:00Z",
            {"kind": "decision", "topic": "other", "choice": None},
            change_id="ch-1",
        ),
        at_event(
            "task.profiled",
            "2026-11-12T16:01:30Z",
            {"scope": "pbi", "task_type": "feature", "domain": "skill", "case_id": CASE},
        ),
        at_event(
            "pbi.attempted",
            "2026-11-12T16:02:00Z",
            attempted,
            pbi="PBI-1",
            config_fp="8fb964a69ff4",
            unit_id=f"at:{AT_RUN}:PBI-1:1",
        ),
        at_event(
            "question.asked",
            "2026-11-12T16:03:00Z",
            {"kind": "rework-limit", "topic": "other", "items": 1},
        ),
        at_event(
            "question.answered",
            "2026-11-12T16:04:00Z",
            {"kind": "rework-limit", "topic": "other", "choice": None},
        ),
        at_event("episode.ended", "2026-11-12T16:05:00Z", {"episode": "build", "status": "done"}),
        at_event("run.ended", "2026-11-12T16:06:00Z", {"result": "finished"}),
        at_event("review.judged", "2026-11-12T16:07:00Z", {"recommendation": "pass"}),
        {"schema": "other/1", "type": "x"},
    ]
    write_lines(base / "events.jsonl", events)
    run_row = {
        "schema": 1,
        "repo": "repo",
        "work_key": "repo:claude/x:W-001",
        "pbi": "PBI-1",
        "report": "PBI-1",
        "attempt": 1,
        "run_id": AT_RUN,
        "rework": 0,
        "tier": "sonnet",
        "runner": "claude",
        "model": "sonnet",
        "effort": "high",
        "verify": {"passed": 2, "total": 2, "codes": [0, 0]},
        "judged": {
            "outcome": "差し戻し",
            "findings": {"重大": 2, "中": 0, "軽微": 0},
            "cause": ["model"],
        },
        "unchanged": {"checked": False, "violation": None},
        "memory": {"violation": False},
        "prompt": {"worker": "043d0718a13e"},
    }
    old_row = dict(
        run_row,
        run_id="old-run",
        report="PBI-2",
        pbi="PBI-2",
        time={"seconds": 50},
        cost_reported=0.2,
        judged={"outcome": "合格", "findings": {}, "cause": None},
        tokens={"total": 999},
    )
    write_lines(base / "runs.jsonl", [run_row, old_row])
    write_lines(
        base / "cases.jsonl",
        [
            {
                "schema": "atelier.case/1",
                "id": CASE,
                "task": SECRET,
                "private": {"rework": SECRET},
                "size": {"facts": {"files_changed": 3}},
                "result": {"passed": True},
            }
        ],
    )
    if presentations is not None:
        write_lines(base / "presentations.jsonl", presentations)
    return root
