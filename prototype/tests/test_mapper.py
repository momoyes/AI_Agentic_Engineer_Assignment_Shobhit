"""Tests for the Mapper interface stub.

The Mapper itself is the production interface from ARCHITECTURE.md §2; the
prototype ships only the prefix-similarity selector. These tests pin the
bucket-routing logic and the contract a downstream Adjuster sees."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adjuster.loader import load_coa  # noqa: E402
from adjuster.mapper import Mapper, MapperContext  # noqa: E402


class TestMapper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.coa = load_coa(ROOT.parent / "inputs" / "chart_of_accounts.csv")

    def test_je005_routes_to_suggest(self):
        """6315 has 6310 as a 75% prefix match — between min_confidence (0.55)
        and auto_accept_above (0.95), so it should suggest, not auto-accept."""
        m = Mapper(self.coa)
        d = m.map("6315", MapperContext(account_name_hint="Conf travel"))
        self.assertEqual(d.bucket, "suggest")
        self.assertEqual(d.chosen_code, "6310")
        self.assertGreaterEqual(d.confidence, 0.55)
        self.assertLess(d.confidence, 0.95)
        self.assertTrue(d.candidates)

    def test_no_candidates_escalates(self):
        m = Mapper(self.coa)
        d = m.map("ZZZZ", MapperContext())
        self.assertEqual(d.bucket, "escalate")
        self.assertIsNone(d.chosen_code)
        self.assertEqual(d.candidates, [])

    def test_auto_accept_threshold_is_strict(self):
        """An exact code match would score 1.0 and route to auto-accept."""
        m = Mapper(self.coa)
        # 6310 is in the COA exactly, so generate_candidates would pick it
        # with confidence 1.0.
        d = m.map("6310", MapperContext())
        self.assertEqual(d.bucket, "auto-accept")
        self.assertEqual(d.chosen_code, "6310")


if __name__ == "__main__":
    unittest.main()
