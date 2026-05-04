"""Decision pipeline: findings → decision → explanation.

The decision rules live here, not in the validators or the LLM. A reviewer
can read this file and know exactly when an entry will be accepted,
rejected, or quarantined.

Tiered handling for UNBALANCED findings (per Amit Patel email 2026-04-30):
- tier-a (|diff| ≤ fx_rounding_abs)         → accept; replace UNBALANCED
                                               with AUTO_CORRECTED finding;
                                               main.py emits an
                                               auto_correction audit event.
- tier-b (|diff| ≤ je_warn_band_max
          and a single-line fix is proposable)
                                            → accept with reconciliation
                                               note; replace UNBALANCED
                                               with IMBALANCE_WARN finding
                                               (severity=warning).
- tier-c (anything else, incl. unproposable
          fixes within the warn band)        → reject (HALT).

See ARCHITECTURE.md §5 and ASSUMPTIONS.md A1/A12."""
from __future__ import annotations

from ..config import CONFIG
from .llm import explain
from ..models import (
    Account,
    DecisionRecord,
    Finding,
    JournalEntry,
    MappingSuggestion,
    ProposedFix,
)
from .validators import propose_balance_fix, run_all_checks, suggest_mappings


# Findings that indicate a fixable, human-reviewable situation rather than a
# hard structural defect. Quarantine, don't reject.
QUARANTINE_CODES = {"UNMAPPED_ACCOUNT", "CIRCULAR_NET_ZERO"}

# Findings that always reject regardless of severity (hard structural).
HARD_ERROR_CODES = {
    "POSTING_TO_HEADER",
    "NEGATIVE_AMOUNT",
    "DEBIT_AND_CREDIT_ON_SAME_LINE",
    "DATE_OUT_OF_PERIOD",
}


LLMMode = str  # 'off' | 'prose' | 'graph'


def decide_one(
    je: JournalEntry,
    coa: dict[str, Account],
    use_llm: bool = False,
    *,
    llm_mode: LLMMode | None = None,
) -> DecisionRecord:
    """Validate one JE.

    `llm_mode` (preferred) selects the variant:
      - 'off'   : pure deterministic; prose from a template.
      - 'prose' : deterministic decisions + LLM-rewritten explanation.
      - 'graph' : LangGraph + RAG; LLM does structural / existence / semantic
                  checks, retrieving policy/COA snippets from Chroma. The
                  *demonstration* path — see ARCHITECTURE.md.

    `use_llm` is kept for back-compat: True ⇒ 'prose', False ⇒ 'off'.
    """
    if llm_mode is None:
        llm_mode = "prose" if use_llm else "off"

    if llm_mode == "graph":
        from ..langgraph.graph import run_graph
        return run_graph(je, coa)

    findings = run_all_checks(je, coa)

    decision: str
    suggestions: list[MappingSuggestion] = []
    proposed_fix: ProposedFix | None = None

    if not findings:
        decision = "accept"
    elif _has_hard_error(findings):
        decision = "reject"
    else:
        unbalanced = next((f for f in findings if f.code == "UNBALANCED"), None)
        if unbalanced is not None:
            decision, proposed_fix, replacement = _decide_unbalanced(je, unbalanced)
            if replacement is not None:
                findings = [replacement if f is unbalanced else f for f in findings]
        elif any(f.code in QUARANTINE_CODES for f in findings):
            decision = "quarantine"
        else:
            decision = "reject"

        if decision == "quarantine":
            for f in findings:
                if f.code == "UNMAPPED_ACCOUNT":
                    # Pass the offending line's memo as a name hint so the
                    # rapidfuzz-backed scorer can match on account semantics
                    # (e.g. "Conf travel" → "Travel and Entertainment"), not
                    # just numeric code prefix.
                    line_memo: str | None = None
                    if f.line_index is not None and f.line_index < len(je.lines):
                        line_memo = je.lines[f.line_index].memo
                    suggestions.extend(
                        suggest_mappings(
                            f.detail.get("account", ""),
                            coa,
                            name_hint=line_memo,
                        )
                    )

    record = DecisionRecord(
        je_id=je.id,
        description=je.description,
        decision=decision,
        findings=findings,
        suggestions=suggestions,
        proposed_fix=proposed_fix,
    )
    record.explanation = explain(record, use_llm=(llm_mode == "prose"))
    return record


def _decide_unbalanced(
    je: JournalEntry, finding: Finding
) -> tuple[str, ProposedFix | None, Finding | None]:
    """Apply tier-a / tier-b / tier-c to an UNBALANCED finding.

    Returns (decision, proposed_fix, replacement_finding). Caller substitutes
    the replacement finding for the original UNBALANCED finding so the audit
    log carries the policy outcome (AUTO_CORRECTED or IMBALANCE_WARN), not
    the raw structural fact.
    """
    diff_signed = float(finding.detail.get("difference", 0.0))
    diff = abs(diff_signed)
    proposal = propose_balance_fix(je)

    if diff <= CONFIG.materiality.fx_rounding_abs:
        # Tier-a: sub-$500 rounding noise. Auto-correct via a plug to the
        # configured rounding account; main.py recognizes AUTO_CORRECTED and
        # emits a structured `auto_correction` audit event with source_je,
        # delta, and action. The plug itself is a downstream concern (the
        # statement assembler in the full system); this slice records the
        # decision and the audit trail.
        replacement = Finding(
            code="AUTO_CORRECTED",
            severity="info",
            message=(
                f"Imbalance of {diff:,.2f} is within the $"
                f"{CONFIG.materiality.fx_rounding_abs:,.0f} auto-correction "
                f"threshold. Plug routed to {CONFIG.materiality.rounding_account}."
            ),
            je_id=je.id,
            detail={
                "difference": diff_signed,
                "rounding_account": CONFIG.materiality.rounding_account,
                "action": "auto_corrected_with_plug",
            },
        )
        return "accept", proposal, replacement

    if diff <= CONFIG.materiality.je_warn_band_max:
        if proposal is not None:
            replacement = Finding(
                code="IMBALANCE_WARN",
                severity="warning",
                message=(
                    f"Imbalance of {diff:,.2f} is within the warn band ($"
                    f"{CONFIG.materiality.fx_rounding_abs:,.0f}–$"
                    f"{CONFIG.materiality.je_warn_band_max:,.0f}). Statement "
                    "produced with a reconciliation note; reviewer sign-off "
                    "required before close."
                ),
                je_id=je.id,
                detail={
                    "difference": diff_signed,
                    "action": "accepted_with_reconciliation_note",
                    "proposed_line_index": proposal.line_index,
                    "proposed_field": proposal.field,
                    "proposed_amount": proposal.proposed_amount,
                },
            )
            return "accept", proposal, replacement
        # No single-line fix proposable → fall through to reject (HALT).

    return "reject", None, None


def _has_hard_error(findings: list[Finding]) -> bool:
    return any(f.code in HARD_ERROR_CODES for f in findings)


def decide_batch(
    entries: list[JournalEntry],
    coa: dict[str, Account],
    *,
    use_llm: bool = False,
    llm_mode: LLMMode | None = None,
) -> list[DecisionRecord]:
    return [
        decide_one(je, coa, use_llm=use_llm, llm_mode=llm_mode)
        for je in entries
    ]
