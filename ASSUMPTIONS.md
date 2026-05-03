# Working assumptions

Each assumption maps to one of the three clarifying questions or to a defect
discovered in the data. If a reviewer disagrees, the place to change behavior
is named in the **Lever** column.

> **A0 — Policy provenance.** A1, A2, and A3 are not assumptions in the
> usual sense: they were sent as clarifying questions and answered by Amit
> Patel via email on **2026-04-30**. The policy below transcribes that reply.
> Where this slice extends Amit's policy beyond what was literally asked
> (e.g., applying the FX-rounding tier to non-FX JE imbalances in this
> validator), the extension is called out inline. A4–A15 remain working
> assumptions over the data.

---

## Assumptions tied to clarifying questions

### A1 — Materiality thresholds (Q1) — **confirmed by Amit 2026-04-30**

Three named tiers for any debit/credit imbalance, plus an absolute BS tie-out
rule. Every threshold-based correction is logged.

| Tier | Trigger | Response | Lever |
|---|---|---|---|
| **a — auto-correct** | abs(diff) ≤ $500 (FX rounding scope) | Auto-correct via plug to `7300 FX Gain/Loss - Realized`; emit a structured `auto_correction` event to the audit log with source entry, delta, and action. **Never silent.** | `config.materiality.fx_rounding_abs` |
| **b — warn** | $500 < abs(diff) ≤ $10,000 | Accept the entry; emit an `IMBALANCE_WARN` finding (severity=warning); produce the statement with a reconciliation note. **Do not halt.** Reviewer sign-off required before close. | `config.materiality.je_warn_band_max` |
| **c — halt** | abs(diff) > $10,000, **or** BS does not foot (Assets ≠ Liabilities + Equity) | HALT and surface a structured error to the user before producing output. | `config.materiality.je_halt_threshold`, `config.materiality.bs_tieout_abs` |
| JE individual line | Must be exact (no tolerance) | Hard reject the line | `config.materiality.je_line_tolerance` |

**This-slice extension.** Amit's tier-a is worded for FX rounding
specifically. The Adjuster slice does not run FX translation, so it cannot
distinguish FX-rounding noise from arbitrary sub-$500 imbalance. We apply
tier-a uniformly to any sub-$500 JE imbalance, with the plug routed to the
configured rounding account and the audit event emitted regardless of source.
Reviewers who want a stricter read can lower `fx_rounding_abs` to 0; the
behavior is config-driven, not hard-coded.

### A2 — Unmapped account handling (Q2) — **confirmed by Amit 2026-04-30**

- Agent **never** auto-creates COA nodes. The COA is the source of truth.
- **TB account not in COA** (`9999 Suspense - Unmapped`): quarantine the row,
  assign it a `SUSPENSE` holding bucket, escalate with a human-readable
  explanation (e.g., "Account 9999 — no COA node found. Possible rename or
  missing mapping.").
- **COA account marked `TBD` or ambiguous**: include in output with a
  `confidence < 1.0` and flag for reviewer sign-off. Do not silently assign.
- **JE references unmapped account** (JE-005 → `6315`): quarantine the entry,
  generate ranked mapping candidates with confidence scores, require human
  acceptance before posting. The agent **chooses** among code-generated
  candidates; it never mints one.
- **No hard stop on a single unmapped account.** Partial output with clear
  quarantine is more useful than rejecting the entire dataset — Amit's
  multi-entity production data routinely contains renamed/legacy accounts.

### A3 — FX policy (Q3) — **confirmed by Amit 2026-04-30**

- **Missing period-end rate:** use the most recent available rate from
  `fx_rates.csv`, mark the cell/account in output as
  `"ESTIMATED RATE—period-end unavailable"`, log the fallback to the audit
  trail. **Do not halt** the close.
- **Missing period-average rate** (P&L translation): same fallback logic.
- **Forbidden:** interpolation, calling an external API, or silently
  approximating without a flag. Work only with what is in `fx_rates.csv`.
- **Translation method:** current-rate (everything at period-end; differences
  to `3310 FX Translation Reserve`). This was not in Amit's reply but is
  consistent with the COA (`3310` exists) and aligns with his "do not halt"
  posture — temporal-method translation would have been called out if it
  were the policy. Documented as a residual assumption pending Q3-bis.

---

## Assumptions from data inspection

### A4 — Duplicate account codes
The two `6310` rows in the TB are summed (combined debit = 283,500). No
warning emitted; this is a normal multi-row export pattern.

### A5 — Renamed accounts across periods
Prior-period `6905 Sundry Operating Expenses` is treated as **retired**: its
opening balance does not roll forward to the current period. Surfaced as an
info-level finding in the reconciliation report so a human can confirm.

### A6 — Sign-flippy contras
`7310 FX Gain/Loss - Unrealized` is typed Expense/Debit in the COA but holds
a credit balance in the TB. Treated as valid: contra-style accounts flip
sign in normal use. The validator flags it only if magnitude exceeds
materiality.

### A7 — TBD cash-flow categories
`1150 Other Current Assets` and `2170 Intercompany Payable` are mapped to
`Operating` for cash-flow purposes, with a human-review flag emitted.

### A8 — Empty COA header
`1290 Other Assets` has no children. Treated as valid (header reserved for
future use), not a defect. Quietly excluded from BS roll-up.

### A9 — Period and functional currency
- Period: 2024-Q4
- Functional currency: USD
- Both per the inputs README; not parameterized.

### A10 — Idempotency
Each JE has an `id` field which serves as the idempotency key. Re-running the
same batch produces identical decisions and emits no duplicate postings.

### A11 — Functional-currency exemption from FX rate lookup
`USD` has no `opening` rate in `fx_rates.csv`. This is intentional: the
functional currency does not need a translation rate. The FX agent skips the
lookup when `currency == functional_currency`. Any *other* missing rate
falls back to the most recent available rate per A3 (Amit 2026-04-30) and
the cell is flagged `ESTIMATED RATE—period-end unavailable`; it does not
halt the close.

### A12 — Tiered handling for unbalanced JEs
Mirror of A1 expressed in terms of pipeline behavior. Implemented in
`prototype/adjuster/pipeline.py::_decide_unbalanced`:

| Tier | Trigger | Response | Audit |
|---|---|---|---|
| a | abs(diff) ≤ `materiality.fx_rounding_abs` ($500) | Replace UNBALANCED with `AUTO_CORRECTED` (info); decision = **accept**; surface plug in `proposed_fix`. | Additional `auto_correction` event in `audit_log.jsonl` carrying source entry, delta, action, plug. |
| b | $500 < abs(diff) ≤ `materiality.je_warn_band_max` ($10k) and a single-line fix is proposable | Replace UNBALANCED with `IMBALANCE_WARN` (warning); decision = **accept** with reconciliation note; `proposed_fix` surfaced for reviewer sign-off. | The `IMBALANCE_WARN` finding event is itself the reconciliation-note record. |
| c | abs(diff) > `materiality.je_halt_threshold` ($10k), or warn-band imbalance with no proposable fix | Decision = **reject**; raw `UNBALANCED` finding (error) preserved. | Standard `finding` + `decision` events. |

**Seeded-data behavior:**
- JE-002 ($3,500) → tier-b: accepted with `IMBALANCE_WARN` and a proposal
  to raise the credit line from 25,000 to 28,500, confidence 85%.
- No seeded JE falls in tier-a; the auto-correction path is exercised by
  `tests/test_integration.py::TestAutoCorrectionEvent` via a synthetic JE.
- No seeded JE breaches tier-c; rejection is exercised via the hard-error
  paths (POSTING_TO_HEADER, etc.) in the validator unit tests.

### A13 — Mapping is prefix-only in the prototype
The shipped Adjuster uses prefix similarity to suggest mappings for unmapped
codes (sufficient to surface `6310` for JE-005's `6315`). The production
Mapper described in `ARCHITECTURE.md` §2 takes the same code-generated
candidate set and asks the LLM to choose using account name, parent code,
prior-period analogues, and amount magnitude. Same interface, different
selector implementation.

### A14 — JE production metadata absent from inputs
The seeded JEs do not carry `approver`, `evidence_link`, or `posted_by`
fields. The prototype tolerates this (each is optional in the loader). For
production, these become required at the API boundary — they are SOX 404
evidence, not nice-to-haves. Surfaced as a per-JE warning when absent.

### A15 — Order of operations is pipeline-pinned
The Orchestrator pins the order: ingest → map → **adjust** → translate →
assemble → validate. JE-003 (EUR cash uplift to period-end rate) only makes
sense when adjustments post before FX translation; running translation first
would either double-count or strand JE-003. An out-of-order run is a
configuration error, not an agent decision.

---

## How to override

All thresholds and policies live in `prototype/adjuster/config.py`. Any
assumption above can be flipped without touching validation logic.
