"""Domain dataclasses. Plain stdlib — no Pydantic dependency."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal


Decision = Literal["accept", "reject", "quarantine"]
Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Account:
    code: str
    name: str
    account_type: str
    parent_code: str
    statement: str
    cf_category: str
    normal_balance: str


@dataclass(frozen=True)
class JELine:
    account: str
    debit: float
    credit: float
    memo: str


@dataclass(frozen=True)
class JournalEntry:
    id: str
    description: str
    date: str
    source: str
    lines: tuple[JELine, ...]

    @property
    def total_debit(self) -> float:
        return round(sum(l.debit for l in self.lines), 2)

    @property
    def total_credit(self) -> float:
        return round(sum(l.credit for l in self.lines), 2)


@dataclass(frozen=True)
class Finding:
    code: str            # e.g. "UNBALANCED", "UNMAPPED_ACCOUNT"
    severity: Severity
    message: str
    je_id: str
    line_index: int | None = None
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MappingSuggestion:
    candidate_code: str
    candidate_name: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class ProposedFix:
    """A code-generated proposal for an unbalanced JE. Names the line and the
    target amount; the LLM (if used) only writes the rationale prose. The
    proposal is never applied automatically — it is surfaced to a human in
    the quarantine queue."""
    line_index: int
    field: str                  # "debit" or "credit"
    current_amount: float
    proposed_amount: float
    rationale: str
    confidence: float


@dataclass
class DecisionRecord:
    je_id: str
    description: str
    decision: Decision
    findings: list[Finding]
    explanation: str = ""
    suggestions: list[MappingSuggestion] = field(default_factory=list)
    proposed_fix: ProposedFix | None = None

    def to_json(self) -> dict:
        return {
            "je_id": self.je_id,
            "description": self.description,
            "decision": self.decision,
            "findings": [asdict(f) for f in self.findings],
            "explanation": self.explanation,
            "suggestions": [asdict(s) for s in self.suggestions],
            "proposed_fix": asdict(self.proposed_fix) if self.proposed_fix else None,
        }
