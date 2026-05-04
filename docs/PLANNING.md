# AI Agentic Engineer Assignment — Planning Document

This document captures the pre-build thinking for the assignment: the three
clarifying questions to send before starting, the prototype slice to build, and
the outline of the architecture doc.

---

## 1. Clarifying questions to send (max 3)

You only get 3, so each one has to unblock multiple downstream decisions.

### Q1 — Materiality and tolerance thresholds

> For the period close, what's the materiality threshold for (a) TB
> debit/credit imbalance, (b) FX rounding residuals, and (c) BS asset =
> liability + equity tie-out? Is there a documented policy, or should we use
> a default like the lower of $X or 0.5% of total assets?

**Why this is the highest-value question:** it determines whether the validator
rejects, warns, or auto-posts a rounding plug. Without it you can't define the
validation loop, the human-in-the-loop trigger, or the rounding-plug behavior.
Also signals you understand how real close processes work.

### Q2 — Authority of the COA vs. authority of the source ERP

> When the TB contains an account code that's not in the COA (e.g.,
> `9999 Suspense`, or a JE references `6315`), is the agent allowed to
> (a) auto-create a COA node under a default parent, (b) propose a mapping to
> a human, or (c) hard-reject the whole batch? Same question for ambiguous
> `cf_category` values like `TBD`.

**Why:** this is the difference between a mapper that *suggests* and a mapper
that *acts*, and it's the core of the whole assignment. It also flushes out who
owns the COA — finance ops vs. the system.

### Q3 — FX policy for missing rates and translation method

> For the missing GBP period-end rate: should the agent (a) block the run,
> (b) use the period-average as a fallback, or (c) carry the prior period-end
> rate forward? And: are we doing temporal-method translation (monetary at
> period-end, non-monetary at historical) or current-rate translation
> (everything at period-end with the difference to CTA)?

**Why:** without this you can't translate the multi-currency cash rows
correctly, and the answer determines whether `3310 FX Translation Reserve`
moves at all. It also signals you know that "FX revaluation" and
"FX translation" are different operations — real domain humility.

### Questions deliberately NOT asked

- "Is the suspense account 9999 a real defect or should it stay?" — too
  narrow; handled by Q2's answer.
- "What ERP are we actually targeting?" — low signal; the assignment says
  "NetSuite-style" and the format is generic.
- "Do you want SOCIE in the prototype?" — the brief says pick one slice.

---

## 2. Slice to build: Manual Adjustments Agent

### Ranking of the four slice options

| Slice | Signal density | Risk | Verdict |
|---|---|---|---|
| TB→COA mapper | High — confidence scores, HITL escalation, embedding similarity | Tempting but everyone picks this; reviewers have seen 50 of them | Skip |
| **Manual adjustments validator** | **Very high — touches every failure mode in the brief** | **Bounded scope; data has 3+ defects in 10 entries** | **Pick this** |
| P&L + BS generator with verifier | Medium — mostly arithmetic; the LLM "verifier" is hard to make non-trivial | Easy to ship something polished but really just a calculator | Skip |
| Reconciliation agent | High but requires generating two TBs to compare first → scope creep | Best as a follow-on to the adjustments agent | Skip |

### Why the adjustments agent wins on the rubric specifically

- **Problem decomposition:** forces you to separate (1) structural validation
  = pure code (debits = credits, account exists, dates in period),
  (2) semantic validation = LLM (does the memo match the lines? is this really
  a reclass or a reversal?), (3) policy validation = config-driven rules
  (materiality, approver tier).
- **Agentic judgment:** the strongest answer is *"the LLM does not validate
  arithmetic — it explains rejections in plain English and flags semantic
  anomalies."* That's exactly the "knows when NOT to use an LLM" signal.
- **Production thinking:** each JE has an `id` and `source` field already, so
  idempotency and traceability fall out naturally. Quarantine queue is a real
  concept finance teams understand.
- **Domain humility:** forces you to confront that JE-008 (circular IC) is
  *technically valid* (debits = credits) but *economically meaningless* —
  exactly the kind of nuance the brief hints at.

### Concrete output for the slice

For each of the 10 JEs, emit:

```json
{
  "id": "JE-XXX",
  "decision": "accept | reject | quarantine",
  "reasons": ["..."],
  "plain_english_explanation": "...",
  "suggested_fix": "..."
}
```

Expected decisions per the seeded data (pre-build prediction):

| JE | Expected decision | Reason |
|---|---|---|
| JE-001 | accept | Bonus accrual, balanced |
| JE-002 | reject † | debit 28,500 ≠ credit 25,000 |
| JE-003 | accept | FX reval, balanced |
| JE-004 | accept | Bad debt provision, balanced |
| JE-005 | quarantine | Account 6315 not in COA — propose mapping with confidence |
| JE-006 | accept | Depreciation catch-up, balanced |
| JE-007 | accept | Deferred tax true-up, balanced |
| JE-008 | quarantine | Circular IC — same account 2170 debited and credited; economically null |
| JE-009 | accept | Legal accrual, balanced |
| JE-010 | accept | LT debt reclass, balanced |

> † **Updated post-policy.** This pre-build prediction assumed a strict
> "imbalance → reject" policy. Amit Patel's reply on **2026-04-30**
> introduced a tiered materiality policy (≤$500 auto-correct; $500–$10k
> WARN with reconciliation note, do not halt; >$10k HALT). Under that
> policy, JE-002's $3,500 imbalance is **tier-b → accept (warn)** with
> an `IMBALANCE_WARN` finding and a proposed credit-line adjustment for
> reviewer sign-off. The shipped Adjuster implements the post-policy
> behavior; `ASSUMPTIONS.md` A1 + A12 trace the provenance.

---

## 3. Architecture document outline

Target ~6–8 pages. Each section has one key claim it must land.

### 1. Problem framing (½ page)
- "Generate financials" decomposes into 7 sub-problems with very different
  reliability bars: ingest, FX translation, COA mapping, adjustment
  validation, statement assembly, tie-out validation, traceability.
- **Land:** arithmetic is non-negotiable; semantic interpretation is where AI
  earns its keep.

### 2. Agent topology (1 page) — answers brief's first question
- Recommend: **orchestrator + narrow specialist agents + deterministic core**,
  not a flat swarm.
- Specialists: `Mapper` (TB↔COA), `Adjuster` (JE validator), `Translator`
  (FX), `Assembler` (statement builder — **deterministic, no LLM**),
  `Reconciler`, `Auditor` (validation loop).
- Justify: specialists keep prompts narrow → cheaper, easier to eval, easier
  to swap for code when an agent turns out to be over-engineered.
- Include a diagram. Solid line = data, dashed line = agent calls, so the
  reader can see how thin the LLM surface actually is.

### 3. The deterministic / LLM line (1 page) — answers brief's second question

| Deterministic | LLM |
|---|---|
| Arithmetic, double-entry checks | Fuzzy account-name matching (over a code-generated candidate set) |
| FX math | Plain-English rejection explanations |
| Hierarchy roll-ups | Anomaly flagging on JE memos |
| BS tie-out | Ambiguous-category resolution |
| Statement layout | One-shot reconciliation narratives |
| Idempotency keys, lineage graph | Confidence scores on suggestions |

**Land: the LLM never produces a number that ends up on a statement.** It only
produces labels, explanations, and confidence scores.

### 4. Data and lineage model (1 page) — sets up traceability
- Every posted line has: `source_id` (TB row, JE id, system-generated),
  `transformation_chain` (FX-translated, reclassed, eliminated),
  `agent_id + prompt_hash` if an agent touched it.
- Append-only event log; statements are projections.
- Show: from a single BS cell, query returns the N source rows that sum to it.

### 5. Failure modes (1.5 pages) — answers brief's third question

Table: *failure mode → detector (code or agent) → response (block / quarantine
/ auto-fix / warn)*. Cover the brief's five plus the ones in the data.

| Failure mode | Detector | Response |
|---|---|---|
| Hallucinated mapping | Code generates candidate set; LLM picks + scores | LLM cannot mint mappings; only chooses |
| Debit ≠ credit | Code | Reject JE |
| No COA fit | Code | Quarantine with LLM-generated suggestions |
| FX rate gap | Code | Block, surface to user |
| Circular IC | Code (account_in == account_out) | Quarantine, LLM-explained |
| Duplicate account codes | Code | Sum + warn |
| Ambiguous cf_category (TBD) | Code finds; LLM proposes | Quarantine for review |
| Renamed account across periods | Code (set diff prior vs current) | Surface + LLM suggests successor |
| Missing period-end rate | Code | Block |
| Suspense dumping ground | Code (>materiality threshold) | Warn |
| Sign-flippy contra (7310) | Code (sign vs normal_balance) | Allow, but flag |

### 6. Validation and self-correction loop (1 page) — fourth question
- Pre-flight checks (run before any LLM call): TB sums, JE sums, account-set
  diff vs COA, FX rate coverage.
- Post-assembly checks: BS A=L+E, P&L net income flows to RE, cash flow ties
  to BS cash delta, opening equity = prior-period closing.
- On failure: agent gets the **specific check that failed + the data**, not a
  generic "fix it" prompt. Bounded retry (≤2). On second failure → human queue
  with structured diff.
- **Land:** the loop is bounded and the LLM is told exactly what failed.

### 7. Auditor's-eye traceability (½ page) — fifth question
- "Click any cell" demo: BS cell → roll-up children → leaf accounts →
  posted lines → source (TB row + adjustments) → original CSV row number /
  JE id.
- Every agent decision is a row in the event log with prompt + model +
  confidence + human override status.

### 8. Out of scope (¼ page)
- Multi-entity consolidation, intercompany elimination matrices, tax
  provisioning logic, period-locking, GAAP↔IFRS — named explicitly so
  reviewers know you know they exist.

### 9. The prototype (½ page)
- Points to the adjustments-agent slice in the repo. One paragraph on how it
  instantiates the architecture above, so the doc and the code are clearly
  the same system.

### Reflection (separate, 1 page)
- **3-month version:** real eval harness, golden-set of past closes, COA
  versioning, full lineage UI.
- **Where it breaks at scale:** COA matching becomes O(accounts × candidates);
  per-JE LLM calls explode cost — needs candidate caching + batch validation.
- **AI tools:** candid notes on where Claude/Cursor helped (boilerplate, COA
  parsing) vs misled (initially over-agentified the assembler).
- **One thing they're underestimating:** the COA itself is the product. Every
  interesting failure mode in the data is really a COA-quality problem in
  disguise. A great agent against a bad COA still produces wrong statements.

---

## 4. Defects already spotted in the input data

Found by inspection before any code runs. The PDF lists some seeded defects;
the inputs README warns there are more.

- **TB duplicate:** `6310 Travel and Entertainment` appears twice (two rows).
- **TB orphan:** `9999 Suspense - Unmapped` is not in the COA.
- **TB multi-currency:** `1110` has EUR/GBP rows in original currency; needs
  FX translation to USD before summing.
- **COA ambiguous cf_category:** `1150` and `2170` marked `TBD`.
- **COA empty header:** `1290 Other Assets` has no children.
- **COA odd:** `7310 FX Gain/Loss - Unrealized` typed Expense with Debit
  normal balance, but has a credit (gain) in TB — sign-flippy account.
- **JE-002 unbalanced:** debit 28,500 vs credit 25,000.
- **JE-005 unmapped:** account `6315` not in COA.
- **JE-008 circular IC:** same account `2170` debited and credited — net zero.
- **fx_rates missing:** GBP has no `period_end` rate, so GBP cash on the BS
  can't be translated.
- **Prior-period rename:** `6905 Sundry Operating Expenses` exists in prior
  period but not in current TB or COA — likely renamed/retired.
- **TB imbalance:** total debits ≠ total credits in the raw TB before any
  reconciliation.
