# AI Agentic Engineer — Take-Home

Designing an agentic system for ERP-driven financial statement generation.
The brief asks for an architecture document plus a working prototype of
**one** slice. This repo delivers both, against the seeded messy data in
`inputs/`.

## Headline result

The chosen slice — the **Manual Adjustments Agent** — runs end-to-end on the
10 seeded journal entries against the materiality policy confirmed by Amit
Patel via email on **2026-04-30** (see `ASSUMPTIONS.md` A0–A3):

| Decision | Count | Examples |
|---|---|---|
| **Accept** | 8 | 7 clean entries plus JE-002 ($3,500 imbalance, **tier-b warn band**: accepted with reconciliation note + `IMBALANCE_WARN` finding; proposed fix raises credit line to 28,500 for reviewer sign-off). |
| **Quarantine** | 2 | JE-005 (`6315` not in COA → suggests `6310`), JE-008 (circular IC on `2170`). |
| **Reject** | 0 | No seeded JE breaches the $10k tier-c HALT threshold or trips a hard structural defect (header posting, negative amount). |

Findings, suggestions, plain-English LLM explanations, and (for tier-a
auto-corrections) structured `auto_correction` audit events land in
`prototype/output/` and are also viewable in the Streamlit dashboard
(`prototype/streamlit_app.py`).

## Reading order

1. **`ARCHITECTURE.md`** — main artifact. Agent topology (Mermaid diagram in §2),
   the deterministic/LLM line, every failure mode mapped to a detector and a
   response, defects discovered in the sample data beyond what the brief
   listed (§6), validation/self-correction loop, and auditor-trace demo.
2. **`PLANNING.md`** — pre-build planning, ranking of the four slice options,
   why Manual Adjustments was picked.
3. **`prototype/README.md`** — how to run the prototype, what's deterministic
   vs. LLM, expected per-JE decisions, limitations.
4. **`REFLECTION.md`** — required 1-page reflection: what changes with 3
   months, where this breaks at scale, AI tool usage, what the brief
   underestimates.
5. **`ASSUMPTIONS.md`** — every threshold and policy with the config knob
   that controls it.
6. **`email_draft.md`** — the 3 clarifying questions that would precede this
   work.

## Run the prototype

```bash
cd prototype
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# template explanations — zero deps, no API key needed
python3 main.py --inputs ../inputs --out output

# LLM-generated explanations — Anthropic preferred, OpenAI fallback
ANTHROPIC_API_KEY=sk-ant-... python3 main.py --inputs ../inputs --out output --llm
python3 -m unittest discover tests
```

## (Optional) Run the Streamlit dashboard

A reviewer dashboard that reads `prototype/output/decisions.json` and shows
all five specialist agents from `ARCHITECTURE.md` §2 — only the Adjuster is
live; the others render a "Coming soon" spec card so the full system
surface is visible without overstating what ships.

```bash
cd prototype
streamlit run streamlit_app.py   # http://localhost:8501
```

For the live Adjuster, the sidebar offers three modes:
- **Deterministic** — pure mathematics, no API calls (Levenshtein + Jaccard + numeric prefix)
- **LLM** — same decisions and findings, prose rewritten by Claude / OpenAI
- **Compare** — both, side-by-side

## Repo layout

```
.
├── ARCHITECTURE.md       ← main deliverable: full-system design
├── PLANNING.md           ← pre-build planning + slice ranking
├── REFLECTION.md         ← 1-page reflection (required)
├── ASSUMPTIONS.md        ← config-knob index for every threshold
├── email_draft.md        ← 3 clarifying questions
├── inputs/               ← provided messy data (untouched)
└── prototype/            ← Manual Adjustments Agent (the chosen slice)
    ├── adjuster/         ← loader → validators → pipeline → llm
    ├── streamlit_app.py  ← reviewer dashboard
    ├── tests/
    └── output/           ← decisions.json, audit_log.jsonl, report.md
```
