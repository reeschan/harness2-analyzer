"""harness2-analyzer, stage 1: evaluation units, failure signatures, a report and an evaluator.

The package reads harness2 traces (`trace.jsonl` + `summary.json`) and atelier records
(`docs/pbi/events.jsonl`, `runs.jsonl`, `cases.jsonl`), turns them into `harness.unit/1` rows
without any body text, gives each row a deterministic signature, and writes an offline HTML
report. The evaluator `analyzer` plugs the same checks into `harness2-evaluate`.
"""

__version__ = "0.1.0"
