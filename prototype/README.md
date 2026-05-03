# Manual Adjustments Agent — Prototype

One slice of the agentic financial-reporting system: validates a batch of
manual journal entries against a chart of accounts, decides accept / reject /
quarantine, and emits plain-English explanations for each decision.

## Why this slice

See `../PLANNING.md` for the full reasoning. Short version: this slice
exercises every failure mode in the brief (debit ≠ credit, unmapped
accounts, circular intercompany, ambiguous categories) on bounded scope, and
it forces a clean separation between deterministic validation and
LLM-generated explanation.

## What's deterministic vs. LLM

| Layer | Implementation |
|---|---|
| Structural validation (balance, account existence, sign rules, dates) | Pure code (`adjuster/validators.py`) |
| Decision logic (accept / reject / quarantine + rule mapping) | Pure code (`adjuster/pipeline.py`) |
| Mapping suggestions for unknown accounts (candidate generation + ranking) | Pure code (prefix + edit distance) |
| Plain-English explanation of the decision | LLM with deterministic template fallback (`adjuster/llm.py`) |

The LLM never produces a number, never decides accept/reject, and never
mints a mapping. It chooses among code-generated candidates and writes
human-readable prose.

## Setup

```bash
cd prototype
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt    # only needed for LLM mode
```

Stdlib-only mode works with no install.

## Run

```bash
# template explanations (no API key needed)
python3 main.py --inputs ../inputs --out output

# LLM-generated explanations — Anthropic preferred, OpenAI fallback
ANTHROPIC_API_KEY=sk-ant-... python3 main.py --inputs ../inputs --out output --llm
# or
OPENAI_API_KEY=sk-...        python3 main.py --inputs ../inputs --out output --llm
```

Provider preference (in `adjuster/llm.py`): Anthropic if `ANTHROPIC_API_KEY`
is set → OpenAI if `OPENAI_API_KEY` is set → deterministic template if
neither. The `--llm` flag never crashes on missing keys; it falls through to
the template.

Outputs land in `output/`:
- `decisions.json` — structured per-JE decision record
- `report.md` — human-readable summary for finance reviewers
- `audit_log.jsonl` — append-only event log; one line per finding

## Expected decisions on the seeded inputs

Headline counts on the bundled batch: **8 accept / 2 quarantine / 0 reject**
under the materiality policy confirmed by Amit Patel on 2026-04-30 (see
`../ASSUMPTIONS.md` A1).

| JE | Decision | Why |
|---|---|---|
| JE-001 | accept | Balanced bonus accrual |
| JE-002 | accept (warn) | Debit 28,500 ≠ credit 25,000; $3,500 imbalance falls in tier-b warn band ($500–$10k). Accepted with `IMBALANCE_WARN`; proposed fix raises credit line to 28,500 (confidence 85%) for reviewer sign-off. |
| JE-003 | accept | Balanced FX revaluation |
| JE-004 | accept | Balanced bad-debt provision |
| JE-005 | quarantine | Account `6315` not in COA — proposes `6310` |
| JE-006 | accept | Balanced depreciation catch-up |
| JE-007 | accept | Balanced deferred-tax true-up |
| JE-008 | quarantine | Same account `2170` debited and credited — economically null |
| JE-009 | accept | Balanced legal accrual |
| JE-010 | accept | Balanced LT-debt reclass |

No seeded JE falls in tier-a (auto-correct, ≤$500). The
`auto_correction` audit event path is exercised in
`tests/test_integration.py::TestAutoCorrectionEvent` with a synthetic JE.

## Tests

```bash
python3 -m unittest discover tests
```

## Layout

```
prototype/
├── main.py                  # CLI entry
├── requirements.txt
├── adjuster/
│   ├── __init__.py
│   ├── config.py            # thresholds, period, materiality
│   ├── models.py            # dataclasses (JE, Finding, Decision)
│   ├── loader.py            # CSV/JSON ingestion
│   ├── validators.py        # deterministic checks
│   ├── llm.py               # optional explainer with template fallback
│   └── pipeline.py          # orchestrates check → decide → explain
├── tests/
│   └── test_validators.py
└── output/                  # generated artifacts (gitignored)
```

## Validators implemented but not triggered by the seeded data

- **`DATE_OUT_OF_PERIOD`** — implemented in `validators.py`, but every JE in
  `manual_adjustments.json` is dated within 2024-Q4, so it never fires on
  this dataset. Included for completeness; covered by a unit test.

## Limitations

This is one slice. It assumes a valid COA file, does not perform FX
translation, and does not assemble statements. See `../ARCHITECTURE.md`
for where this slice sits in the full system.
