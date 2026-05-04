"""Optional LLM explainer.

The LLM only writes prose. It does not validate, decide, or generate
mappings. Provider preference: Anthropic (if ANTHROPIC_API_KEY set), then
OpenAI (if OPENAI_API_KEY set), else a deterministic template so the
prototype runs end-to-end with zero dependencies. Anthropic is preferred
because the architecture is designed against Claude's tool-use semantics;
OpenAI is a drop-in fallback for environments where Anthropic isn't
available."""
from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from .config import CONFIG

if TYPE_CHECKING:
    from .models import DecisionRecord


SYSTEM_PROMPT = """You are a finance ops assistant. Given a structured journal-entry
validation result, write a 1-3 sentence plain-English explanation aimed at a
non-technical accountant. Rules:
- Do not invent facts beyond the findings.
- Do not produce or modify numbers.
- If a fix is suggested, phrase it as a question.
- Be specific (cite account codes, amounts, JE id) but concise."""


def explain(record: "DecisionRecord", *, use_llm: bool) -> str:
    if use_llm:
        try:
            if os.environ.get("ANTHROPIC_API_KEY"):
                return _explain_with_claude(record)
            if os.environ.get("OPENAI_API_KEY"):
                return _explain_with_openai(record)
            return _template_explanation(record)
        except Exception as exc:  # pragma: no cover — graceful fallback
            return _template_explanation(record) + f"\n[LLM unavailable: {exc}]"
    return _template_explanation(record)


# ---------------------------------------------------------------------------
# Template (no-LLM) path — deterministic, ships in the no-key default mode
# ---------------------------------------------------------------------------

_TEMPLATES = {
    "UNBALANCED": (
        "{je_id} debits ({td:,.2f}) do not equal credits ({tc:,.2f}); the "
        "entry is off by {diff:,.2f}. {fix_hint}"
    ),
    "AUTO_CORRECTED": (
        "{je_id} was off by {abs_diff:,.2f}, within the auto-correction "
        "threshold. A plug to the rounding account has been logged to the "
        "audit trail. {fix_hint}"
    ),
    "IMBALANCE_WARN": (
        "{je_id} was off by {abs_diff:,.2f}, in the warn band. The entry is "
        "accepted with a reconciliation note; reviewer sign-off is required "
        "before the period closes. {fix_hint}"
    ),
    "UNMAPPED_ACCOUNT": (
        "{je_id} was held for review because account '{acct}' is not in the "
        "chart of accounts. Did you mean {top_suggest}?"
    ),
    "POSTING_TO_HEADER": (
        "{je_id} was rejected because it posts to a header account, which "
        "cannot receive entries directly. Move the line to a leaf account "
        "under that header."
    ),
    "CIRCULAR_NET_ZERO": (
        "{je_id} debits and credits the same account ('{acct}') with the "
        "same amount, so it has no effect on the financials. Was a different "
        "settlement counterparty intended?"
    ),
    "DATE_OUT_OF_PERIOD": (
        "{je_id} was rejected because its date ({date}) falls outside the "
        "{period} reporting period."
    ),
    "ACCEPT": "{je_id} ({desc}) is balanced and all accounts are valid; accepted.",
}


def _template_explanation(record: "DecisionRecord") -> str:
    # An accept with no findings is the clean path. An accept *with* findings
    # is the tier-a (AUTO_CORRECTED) or tier-b (IMBALANCE_WARN) path: fall
    # through to the finding-driven template so the prose reflects the
    # policy outcome, not a generic "balanced" line.
    if record.decision == "accept" and not record.findings:
        return _TEMPLATES["ACCEPT"].format(
            je_id=record.je_id, desc=record.description
        )
    if not record.findings:
        return f"{record.je_id}: {record.decision}."
    primary = record.findings[0]
    detail = primary.detail or {}
    top_suggest = (
        f"{record.suggestions[0].candidate_code} "
        f"({record.suggestions[0].candidate_name})"
        if record.suggestions
        else "another account"
    )
    fix_hint = ""
    if record.proposed_fix is not None:
        pf = record.proposed_fix
        fix_hint = (
            f"Proposed fix (confidence {pf.confidence:.0%}): adjust line "
            f"{pf.line_index} {pf.field} from {pf.current_amount:,.2f} to "
            f"{pf.proposed_amount:,.2f}. Confirm before posting."
        )
    return _TEMPLATES.get(primary.code, "{je_id}: {msg}").format(
        je_id=record.je_id,
        desc=record.description,
        td=detail.get("total_debit", 0.0),
        tc=detail.get("total_credit", 0.0),
        diff=detail.get("difference", 0.0),
        abs_diff=abs(detail.get("difference", 0.0)),
        acct=detail.get("account", ""),
        date=getattr(record, "date", ""),
        period=CONFIG.period.label,
        top_suggest=top_suggest,
        msg=primary.message,
        fix_hint=fix_hint,
    )


# ---------------------------------------------------------------------------
# Live LLM path
# ---------------------------------------------------------------------------

def _explain_with_claude(record: "DecisionRecord") -> str:
    try:
        from anthropic import Anthropic
    except ImportError:
        return _template_explanation(record)

    client = Anthropic()
    payload = json.dumps(record.to_json(), indent=2, default=str)
    response = client.messages.create(
        model=CONFIG.anthropic_model,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": payload}],
    )
    return response.content[0].text.strip()


def _explain_with_openai(record: "DecisionRecord") -> str:
    try:
        from openai import OpenAI
    except ImportError:
        return _template_explanation(record)

    client = OpenAI()
    payload = json.dumps(record.to_json(), indent=2, default=str)
    response = client.chat.completions.create(
        model=CONFIG.openai_model,
        max_tokens=300,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": payload},
        ],
    )
    return response.choices[0].message.content.strip()
