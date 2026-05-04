# One-page reflection

## What I'd build differently with 3 months instead of 8 hours

A real eval harness comes first. The prototype today pins decisions for
10 hand-coded JEs; a real system needs a labeled corpus of historical
adjustments — accepted, rejected with reason, quarantined and resolved —
to drive (a) a regression suite for the deterministic checks and (b) an
offline benchmark for the LLM explainer and mapper. Without that, every
prompt change is a vibes-based migration.

After the eval harness: a versioned COA. The COA is itself a slowly
mutating dataset (renames, splits, retirements, re-parenting), and
almost every interesting failure in the seeded data is downstream of a
COA edit that wasn't propagated. A COA service with effective dates,
change events, and a successor-account graph would absorb half the
complexity the agent currently has to compensate for.

Then the lineage UI. The architecture treats statements as projections
over an event log; the prototype writes JSONL but doesn't yet *render*
the click-through trail. Three months gets you the auditor demo: open a
BS cell, see every source row, every JE, every agent decision with
prompt + model + confidence, and the override history.

## Where the prototype breaks at scale

- **Mapping cost.** Per-JE LLM calls are fine for a 10-entry batch,
  ruinous for a 50,000-entry month. Fix: candidate generation stays
  per-row but LLM selection batches with prompt caching, and
  high-confidence cases (>0.9) skip the LLM entirely.
- **COA matching is O(accounts × candidates).** With 5,000-account
  enterprise COAs, naive prefix scoring becomes the bottleneck. Fix:
  pre-compute name embeddings, ANN search to get top-K in O(log n),
  refine with prefix + edit distance.
- **Multi-entity consolidation.** The current architecture is
  single-entity. Adding consolidation means an entity dimension on
  every event, an eliminations matrix as a first-class structure, and
  CTA computation per sub. The validation loop's tie-outs change shape:
  consolidated A=L+E *plus* per-entity A=L+E *plus* eliminations =
  consolidated.
- **Audit log volume.** JSONL append works for 10k events/day. At
  enterprise scale (millions of postings, multiple periods open) it
  needs to live in an event-store backend with efficient projection
  rebuilds.

## How I used AI tools

**Helped:**

- **Problem decomposition.** Walking through the seeded data and naming
  every defect *before* writing code surfaced three failure modes the
  brief didn't list (renamed accounts, sign-flippy contras, empty COA
  headers).
- **Boilerplate.** Dataclass scaffolds, the CLI entry, the template
  explainer — generated and then edited down. Saved ~90 minutes of
  typing.
- **Exhaustive coverage.** The architecture diagram and the
  failure-mode table benefited from forcing the LLM to enumerate; I
  went back and added rows after it surfaced candidates I hadn't
  thought of.

**Misled me:**

- **First-pass topology.** I started with five agents including a
  separate "ConfidenceAgent" before realizing confidence is a return
  value, not a role. Cut.
- **The LLM wanted to use itself for arithmetic.** Generated code that
  prompted Claude to "verify the JE balances" — exactly the
  anti-pattern the brief warns against. Replaced with a 3-line code
  check.
- **Over-eager error handling.** Initial code wrapped every loader in
  try/except and emitted "graceful" warnings. Removed: at boundaries
  (file IO, LLM calls) errors should propagate; at the validator level,
  findings *are* the error channel.
- **Tier-b proposal.** I drafted an LLM-driven path that asked Claude
  to return structured JSON with the proposed line and amount. Worked,
  but pushed schema validation into the agent layer for no gain — the
  proposal heuristic is five lines of code. Refactored: code computes
  the proposal, LLM writes the rationale around it.

## One thing I think you're underestimating

**The COA is the product.** The brief frames the COA as one of three
inputs, sitting alongside the TB and adjustments. In practice it's the
source of truth that the other two inputs are *interpreted against*,
and every interesting defect in the seeded data — `1150 cf_category=TBD`,
`1290` header with no children, `6905` renamed across periods, `9999`
orphan, `6315` referenced but not defined, `7310` typed Expense holding
a credit — is at root a COA-quality problem.

A great agent against a bad COA still produces wrong statements. So the
highest-leverage feature in the roadmap is not a smarter mapping LLM.
It's a COA editor that surfaces ambiguity ("you've left these
`cf_category` blank") at *creation* time, with versioning, change
events, and a successor-account graph for period-over-period
continuity. Get that right and most of the agent complexity disappears,
because the agent stops compensating for upstream data quality and
starts doing the thing the brief actually asked for: combining clean
inputs into clean statements.
