"""Order rules over atelier events: an event that came without what must come before it.

Each rule is data: the event type it checks, the type that must come first in the same run,
the data keys the two must agree on, and whether one earlier event backs only one later
event. `check` runs every rule and returns the breaks; `skipped` names the rules it could not
check because the input has no event of the type they need (for example presentations,
which atelier main does not record yet).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .atelier import PRESENTED, Event


@dataclass(frozen=True)
class Before:
    """`event` needs an earlier `requires` in the same run that agrees on the `match` keys.

    With `consume`, one earlier event backs at most one later event. With `needs_source`, the
    rule is checked only when the input holds at least one `requires` event at all.
    """

    name: str
    event: str
    requires: str
    match: tuple[str, ...] = ()
    consume: bool = True
    needs_source: bool = False
    viewpoint: str = "reproducibility"
    severity: str = "minor"
    suggestion: str = ""


@dataclass(frozen=True)
class NotAfter:
    """None of `types` may come after `end` in the same run."""

    name: str
    end: str
    types: tuple[str, ...]
    viewpoint: str = "reproducibility"
    severity: str = "minor"
    suggestion: str = ""


Rule = Before | NotAfter

# The work an atelier run does; none of it should be recorded after the run ended.
WORK_TYPES = (
    "episode.started",
    "episode.ended",
    "stage.decided",
    "pbi.attempted",
    "review.judged",
    "test.judged",
    "question.asked",
    "question.answered",
)

DEFAULT_RULES: tuple[Rule, ...] = (
    Before(
        name="answer-without-presentation",
        event="question.answered",
        requires=PRESENTED,
        match=("kind", "topic"),
        needs_source=True,
        viewpoint="safety",
        severity="major",
        suggestion="Show the question to the user and record the presentation before answer.",
    ),
    Before(
        name="answer-without-question",
        event="question.answered",
        requires="question.asked",
        match=("kind",),
        viewpoint="safety",
        severity="minor",
        suggestion="Record question.asked when the question is raised, before it is answered.",
    ),
    Before(
        name="episode-end-without-start",
        event="episode.ended",
        requires="episode.started",
        match=("episode",),
        suggestion="Record episode.started when the conductor launches the episode.",
    ),
    Before(
        name="episode-cost-without-start",
        event="episode.cost",
        requires="episode.started",
        match=("episode",),
        consume=False,
        severity="info",
        suggestion="Record episode.started for every episode whose cost is measured.",
    ),
    Before(
        name="run-end-without-start",
        event="run.ended",
        requires="run.started",
        severity="info",
        suggestion="Record run.started at conductor start, or start the log before the run.",
    ),
    NotAfter(
        name="work-after-run-end",
        end="run.ended",
        types=WORK_TYPES,
        suggestion="Do not record work after the run ended; start a new run instead.",
    ),
)


@dataclass(frozen=True)
class Break:
    """One event that broke one rule."""

    rule: Rule
    event: Event

    def to_dict(self) -> dict:
        """The break as a body-free row of `order.jsonl`."""
        return {
            "rule": self.rule.name,
            "viewpoint": self.rule.viewpoint,
            "severity": self.rule.severity,
            "run_id": self.event.run_id,
            "type": self.event.type,
            "ts": self.event.ts,
            "seq": self.event.seq,
            "pbi": self.event.pbi,
            "source": self.event.source,
            "line": self.event.line,
        }


def _before(earlier: Event, later: Event) -> bool:
    """Whether `earlier` came first; within one second, a line of another file counts as first."""
    if earlier.ts != later.ts:
        return earlier.ts < later.ts
    if earlier.source != later.source:
        return True
    return earlier.line < later.line


def _agrees(a: Event, b: Event, keys: Sequence[str]) -> bool:
    """Whether the two events agree on every key both of them carry."""
    for key in keys:
        left, right = a.data.get(key), b.data.get(key)
        if left is not None and right is not None and left != right:
            return False
    return True


def check(
    events: Iterable[Event], rules: Sequence[Rule] = DEFAULT_RULES
) -> tuple[list[Break], list[str]]:
    """The breaks of `rules` over `events`, and the names of the rules that were skipped."""
    by_run: dict[str, list[Event]] = defaultdict(list)
    seen_types: set[str] = set()
    for event in events:
        by_run[event.run_id].append(event)
        seen_types.add(event.type)
    breaks: list[Break] = []
    skipped: list[str] = []
    for rule in rules:
        if isinstance(rule, Before) and rule.needs_source and rule.requires not in seen_types:
            skipped.append(rule.name)
            continue
        for run_id in sorted(by_run):
            run = sorted(by_run[run_id], key=lambda e: (e.ts, e.source, e.line))
            if isinstance(rule, Before):
                breaks.extend(Break(rule, e) for e in _check_before(rule, run))
            else:
                breaks.extend(Break(rule, e) for e in _check_not_after(rule, run))
    breaks.sort(key=lambda b: (b.event.ts, b.event.source, b.event.line, b.rule.name))
    return breaks, skipped


def _check_before(rule: Before, run: list[Event]) -> list[Event]:
    """The `rule.event` events of one run with no earlier `rule.requires` to back them."""
    pool = [e for e in run if e.type == rule.requires]
    used: set[int] = set()
    broken = []
    for event in run:
        if event.type != rule.event:
            continue
        backer = next(
            (
                index
                for index, earlier in enumerate(pool)
                if index not in used
                and _before(earlier, event)
                and _agrees(earlier, event, rule.match)
            ),
            None,
        )
        if backer is None:
            broken.append(event)
        elif rule.consume:
            used.add(backer)
    return broken


def _check_not_after(rule: NotAfter, run: list[Event]) -> list[Event]:
    """The events of `rule.types` that came after the first `rule.end` of one run."""
    end = next((e for e in run if e.type == rule.end), None)
    if end is None:
        return []
    return [e for e in run if e.type in rule.types and _before(end, e)]
