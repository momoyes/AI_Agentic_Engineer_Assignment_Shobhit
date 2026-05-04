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

from ..config import CONFIG
from ..deterministic.llm import explain as llm_explain
from ..models import (
    Account,
    DecisionRecord,
    Finding,
    JournalEntry,
    MappingSuggestion,
    ProposedFix,
)
from ..deterministic.pipeline import HARD_ERROR_CODES
from ..deterministic.validators import propose_balance_fix


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
# Strict JSON schemas — pinned per node so the model can only emit codes
# the policy actually defines. Eliminates the "model invents JE_DATE_OUT_OF_PERIOD"
# class of hallucination.
# ---------------------------------------------------------------------------

_STRUCTURAL_SCHEMA = {
    "name": "structural_findings",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "enum": [
                                "UNBALANCED",
                                "NEGATIVE_AMOUNT",
                                "DEBIT_AND_CREDIT_ON_SAME_LINE",
                                "DATE_OUT_OF_PERIOD",
                                "POSTING_TO_HEADER",
                            ],
                        },
                        "severity": {"type": "string", "enum": ["error", "warning", "info"]},
                        "message": {"type": "string"},
                        "line_index": {"type": ["integer", "null"]},
                    },
                    "required": ["code", "severity", "message", "line_index"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["findings"],
        "additionalProperties": False,
    },
}

_EXISTENCE_SCHEMA = {
    "name": "existence_check",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "finding": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {
                            "code": {
                                "type": "string",
                                "enum": ["UNMAPPED_ACCOUNT", "POSTING_TO_HEADER"],
                            },
                            "severity": {"type": "string", "enum": ["error", "warning"]},
                            "message": {"type": "string"},
                            "line_index": {"type": ["integer", "null"]},
                        },
                        "required": ["code", "severity", "message", "line_index"],
                        "additionalProperties": False,
                    },
                ],
            },
            "suggestion": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {
                            "candidate_code": {"type": "string"},
                            "candidate_name": {"type": "string"},
                            "confidence": {"type": "number"},
                            "reason": {"type": "string"},
                        },
                        "required": ["candidate_code", "candidate_name", "confidence", "reason"],
                        "additionalProperties": False,
                    },
                ],
            },
        },
        "required": ["finding", "suggestion"],
        "additionalProperties": False,
    },
}

_SEMANTIC_SCHEMA = {
    "name": "semantic_anomalies",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "enum": ["SEMANTIC_ANOMALY"]},
                        "severity": {"type": "string", "enum": ["info", "warning"]},
                        "message": {"type": "string"},
                        "line_index": {"type": ["integer", "null"]},
                    },
                    "required": ["code", "severity", "message", "line_index"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["findings"],
        "additionalProperties": False,
    },
}

_DECIDE_SCHEMA = {
    "name": "tier_decision",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["accept", "quarantine", "reject"]},
            "replace_imbalance_with": {
                "anyOf": [
                    {"type": "null"},
                    {"type": "string", "enum": ["AUTO_CORRECTED", "IMBALANCE_WARN"]},
                ],
            },
            "rationale": {"type": "string"},
        },
        "required": ["decision", "replace_imbalance_with", "rationale"],
        "additionalProperties": False,
    },
}


# ---------------------------------------------------------------------------
# Provider-agnostic JSON call
# ---------------------------------------------------------------------------

def _chat_json(
    *,
    system: str,
    user: str,
    max_tokens: int = 600,
    json_schema: dict | None = None,
) -> tuple[dict, dict]:
    """Call whichever LLM provider is configured. Return (parsed_json, telemetry).

    `json_schema` is OpenAI's strict-mode schema dict (with name/strict/schema
    keys). When provided, the OpenAI branch uses `response_format=json_schema`
    so the model can only return values that match. The Anthropic branch
    embeds the schema into the system prompt (Claude doesn't have a native
    strict-schema mode here) and parses the result.

    Anthropic is preferred (matches the rest of the prototype). Falls back to
    OpenAI. Raises if neither is configured — graph mode is not supposed to
    run silently against a template.
    """
    started = time.perf_counter()
    if os.environ.get("ANTHROPIC_API_KEY"):
        from anthropic import Anthropic
        client = Anthropic()
        sys_prompt = system + "\n\nReply with a single JSON object. No prose."
        if json_schema is not None:
            sys_prompt += (
                "\n\nThe JSON MUST conform to this schema (treat enums as the "
                "complete and only set of allowed values):\n"
                + json.dumps(json_schema["schema"], indent=2)
            )
        resp = client.messages.create(
            model=CONFIG.anthropic_model,
            max_tokens=max_tokens,
            system=sys_prompt,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text.strip()
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
        if json_schema is not None:
            response_format = {"type": "json_schema", "json_schema": json_schema}
        else:
            response_format = {"type": "json_object"}
        resp = client.chat.completions.create(
            model=CONFIG.openai_model,
            max_tokens=max_tokens,
            response_format=response_format,
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
        "actually supports for THIS entry; if a check passes, do NOT emit a "
        "finding for it.\n\n"
        f"Reporting period: {CONFIG.period.label} — {CONFIG.period.start} "
        f"through {CONFIG.period.end} inclusive. A JE date that falls within "
        "this window (string compare on ISO YYYY-MM-DD works) is in-period. "
        "Do NOT emit DATE_OUT_OF_PERIOD for in-period dates.\n\n"
        "Allowed finding codes (use these exactly, no others): UNBALANCED, "
        "NEGATIVE_AMOUNT, DEBIT_AND_CREDIT_ON_SAME_LINE, DATE_OUT_OF_PERIOD, "
        "POSTING_TO_HEADER.\n\n"
        "Policy snippets:\n"
        + "\n---\n".join(s["text"] for s in snippets)
    )
    user = json.dumps(_je_to_dict(je))
    try:
        result, telemetry = _chat_json(
            system=system, user=user, json_schema=_STRUCTURAL_SCHEMA,
        )
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
            "You are a COA mapping agent. The user message tells you whether "
            "the cited account is in the COA and whether it is a Header. "
            "Rules: if `in_coa` is false → emit an UNMAPPED_ACCOUNT finding "
            "(severity error) AND pick the best candidate as the suggestion. "
            "If `is_header` is true → emit a POSTING_TO_HEADER finding "
            "(severity error), suggestion may be null. Otherwise → both "
            "fields null. Allowed finding codes: UNMAPPED_ACCOUNT, "
            "POSTING_TO_HEADER. Pick suggestions ONLY from the supplied "
            "`candidates` list — never invent a code."
        )
        user = json.dumps({
            "line_index": i, "cited_account": line.account, "memo": line.memo,
            "candidates": candidates,
            "in_coa": line.account in coa,
            "is_header": line.account in coa and coa[line.account].account_type == "Header",
        })
        try:
            result, telemetry = _chat_json(
                system=system, user=user, max_tokens=400,
                json_schema=_EXISTENCE_SCHEMA,
            )
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
        "Use code SEMANTIC_ANOMALY only. Empty findings list if nothing is "
        "notable — do NOT manufacture concerns just to fill the output."
    )
    user = json.dumps(_je_to_dict(je))
    try:
        result, telemetry = _chat_json(
            system=system, user=user, max_tokens=400,
            json_schema=_SEMANTIC_SCHEMA,
        )
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
    abs_diff = abs(round(je.total_debit - je.total_credit, 2))
    system = (
        "You are the Adjuster decision agent. Apply the tiered policy "
        "snippets to the findings.\n\n"
        f"Materiality thresholds (USD): tier-a auto-correct ≤ "
        f"{CONFIG.materiality.fx_rounding_abs:,.2f}; tier-b warn band > "
        f"{CONFIG.materiality.fx_rounding_abs:,.2f} and ≤ "
        f"{CONFIG.materiality.je_warn_band_max:,.2f}; tier-c reject > "
        f"{CONFIG.materiality.je_halt_threshold:,.2f}.\n\n"
        "If there is no UNBALANCED finding, set replace_imbalance_with to null. "
        "If there is one and abs(diff) ≤ tier-a → AUTO_CORRECTED + accept. "
        "If tier-b → IMBALANCE_WARN + accept. If tier-c → null + reject.\n\n"
        "Decision must be one of: accept, quarantine, reject.\n\n"
        "Policy snippets:\n"
        + "\n---\n".join(s["text"] for s in snippets)
    )
    user = json.dumps({
        "je_id": je.id,
        "totals": {"debit": je.total_debit, "credit": je.total_credit,
                   "diff": round(je.total_debit - je.total_credit, 2),
                   "abs_diff": abs_diff},
        "findings": [
            {"code": f.code, "severity": f.severity, "message": f.message}
            for f in findings
        ],
    })
    try:
        result, telemetry = _chat_json(
            system=system, user=user, max_tokens=400,
            json_schema=_DECIDE_SCHEMA,
        )
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
