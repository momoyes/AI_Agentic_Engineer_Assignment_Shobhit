# Clarifying questions — copy/paste email

> **Status: answered.** Sent before the build; Amit Patel replied on
> **2026-04-30** with a tiered materiality policy, an explicit
> "do-not-auto-create accounts" rule, and a "do-not-halt on FX gaps"
> fallback. The reply is transcribed verbatim in `ASSUMPTIONS.md` A1–A3 and
> drives the pipeline tiers in `prototype/adjuster/pipeline.py`.

**To:** [recruiter / hiring manager]
**Subject:** AI Agentic Engineer take-home — three clarifying questions before I start

---

Hi [name],

Before I start the build, I wanted to send the three clarifying questions
below. Answers would let me make sharper design choices.

**1. Materiality and tolerance thresholds.** For the period close, what's the
materiality threshold for (a) TB debit/credit imbalance, (b) FX rounding
residuals, and (c) the BS asset = liability + equity tie-out? Is there a
documented policy, or should I default to something like the lower of $10k or
0.5% of total assets? This drives whether the validator rejects, warns, or
auto-posts a rounding plug.

**2. Authority of the COA vs. the source ERP.** When the TB or a journal entry
references an account that isn't in the COA (e.g., the `9999 Suspense` row in
the TB, or account `6315` referenced by JE-005), is the agent allowed to
(a) auto-create a COA node under a default parent, (b) propose a mapping to a
human, or (c) hard-reject the whole batch? Same question for `cf_category`
values that are marked `TBD` in the COA.

**3. FX policy for missing rates and translation method.** The fx_rates file
is missing a GBP period-end rate. Should the agent (a) block the run,
(b) fall back to period-average, or (c) carry the prior period-end rate
forward? And: are we using temporal-method translation (monetary at
period-end, non-monetary at historical) or current-rate translation
(everything at period-end with the difference flowing to CTA)?

Happy to proceed without answers if you'd prefer — I'll surface every
assumption in the architecture doc.

Thanks,
[your name]
