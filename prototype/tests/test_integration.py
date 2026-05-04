"""End-to-end integration tests.

Run the full pipeline against the seeded inputs and pin the shape and
headline counts of the output. These tests are what an auditor reads to
confirm "this slice still does what the docs say it does."
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT.parent / "inputs"


class TestEndToEnd(unittest.TestCase):
    """Invokes main.py as a subprocess and verifies the artifacts on disk."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.outdir = Path(cls.tmp.name)
        result = subprocess.run(
            [sys.executable, "main.py", "--inputs", str(INPUTS), "--out", str(cls.outdir)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        cls.stdout = result.stdout
        cls.stderr = result.stderr
        cls.returncode = result.returncode

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_exits_zero(self):
        self.assertEqual(
            self.returncode, 0, f"main.py failed:\n{self.stderr}"
        )

    def test_writes_three_artifacts(self):
        for name in ("decisions.json", "audit_log.jsonl", "report.md"):
            self.assertTrue(
                (self.outdir / name).exists(), f"missing {name}"
            )

    def test_decisions_json_shape(self):
        records = json.loads((self.outdir / "decisions.json").read_text())
        self.assertEqual(len(records), 10, "expected 10 JEs")

        required_keys = {
            "je_id", "description", "decision",
            "findings", "explanation", "suggestions", "proposed_fix",
        }
        for r in records:
            self.assertEqual(
                required_keys, set(r.keys()),
                f"unexpected schema for {r.get('je_id')}: {set(r.keys()) ^ required_keys}",
            )
            self.assertIn(r["decision"], {"accept", "quarantine", "reject"})

    def test_headline_counts_match_docs(self):
        """The README and ARCHITECTURE.md advertise these counts; if they
        drift, the docs need updating too."""
        records = json.loads((self.outdir / "decisions.json").read_text())
        counts = {"accept": 0, "quarantine": 0, "reject": 0}
        for r in records:
            counts[r["decision"]] += 1
        self.assertEqual(
            counts, {"accept": 8, "quarantine": 2, "reject": 0},
            "headline counts changed — update README.md and ARCHITECTURE.md",
        )

    def test_je002_warn_band_with_proposal(self):
        """JE-002 ($3,500 imbalance) falls in the warn band per Amit's policy:
        accepted with reconciliation note; proposed fix still surfaced."""
        records = json.loads((self.outdir / "decisions.json").read_text())
        je002 = next(r for r in records if r["je_id"] == "JE-002")
        self.assertEqual(je002["decision"], "accept")
        codes = {f["code"] for f in je002["findings"]}
        self.assertIn("IMBALANCE_WARN", codes)
        self.assertNotIn("UNBALANCED", codes)
        pf = je002["proposed_fix"]
        self.assertIsNotNone(pf)
        self.assertEqual(pf["field"], "credit")
        self.assertEqual(pf["proposed_amount"], 28500.0)

    def test_audit_log_replays(self):
        """Append-only event log; one event per finding plus one decision per JE.
        Also: tier-a auto-corrections (none in seeded data) would emit an
        additional `auto_correction` event — see test_audit_log_emits_auto_correction."""
        lines = (self.outdir / "audit_log.jsonl").read_text().strip().splitlines()
        events = [json.loads(line) for line in lines]
        decisions = [e for e in events if e["event"] == "decision"]
        findings = [e for e in events if e["event"] == "finding"]
        self.assertEqual(len(decisions), 10)
        # Every JE that yielded a non-accept decision OR a warn finding should
        # have at least one finding event.
        je_ids_with_findings = {f["je_id"] for f in findings}
        non_clean_decisions = {
            d["je_id"] for d in decisions if d["decision"] != "accept"
        }
        # JE-002 is accept-with-warn so must also have a finding event.
        self.assertIn("JE-002", je_ids_with_findings)
        self.assertTrue(non_clean_decisions.issubset(je_ids_with_findings))


class TestAutoCorrectionEvent(unittest.TestCase):
    """Tier-a auto-corrections must emit a structured `auto_correction` event
    per Amit Patel email 2026-04-30. None of the seeded JEs fall in tier-a, so
    we synthesise a sub-$500 imbalance and run the pipeline directly."""

    def test_audit_log_emits_auto_correction(self):
        import sys as _sys
        _sys.path.insert(0, str(ROOT))
        from adjuster.loader import load_coa  # noqa: E402
        from adjuster.models import JELine, JournalEntry  # noqa: E402
        from adjuster.deterministic.pipeline import decide_one  # noqa: E402

        coa = load_coa(INPUTS / "chart_of_accounts.csv")
        # $250 imbalance: well inside tier-a ($500 threshold).
        je = JournalEntry(
            id="JE-TIER-A",
            description="synthetic tier-a rounding",
            date="2024-12-31",
            source="test",
            lines=(
                JELine(account="6100", debit=10_000.0, credit=0.0, memo=""),
                JELine(account="2120", debit=0.0, credit=9_750.0, memo=""),
            ),
        )
        record = decide_one(je, coa, use_llm=False)
        self.assertEqual(record.decision, "accept")
        codes = {f.code for f in record.findings}
        self.assertIn("AUTO_CORRECTED", codes)

        with tempfile.TemporaryDirectory() as tmp:
            from main import write_audit_log  # noqa: E402
            log_path = Path(tmp) / "audit_log.jsonl"
            write_audit_log([record], log_path)
            events = [json.loads(l) for l in log_path.read_text().strip().splitlines()]
            kinds = [e["event"] for e in events]
            self.assertIn("auto_correction", kinds)
            ac = next(e for e in events if e["event"] == "auto_correction")
            self.assertEqual(ac["je_id"], "JE-TIER-A")
            self.assertEqual(ac["source_entry"], "JE-TIER-A")
            self.assertAlmostEqual(ac["delta"], 250.0)
            self.assertEqual(ac["action"], "auto_corrected_with_plug")
            self.assertEqual(ac["rounding_account"], "7300")
            self.assertIsNotNone(ac["plug"])


if __name__ == "__main__":
    unittest.main()
