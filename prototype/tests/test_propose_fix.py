"""Direct unit tests for the tier-b proposal heuristic.

Covers the four shapes the function has to handle:
- credits short by N (debit-heavy)
- debits short by N (credit-heavy)
- multiple lines on the short side (lower confidence, picks largest)
- no lines on the short side (returns None)
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adjuster.models import JELine, JournalEntry  # noqa: E402
from adjuster.validators import propose_balance_fix  # noqa: E402


def _je(*lines: tuple[str, float, float]) -> JournalEntry:
    return JournalEntry(
        id="JE-TEST",
        description="test",
        date="2024-12-31",
        source="test",
        lines=tuple(JELine(account=a, debit=d, credit=c, memo="") for a, d, c in lines),
    )


class TestProposeBalanceFix(unittest.TestCase):
    def test_balanced_returns_none(self):
        je = _je(("1110", 100.0, 0.0), ("2110", 0.0, 100.0))
        self.assertIsNone(propose_balance_fix(je))

    def test_credits_short(self):
        # debits 28,500; credits 25,000 → propose credit line up by 3,500
        je = _je(("6300", 28500.0, 0.0), ("6310", 0.0, 25000.0))
        pf = propose_balance_fix(je)
        self.assertIsNotNone(pf)
        self.assertEqual(pf.field, "credit")
        self.assertEqual(pf.line_index, 1)
        self.assertEqual(pf.proposed_amount, 28500.0)
        self.assertEqual(pf.confidence, 0.85)

    def test_debits_short(self):
        # credits 1,000; debits 800 → propose debit line up by 200
        je = _je(("6300", 800.0, 0.0), ("2110", 0.0, 1000.0))
        pf = propose_balance_fix(je)
        self.assertIsNotNone(pf)
        self.assertEqual(pf.field, "debit")
        self.assertEqual(pf.line_index, 0)
        self.assertEqual(pf.proposed_amount, 1000.0)

    def test_multiple_lines_lowers_confidence_and_picks_largest(self):
        # Two credit lines short by 500 total; pick the larger.
        je = _je(
            ("6300", 1500.0, 0.0),
            ("6310", 0.0, 600.0),
            ("6320", 0.0, 400.0),
        )
        pf = propose_balance_fix(je)
        self.assertIsNotNone(pf)
        self.assertEqual(pf.field, "credit")
        self.assertEqual(pf.line_index, 1)  # the 600.0 line, not 400.0
        self.assertEqual(pf.proposed_amount, 1100.0)
        self.assertLess(pf.confidence, 0.85)

    def test_no_short_side_lines_returns_none(self):
        # Pathological: only debit lines but credits exceed debits by some
        # off-balance. propose_balance_fix should give up cleanly.
        je = _je(("6300", 100.0, 0.0), ("6310", 50.0, 0.0))
        # diff = 150 - 0 = 150; credits short, but no credit lines exist.
        pf = propose_balance_fix(je)
        self.assertIsNone(pf)


if __name__ == "__main__":
    unittest.main()
