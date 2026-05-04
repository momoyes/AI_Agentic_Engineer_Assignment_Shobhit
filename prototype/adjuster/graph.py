"""LangGraph variant of the Adjuster.

This is the *demonstration* path, not the recommended one. It exists so a
reviewer can compare:

  - the deterministic Adjuster (`pipeline.py`), which puts code in the
    structural-check seat and the LLM in the prose seat, and
  - this LangGraph + RAG variant, which puts the LLM in the structural,
    existence, and semantic-check seats, with policy/COA snippets retrieved
    from Chroma.

Both produce a `DecisionRecord` of the same shape so they slot into the
Streamlit Compare view side-by-side. The structural argument the
deliverable wants to make is that the deterministic path is *better* on
the arithmetic checks — faster, cheaper, exactly correct, fully
auditable. The LangGraph path is included so that argument is a
side-by-side observation rather than a claim.

Topology (parallel fan-out, single converge, then sequential):

    START ─┬──► structural ──┐
           ├──► existence  ──┼──► aggregate ──► decide ──► explain ──► END
           └──► semantic   ──┘

The three checks share a `findings` channel via `Annotated[list,
operator.add]` so concurrent appends merge cleanly instead of overwriting.
A `trace` channel does the same for per-node telemetry.
"""
from __future__ import annotations

import json
import os
import time
from operator import add
from typing import Annotated, TypedDict

from .config import CONFIG
from .llm import explain as llm_explain
from .models import (
    Account,
    DecisionRecord,
    Finding,
    JournalEntry,
    MappingSuggestion,
    ProposedFix,
)
from .pipeline import HARD_ERROR_CODES
from .validators import propose_balance_fix


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class AdjusterState(TypedDict, total=False):
    je: JournalEntry
    coa: dict[str, Account]
    findings: Annotated[list[Finding], add]
    trace: Annotated[list[dict], add]
    suggestions: list[MappingSuggestion]
    proposed_fix: ProposedFix | None
    decision: str
    explanation: str


# ---------------------------------------------------------------------------
# Provider-agnostic JSON call
# ---------------------------------------------------------------------------

def _chat_json(*, system: str, user: str, max_tokens: int = 600) -> tuple[dict, dict]:
    """Call whichever LLM provider is configured. Return (parsed_json, telemetry).

    Anthropic is preferred (matches the rest of the prototype). Falls back to
    OpenAI. Raises if neither is configured — graph mode is not supposed to
    run silently against a template.
    """
    started = time.perf_counter()
    if os.environ.get("ANTHROPIC_API_KEY"):
        from anthropic import Anthropic
        client = Anthropic()
        resp = client.messages.create(
            model=CONFIG.anthropic_model,
            max_tokens=max_tokens,
            system=system + "\n\nReply with a single JSON object. No prose.",
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text.strip()
        # strip ```json fences if the model added them
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        telemetry = {
            "provider": "anthropic",
            "model": CONFIG.anthropic_model,
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        return json.loads(text), telemetry

    if os.environ.get("OPENAI_API_KEY"):
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model=CONFIG.openai_model,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content.strip()
        telemetry = {
            "provider": "openai",
            "model": CONFIG.openai_model,
            "input_tokens": resp.usage.prompt_tokens,
            "output_tokens": resp.usage.completion_tokens,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        return json.loads(text), telemetry

    raise RuntimeError(
        "Graph mode requires ANTHROPIC_API_KEY or OPENAI_API_KEY in the environment."
    )


def _je_to_dict(je: JournalEntry) -> dict:
    return {
        "id": je.id,
        "description": je.description,
        "date": je.date,
        "lines": [
            {"account": l.account, "debit": l.debit, "credit": l.credit, "memo": l.memo}
            for l in je.lines
        ],
        "totals": {"debit": je.total_debit, "credit": je.total_credit},
    }


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def structural_node(state: AdjusterState) -> dict:
    """LLM checks balance, sign, header-posting, and period rules — grounded
    in policy snippets retrieved from the `adjuster_policy` Chroma collection.
    """
    from .rag import query_policy
    je = state["je"]
    snippets = query_policy(
        "debit credit balance signs header posting date period rules",
        n_results=4,
    )
    system = (
        "You are an Adjuster policy enforcement agent. Apply the policy "
        "snippets below to the journal entry. Only emit findings the policy "
        "supports. Output JSON: {\"findings\": [{\"code\": str, \"severity\": "
        "str, \"message\": str, \"line_index\": int|null, \"detail\": object}]}.\n\n"
        "Policy snippets:\n"
        + "\n---\n".join(s["text"] for s in snippets)
    )
    user = json.dumps(_je_to_dict(je))
    try:
        result, telemetry = _chat_json(system=system, user=user)
    except Exception as exc:
        return {
            "trace": [{"node": "structural", "error": str(exc)}],
        }
    findings = [_finding_from_json(f, je.id) for f in result.get("findings", [])]
    return {
        "findings": findings,
        "trace": [{
            "node": "structural", "policy_snippets": [s["id"] for s in snippets],
            "findings_emitted": len(findings), **telemetry,
        }],
    }


def existence_node(state: AdjusterState) -> dict:
    """For each line, retrieve the most-similar COA accounts and ask the LLM
    whether the cited account is real or whether one of the candidates is the
    intended target. Demonstrates RAG + LLM disambiguation."""
    from .rag import query_coa
    je, coa = state["je"], state["coa"]

    findings: list[Finding] = []
    suggestions: list[MappingSuggestion] = []
    snippets_log: list[dict] = []
    total_telemetry = {"input_tokens": 0, "output_tokens": 0, "duration_ms": 0}
    provider = None
    model = None

    for i, line in enumerate(je.lines):
        if line.account in coa and coa[line.account].account_type != "Header":
            continue  # cheap pre-check; saves a round-trip
        candidates = query_coa(
            f"{line.account} {line.memo}", coa=coa, n_results=5
        )
        snippets_log.append({"line": i, "candidates": [c["code"] for c in candidates]})
        system = (
            "You are a COA mapping agent. Decide whether the cited account "
            "code exists or is a header. If it does not exist as a leaf, "
            "pick the best candidate from the list and emit an "
            "UNMAPPED_ACCOUNT finding plus a suggestion. Output JSON: "
            "{\"finding\": object|null, \"suggestion\": "
            "{\"candidate_code\": str, \"candidate_name\": str, \"confidence\": "
            "number, \"reason\": str} | null}."
        )
        user = json.dumps({
            "line_index": i, "cited_account": line.account, "memo": line.memo,
            "candidates": candidates,
            "in_coa": line.account in coa,
            "is_header": line.account in coa and coa[line.account].account_type == "Header",
        })
        try:
            result, telemetry = _chat_json(system=system, user=user, max_tokens=400)
        except Exception as exc:
            return {"trace": [{"node": "existence", "line": i, "error": str(exc)}]}
        provider = telemetry["provider"]
        model = telemetry["model"]
        for k in ("input_tokens", "output_tokens", "duration_ms"):
            total_telemetry[k] += telemetry[k]
        if result.get("finding"):
            findings.append(_finding_from_json(result["finding"], je.id, default_line=i))
        if result.get("suggestion"):
            s = result["suggestion"]
            suggestions.append(MappingSuggestion(
                candidate_code=s["candidate_code"],
                candidate_name=s["candidate_name"],
                confidence=float(s["confidence"]),
                reason=s["reason"],
            ))

    return {
        "findings": findings,
        "suggestions": suggestions,
        "trace": [{
            "node": "existence",
            "lines_checked": len(je.lines),
            "candidates_per_line": snippets_log,
            "provider": provider, "model": model,
            **total_telemetry,
        }],
    }


def semantic_node(state: AdjusterState) -> dict:
    """No RAG. Pure LLM judgement on memo coherence — the one place the
    LLM earns its keep in this graph (text understanding, not arithmetic)."""
    je = state["je"]
    system = (
        "You are an anomaly-detection agent. Read the JE and judge whether "
        "the memos and the line postings tell a coherent story. Examples of "
        "issues: a memo that says 'reverse' but the entry posts forward, an "
        "intercompany memo against a non-IC account, suspicious round amounts. "
        "Output JSON: {\"findings\": [{\"code\": \"SEMANTIC_ANOMALY\", "
        "\"severity\": \"info\"|\"warning\", \"message\": str}]}. Empty list "
        "if nothing notable."
    )
    user = json.dumps(_je_to_dict(je))
    try:
        result, telemetry = _chat_json(system=system, user=user, max_tokens=400)
    except Exception as exc:
        return {"trace": [{"node": "semantic", "error": str(exc)}]}
    findings = [_finding_from_json(f, je.id) for f in result.get("findings", [])]
    return {
        "findings": findings,
        "trace": [{
            "node": "semantic", "findings_emitted": len(findings), **telemetry,
        }],
    }


def aggregate_node(state: AdjusterState) -> dict:
    """Pure code: dedupe parallel findings by (code, line_index)."""
    seen: set[tuple[str, int | None]] = set()
    deduped: list[Finding] = []
    for f in state.get("findings", []):
        key = (f.code, f.line_index)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    return {
        "findings": [],   # additive channel: we return [] then overwrite via reset
        "trace": [{"node": "aggregate", "merged": len(deduped)}],
        # we can't replace findings via the additive channel — see decide_node
        # which reads state directly. Stash the dedup in trace for visibility.
        "_deduped_findings": deduped,  # noqa: pretty hacky but the reducer is additive
    }


def decide_node(state: AdjusterState) -> dict:
    """RAG over policy → LLM picks accept / quarantine / reject and (if
    tier-b) replaces the imbalance finding with IMBALANCE_WARN."""
    from .rag import query_policy

    # Pull deduped findings stashed by aggregate, fall back to raw additive
    # channel.
    findings: list[Finding] = state.get("_deduped_findings") or list(
        state.get("findings", [])
    )
    je = state["je"]

    # Hard-error fast path — pure code, no LLM call needed.
    hard = [f for f in findings if f.code in HARD_ERROR_CODES]
    if hard:
        return {
            "decision": "reject",
            "_final_findings": findings,
            "proposed_fix": None,
            "trace": [{"node": "decide", "fast_path": "hard_error",
                       "codes": [f.code for f in hard]}],
        }

    snippets = query_policy(
        "tier accept quarantine reject imbalance threshold halt",
        n_results=5,
    )
    system = (
        "You are the Adjuster decision agent. Apply the tiered policy "
        "snippets to the findings and output JSON: {\"decision\": "
        "\"accept\"|\"quarantine\"|\"reject\", \"replace_imbalance_with\": "
        "\"AUTO_CORRECTED\"|\"IMBALANCE_WARN\"|null, \"rationale\": str}.\n\n"
        "Policy snippets:\n"
        + "\n---\n".join(s["text"] for s in snippets)
    )
    user = json.dumps({
        "je_id": je.id,
        "totals": {"debit": je.total_debit, "credit": je.total_credit,
                   "diff": round(je.total_debit - je.total_credit, 2)},
        "findings": [
            {"code": f.code, "severity": f.severity, "message": f.message}
            for f in findings
        ],
    })
    try:
        result, telemetry = _chat_json(system=system, user=user, max_tokens=400)
    except Exception as exc:
        return {
            "decision": "reject",
            "_final_findings": findings,
            "trace": [{"node": "decide", "error": str(exc)}],
        }

    decision = result.get("decision", "reject")
    replacement = result.get("replace_imbalance_with")
    proposed = propose_balance_fix(je) if replacement in ("AUTO_CORRECTED", "IMBALANCE_WARN") else None

    if replacement and any(f.code == "UNBALANCED" for f in findings):
        new_code = replacement
        diff = round(je.total_debit - je.total_credit, 2)
        if new_code == "AUTO_CORRECTED":
            msg = (
                f"Imbalance of {abs(diff):,.2f} within auto-correction "
                f"threshold; LLM-decided plug to {CONFIG.materiality.rounding_account}."
            )
            new_finding = Finding(
                code="AUTO_CORRECTED", severity="info", message=msg, je_id=je.id,
                detail={"difference": diff, "rounding_account": CONFIG.materiality.rounding_account,
                        "action": "auto_corrected_with_plug"},
            )
        else:
            msg = (
                f"Imbalance of {abs(diff):,.2f} within warn band; LLM-decided "
                "to accept with reconciliation note."
            )
            new_finding = Finding(
                code="IMBALANCE_WARN", severity="warning", message=msg, je_id=je.id,
                detail={"difference": diff, "action": "accepted_with_reconciliation_note"},
            )
        findings = [new_finding if f.code == "UNBALANCED" else f for f in findings]

    return {
        "decision": decision,
        "_final_findings": findings,
        "proposed_fix": proposed,
        "trace": [{
            "node": "decide", "decision": decision, "replacement": replacement,
            "rationale": result.get("rationale"), **telemetry,
        }],
    }


def explain_node(state: AdjusterState) -> dict:
    """Reuse the existing llm.explain() helper so the prose looks the same
    across modes. Build a synthetic DecisionRecord from final state."""
    findings = state.get("_final_findings") or list(state.get("findings", []))
    je = state["je"]
    record = DecisionRecord(
        je_id=je.id,
        description=je.description,
        decision=state.get("decision", "reject"),
        findings=findings,
        suggestions=state.get("suggestions") or [],
        proposed_fix=state.get("proposed_fix"),
    )
    started = time.perf_counter()
    record.explanation = llm_explain(record, use_llm=True)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return {
        "explanation": record.explanation,
        "trace": [{"node": "explain", "duration_ms": duration_ms}],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finding_from_json(d: dict, je_id: str, *, default_line: int | None = None) -> Finding:
    return Finding(
        code=d.get("code", "UNKNOWN"),
        severity=d.get("severity", "info"),
        message=d.get("message", ""),
        je_id=je_id,
        line_index=d.get("line_index", default_line),
        detail=d.get("detail") or {},
    )


# ---------------------------------------------------------------------------
# Compile and run
# ---------------------------------------------------------------------------

_compiled = None


def _build():
    global _compiled
    if _compiled is not None:
        return _compiled
    from langgraph.graph import StateGraph, START, END
    g = StateGraph(AdjusterState)
    g.add_node("structural", structural_node)
    g.add_node("existence", existence_node)
    g.add_node("semantic", semantic_node)
    g.add_node("aggregate", aggregate_node)
    g.add_node("decide", decide_node)
    g.add_node("explain", explain_node)

    g.add_edge(START, "structural")
    g.add_edge(START, "existence")
    g.add_edge(START, "semantic")
    g.add_edge("structural", "aggregate")
    g.add_edge("existence", "aggregate")
    g.add_edge("semantic", "aggregate")
    g.add_edge("aggregate", "decide")
    g.add_edge("decide", "explain")
    g.add_edge("explain", END)

    _compiled = g.compile()
    return _compiled


def run_graph(je: JournalEntry, coa: dict[str, Account]) -> DecisionRecord:
    """Run the LangGraph variant on one JE. Returns a DecisionRecord with
    the per-node trace stuffed into `record.findings[*].detail` would be
    invasive — instead we attach it as an attribute the Streamlit UI reads.
    """
    app = _build()
    final = app.invoke({"je": je, "coa": coa, "findings": [], "trace": []})
    findings = final.get("_final_findings") or list(final.get("findings", []))
    record = DecisionRecord(
        je_id=je.id,
        description=je.description,
        decision=final.get("decision", "reject"),
        findings=findings,
        suggestions=final.get("suggestions") or [],
        proposed_fix=final.get("proposed_fix"),
    )
    record.explanation = final.get("explanation", "")
    # Non-dataclass attribute — DecisionRecord is a regular @dataclass so this
    # is allowed. Streamlit reads it; CLI ignores it.
    record.trace = final.get("trace", [])
    return record
