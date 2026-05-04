# One-page reflection

## A note on the materiality policy

The thresholds and tier behavior used in this slice were not picked from a
hat. Three clarifying questions were sent to Amit Patel before any code
was written, and he replied on **2026-04-30** with a tiered policy:
≤$500 auto-correct (silent + logged), $500–$10k WARN with
reconciliation note (do not halt), >$10k or BS that does not foot HALT.
The Adjuster's pipeline tiers, the `auto_correction` audit event, the
`IMBALANCE_WARN` finding code, and the FX-rate fallback all transcribe that
reply directly. ASSUMPTIONS.md A0 traces the provenance.

## A demonstration: putting the LLM in the arithmetic seat

The repo ships a second LLM variant — `prototype/adjuster/langgraph/graph.py` — that
uses LangGraph + Chroma RAG to let the LLM run the structural, existence,
and semantic checks against retrieved policy and COA snippets. It *works*.
On the seeded data it produces decisions broadly aligned with the
deterministic version. But it's strictly worse along every axis I care
about: ~5,000× slower per JE (an LLM round-trip vs. a Python `if`), real
dollars per close (every batch is N+1 model calls), non-deterministic
output (you cannot replay it bit-for-bit), and the auditor cannot point
to a line of code that produced "debit ≠ credit by $3,500" — the model
did. The Streamlit "Compare" view renders all three modes side-by-side
per JE so a reviewer sees the gap rather than reading me claim it. That's
the case for the *deterministic-with-LLM-prose-only* default the
Adjuster ships with.

## What I'd build differently with 3 months instead of 8 hours

A real eval harness comes first. The prototype today pins decisions for
10 hand-coded JEs; a real system needs a labeled corpus of historical
adjustments — accepted, rejected with reason, quarantined and resolved —
to drive (a) a regression suite for the deterministic checks and (b) an
offline benchmark for the LLM explainer and mapper. Without that, every
prompt change is a vibes-based migration.

After the eval harness: a versioned COA. The COA is itself a slowly
mutating dataset (renames, splits, retirements, re-parenting), and almost
every interesting failure in the seeded data is downstream of a COA edit
that wasn't propagated. A COA service with effective dates, change
events, and a successor-account graph would absorb half the complexity
the agent currently has to compensate for.

Then the lineage UI. The architecture treats statements as projections
over an event log; the prototype writes JSONL but doesn't yet *render*
the click-through trail. Three months gets you the auditor demo: open a
BS cell, see every source row, every JE, every agent decision with prompt
+ model + confidence, and the override history.

## Where the prototype breaks at scale

- **Mapping cost.** Per-JE LLM calls are fine for a 10-entry batch and
  ruinous for a 50,000-entry month. Fix: candidate generation stays
  per-row but LLM selection runs in batches with prompt caching, and the
  high-confidence cases (>0.9) skip the LLM entirely.
- **COA matching is O(accounts × candidates).** With 5,000-account
  enterprise COAs, naive prefix scoring becomes the bottleneck. Fix:
  pre-compute name embeddings, use ANN search to get top-K candidates
  in O(log n), then refine with prefix + edit distance.
- **Multi-entity consolidation.** The current architecture is
  single-entity. Adding consolidation means an entity dimension on every
  event, an eliminations matrix as a first-class data structure, and CTA
  computation per sub. The validation loop's tie-outs change shape:
  consolidated A=L+E plus per-entity A=L+E plus eliminations =
  consolidated.
- **Audit log volume.** JSONL append works for 10k events/day. At
  enterprise scale (millions of postings, multiple periods open) it
  needs to live in an event-store-style backend with efficient projection
  rebuilds.

## How I used AI tools

Helped:
- **Claude (this session) for problem decomposition.** Walking through
  the seeded data and naming every defect *before* writing code surfaced
  three failure modes the brief didn't list (renamed accounts, sign-flippy
  contras, empty COA headers).
- **Claude for boilerplate.** Dataclass scaffolds, the CLI entry, the
  template-based explainer — all generated and then edited down. Saved
  maybe 90 minutes of typing.
- **Claude for the architecture diagram and the failure-mode table.**
  Useful for forcing exhaustive coverage; I went back and added Q9–Q16
  rows after the LLM listed candidates I hadn't thought of.

Misled me:
- **First-pass agent topology.** I started with five agents including a
  separate "ConfidenceAgent" before realizing confidence is a return
  value, not a role. Cut it.
- **The LLM wanted to use itself for arithmetic checks.** Generated code
  that prompted Claude to "verify the JE balances" — exactly the
  anti-pattern the brief calls out. Replaced with a 3-line code check.
- **Over-eager error handling.** Initial code wrapped every loader in
  try/except and emitted "graceful" warnings. Removed: at boundaries
  (file IO, LLM calls) errors should propagate; at the validator level,
  findings *are* the error channel.
- **First-pass tier-b proposal.** I drafted an LLM-driven path that asked
  Claude to return structured JSON with the proposed line and amount.
  Worked, but pushed structured-output schema validation into the agent
  layer for no real gain — the proposal heuristic is a five-line
  deterministic computation. Refactored: code computes the proposal, the
  LLM (when used) writes the rationale prose around it. Same architectural
  rule as the validators: code does the math, LLM does the prose.

## One thing I think you're underestimating

**The COA is the product.** The brief frames COA as one of three inputs,
sitting alongside the TB and adjustments. In practice it's the source of
truth that the other two inputs are *interpreted against*, and every
interesting defect in the seeded data — `1150 cf_category=TBD`, `1290`
header with no children, `6905` renamed across periods, `9999` orphan,
`6315` referenced but not defined, `7310` typed Expense holding a credit —
is at root a COA-quality problem.

This has a structural consequence: a great agent against a bad COA
produces wrong statements. So the highest-leverage feature in the
roadmap is not a smarter mapping LLM. It's a COA editor that surfaces
ambiguity ("you've left these `cf_category` values blank") at *creation*
time, with versioning, change events, and a successor-account graph for
period-over-period continuity. Get that right and most of the agent
complexity disappears, because the agent stops compensating for upstream
data quality and starts doing the thing the brief actually asked for:
combining clean inputs into clean statements.
