"""Stage 1 signatures: one row per unit and the counts per `sig_id` (design §5.1).

A harness2 unit's parts are `outcome`, `role`, `where`, `err` (the hash of the normalized error),
`fell_back`, `attempts`, `switched`, `fenced` and `diff_types`. An atelier unit keeps the
signature atelier wrote on `pbi.attempted` (or the one `record_run.signature_of` gives a
`runs.jsonl` row). Every `sig_id` is the first 12 hex digits of the sha256 of the parts dumped
as sorted, compact JSON, the same rule on both sides.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from .ids import sig_id_of

NO_CHANGE = "-"


def version_of(unit: Mapping[str, Any]) -> str:
    """What a unit ran under: its change id, else its config fingerprint, else its prompt hash."""
    if unit.get("change_id"):
        return str(unit["change_id"])
    condition = unit.get("condition") or {}
    for key, prefix in (("config_fp", "cfg:"), ("harness_fp", "fp:"), ("prompt", "prompt:")):
        if condition.get(key):
            return prefix + str(condition[key])
    return NO_CHANGE


def signature_rows(units: Iterable[Mapping[str, Any]]) -> tuple[list[dict], list[str]]:
    """`signatures.jsonl` rows and warnings for signatures whose id does not match the parts."""
    rows, warnings = [], []
    for unit in units:
        signature = unit.get("signature")
        if not isinstance(signature, Mapping) or not signature.get("sig_id"):
            warnings.append(f"{unit['unit_id']}: no signature")
            continue
        parts = signature.get("parts")
        if isinstance(parts, Mapping) and sig_id_of(parts) != signature["sig_id"]:
            warnings.append(
                f"{unit['unit_id']}: sig_id {signature['sig_id']} does not match its parts"
            )
        rows.append(
            {
                "unit_id": unit["unit_id"],
                "system": unit["system"],
                "task_id": unit["task_id"],
                "split": unit["split"],
                "version": version_of(unit),
                "passed": bool((unit.get("outcome") or {}).get("passed")),
                "sig_id": signature["sig_id"],
                "parts": parts,
            }
        )
    return rows, warnings


def summarize(rows: Iterable[Mapping[str, Any]]) -> dict:
    """`sig_summary.json`: per system the unit count, and per `sig_id` its counts and spread.

    `rate` is the share of the system's units with that signature; `first_version` is the
    version (change id or fingerprint) of the first unit seen with it, in input order.
    """
    rows = list(rows)
    totals: Counter[str] = Counter(row["system"] for row in rows)
    sigs: dict[str, dict] = {}
    by_version: dict[str, Counter[str]] = defaultdict(Counter)
    tasks: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        sig = sigs.get(row["sig_id"])
        if sig is None:
            sig = {
                "sig_id": row["sig_id"],
                "system": row["system"],
                "parts": row["parts"],
                "count": 0,
                "failing": 0,
                "first_version": row["version"],
            }
            sigs[row["sig_id"]] = sig
        sig["count"] += 1
        sig["failing"] += 0 if row["passed"] else 1
        by_version[row["sig_id"]][row["version"]] += 1
        tasks[row["sig_id"]].add(row["task_id"])
    for sig_id, sig in sigs.items():
        sig["rate"] = round(sig["count"] / totals[sig["system"]], 4)
        sig["tasks"] = len(tasks[sig_id])
        sig["versions"] = dict(sorted(by_version[sig_id].items()))
    ordered = sorted(sigs.values(), key=lambda s: (-s["failing"], -s["count"], s["sig_id"]))
    return {"schema": "h2a.sig_summary/1", "units": dict(sorted(totals.items())), "sigs": ordered}
