"""Vector store for the LangGraph LLM-mode Adjuster.

Two Chroma collections:

- ``adjuster_policy`` — every materiality rule from ASSUMPTIONS.md A1 + the
  hard structural rules, one snippet each. Queried by structural / decide
  nodes to ground LLM output in policy text rather than asking the model to
  recite from memory.
- ``coa_accounts`` — every leaf account from the COA, one doc each, with
  account_code + name + parent + statement type as the embedding text.
  Queried by the existence node when the LLM needs to disambiguate an
  unknown code.

Storage is SQLite under ``prototype/output/chroma/`` — no server, no extra
moving parts. Seeded lazily on first call so import cost is zero when graph
mode is not used.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Account


# prototype/output/chroma/ — three .parent calls because this file is now
# at prototype/adjuster/langgraph/rag.py
_CHROMA_DIR = Path(__file__).resolve().parents[2] / "output" / "chroma"

_POLICY_DOCS = [
    {
        "id": "tier-a-auto-correct",
        "text": (
            "Tier A — auto-correct. When the absolute imbalance between "
            "total debits and total credits is at most 500 USD, the entry "
            "is accepted. A balancing plug is recorded against the "
            "configured rounding account (default 7300 FX Gain/Loss - "
            "Realized) and a structured `auto_correction` event is written "
            "to the audit log carrying the source entry, the delta, and "
            "the action. Per Amit Patel email 2026-04-30."
        ),
        "metadata": {"tier": "a", "topic": "imbalance", "halts": "no"},
    },
    {
        "id": "tier-b-warn-band",
        "text": (
            "Tier B — warn band. When the absolute imbalance is greater "
            "than 500 and at most 10,000 USD, the entry is accepted with "
            "a reconciliation note. Replace the raw UNBALANCED finding "
            "with IMBALANCE_WARN (severity warning). Reviewer sign-off is "
            "required before the period closes. Surface a proposed "
            "single-line fix when one exists."
        ),
        "metadata": {"tier": "b", "topic": "imbalance", "halts": "no"},
    },
    {
        "id": "tier-c-halt",
        "text": (
            "Tier C — halt. When the absolute imbalance exceeds 10,000 "
            "USD, or the Balance Sheet does not foot (Assets ≠ Liabilities "
            "+ Equity), the run halts and a structured error is surfaced "
            "to the user. The JE is rejected; preserve the raw UNBALANCED "
            "finding."
        ),
        "metadata": {"tier": "c", "topic": "imbalance", "halts": "yes"},
    },
    {
        "id": "hard-error-header-posting",
        "text": (
            "Postings to header accounts are rejected. Headers exist for "
            "roll-up only and cannot receive entries directly. Move the "
            "line to a leaf account under that header."
        ),
        "metadata": {"tier": "hard", "topic": "structural", "halts": "yes"},
    },
    {
        "id": "hard-error-negative-amount",
        "text": (
            "Lines with negative debit or credit amounts are rejected. "
            "Both amounts must be non-negative; sign is determined by the "
            "side (debit vs credit), not by the magnitude."
        ),
        "metadata": {"tier": "hard", "topic": "structural", "halts": "yes"},
    },
    {
        "id": "hard-error-debit-and-credit-on-same-line",
        "text": (
            "A single JE line cannot have both a non-zero debit and a "
            "non-zero credit. Reject the entry; this indicates a "
            "structurally malformed posting."
        ),
        "metadata": {"tier": "hard", "topic": "structural", "halts": "yes"},
    },
    {
        "id": "hard-error-date-out-of-period",
        "text": (
            "JE dates outside the reporting period are rejected. The "
            "current period is 2024-Q4 (2024-10-01 to 2024-12-31). "
            "Out-of-period entries belong to a different close."
        ),
        "metadata": {"tier": "hard", "topic": "period", "halts": "yes"},
    },
    {
        "id": "quarantine-unmapped-account",
        "text": (
            "Entries that reference an account code missing from the COA "
            "are quarantined for human review. The agent never auto-creates "
            "COA nodes — Amit Patel 2026-04-30. Surface ranked candidate "
            "mappings with confidence scores from the COA."
        ),
        "metadata": {"tier": "quarantine", "topic": "mapping", "halts": "no"},
    },
    {
        "id": "quarantine-circular-intercompany",
        "text": (
            "An entry that debits and credits the same account with "
            "offsetting amounts has zero economic effect. Technically "
            "valid double-entry but substantively meaningless. Quarantine "
            "for human review; do not silently accept."
        ),
        "metadata": {"tier": "quarantine", "topic": "structural", "halts": "no"},
    },
    {
        "id": "audit-log-requirement",
        "text": (
            "Every threshold-based correction must appear in a "
            "machine-readable audit log carrying the source entry, the "
            "delta, and the action taken. No silent fixes. Per Amit Patel "
            "email 2026-04-30."
        ),
        "metadata": {"tier": "policy", "topic": "audit", "halts": "no"},
    },
]


def _client():
    """Lazy import so chromadb is only loaded when graph mode runs."""
    import chromadb
    _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(_CHROMA_DIR))


def _ensure_policy_collection(client) -> "object":
    coll = client.get_or_create_collection("adjuster_policy")
    if coll.count() < len(_POLICY_DOCS):
        # Re-seed only when stale (e.g. version bump). Idempotent enough
        # for the take-home; production would version on a hash.
        coll.upsert(
            ids=[d["id"] for d in _POLICY_DOCS],
            documents=[d["text"] for d in _POLICY_DOCS],
            metadatas=[d["metadata"] for d in _POLICY_DOCS],
        )
    return coll


def _ensure_coa_collection(client, coa: dict[str, "Account"]) -> "object":
    coll = client.get_or_create_collection("coa_accounts")
    if coll.count() == len(coa):
        return coll  # already seeded for this COA
    docs, ids, metadatas = [], [], []
    for acct in coa.values():
        ids.append(acct.code)
        docs.append(
            f"Account {acct.code} {acct.name}. "
            f"Type: {acct.account_type}. Parent: {acct.parent_code or 'none'}. "
            f"Statement: {acct.statement or 'unspecified'}. "
            f"Cash-flow category: {acct.cf_category or 'unspecified'}. "
            f"Normal balance: {acct.normal_balance or 'unspecified'}."
        )
        metadatas.append(
            {
                "code": acct.code,
                "name": acct.name,
                "type": acct.account_type,
                "statement": acct.statement or "",
            }
        )
    # upsert (handles re-seed when COA grows / shrinks)
    coll.upsert(ids=ids, documents=docs, metadatas=metadatas)
    return coll


# ---------------------------------------------------------------------------
# Public query surface — small, deliberate. No `query_one_thing_unstructured`
# helper; nodes call these directly with named args.
# ---------------------------------------------------------------------------

def query_policy(query: str, *, n_results: int = 3) -> list[dict]:
    """Return top-K policy snippets for a free-text query."""
    coll = _ensure_policy_collection(_client())
    res = coll.query(query_texts=[query], n_results=n_results)
    return [
        {"id": _id, "text": doc, "metadata": md}
        for _id, doc, md in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0]
        )
    ]


def query_coa(
    query: str, *, coa: dict[str, "Account"], n_results: int = 5
) -> list[dict]:
    """Return top-K COA accounts most semantically similar to the query."""
    client = _client()
    coll = _ensure_coa_collection(client, coa)
    res = coll.query(query_texts=[query], n_results=n_results)
    return [
        {"code": _id, "text": doc, "metadata": md}
        for _id, doc, md in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0]
        )
    ]


def reset() -> None:
    """Delete the persisted collections — useful for tests / re-seeding."""
    import shutil
    if _CHROMA_DIR.exists():
        shutil.rmtree(_CHROMA_DIR)
