"""`h2a` end to end, and the `analyzer` evaluator through `harness2-evaluate`."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from harness2_analyzer import cli
from harness2_analyzer.ids import read_jsonl

from .helpers import SECRET, atelier_repo, harness2_run

try:
    from harness2.cli import evaluate as h2_evaluate
    from harness2.core import evaluate as h2_eval_core
except ImportError:  # pragma: no cover - harness2 is a declared dependency
    h2_evaluate = None


def run(argv: list[str]) -> tuple[int, str]:
    """Run `h2a` with these arguments; (exit code, stdout)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(argv)
    return code, out.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        harness2_run(self.root / "runs" / "t1")
        atelier_repo(self.root / "repo")
        self.out = self.root / "analysis"

    def tearDown(self):
        self.tmp.cleanup()

    def ingest(self, *extra: str) -> int:
        code, _ = run(
            [
                "ingest",
                "--runs",
                str(self.root / "runs"),
                "--atelier",
                str(self.root / "repo"),
                "--out",
                str(self.out),
                *extra,
            ]
        )
        return code

    def test_ingest_signature_report(self):
        self.assertEqual(self.ingest("--change-id", "pre-refactor"), 0)
        units = [row for _, row in read_jsonl(self.out / "units.jsonl")]
        self.assertEqual(
            sorted(u["system"] for u in units), ["atelier", "atelier", "harness2", "harness2"]
        )
        self.assertTrue(all(u["change_id"] for u in units))
        meta = json.loads((self.out / "ingest.json").read_text("utf-8"))
        self.assertEqual(meta["order_breaks"], 3)
        self.assertEqual(meta["order_skipped"], ["answer-without-presentation"])
        self.assertEqual(len(meta["traces"]), 1)
        self.assertEqual(run(["signature", "--dir", str(self.out)])[0], 0)
        summary = json.loads((self.out / "sig_summary.json").read_text("utf-8"))
        self.assertEqual(summary["units"], {"atelier": 2, "harness2": 2})
        self.assertEqual(run(["report", "--dir", str(self.out)])[0], 0)
        page = (self.out / "report.html").read_text("utf-8")
        self.assertIn("answer-without-question", page)
        self.assertIn("pre-refactor", page)
        self.assertNotIn("<script", page)
        for path in self.out.rglob("*"):
            if path.is_file():
                self.assertNotIn(SECRET, path.read_text("utf-8"), path)

    def test_split_manifest(self):
        manifest = self.root / "split.json"
        manifest.write_text(json.dumps({"train": ["lab/t1"], "test": []}), "utf-8")
        self.assertEqual(self.ingest("--split", str(manifest)), 0)
        units = [row for _, row in read_jsonl(self.out / "units.jsonl")]
        h2 = [u for u in units if u["system"] == "harness2"]
        self.assertTrue(all(u["split"] == "train" and u["split_set_id"] for u in h2))

    def test_errors(self):
        self.assertEqual(run(["ingest", "--out", str(self.out)])[0], 2)
        self.assertEqual(run(["ingest", "--runs", str(self.root / "nope")])[0], 2)
        self.assertEqual(run(["signature", "--dir", str(self.root / "empty")])[0], 2)


@unittest.skipIf(h2_evaluate is None, "harness2 is not installed")
class EvaluatorTest(unittest.TestCase):
    def test_entry_point_is_registered(self):
        self.assertIn("analyzer", h2_eval_core.names())
        evaluator = h2_eval_core.evaluator_for("analyzer", options={"min_repeat": 2})
        self.assertEqual([v.name for v in evaluator.viewpoints()], ["safety", "reproducibility"])

    def test_harness2_evaluate_shows_the_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a", "b", "c"):
                harness2_run(root / "runs" / name, run=name * 32, fail_message="boom")
            atelier_repo(root / "repo")
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                cli.main(
                    [
                        "ingest",
                        "--atelier",
                        str(root / "repo"),
                        "--out",
                        str(root / "runs" / "analysis"),
                    ]
                )
                code = h2_evaluate.main(
                    [
                        "--runs",
                        str(root / "runs"),
                        "--evaluators",
                        "rules,analyzer",
                        "--out",
                        str(root / "eval"),
                    ]
                )
            self.assertEqual(code, 0)
            result = json.loads((root / "eval" / "findings.json").read_text("utf-8"))
        self.assertEqual(result["errors"], [])
        keys = [f["key"] for f in result["findings"]]
        order = [k for k in keys if k.startswith("analyzer:order:")]
        self.assertEqual(len(order), 3)
        self.assertTrue(any(":answer-without-question:" in k for k in order))
        self.assertTrue(any(k.startswith("analyzer:repeat:harness2:") for k in keys))
        self.assertTrue(any(k.startswith("rules:") for k in keys))
        self.assertEqual(result["dropped"]["no_evidence"], 0)


if __name__ == "__main__":
    unittest.main()
