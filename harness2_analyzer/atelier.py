"""atelier records: events, runs and cases, read into events and `harness.unit/1` rows.

The shapes follow reeschan/atelier main: `skills/atelier/harness.schema.json` (the allowlist of
`atelier.event/1`) and the addendum of `docs/specs/harness-metrics.md` ("hillclimb と analyzer
連携"). Two line shapes are read:

- the local `docs/pbi/events.jsonl` row: `schema: atelier.event/1` with `run_id`, `seq`,
  `stage`, `pbi`, `type`, `data` and the optional linking keys at the top level;
- the outer `harness.event/1` envelope of the harness-log sender, with
  `data_schema: atelier.event/1`.

Only identifiers and allowlisted values are read. `runs.jsonl` gives the verify counts and the
attempts of runs older than the events; `cases.jsonl` gives only the case id and the size of
the change. No body text (a case's task, harness or private sections) is read.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .ids import read_jsonl, safe_id, sig_id_of
from .units import Split, System, make_unit, root_cause_of

EVENT_SCHEMA = "atelier.event/1"
ENVELOPE_SCHEMA = "harness.event/1"
# The linking keys of the outer envelope (addendum §3.1) and of the local rows.
LINK_KEYS = ("change_id", "config_fp", "split", "split_set_id", "trial", "unit_id")
# A presentation row has no event type of its own yet; it is read as this type.
PRESENTED = "question.presented"
PRESENTATIONS_NAME = "presentations.jsonl"

OUTCOMES_JA = {
    "合格": "pass",
    "差し戻し": "sent-back",
    "昇格": "promoted",
    "使えず": "unusable",
    "保留": "held",
}
OUTCOME_ALIASES = {
    "rework": "sent-back",
    "tier-up": "promoted",
    "unavailable": "unusable",
    "hold": "held",
}
FINDINGS_JA = (("critical", "重大"), ("major", "中"), ("minor", "軽微"))


@dataclass
class Event:
    """One atelier event, with where it was read from and its place in the input order."""

    type: str
    run_id: str
    ts: str
    seq: int | None = None
    stage: str | None = None
    pbi: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    links: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    line: int = 0
    order: int = 0


def pbi_dir(path: Path) -> Path:
    """The `docs/pbi` folder of a repository root, or `path` itself when it holds the files."""
    path = Path(path)
    nested = path / "docs" / "pbi"
    return nested if nested.is_dir() else path


def event_of(row: Mapping[str, Any], *, source: str = "", line: int = 0) -> Event | None:
    """The event in one line of either shape, or None for any other line."""
    schema = row.get("schema")
    if schema == ENVELOPE_SCHEMA and row.get("data_schema") != EVENT_SCHEMA:
        return None
    if schema not in (EVENT_SCHEMA, ENVELOPE_SCHEMA):
        return None
    kind, data = row.get("type"), row.get("data")
    if not isinstance(kind, str) or not isinstance(data, Mapping):
        return None
    seq = row.get("seq")
    return Event(
        type=kind,
        run_id=str(row.get("run_id") or "adhoc"),
        ts=str(row.get("ts") or ""),
        seq=seq if isinstance(seq, int) and not isinstance(seq, bool) else None,
        stage=row.get("stage") if isinstance(row.get("stage"), str) else None,
        pbi=row.get("pbi") if isinstance(row.get("pbi"), str) else None,
        data=dict(data),
        links={key: row[key] for key in LINK_KEYS if row.get(key) is not None},
        source=source,
        line=line,
    )


def presentation_of(row: Mapping[str, Any], *, source: str = "", line: int = 0) -> Event | None:
    """A `presentations.jsonl` row as a `question.presented` event.

    The row's shape is not fixed yet on atelier main; only `run_id`, `ts`, `seq` and the
    question's `kind` and `topic` are read when present.
    """
    if not row.get("run_id"):
        return None
    seq = row.get("seq")
    data = {key: row[key] for key in ("kind", "topic") if isinstance(row.get(key), str)}
    return Event(
        type=PRESENTED,
        run_id=str(row["run_id"]),
        ts=str(row.get("ts") or ""),
        seq=seq if isinstance(seq, int) and not isinstance(seq, bool) else None,
        data=data,
        source=source,
        line=line,
    )


@dataclass
class Records:
    """What one or more atelier folders hold, already reduced to identifiers."""

    events: list[Event] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)
    cases: dict[str, dict] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    presentations_read: bool = False
    skipped: int = 0


def read_records(folders: Iterable[Path]) -> Records:
    """Read `events.jsonl`, `presentations.jsonl`, `runs.jsonl` and `cases.jsonl` of each folder.

    The events of all folders are put in one order: by time, then by file and line.
    """
    records = Records()
    for folder in folders:
        base = pbi_dir(Path(folder))
        events_path = base / "events.jsonl"
        if events_path.is_file():
            records.sources.append(str(events_path))
            for line, row in read_jsonl(events_path):
                event = event_of(row, source=str(events_path), line=line)
                if event is None:
                    records.skipped += 1
                else:
                    records.events.append(event)
        for presentations in (base / PRESENTATIONS_NAME, base / "conductor" / PRESENTATIONS_NAME):
            if not presentations.is_file():
                continue
            records.presentations_read = True
            records.sources.append(str(presentations))
            for line, row in read_jsonl(presentations):
                event = presentation_of(row, source=str(presentations), line=line)
                if event is not None:
                    records.events.append(event)
        runs_path = base / "runs.jsonl"
        if runs_path.is_file():
            records.sources.append(str(runs_path))
            records.runs.extend(row for _, row in read_jsonl(runs_path))
        cases_path = base / "cases.jsonl"
        if cases_path.is_file():
            records.sources.append(str(cases_path))
            for _, row in read_jsonl(cases_path):
                if isinstance(row.get("id"), str):
                    records.cases[row["id"]] = {"size": row.get("size")}
    records.events.sort(key=lambda event: (event.ts, event.source, event.line))
    for index, event in enumerate(records.events):
        event.order = index
    return records


# -- signatures (the same rules as atelier's record_run.signature_of) --------------------------


def failed_stage(n: Any) -> str:
    """Failed verify commands rounded to `0`, `1`, `2-3` or `4-up`."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        return "0"
    if n < 2:
        return str(n)
    return "2-3" if n <= 3 else "4-up"


def finding_stage(n: Any) -> str:
    """A findings count rounded to `0`, `1` or `2-up`."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        return "0"
    return "0" if n == 0 else "1" if n == 1 else "2-up"


def run_outcome(row: Mapping[str, Any]) -> str | None:
    """The English outcome of a `runs.jsonl` row (Japanese or English aliases)."""
    outcome = (row.get("judged") or {}).get("outcome")
    if not isinstance(outcome, str):
        return None
    outcome = OUTCOMES_JA.get(outcome, outcome)
    return OUTCOME_ALIASES.get(outcome, outcome)


def run_findings(row: Mapping[str, Any]) -> dict[str, int | None]:
    """The findings counts of a `runs.jsonl` row under English keys."""
    found = (row.get("judged") or {}).get("findings")
    if not isinstance(found, Mapping):
        return {}
    out: dict[str, int | None] = {}
    for en, ja in FINDINGS_JA:
        value = found.get(ja, found.get(en))
        out[en] = value if isinstance(value, int) and not isinstance(value, bool) else None
    return out


def _failed(verify: Any) -> int:
    """`total - passed` of a verify record, 0 when it is missing."""
    if isinstance(verify, Mapping):
        total, passed = verify.get("total"), verify.get("passed")
        if isinstance(total, int) and isinstance(passed, int):
            return max(0, total - passed)
    return 0


def signature_of_run(row: Mapping[str, Any]) -> dict:
    """The signature atelier's `record_run.signature_of` gives a `runs.jsonl` row."""
    causes = (row.get("judged") or {}).get("cause")
    causes = sorted(causes) if isinstance(causes, list) else []
    findings = run_findings(row)
    parts = {
        "outcome": run_outcome(row),
        "causes": causes,
        "verify_failed_n": failed_stage(_failed(row.get("verify"))),
        "findings": {en: finding_stage(findings.get(en)) for en, _ in FINDINGS_JA},
        "unchanged": (row.get("unchanged") or {}).get("violation") is True,
        "memory_violation": (row.get("memory") or {}).get("violation") is True,
        "test_failing": [],
        "fallback_any": False,
        "runner": row.get("runner"),
        "tier": row.get("tier"),
    }
    return {"sig_id": sig_id_of(parts), "parts": parts}


# -- units --------------------------------------------------------------------------------------


def size_bucket(n: Any) -> str:
    """Changed files rounded the way `taxonomy.json` does: 1, 2-3, 4-8, 9-20, 21+."""
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        return "0"
    for top, name in ((1, "1"), (3, "2-3"), (8, "4-8"), (20, "9-20")):
        if n <= top:
            return name
    return "21+"


def _report_key(run_id: Any, report: Any, attempt: Any) -> tuple[str, str, int]:
    """The key that joins a `pbi.attempted` event and its `runs.jsonl` row."""
    return (str(run_id), str(report), attempt if isinstance(attempt, int) else 1)


def _report_of(event: Event) -> str:
    """The report name of an attempt: `PBI-1`, or `PBI-1.rework-2` on a rework."""
    unit_id = event.links.get("unit_id")
    if isinstance(unit_id, str) and unit_id.count(":") >= 3:
        return unit_id.split(":")[-2]
    rework = event.data.get("rework")
    if isinstance(rework, int) and rework > 0:
        return f"{event.pbi}.rework-{rework}"
    return str(event.pbi)


def _tokens(values: Iterable[Any]) -> int | None:
    """The sum of the integer token counts, None when there are none."""
    numbers = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
    return sum(numbers) if numbers else None


def _number(value: Any) -> float | int | None:
    """`value` when it is a real number and not a bool."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def units_from_records(
    records: Records,
    *,
    split_index: Mapping[str, str] | None = None,
    split_set_id: str | None = None,
    default_change: str | None = None,
) -> list[dict]:
    """One unit per `pbi.attempted` event, plus one per `runs.jsonl` row no event covers."""
    split_index = split_index or {}
    runs_by_key = {
        _report_key(row.get("run_id"), row.get("report") or row.get("pbi"), row.get("attempt")): row
        for row in records.runs
    }
    groups = _groups(records)
    units: list[dict] = []
    covered: set[tuple[str, str, int]] = set()
    trials: dict[tuple, int] = defaultdict(int)
    for event in records.events:
        if event.type != "pbi.attempted":
            continue
        data = event.data
        report = _report_of(event)
        key = _report_key(event.run_id, report, data.get("attempt"))
        covered.add(key)
        row = runs_by_key.get(key, {})
        case_id = data.get("case_id") or _case_id(row)
        signature = data.get("signature")
        if not isinstance(signature, Mapping) or not signature.get("sig_id"):
            signature = signature_of_run(row) if row else None
        units.append(
            _unit(
                unit_id=event.links.get("unit_id") or f"at:{event.run_id}:{report}:{key[2]}",
                run_id=event.run_id,
                case_id=case_id,
                group=_group(groups, case_id, event.run_id, records),
                links=event.links,
                condition=data,
                outcome=data.get("outcome"),
                causes=data.get("causes"),
                verify=row.get("verify"),
                usd=data.get("usd"),
                seconds=data.get("seconds"),
                tokens=_tokens(
                    data.get(k) for k in ("input", "output", "cache_read", "cache_write")
                ),
                prompt=(row.get("prompt") or {}).get("worker"),
                signature=signature,
                split_index=split_index,
                split_set_id=split_set_id,
                default_change=default_change,
            )
        )
    for row in records.runs:
        report = row.get("report") or row.get("pbi")
        key = _report_key(row.get("run_id"), report, row.get("attempt"))
        if key in covered or not row.get("run_id") or not report:
            continue
        case_id = _case_id(row)
        tokens = row.get("tokens") or {}
        units.append(
            _unit(
                unit_id=f"at:{row['run_id']}:{report}:{key[2]}",
                run_id=str(row["run_id"]),
                case_id=case_id,
                group=_group(groups, case_id, str(row["run_id"]), records),
                links={},
                condition=row,
                outcome=run_outcome(row),
                causes=(row.get("judged") or {}).get("cause"),
                verify=row.get("verify"),
                usd=row.get("cost_reported"),
                seconds=(row.get("time") or {}).get("seconds"),
                tokens=tokens.get("total") if isinstance(tokens, Mapping) else None,
                prompt=(row.get("prompt") or {}).get("worker"),
                signature=signature_of_run(row),
                split_index=split_index,
                split_set_id=split_set_id,
                default_change=default_change,
            )
        )
    for unit in units:
        key = (unit["task_id"], unit["change_id"], unit["condition"]["config_fp"])
        if unit["trial"] is None:
            trials[key] += 1
            unit["trial"] = trials[key]
    return units


def _case_id(row: Mapping[str, Any]) -> str | None:
    """`<repo>:<work_key>:<PBI>` of a `runs.jsonl` row, the id `export_cases.py` gives."""
    if row.get("repo") and row.get("work_key") and row.get("pbi"):
        return f"{row['repo']}:{row['work_key']}:{row['pbi']}"
    return None


def _groups(records: Records) -> dict[str, str]:
    """`task_type/domain` of each case id and each run id, from `task.profiled`."""
    groups: dict[str, str] = {}
    for event in records.events:
        if event.type != "task.profiled":
            continue
        data = event.data
        label = f"{data.get('task_type') or 'unknown'}/{data.get('domain') or 'unknown'}"
        if data.get("scope") == "pbi" and data.get("case_id"):
            groups[str(data["case_id"])] = label
        else:
            groups.setdefault(f"run:{event.run_id}", label)
    return groups


def _group(groups: Mapping[str, str], case_id: Any, run_id: str, records: Records) -> str:
    """The case's profile, else the run's, plus the size of the change from `cases.jsonl`."""
    label = groups.get(str(case_id)) or groups.get(f"run:{run_id}") or "unknown/unknown"
    size = (records.cases.get(str(case_id)) or {}).get("size")
    facts = size.get("facts") if isinstance(size, Mapping) else None
    if isinstance(facts, Mapping):
        label += f"/files-{size_bucket(facts.get('files_changed'))}"
    return label


def _unit(
    *,
    unit_id: str,
    run_id: str,
    case_id: Any,
    group: str,
    links: Mapping[str, Any],
    condition: Mapping[str, Any],
    outcome: Any,
    causes: Any,
    verify: Any,
    usd: Any,
    seconds: Any,
    tokens: Any,
    prompt: Any,
    signature: Any,
    split_index: Mapping[str, str],
    split_set_id: str | None,
    default_change: str | None,
) -> dict:
    """One atelier unit from the pieces both sources share."""
    split = links.get("split") or split_index.get(str(case_id)) or Split.UNSPLIT
    if split not in Split.ALL:
        split = Split.UNSPLIT
    verify_out = None
    if isinstance(verify, Mapping) and isinstance(verify.get("total"), int):
        verify_out = {"passed": verify.get("passed"), "total": verify.get("total")}
    trial = links.get("trial")
    return make_unit(
        unit_id=safe_id(unit_id),
        system=System.ATELIER,
        run_id=safe_id(run_id),
        task_id=safe_id(case_id or f"{run_id}:unknown"),
        group=safe_id(group),
        split=split,
        split_set_id=safe_id(links.get("split_set_id") or split_set_id),
        change_id=safe_id(links.get("change_id") or default_change),
        condition={
            "config_fp": safe_id(links.get("config_fp")),
            "prompt": safe_id(prompt),
            "runner": safe_id(condition.get("runner")),
            "model": safe_id(condition.get("model")),
            "effort": safe_id(condition.get("effort")),
            "tier": safe_id(condition.get("tier")),
        },
        trial=trial if isinstance(trial, int) and not isinstance(trial, bool) else None,
        outcome={
            "status": safe_id(outcome),
            "passed": outcome == "pass",
            "verify": verify_out,
            "usd": _number(usd),
            "seconds": _number(seconds),
            "tokens": tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
        },
        signature=dict(signature) if isinstance(signature, Mapping) else None,
        root_cause=root_cause_of(causes if isinstance(causes, list) else None),
    )
