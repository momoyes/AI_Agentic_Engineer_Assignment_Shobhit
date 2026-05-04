"""Tests pin the deterministic validators against the seeded inputs.

These tests are the safety net: they prove the agent makes the right call on
every JE in the bundle without depending on an LLM."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adjuster.loader import load_adjustments, load_coa  # noqa: E402
from adjuster.deterministic import decide_batch  # noqa: E402

INPUTS = ROOT.parent / "inputs"


class TestSeededInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.coa = load_coa(INPUTS / "chart_of_accounts.csv")
        _, _, cls.entries = load_adjustments(INPUTS / "manual_adjustments.json")
        cls.records = {
            r.je_id: r for r in decide_batch(cls.entries, cls.coa, use_llm=False)
        }

    def test_je001_accept(self):
        self.assertEqual(self.records["JE-001"].decision, "accept")

    def test_je002_warn_band_accepts_with_note(self):
        """Tier-b ($500–$10k): $3,500 imbalance is accepted with a
        reconciliation note (per Amit Patel email 2026-04-30). The proposed
        fix is still surfaced for the reviewer."""
        r = self.records["JE-002"]
        self.assertEqual(r.decision, "accept")
        self.assertTrue(
            any(f.code == "IMBALANCE_WARN" for f in r.findings),
            "warn-band findings should carry IMBALANCE_WARN, not raw UNBALANCED",
        )
        self.assertFalse(
            any(f.code == "UNBALANCED" for f in r.findings),
            "raw UNBALANCED finding should be replaced by the policy outcome",
        )
        self.assertIsNotNone(r.proposed_fix, "tier-b should still propose a fix")
        pf = r.proposed_fix
        self.assertEqual(pf.field, "credit")
        self.assertEqual(pf.line_index, 1)
        self.assertEqual(pf.current_amount, 25000.0)
        self.assertEqual(pf.proposed_amount, 28500.0)
        self.assertGreaterEqual(pf.confidence, 0.55)

    def test_je003_accept(self):
        self.assertEqual(self.records["JE-003"].decision, "accept")

    def test_je004_accept(self):
        self.assertEqual(self.records["JE-004"].decision, "accept")

    def test_je005_quarantine_unmapped(self):
        r = self.records["JE-005"]
        self.assertEqual(r.decision, "quarantine")
        self.assertTrue(any(f.code == "UNMAPPED_ACCOUNT" for f in r.findings))
        self.assertTrue(r.suggestions, "should propose mapping candidates")
        # 6315 should suggest 6310 first
        self.assertEqual(r.suggestions[0].candidate_code, "6310")

    def test_je006_accept(self):
        self.assertEqual(self.records["JE-006"].decision, "accept")

    def test_je007_accept(self):
        self.assertEqual(self.records["JE-007"].decision, "accept")

    def test_je008_quarantine_circular(self):
        r = self.records["JE-008"]
        self.assertEqual(r.decision, "quarantine")
        self.assertTrue(any(f.code == "CIRCULAR_NET_ZERO" for f in r.findings))

    def test_je009_accept(self):
        self.assertEqual(self.records["JE-009"].decision, "accept")

    def test_je010_accept(self):
        self.assertEqual(self.records["JE-010"].decision, "accept")


if __name__ == "__main__":
    unittest.main()
