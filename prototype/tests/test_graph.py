"""LangGraph + Chroma RAG variant of the Adjuster.

These tests are skipped when no LLM provider key is set, since the graph
mode insists on a real model — it has no template fallback by design.

When a key *is* present we verify two contracts:

1. **Shape parity.** `run_graph` returns a `DecisionRecord` with the same
   fields the rest of the pipeline produces, plus a `trace` attribute that
   carries per-node telemetry (provider, model, token counts, durations).
2. **Structural correctness on the clean cases.** The clean entries
   (JE-001, JE-003, JE-006, JE-007, JE-009, JE-010) should still come out
   `accept`. The graph variant may *differ* on JE-002 / JE-005 / JE-008 —
   that divergence is itself the demonstration point and is captured in
   the Streamlit Compare view, not asserted here.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INPUTS = ROOT.parent / "inputs"


def _has_provider() -> bool:
    return bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    )


@unittest.skipUnless(
    _has_provider(),
    "Set ANTHROPIC_API_KEY or OPENAI_API_KEY to run graph-mode tests; "
    "graph mode has no template fallback.",
)
class TestLangGraphAdjuster(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from adjuster.loader import load_adjustments, load_coa  # noqa: E402
        from adjuster.langgraph.graph import run_graph  # noqa: E402

        cls.coa = load_coa(INPUTS / "chart_of_accounts.csv")
        _, _, cls.entries = load_adjustments(INPUTS / "manual_adjustments.json")
        cls.records = {e.id: run_graph(e, cls.coa) for e in cls.entries}

    def test_shape_parity(self):
        for je_id, r in self.records.items():
            self.assertIsNotNone(r.decision, f"{je_id} missing decision")
            self.assertIn(r.decision, {"accept", "quarantine", "reject"})
            self.assertIsInstance(r.findings, list)
            self.assertIsNotNone(r.explanation, f"{je_id} missing explanation")
            self.assertTrue(
                hasattr(r, "trace"),
                f"{je_id} missing graph trace; needed for the Streamlit demo",
            )
            # At minimum, the structural / existence / semantic / aggregate /
            # decide / explain nodes should each have appended one entry —
            # six total (or fewer if a node short-circuited on error).
            self.assertGreaterEqual(len(r.trace), 4, f"{je_id} trace too thin")

    def test_clean_cases_still_accept(self):
        clean = ["JE-001", "JE-003", "JE-006", "JE-007", "JE-009", "JE-010"]
        for je_id in clean:
            self.assertEqual(
                self.records[je_id].decision, "accept",
                f"{je_id} should still accept under graph mode",
            )

    def test_trace_carries_provider_and_tokens(self):
        any_telemetry = False
        for r in self.records.values():
            for entry in r.trace:
                if "provider" in entry and "input_tokens" in entry:
                    any_telemetry = True
                    self.assertGreater(entry["input_tokens"], 0)
        self.assertTrue(any_telemetry, "no node emitted token telemetry")


if __name__ == "__main__":
    unittest.main()
