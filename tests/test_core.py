"""Units, signatures and order rules, without harness2."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from harness2_analyzer import atelier, h2trace, order
from harness2_analyzer.ids import norm_error, safe_id, sig_id_of
from harness2_analyzer.signature import signature_rows, summarize, version_of
from harness2_analyzer.units import check_unit, make_unit, root_cause_of

from .helpers import AT_RUN, CASE, REAL_PARTS, REAL_SIG, at_event, atelier_repo, harness2_run


class IdsTest(unittest.TestCase):
    def test_sig_id_matches_atelier(self):
        # The parts and id of a real pbi.attempted line: the same rule as record_run.sig_id_of.
        self.assertEqual(sig_id_of(REAL_PARTS), REAL_SIG)

    def test_norm_error_drops_run_specific_pieces(self):
        a = norm_error("run 0123456789abcdef failed at /tmp/a/b.py line 12 at 2026-11-10T00:00:01Z")
        b = norm_error(
            "run fedcba9876543210 failed at /home/x/y.py line 99 at 2026-11-11T09:00:00Z"
        )
        self.assertEqual(a, b)
        self.assertEqual(norm_error({"sha256": "ab", "chars": 900}), "<H>")
        self.assertEqual(norm_error(""), "")

    def test_safe_id_hashes_free_text(self):
        self.assertEqual(safe_id("PBI-1"), "PBI-1")
        self.assertTrue(safe_id("two words").startswith("h:"))
        self.assertIsNone(safe_id(None))


class UnitTest(unittest.TestCase):
    def unit(self, **fields):
        base = dict(unit_id="u:1", system="atelier", run_id="r", task_id="t", split="unsplit")
        base.update(fields)
        return make_unit(**base)

    def test_check_unit_accepts_identifiers(self):
        check_unit(self.unit(condition={"model": "sonnet", "k": 2}, outcome={"passed": True}))

    def test_check_unit_rejects_free_text(self):
        with self.assertRaises(ValueError):
            check_unit(self.unit(condition={"note": "this is a sentence"}))
        with self.assertRaises(ValueError):
            check_unit(self.unit(split="test"))
        with self.assertRaises(ValueError):
            make_unit(text="x")

    def test_root_cause_mapping(self):
        self.assertEqual(root_cause_of(["model"]), "agent")
        self.assertEqual(root_cause_of(["convention", "scope"]), "harness")
        self.assertEqual(root_cause_of(["spec", "model"]), "unknown")
        self.assertEqual(root_cause_of(["flaky"]), "noise")
        self.assertIsNone(root_cause_of([]))


class Harness2UnitsTest(unittest.TestCase):
    def test_one_unit_per_rollout_with_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = harness2_run(Path(tmp) / "t1")
            units = h2trace.units_from_spans(
                h2trace.read_traces([path]),
                split_index={"lab/t1": "heldout"},
                default_change="base",
            )
        for unit in units:
            check_unit(unit)
        by_role = {u["condition"]["role"]: u for u in units}
        self.assertEqual(set(by_role), {"base", "candidate"})
        base, cand = by_role["base"], by_role["candidate"]
        self.assertTrue(base["outcome"]["passed"])
        self.assertEqual(cand["outcome"]["status"], "fail")
        parts = cand["signature"]["parts"]
        self.assertEqual(parts["where"], "mode:adapt")
        self.assertTrue(parts["fell_back"])
        self.assertEqual(parts["attempts"], "3-up")
        self.assertEqual(parts["diff_types"], ["src/.py"])
        self.assertEqual(len(parts["err"]), 12)
        self.assertEqual(cand["signature"]["sig_id"], sig_id_of(parts))
        self.assertEqual(cand["split"], "heldout")
        self.assertEqual(cand["change_id"], "base")
        self.assertEqual(cand["condition"]["model"], "m1")
        self.assertEqual(cand["unit_id"], f"h2:{cand['run_id']}:cand")

    def test_same_failure_in_two_runs_has_one_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = harness2_run(Path(tmp) / "a", run="a" * 32, fail_message="boom at /x/y/z.py:1")
            b = harness2_run(Path(tmp) / "b", run="b" * 32, fail_message="boom at /q/r/s.py:7")
            units = h2trace.units_from_spans(h2trace.read_traces([a, b]))
        sigs = {u["signature"]["sig_id"] for u in units if u["condition"]["role"] == "candidate"}
        self.assertEqual(len(sigs), 1)
        trials = sorted(u["trial"] for u in units if u["condition"]["role"] == "candidate")
        self.assertEqual(trials, [1, 2])


class AtelierUnitsTest(unittest.TestCase):
    def test_both_line_shapes(self):
        local = at_event("question.asked", "t", {"kind": "approve"}, change_id="c1")
        envelope = {
            "schema": "harness.event/1",
            "data_schema": "atelier.event/1",
            "ts": "t",
            "run_id": "r",
            "seq": 2,
            "stage": None,
            "pbi": None,
            "type": "question.asked",
            "data": {"kind": "approve"},
            "split": "train",
        }
        self.assertEqual(atelier.event_of(local).links, {"change_id": "c1"})
        self.assertEqual(atelier.event_of(envelope).links, {"split": "train"})
        self.assertIsNone(atelier.event_of({"schema": "harness.event/1", "data_schema": "x/1"}))

    def test_units_from_events_and_old_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = atelier.read_records([atelier_repo(Path(tmp))])
        self.assertEqual(records.skipped, 1)
        units = {u["unit_id"]: u for u in atelier.units_from_records(records, default_change="pre")}
        for unit in units.values():
            check_unit(unit)
        unit = units[f"at:{AT_RUN}:PBI-1:1"]
        self.assertEqual(unit["task_id"], CASE)
        self.assertEqual(unit["signature"]["sig_id"], REAL_SIG)
        self.assertEqual(unit["outcome"]["verify"], {"passed": 2, "total": 2})
        self.assertEqual(unit["root_cause"], "agent")
        self.assertEqual(unit["group"], "feature/skill/files-2-3")
        self.assertEqual(unit["condition"]["config_fp"], "8fb964a69ff4")
        self.assertEqual(unit["change_id"], "pre")
        # The runs.jsonl row the events do not cover becomes a unit of its own.
        old = units["at:old-run:PBI-2:1"]
        self.assertTrue(old["outcome"]["passed"])
        self.assertEqual(old["outcome"]["tokens"], 999)
        self.assertEqual(old["signature"]["parts"]["outcome"], "pass")

    def test_runs_signature_matches_the_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = atelier.read_records([atelier_repo(Path(tmp))])
        row = next(r for r in records.runs if r["run_id"] == AT_RUN)
        self.assertEqual(atelier.signature_of_run(row)["sig_id"], REAL_SIG)


class SignatureTest(unittest.TestCase):
    def test_summary_counts_and_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = atelier.read_records([atelier_repo(Path(tmp))])
        units = atelier.units_from_records(records)
        rows, warnings = signature_rows(units)
        self.assertEqual(warnings, [])
        summary = summarize(rows)
        self.assertEqual(summary["units"], {"atelier": 2})
        first = summary["sigs"][0]
        self.assertEqual((first["sig_id"], first["failing"]), (REAL_SIG, 1))
        self.assertEqual(first["first_version"], "cfg:8fb964a69ff4")

    def test_version_order(self):
        self.assertEqual(version_of({"change_id": "c", "condition": {}}), "c")
        self.assertEqual(version_of({"condition": {"prompt": "p"}}), "prompt:p")
        self.assertEqual(version_of({"condition": {}}), "-")

    def test_mismatched_sig_id_warns(self):
        unit = make_unit(
            unit_id="u",
            system="atelier",
            run_id="r",
            task_id="t",
            split="unsplit",
            signature={"sig_id": "000000000000", "parts": REAL_PARTS},
            outcome={"passed": False},
        )
        _, warnings = signature_rows([unit])
        self.assertEqual(len(warnings), 1)


class OrderTest(unittest.TestCase):
    def events(self, rows, presentations=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            atelier_repo(root, presentations=presentations)
            (root / "docs/pbi/events.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows), "utf-8"
            )
            return atelier.read_records([root]).events

    def test_default_rules_on_the_fixture(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = atelier.read_records([atelier_repo(Path(tmp))])
        breaks, skipped = order.check(records.events)
        self.assertEqual(
            sorted(b.rule.name for b in breaks),
            ["answer-without-question", "run-end-without-start", "work-after-run-end"],
        )
        self.assertEqual(skipped, ["answer-without-presentation"])
        answered = next(b for b in breaks if b.rule.name == "answer-without-question")
        self.assertEqual(answered.event.data["kind"], "decision")

    def test_presentation_rule_runs_once_presentations_exist(self):
        rows = [
            at_event("question.asked", "2026-01-01T00:00:00Z", {"kind": "approve"}),
            at_event("question.answered", "2026-01-01T00:00:05Z", {"kind": "approve"}),
            at_event("question.asked", "2026-01-01T00:01:00Z", {"kind": "decision"}),
            at_event("question.answered", "2026-01-01T00:01:05Z", {"kind": "decision"}),
        ]
        shown = [{"run_id": AT_RUN, "ts": "2026-01-01T00:00:05Z", "kind": "approve"}]
        breaks, skipped = order.check(self.events(rows, presentations=shown))
        self.assertEqual(skipped, [])
        missing = [b for b in breaks if b.rule.name == "answer-without-presentation"]
        self.assertEqual([b.event.data["kind"] for b in missing], ["decision"])

    def test_one_question_backs_one_answer(self):
        rows = [
            at_event("question.asked", "2026-01-01T00:00:00Z", {"kind": "approve"}),
            at_event("question.answered", "2026-01-01T00:00:05Z", {"kind": "approve"}),
            at_event("question.answered", "2026-01-01T00:00:09Z", {"kind": "approve"}),
        ]
        breaks, _ = order.check(self.events(rows))
        self.assertEqual([b.event.ts for b in breaks], ["2026-01-01T00:00:09Z"])

    def test_runs_are_checked_apart(self):
        rows = [
            at_event("question.asked", "2026-01-01T00:00:00Z", {"kind": "approve"}, run="r1"),
            at_event("question.answered", "2026-01-01T00:00:05Z", {"kind": "approve"}, run="r2"),
        ]
        breaks, _ = order.check(self.events(rows))
        self.assertEqual(
            [(b.rule.name, b.event.run_id) for b in breaks], [("answer-without-question", "r2")]
        )


if __name__ == "__main__":
    unittest.main()
