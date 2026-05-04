"""LangGraph + Chroma RAG Adjuster — the demonstration variant.

This subpackage is intentionally separate from `deterministic` so the
import graph itself reflects the architectural argument: the deterministic
path has zero dependencies on LangGraph or Chroma; the LangGraph path
depends on both.

Heavy deps live in `prototype/requirements-llm.txt`.
"""
from .graph import run_graph  # noqa: F401
