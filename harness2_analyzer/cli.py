"""`h2a`: the analyzer's command line (stage 1: ingest, signature, report).

    h2a ingest --runs RUNS --atelier REPO --out analysis
    h2a signature --dir analysis
    h2a report --dir analysis

`ingest` writes `units.jsonl` (`harness.unit/1`), `order.jsonl` (the order breaks of the
atelier events), `ingest.json` (the inputs and warnings) and, unless `--no-traces`, one
harness2 trace per atelier run under `<out>/traces/`, so that
`harness2-evaluate --runs <out>/traces --evaluators rules,analyzer` can point at the events.
`signature` writes `signatures.jsonl` and `sig_summary.json`; `report` writes `report.html`.

Exit codes: 0 when done, 2 for a bad argument or missing input.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import atelier, bridge, h2trace, order, report, signature
from .ids import read_jsonl, write_json, write_jsonl
from .units import Split, check_unit

UNITS = "units.jsonl"
ORDER = "order.jsonl"
META = "ingest.json"
SIGNATURES = "signatures.jsonl"
SUMMARY = "sig_summary.json"
REPORT = "report.html"


def load_split(path: Path) -> tuple[dict[str, str], str]:
    """A split manifest as `{task id: split}` and its split set id.

    Reads harness2's `split_tasks.py` output (`train` and `test` lists; `test` is held out) and
    atelier's manifest (`heldout` and optional `train`, `regression` lists and `split_set_id`).
    The split set id is the manifest's own, else the first 12 hex digits of its sha256.
    """
    raw = Path(path).read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"split manifest {path} is not a JSON object")
    index: dict[str, str] = {}
    for name, split in (
        ("train", Split.TRAIN),
        ("test", Split.HELDOUT),
        ("heldout", Split.HELDOUT),
        ("regression", Split.REGRESSION),
    ):
        for task_id in data.get(name) or []:
            index[str(task_id)] = split
    split_set_id = data.get("split_set_id") or hashlib.sha256(raw).hexdigest()[:12]
    return index, str(split_set_id)


def cmd_ingest(args: argparse.Namespace) -> int:
    """Read the inputs and write the units, the order breaks and the bridged traces."""
    if not args.runs and not args.atelier:
        print("error: give --runs, --atelier or both", file=sys.stderr)
        return 2
    for folder in [*args.runs, *args.atelier]:
        if not Path(folder).is_dir():
            print(f"error: {folder} is not a directory", file=sys.stderr)
            return 2
    split_index: dict[str, str] = {}
    split_set_id = None
    if args.split:
        try:
            split_index, split_set_id = load_split(Path(args.split))
        except (OSError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    out = Path(args.out)
    sources: list[str] = []
    warnings: list[str] = []
    units: list[dict] = []
    for root in args.runs:
        paths, _ = h2trace.find_runs(Path(root))
        if not paths:
            warnings.append(f"no {h2trace.TRACE_NAME} under {root}")
        sources += [str(p) for p in paths]
        units += h2trace.units_from_spans(
            h2trace.read_traces(paths),
            split_index=split_index,
            split_set_id=split_set_id,
            default_change=args.change_id,
        )
    records = atelier.read_records(Path(p) for p in args.atelier)
    if args.atelier and not records.sources:
        warnings.append("no atelier records (events.jsonl, runs.jsonl, cases.jsonl) found")
    if records.skipped:
        warnings.append(
            f"{records.skipped} event line(s) were not atelier.event/1 and were skipped"
        )
    sources += records.sources
    units += atelier.units_from_records(
        records, split_index=split_index, split_set_id=split_set_id, default_change=args.change_id
    )
    for unit in units:
        check_unit(unit)
    breaks, skipped = order.check(records.events)
    write_jsonl(out / UNITS, units)
    write_jsonl(out / ORDER, (b.to_dict() for b in breaks))
    traces = []
    if not args.no_traces and records.events:
        traces = bridge.write_traces(records.events, out / "traces")
    write_json(
        out / META,
        {
            "schema": "h2a.ingest/1",
            "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sources": sources,
            "units": len(units),
            "events": len(records.events),
            "order_breaks": len(breaks),
            "order_skipped": skipped,
            "traces": [str(p) for p in traces],
            "warnings": warnings,
        },
    )
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(
        f"wrote {out / UNITS}: {len(units)} unit(s), {len(breaks)} order break(s), "
        f"{len(traces)} atelier trace(s)"
    )
    return 0


def _units(folder: Path) -> list[dict] | None:
    """The units of an analysis folder, or None when `ingest` has not run."""
    path = folder / UNITS
    if not path.is_file():
        print(f"error: {path} is missing; run h2a ingest first", file=sys.stderr)
        return None
    return [row for _, row in read_jsonl(path)]


def cmd_signature(args: argparse.Namespace) -> int:
    """Write `signatures.jsonl` and `sig_summary.json` from the units."""
    folder = Path(args.dir)
    units = _units(folder)
    if units is None:
        return 2
    rows, warnings = signature.signature_rows(units)
    write_jsonl(folder / SIGNATURES, rows)
    write_json(folder / SUMMARY, signature.summarize(rows))
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"wrote {folder / SIGNATURES}: {len(rows)} signature(s)")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Write the HTML report of an analysis folder."""
    folder = Path(args.dir)
    units = _units(folder)
    if units is None:
        return 2
    summary = None
    if (folder / SUMMARY).is_file():
        summary = json.loads((folder / SUMMARY).read_text(encoding="utf-8"))
    breaks = [row for _, row in read_jsonl(folder / ORDER)] if (folder / ORDER).is_file() else []
    meta = {}
    if (folder / META).is_file():
        meta = json.loads((folder / META).read_text(encoding="utf-8"))
    out = Path(args.out) if args.out else folder / REPORT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.render(units, summary, breaks, meta), encoding="utf-8")
    print(f"wrote {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The `h2a` parser with its three subcommands."""
    parser = argparse.ArgumentParser(
        prog="h2a", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="read harness2 traces and atelier records into units")
    ingest.add_argument(
        "--runs",
        action="append",
        default=[],
        metavar="DIR",
        help="a directory of harness2 task runs (repeatable)",
    )
    ingest.add_argument(
        "--atelier",
        action="append",
        default=[],
        metavar="DIR",
        help="an atelier repository or its docs/pbi folder (repeatable)",
    )
    ingest.add_argument("--split", metavar="FILE", help="a split manifest")
    ingest.add_argument("--change-id", metavar="ID", help="the change id of units that record none")
    ingest.add_argument("--out", default="analysis", metavar="DIR", help="the analysis folder")
    ingest.add_argument(
        "--no-traces",
        action="store_true",
        help="do not write harness2 traces of the atelier events",
    )
    ingest.set_defaults(func=cmd_ingest)
    sig = sub.add_parser("signature", help="write the stage 1 signatures and their counts")
    sig.add_argument("--dir", default="analysis", metavar="DIR", help="the analysis folder")
    sig.set_defaults(func=cmd_signature)
    rep = sub.add_parser("report", help="write the offline HTML report")
    rep.add_argument("--dir", default="analysis", metavar="DIR", help="the analysis folder")
    rep.add_argument("--out", metavar="FILE", help="defaults to <dir>/report.html")
    rep.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
