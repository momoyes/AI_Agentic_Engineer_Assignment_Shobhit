"""Streamlit dashboard for the Manual Adjustments Agent.

Three modes side-by-side so a reviewer can see what the LLM does and does
not contribute:

1. Deterministic — pure code, no API keys, prose from a template.
2. LLM — same decisions and findings, but the explanation is rewritten by
   Claude (or OpenAI as fallback).
3. Compare — runs both and renders the prose side-by-side per JE so the
   delta is obvious.

Run:
    streamlit run streamlit_app.py
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from adjuster.loader import load_adjustments, load_coa  # noqa: E402
from adjuster.models import DecisionRecord  # noqa: E402
from adjuster.pipeline import decide_batch  # noqa: E402

INPUTS = ROOT.parent / "inputs"

# ---------------------------------------------------------------------------
# Theme tokens
# ---------------------------------------------------------------------------

PALETTE = {
    "accept": "#0e7c5a",      # emerald
    "quarantine": "#b54708",  # amber
    "reject": "#b42318",      # red
    "info": "#175cd3",        # blue
    "warning": "#b54708",
    "error": "#b42318",
    "muted": "#475467",
    "border": "#e4e7ec",
    "surface": "#ffffff",
    "subtle": "#f9fafb",
    "ink": "#101828",
}

DECISION_COPY = {
    "accept": "Accept",
    "quarantine": "Hold for review",
    "reject": "Reject",
}

# Agent registry — mirrors ARCHITECTURE.md §2. Only "Adjuster" is built; the
# rest render a Coming-soon placeholder so the full system surface is visible
# without overstating what the prototype ships.
AGENTS = [

   {
        "key": "adjuster",
        "name": "Adjuster",
        "tagline": "JE validation",
        "purpose": "Validate manual journal entries against the COA and policy.",
        "deterministic": "All structural checks, decision logic, mapping candidates.",
        "llm": "Plain-English explanation; never validates or decides.",
        "status": "live",
        "interface_file": "prototype/adjuster/pipeline.py",
    },
    {
        "key": "mapper",
        "name": "Mapper",
        "tagline": "TB ↔ COA",
        "purpose": (
            "Resolve unknown account codes to existing COA nodes; detect "
            "renamed accounts across periods."
        ),
        "deterministic": (
            "Generates the candidate set (prefix + edit-distance + name "
            "embeddings)."
        ),
        "llm": (
            "Picks one candidate given account name, parent code, prior-"
            "period analogues, and amount magnitude; emits a confidence."
        ),
        "status": "in_design",
        "interface_file": "prototype/adjuster/mapper.py",
    },
   
    {
        "key": "fx",
        "name": "FX Translator",
        "tagline": "currency translation",
        "purpose": (
            "Translate non-functional-currency balances to USD using the "
            "rate matrix; compute opening / closing CTA."
        ),
        "deterministic": "Pure arithmetic — every cell is a multiplication.",
        "llm": "None. The LLM has no role here by design.",
        "status": "planned",
        "interface_file": "prototype/adjuster/fx.py (planned)",
    },
    {
        "key": "builder",
        "name": "Statement Builder",
        "tagline": "BS / P&L / CF / SOCIE",
        "purpose": (
            "Roll up the post-adjustment, post-translation TB into the four "
            "primary statements per the COA hierarchy."
        ),
        "deterministic": "Tree traversal + roll-up; one correct answer.",
        "llm": (
            "None. An LLM here would be an anti-pattern; the brief warns "
            "against it explicitly."
        ),
        "status": "planned",
        "interface_file": "prototype/adjuster/builder.py (planned)",
    },
    {
        "key": "validator",
        "name": "Validator",
        "tagline": "tie-outs + lineage",
        "purpose": (
            "Cross-statement tie-outs (A=L+E, NI→RE, cash continuity); "
            "decides whether the orchestrator may publish."
        ),
        "deterministic": "Each check is a one-line identity. Pass / fail.",
        "llm": (
            "None for the check itself. Optional prose for *why* a check "
            "failed when escalated to a human."
        ),
        "status": "planned",
        "interface_file": "prototype/adjuster/validator.py (planned)",
    },
]
AGENT_BY_KEY = {a["key"]: a for a in AGENTS}

STATUS_PILL = {
    "live":      ("ok",   "Live"),
    "in_design": ("info", "Interface stub"),
    "planned":   ("warn", "Coming soon"),
}


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _load_inputs() -> tuple[dict, str, str, list]:
    coa = load_coa(INPUTS / "chart_of_accounts.csv")
    period, fcy, entries = load_adjustments(INPUTS / "manual_adjustments.json")
    return coa, period, fcy, entries


@st.cache_data(show_spinner=False)
def _run_deterministic(_coa: dict, _entries: list) -> list[DecisionRecord]:
    return decide_batch(_entries, _coa, use_llm=False)


@st.cache_data(show_spinner=False)
def _run_llm(_coa: dict, _entries: list, _provider_hint: str) -> list[DecisionRecord]:
    return decide_batch(_entries, _coa, use_llm=True)


def _provider_status() -> tuple[str, str, str]:
    """Return (provider, label, tone)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic", "Claude (Anthropic)", "ok"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai", "GPT (OpenAI)", "ok"
    return "template", "Template fallback (no API key)", "warn"


# ---------------------------------------------------------------------------
# CSS — single block so the look is consistent across reruns
# ---------------------------------------------------------------------------

CSS = """
<style>
:root {
  --ink: #101828;
  --muted: #475467;
  --subtle: #f9fafb;
  --surface: #ffffff;
  --border: #e4e7ec;
  --accept: #0e7c5a;
  --quarantine: #b54708;
  --reject: #b42318;
}

/* Tighten Streamlit's default padding */
.block-container {
  padding-top: 1.5rem !important;
  padding-bottom: 3rem !important;
  max-width: 1280px;
}

/* Hide default Streamlit chrome */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}

/* App header */
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 0 1rem 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 1.5rem;
}
.app-header .brand {
  display: flex;
  align-items: center;
  gap: 12px;
}
.app-header .brand-mark {
  width: 36px; height: 36px;
  border-radius: 8px;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
  color: white;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 16px;
  letter-spacing: -0.02em;
}
.app-header h1 {
  margin: 0 !important;
  font-size: 18px !important;
  font-weight: 600 !important;
  color: var(--ink) !important;
  letter-spacing: -0.01em;
}
.app-header .subtitle {
  font-size: 12px; color: var(--muted); margin-top: 2px;
}
.app-header .meta {
  display: flex; gap: 8px; align-items: center;
}

/* Pill / badge */
.pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px; font-weight: 500;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--ink);
  white-space: nowrap;
}
.pill .dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--muted);
}
.pill.ok    { background: #ecfdf3; border-color: #abefc6; color: #067647; }
.pill.warn  { background: #fffaeb; border-color: #fedf89; color: #b54708; }
.pill.error { background: #fef3f2; border-color: #fecdca; color: #b42318; }
.pill.info  { background: #eff8ff; border-color: #b2ddff; color: #175cd3; }
.pill.ok .dot    { background: #17b26a; }
.pill.warn .dot  { background: #f79009; }
.pill.error .dot { background: #f04438; }
.pill.info .dot  { background: #2e90fa; }

/* KPI cards */
.kpi-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.kpi {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 16px 18px;
}
.kpi .label {
  font-size: 12px; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.04em;
  font-weight: 500;
}
.kpi .value {
  font-size: 28px; font-weight: 600;
  color: var(--ink);
  margin-top: 4px;
  letter-spacing: -0.02em;
}
.kpi .delta {
  font-size: 12px; color: var(--muted); margin-top: 2px;
}
.kpi.accent-accept     { border-top: 3px solid var(--accept); }
.kpi.accent-quarantine { border-top: 3px solid var(--quarantine); }
.kpi.accent-reject     { border-top: 3px solid var(--reject); }
.kpi.accent-total      { border-top: 3px solid #0f172a; }

/* Section header */
.section-h {
  display: flex; align-items: baseline; justify-content: space-between;
  margin: 2rem 0 0.75rem 0;
}
.section-h h2 {
  font-size: 14px !important;
  font-weight: 600 !important;
  color: var(--ink) !important;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin: 0 !important;
}
.section-h .hint {
  font-size: 12px; color: var(--muted);
}

/* JE card */
.je-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 18px 20px;
  margin-bottom: 12px;
}
.je-head {
  display: flex; justify-content: space-between; align-items: flex-start;
  gap: 16px;
}
.je-id {
  font-size: 14px; font-weight: 600; color: var(--ink);
  letter-spacing: -0.01em;
}
.je-desc {
  font-size: 13px; color: var(--muted); margin-top: 2px;
}
.je-amount {
  font-variant-numeric: tabular-nums;
  font-size: 13px; color: var(--muted);
  text-align: right;
}
.je-amount strong { color: var(--ink); font-weight: 600; }

/* Findings */
.findings { margin-top: 12px; display: flex; flex-direction: column; gap: 6px; }
.finding {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--subtle);
  border: 1px solid var(--border);
  font-size: 13px;
}
.finding code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px; padding: 1px 6px; border-radius: 4px;
  background: white; border: 1px solid var(--border);
  color: var(--ink);
}
.finding.error    { background: #fef3f2; border-color: #fecdca; }
.finding.warning  { background: #fffaeb; border-color: #fedf89; }
.finding.info     { background: #eff8ff; border-color: #b2ddff; }

/* Prose blocks */
.prose-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px; }
.prose-card {
  background: var(--subtle);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
}
.prose-card .label {
  font-size: 11px; font-weight: 600; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.06em;
  margin-bottom: 6px;
}
.prose-card .body {
  font-size: 13.5px; color: var(--ink); line-height: 1.5;
}
.prose-single {
  background: var(--subtle);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-top: 12px;
}
.prose-single .label {
  font-size: 11px; font-weight: 600; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.06em;
  margin-bottom: 6px;
}
.prose-single .body {
  font-size: 13.5px; color: var(--ink); line-height: 1.5;
}

/* Proposed-fix and suggestions */
.fix-card {
  margin-top: 12px;
  padding: 12px 14px;
  border-radius: 8px;
  background: #eff8ff;
  border: 1px solid #b2ddff;
  font-size: 13px;
  color: #053258;
}
.fix-card .label {
  font-weight: 600; color: #175cd3; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.06em;
  margin-bottom: 4px;
}
.fix-card code {
  background: white; padding: 1px 6px; border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
}

.suggestion-list {
  margin-top: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.suggestion-row {
  display: grid;
  grid-template-columns: 80px 1fr 80px 1fr;
  gap: 12px;
  padding: 10px 14px;
  font-size: 13px;
  align-items: center;
  border-bottom: 1px solid var(--border);
}
.suggestion-row:last-child { border-bottom: none; }
.suggestion-row code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
.suggestion-row .conf {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  text-align: right;
}
.suggestion-row .reason { color: var(--muted); font-size: 12px; }

/* Decision badge inline */
.dbadge {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px;
  border-radius: 6px;
  font-size: 12px; font-weight: 600;
  letter-spacing: 0.02em;
  white-space: nowrap;
}
.dbadge.accept     { background: #ecfdf3; color: #067647; border: 1px solid #abefc6; }
.dbadge.quarantine { background: #fffaeb; color: #b54708; border: 1px solid #fedf89; }
.dbadge.reject     { background: #fef3f2; color: #b42318; border: 1px solid #fecdca; }

/* JE lines table */
.lines-table {
  margin-top: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
  font-size: 13px;
}
.lines-table .head {
  display: grid; grid-template-columns: 80px 1fr 120px 120px;
  gap: 12px;
  padding: 8px 14px;
  background: var(--subtle);
  font-size: 11px; font-weight: 600; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.06em;
  border-bottom: 1px solid var(--border);
}
.lines-table .row {
  display: grid; grid-template-columns: 80px 1fr 120px 120px;
  gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  font-variant-numeric: tabular-nums;
}
.lines-table .row:last-child { border-bottom: none; }
.lines-table .head > div:nth-child(3),
.lines-table .head > div:nth-child(4),
.lines-table .row  > div:nth-child(3),
.lines-table .row  > div:nth-child(4) {
  text-align: right;
}
.lines-table code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

/* Sidebar polish */
section[data-testid="stSidebar"] { background: var(--surface); border-right: 1px solid var(--border); }
section[data-testid="stSidebar"] .block-container { padding: 1.5rem 1rem; }
.sidebar-section-title {
  font-size: 11px; font-weight: 600;
  color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em;
  margin-bottom: 8px;
}

/* Tabs - tighter and cleaner */
.stTabs [data-baseweb="tab-list"] {
  border-bottom: 1px solid var(--border);
  gap: 0;
}
.stTabs [data-baseweb="tab"] {
  font-size: 13px;
  padding: 8px 16px;
  font-weight: 500;
}

/* Mode selector - segmented */
div[data-testid="stRadio"] > div {
  flex-direction: row;
  gap: 0;
  background: var(--subtle);
  padding: 4px;
  border-radius: 8px;
  border: 1px solid var(--border);
  display: inline-flex;
}
div[data-testid="stRadio"] label {
  padding: 6px 14px !important;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px !important;
  margin: 0 !important;
}
div[data-testid="stRadio"] label:has(input:checked) {
  background: var(--surface) !important;
  box-shadow: 0 1px 2px rgba(16,24,40,0.05);
}
</style>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pill(label: str, tone: str = "info") -> str:
    return f'<span class="pill {tone}"><span class="dot"></span>{label}</span>'


def _decision_badge(decision: str) -> str:
    return f'<span class="dbadge {decision}">{DECISION_COPY[decision]}</span>'


def _kpi_row(records: list[DecisionRecord]) -> None:
    counts = Counter(r.decision for r in records)
    total = len(records)
    html = f"""
    <div class="kpi-row">
      <div class="kpi accent-total">
        <div class="label">Entries reviewed</div>
        <div class="value">{total}</div>
        <div class="delta">across the batch</div>
      </div>
      <div class="kpi accent-accept">
        <div class="label">Accepted</div>
        <div class="value">{counts.get('accept', 0)}</div>
        <div class="delta">{counts.get('accept', 0) / total * 100:.0f}% of batch</div>
      </div>
      <div class="kpi accent-quarantine">
        <div class="label">Held for review</div>
        <div class="value">{counts.get('quarantine', 0)}</div>
        <div class="delta">awaiting reviewer sign-off</div>
      </div>
      <div class="kpi accent-reject">
        <div class="label">Rejected</div>
        <div class="value">{counts.get('reject', 0)}</div>
        <div class="delta">tier-c HALT or hard error</div>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def _section(title: str, hint: str = "") -> None:
    st.markdown(
        f'<div class="section-h"><h2>{title}</h2>'
        f'<span class="hint">{hint}</span></div>',
        unsafe_allow_html=True,
    )


def _je_lines_table(je) -> str:
    rows = ""
    for i, line in enumerate(je.lines):
        debit = f"{line.debit:,.2f}" if line.debit else "—"
        credit = f"{line.credit:,.2f}" if line.credit else "—"
        memo = line.memo or "<span style='color:#98a2b3'>no memo</span>"
        rows += (
            f'<div class="row">'
            f'<div><code>{line.account}</code></div>'
            f'<div>{memo}</div>'
            f'<div>{debit}</div>'
            f'<div>{credit}</div>'
            f'</div>'
        )
    return f"""
    <div class="lines-table">
      <div class="head">
        <div>Account</div><div>Memo</div><div>Debit</div><div>Credit</div>
      </div>
      {rows}
      <div class="row" style="background:var(--subtle); font-weight:600;">
        <div></div>
        <div>Total</div>
        <div>{je.total_debit:,.2f}</div>
        <div>{je.total_credit:,.2f}</div>
      </div>
    </div>
    """


def _findings_html(findings) -> str:
    if not findings:
        return ""
    out = '<div class="findings">'
    for f in findings:
        out += (
            f'<div class="finding {f.severity}">'
            f'<code>{f.code}</code>'
            f'<span>{f.message}</span>'
            f'</div>'
        )
    out += '</div>'
    return out


def _proposed_fix_html(pf) -> str:
    if pf is None:
        return ""
    return f"""
    <div class="fix-card">
      <div class="label">Proposed fix &middot; confidence {pf.confidence:.0%}</div>
      <div>Adjust line {pf.line_index} {pf.field} from
        <code>{pf.current_amount:,.2f}</code> →
        <code>{pf.proposed_amount:,.2f}</code>.</div>
      <div style="margin-top:6px;color:var(--muted);font-size:12px;">{pf.rationale}</div>
    </div>
    """


def _suggestions_html(suggestions) -> str:
    if not suggestions:
        return ""
    rows = ""
    for s in suggestions:
        rows += (
            f'<div class="suggestion-row">'
            f'<div><code>{s.candidate_code}</code></div>'
            f'<div>{s.candidate_name}</div>'
            f'<div class="conf">{s.confidence:.0%}</div>'
            f'<div class="reason">{s.reason}</div>'
            f'</div>'
        )
    return f'<div class="suggestion-list">{rows}</div>'


def _entry_index() -> dict[str, object]:
    """Map JE id → raw JournalEntry for line/amount rendering."""
    _, _, entries = _load_inputs()
    return {e.id: e for e in entries}


def _render_je_card(
    r: DecisionRecord,
    *,
    je_lookup: dict,
    prose_label: str = "Explanation",
) -> None:
    je = je_lookup.get(r.je_id)
    head = f"""
    <div class="je-card">
      <div class="je-head">
        <div>
          <div class="je-id">{r.je_id} &nbsp; {_decision_badge(r.decision)}</div>
          <div class="je-desc">{r.description}</div>
        </div>
        <div class="je-amount">
          <div>Debit <strong>{je.total_debit:,.2f}</strong></div>
          <div>Credit <strong>{je.total_credit:,.2f}</strong></div>
        </div>
      </div>
    """
    body = (
        _je_lines_table(je)
        + _findings_html(r.findings)
        + (
            f'<div class="prose-single"><div class="label">{prose_label}</div>'
            f'<div class="body">{r.explanation or "—"}</div></div>'
        )
        + _proposed_fix_html(r.proposed_fix)
        + _suggestions_html(r.suggestions)
    )
    st.markdown(head + body + "</div>", unsafe_allow_html=True)


def _render_je_compare_card(
    det: DecisionRecord, llm: DecisionRecord, *, je_lookup: dict
) -> None:
    je = je_lookup.get(det.je_id)
    head = f"""
    <div class="je-card">
      <div class="je-head">
        <div>
          <div class="je-id">{det.je_id} &nbsp; {_decision_badge(det.decision)}</div>
          <div class="je-desc">{det.description}</div>
        </div>
        <div class="je-amount">
          <div>Debit <strong>{je.total_debit:,.2f}</strong></div>
          <div>Credit <strong>{je.total_credit:,.2f}</strong></div>
        </div>
      </div>
    """
    prose = f"""
    <div class="prose-grid">
      <div class="prose-card">
        <div class="label">Deterministic prose</div>
        <div class="body">{det.explanation or "—"}</div>
      </div>
      <div class="prose-card">
        <div class="label">LLM prose</div>
        <div class="body">{llm.explanation or "—"}</div>
      </div>
    </div>
    """
    body = (
        _je_lines_table(je)
        + _findings_html(det.findings)
        + prose
        + _proposed_fix_html(det.proposed_fix)
        + _suggestions_html(det.suggestions)
    )
    st.markdown(head + body + "</div>", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Manual Adjustments Agent",
    page_icon="◆",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(CSS, unsafe_allow_html=True)

coa, period, fcy, entries = _load_inputs()
provider, provider_label, provider_tone = _provider_status()
je_lookup = {e.id: e for e in entries}

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.markdown(
    f"""
    <div class="app-header">
      <div class="brand">
        <div class="brand-mark">◆</div>
        <div>
          <h1>Manual Adjustments Agent</h1>
          <div class="subtitle">Period close · Journal-entry validation</div>
        </div>
      </div>
      <div class="meta">
        {_pill(f'Period {period}', 'info')}
        {_pill(f'Functional currency {fcy}', 'info')}
        {_pill(f'COA · {len(coa)} accounts', 'info')}
        {_pill(provider_label, 'ok' if provider_tone == 'ok' else 'warn')}
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown(
        '<div class="sidebar-section-title">Agent</div>',
        unsafe_allow_html=True,
    )
    agent_labels = {
        a["key"]: f"{a['name']} · {STATUS_PILL[a['status']][1]}" for a in AGENTS
    }
    selected_agent_key = st.radio(
        "Agent",
        options=[a["key"] for a in AGENTS],
        format_func=lambda k: agent_labels[k],
        index=[a["key"] for a in AGENTS].index("adjuster"),
        label_visibility="collapsed",
    )
    selected_agent = AGENT_BY_KEY[selected_agent_key]
    st.caption(selected_agent["purpose"])

    is_live_agent = selected_agent["status"] == "live"

    if is_live_agent:
        st.markdown(
            '<div class="sidebar-section-title" style="margin-top:1.5rem">Agent mode</div>',
            unsafe_allow_html=True,
        )
        mode = st.radio(
            "Mode",
            ["Deterministic", "LLM", "Compare"],
            index=0,
            label_visibility="collapsed",
            horizontal=True,
        )
        st.caption(
            {
                "Deterministic": (
                    "Pure mathematics. No API calls, no fuzzy-string library. "
                    "Mapping suggestions blend three hand-rolled signals: "
                    "**numeric prefix similarity**, **Levenshtein edit-distance "
                    "ratio**, and **Jaccard token overlap** — every line of math "
                    "is in `validators.py`. Prose comes from a fixed template."
                ),
                "LLM": "Same decisions and findings; explanation rewritten by Claude / OpenAI.",
                "Compare": "Runs both. Findings, decisions, fixes are identical — only prose differs.",
            }[mode]
        )

        st.markdown(
            '<div class="sidebar-section-title" style="margin-top:1.5rem">Filter decisions</div>',
            unsafe_allow_html=True,
        )
        show_accept = st.checkbox("Accept", value=True)
        show_quarantine = st.checkbox("Held for review", value=True)
        show_reject = st.checkbox("Reject", value=True)
    else:
        # Defaults so downstream code doesn't NameError when an unbuilt agent
        # is selected. They're never read in that branch, but keeping the
        # symbols defined prevents subtle bugs if a section is restructured.
        mode = "Deterministic"
        show_accept = show_quarantine = show_reject = True

    st.markdown(
        '<div class="sidebar-section-title" style="margin-top:1.5rem">Provider</div>',
        unsafe_allow_html=True,
    )
    if provider == "anthropic":
        st.success(f"Using {provider_label}")
    elif provider == "openai":
        st.info(f"Using {provider_label}")
    else:
        st.warning(
            "No `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` set. LLM mode falls back to "
            "the deterministic template — decisions still correct, prose just won't differ."
        )

    st.markdown(
        '<div class="sidebar-section-title" style="margin-top:1.5rem">About this slice</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Take-home for the AI Agentic Engineer role. Code does the math; "
        "the LLM only writes prose. Materiality thresholds match the policy "
        "Amit Patel confirmed via email **2026-04-30** — see "
        "`ASSUMPTIONS.md` A1 for the tier-a / tier-b / tier-c rules."
    )

# ---------------------------------------------------------------------------
# Coming-soon agents short-circuit the rest of the page.
# ---------------------------------------------------------------------------

if not is_live_agent:
    status_tone, status_label = STATUS_PILL[selected_agent["status"]]
    other_live = [a for a in AGENTS if a["status"] == "live"]
    other_live_html = " ".join(
        f"<a href='#' style='text-decoration:none'>{_pill(a['name'] + ' · live', 'ok')}</a>"
        for a in other_live
    )
    st.markdown(
        f"""
        <div class="je-card" style="padding:32px 36px;">
          <div style="display:flex;align-items:center;gap:14px;margin-bottom:6px;">
            <div style="font-size:11px;font-weight:600;letter-spacing:.08em;
                        color:var(--muted);text-transform:uppercase">
              Specialist agent
            </div>
            {_pill(status_label, status_tone)}
          </div>
          <h2 style="margin:0 0 4px 0;font-size:22px;letter-spacing:-.02em;
                     color:var(--ink);font-weight:600;">
            {selected_agent['name']}
          </h2>
          <div style="color:var(--muted);font-size:13px;margin-bottom:20px;">
            {selected_agent['tagline']}
          </div>
          <div style="font-size:14px;color:var(--ink);line-height:1.55;
                      max-width:680px;margin-bottom:24px;">
            {selected_agent['purpose']}
          </div>

          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;
                      margin-bottom:20px;">
            <div class="prose-card">
              <div class="label">Deterministic responsibility</div>
              <div class="body">{selected_agent['deterministic']}</div>
            </div>
            <div class="prose-card">
              <div class="label">LLM responsibility</div>
              <div class="body">{selected_agent['llm']}</div>
            </div>
          </div>

          <div style="border-top:1px solid var(--border);padding-top:16px;
                      display:grid;grid-template-columns:1fr 1fr;gap:24px;
                      font-size:13px;">
            <div>
              <div style="font-size:11px;font-weight:600;color:var(--muted);
                          text-transform:uppercase;letter-spacing:.06em;
                          margin-bottom:4px;">
                Where it lives
              </div>
              <code style="background:var(--subtle);padding:2px 6px;
                           border-radius:4px;font-size:12px;">
                {selected_agent['interface_file']}
              </code>
              <div style="color:var(--muted);margin-top:6px;font-size:12px;">
                See <code>ARCHITECTURE.md §2</code> for the full topology.
              </div>
            </div>
            <div>
              <div style="font-size:11px;font-weight:600;color:var(--muted);
                          text-transform:uppercase;letter-spacing:.06em;
                          margin-bottom:4px;">
                Currently live
              </div>
              <div>{other_live_html}</div>
              <div style="color:var(--muted);margin-top:6px;font-size:12px;">
                Pick a live agent in the sidebar to interact with it.
              </div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _section(
        "Why this isn't built yet",
        "scope of the take-home",
    )
    st.markdown(
        "The brief asks for **the architecture of the full system plus a "
        "working prototype of one slice**. The Adjuster was the chosen slice "
        "because it exercises every failure mode the brief lists (debit≠credit, "
        "unmapped accounts, circular IC, ambiguous categories) on bounded scope. "
        "The other agents are described in `ARCHITECTURE.md` but intentionally "
        "not built — over-shipping would have meant cutting depth on the slice "
        "that is built. See `PLANNING.md` for the slice ranking that produced "
        "this choice."
    )
    st.stop()


# ---------------------------------------------------------------------------
# Tabs (live agent)
# ---------------------------------------------------------------------------

tab_overview, tab_decisions, tab_audit, tab_policy = st.tabs(
    ["Overview", "Decisions", "Audit log", "Policy"]
)

# Pre-compute records (cached)
det_records = _run_deterministic(coa, entries)
llm_records = _run_llm(coa, entries, provider) if mode != "Deterministic" else det_records

if mode == "Deterministic":
    primary = det_records
    prose_label = "Explanation · template"
elif mode == "LLM":
    primary = llm_records
    prose_label = f"Explanation · {provider_label}"
else:
    primary = det_records
    prose_label = "see comparison view"

allowed = set()
if show_accept: allowed.add("accept")
if show_quarantine: allowed.add("quarantine")
if show_reject: allowed.add("reject")
filtered = [r for r in primary if r.decision in allowed]


# ----------------------------- Overview tab --------------------------------

with tab_overview:
    _kpi_row(primary)

    _section("Decision distribution", "deterministic counts; LLM mode does not change them")
    counts = Counter(r.decision for r in primary)
    df = pd.DataFrame(
        [
            {"Decision": DECISION_COPY[k], "Count": counts.get(k, 0)}
            for k in ["accept", "quarantine", "reject"]
        ]
    )
    col_chart, col_table = st.columns([2, 1])
    with col_chart:
        st.bar_chart(df.set_index("Decision"), height=220, color="#0f172a")
    with col_table:
        with st.container(border=True):
            for _, row in df.iterrows():
                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;"
                    f"padding:8px 4px;border-bottom:1px solid var(--border);"
                    f"font-size:13px'>"
                    f"<span>{row['Decision']}</span>"
                    f"<strong style='font-variant-numeric:tabular-nums'>{row['Count']}</strong>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

    _section("Findings by code", "what each detector caught across the batch")
    finding_rows = [
        {"Code": f.code, "Severity": f.severity, "JE": r.je_id}
        for r in primary for f in r.findings
    ]
    if finding_rows:
        fdf = pd.DataFrame(finding_rows)
        agg = fdf.groupby(["Code", "Severity"]).size().reset_index(name="Count")
        st.dataframe(
            agg,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Code": st.column_config.TextColumn("Finding code", width="medium"),
                "Severity": st.column_config.TextColumn("Severity", width="small"),
                "Count": st.column_config.NumberColumn("Count", width="small"),
            },
        )
    else:
        st.info("No findings on the seeded batch.")


# ----------------------------- Decisions tab -------------------------------

with tab_decisions:
    _kpi_row(primary)

    if not filtered:
        _section("No entries match the current filter")
        st.info("Toggle the decision filters in the sidebar to bring entries back.")
    else:
        _section(
            f"Journal entries · {len(filtered)} shown",
            f"mode: {mode}",
        )

        if mode == "Compare":
            llm_by_id = {r.je_id: r for r in llm_records}
            for det in filtered:
                _render_je_compare_card(
                    det, llm_by_id.get(det.je_id, det), je_lookup=je_lookup
                )
        else:
            for r in filtered:
                _render_je_card(r, je_lookup=je_lookup, prose_label=prose_label)


# ----------------------------- Audit log tab -------------------------------

with tab_audit:
    _section(
        "Append-only event log",
        "every decision, finding, and tier-a auto-correction is one JSON line",
    )
    log_path = ROOT / "output" / "audit_log.jsonl"
    if not log_path.exists():
        st.info(
            "No `output/audit_log.jsonl` yet. Run `python3 main.py --inputs ../inputs "
            "--out output` from the prototype directory to generate it."
        )
    else:
        import json as _json
        events = [_json.loads(l) for l in log_path.read_text().strip().splitlines()]
        kinds = sorted({e["event"] for e in events})
        sel = st.multiselect("Event types", kinds, default=kinds)
        rows = [e for e in events if e["event"] in sel]
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)


# ----------------------------- Policy tab ----------------------------------

with tab_policy:
    _section(
        "Materiality policy",
        "confirmed by Amit Patel · email 2026-04-30",
    )
    policy_df = pd.DataFrame(
        [
            {
                "Tier": "a — auto-correct",
                "Trigger": "|imbalance| ≤ $500",
                "Decision": "Accept",
                "Audit action": "Plug to rounding account; structured `auto_correction` event",
                "Halts close?": "No",
            },
            {
                "Tier": "b — warn band",
                "Trigger": "$500 < |imbalance| ≤ $10,000",
                "Decision": "Accept (with reconciliation note)",
                "Audit action": "`IMBALANCE_WARN` finding · reviewer sign-off required",
                "Halts close?": "No",
            },
            {
                "Tier": "c — halt",
                "Trigger": "|imbalance| > $10,000  ·  BS does not foot",
                "Decision": "Reject",
                "Audit action": "Raw `UNBALANCED` finding · structured error to user",
                "Halts close?": "Yes",
            },
        ]
    )
    st.dataframe(
        policy_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Tier": st.column_config.TextColumn("Tier", width="small"),
            "Trigger": st.column_config.TextColumn("Trigger", width="medium"),
            "Decision": st.column_config.TextColumn("Decision", width="medium"),
            "Audit action": st.column_config.TextColumn("Audit action", width="large"),
            "Halts close?": st.column_config.TextColumn("Halts close?", width="small"),
        },
    )

    _section("Other policy answers", "")
    st.markdown(
        "**Unmapped accounts.** Never auto-create COA nodes. TB orphans go to "
        "the `SUSPENSE` bucket with a human-readable escalation. Ambiguous "
        "(`TBD`) categories carry confidence < 1.0 and require reviewer sign-off. "
        "Partial output beats a hard stop."
    )
    st.markdown(
        "**Missing FX rates.** Fall back to the most recent available rate; "
        "flag the cell as `ESTIMATED RATE—period-end unavailable`; log the "
        "fallback. Do not halt. Interpolation and external-API calls are "
        "forbidden."
    )
    st.markdown(
        "**Audit log requirement.** Every threshold-based correction is "
        "machine-readable, with source entry, delta, and action. No silent "
        "fixes — see `prototype/output/audit_log.jsonl`."
    )
