"""The `analyzer` evaluator for `harness2-evaluate` (entry point `harness2.evaluators`).

It reads only the run graph, so the same request gives the same findings:

- order (`safety` / `reproducibility`): the atelier events that `h2a ingest` wrote as traces
  (kinds `atelier.*`) are checked against the order rules of `harness2_analyzer.order`, for
  example a `question.answered` with no presentation or no `question.asked` before it.
- repeated failures (`reproducibility`): a failing stage 1 signature that `min_repeat` or more
  units share, over harness2 rollouts and atelier attempts.

    harness2-evaluate --runs analysis/traces --evaluators rules,analyzer

Scores are never read.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from harness2.core import evaluate
from harness2.core.evaluate import EvalRequest, Evidence, Finding, Severity, Viewpoint

from . import bridge, h2trace, order

NAME = "analyzer"
VIEWPOINT_NAMES = ("safety", "reproducibility")


class AnalyzerEvaluator:
    """Order breaks of atelier events and repeated failure signatures.

    `min_repeat` is how many failing units must share a signature before it is a finding;
    `check_order` and `check_repeats` turn the two checks on or off.
    """

    def __init__(
        self, *, min_repeat: int = 3, check_order: bool = True, check_repeats: bool = True
    ) -> None:
        self.min_repeat = int(min_repeat)
        self.check_order = bool(check_order)
        self.check_repeats = bool(check_repeats)

    def viewpoints(self) -> list[Viewpoint]:
        """The two angles this evaluator judges: safety and reproducibility."""
        return [evaluate.viewpoint(name) for name in VIEWPOINT_NAMES]

    def evaluate(self, request: EvalRequest) -> list[Finding]:
        """The findings of both checks; scores are not read."""
        nodes = list(request.graph.nodes.values())
        findings: list[Finding] = []
        if self.check_order:
            findings += self._order(nodes)
        if self.check_repeats:
            findings += self._repeats(nodes)
        return findings

    def _order(self, nodes: list[Any]) -> list[Finding]:
        """One finding per atelier event that broke an order rule."""
        pairs = bridge.events_from_spans(nodes)
        span_of = {id(event): span for event, span in pairs}
        breaks, _ = order.check([event for event, _ in pairs])
        findings = []
        for item in breaks:
            rule, event = item.rule, item.event
            span = span_of[id(event)]
            where = f"atelier run {event.run_id}, {event.type} (seq {event.seq}, {event.ts})"
            if isinstance(rule, order.Before):
                claim = f"{where} came without an earlier {rule.requires}."
            else:
                claim = f"{where} came after {rule.end}."
            findings.append(
                Finding(
                    viewpoint=rule.viewpoint,
                    severity=rule.severity,
                    claim=claim,
                    evidence=(Evidence(run_id=span.run_id, span_id=span.span_id, note=rule.name),),
                    suggestion=rule.suggestion,
                    key=f"{NAME}:order:{rule.name}:{span.run_id}:{span.span_id}",
                    evaluator=NAME,
                )
            )
        return findings

    def _repeats(self, nodes: list[Any]) -> list[Finding]:
        """One finding per failing signature shared by `min_repeat` or more units."""
        groups: dict[tuple[str, str], list[tuple[str, Evidence]]] = defaultdict(list)
        for unit in h2trace.units_from_spans(nodes):
            if unit["outcome"]["passed"]:
                continue
            run_id, _, span_id = unit["unit_id"].removeprefix("h2:").rpartition(":")
            groups[("harness2", unit["signature"]["sig_id"])].append(
                (unit["task_id"], Evidence(run_id=run_id, span_id=span_id))
            )
        for event, span in bridge.events_from_spans(nodes):
            if event.type != "pbi.attempted" or event.data.get("outcome") == "pass":
                continue
            signature = event.data.get("signature")
            if not isinstance(signature, dict) or not signature.get("sig_id"):
                continue
            task = str(event.data.get("case_id") or f"{event.run_id}:{event.pbi}")
            groups[("atelier", str(signature["sig_id"]))].append(
                (task, Evidence(run_id=span.run_id, span_id=span.span_id))
            )
        findings = []
        for (system, sig_id), members in sorted(groups.items()):
            if len(members) < self.min_repeat:
                continue
            tasks = len({task for task, _ in members})
            findings.append(
                Finding(
                    viewpoint="reproducibility",
                    severity=Severity.MINOR,
                    claim=(
                        f"The {system} failure signature {sig_id} appeared in {len(members)} "
                        f"failing units across {tasks} task(s)."
                    ),
                    evidence=tuple(evidence for _, evidence in members[:10]),
                    suggestion=(
                        "Look at the shared cause of this signature (h2a report lists its parts) "
                        "before changing the harness for one task."
                    ),
                    key=f"{NAME}:repeat:{system}:{sig_id}",
                    evaluator=NAME,
                )
            )
        return findings


def evaluator_from_options(**options: Any) -> AnalyzerEvaluator:
    """The entry point factory: `AnalyzerEvaluator(**options)`."""
    return AnalyzerEvaluator(**options)
