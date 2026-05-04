"""Production Mapper interface (stub).

The Adjuster slice in this prototype only uses prefix similarity for
suggestions (see `validators.py::suggest_mappings`). The full architecture
described in `ARCHITECTURE.md` §2 splits the Mapper into two halves:

1. **Candidate generation** — pure code. Prefix scoring + edit distance +
   name embedding lookup. Returns the top-K plausible COA codes for an
   unknown account.
2. **Candidate selection** — LLM. Given the candidate set plus account
   name, parent code hint, prior-period analogues, and amount magnitude,
   the LLM picks one and emits a confidence.

This file pins the production *interface* — the seams the slice would slot
into — without implementing the LLM half. A reviewer can see what the
production agent looks like; the implementation is intentionally absent
because it is not part of the chosen slice.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Account, MappingSuggestion
from .validators import suggest_mappings as generate_candidates


@dataclass(frozen=True)
class MapperContext:
    """Everything the Mapper sees besides the unknown code itself.

    All optional, all surfaced to the LLM in the production selector. The
    prototype's prefix-only suggestor does not need any of this.
    """
    account_name_hint: str | None = None        # original name from source
    parent_code_hint: str | None = None         # if a parent header is named
    prior_period_code: str | None = None        # was this the same account last period?
    amount_magnitude: float | None = None       # rough size, helps disambiguate
    description: str | None = None              # JE description / TB comment


@dataclass(frozen=True)
class MapperDecision:
    """The output the Adjuster expects from the Mapper. The 'auto-accept'
    bucket bypasses human review; 'suggest' goes to the quarantine queue
    with the candidate list; 'escalate' surfaces to a human regardless of
    candidate confidence (e.g., novel account in regulated category)."""
    bucket: str                                  # "auto-accept" | "suggest" | "escalate"
    chosen_code: str | None
    confidence: float
    candidates: list[MappingSuggestion]
    rationale: str                               # for the audit log


class Mapper:
    """Production Mapper interface. The prototype only ships
    `generate_candidates` (prefix-similarity); `choose` is intentionally
    stubbed so the seam is visible but the LLM call is not in scope."""

    def __init__(
        self,
        coa: dict[str, Account],
        *,
        min_confidence: float = 0.55,
        auto_accept_above: float = 0.95,
    ) -> None:
        self.coa = coa
        self.min_confidence = min_confidence
        self.auto_accept_above = auto_accept_above

    def map(self, unknown_code: str, context: MapperContext) -> MapperDecision:
        candidates = generate_candidates(
            unknown_code, self.coa, name_hint=context.account_name_hint
        )
        if not candidates:
            return MapperDecision(
                bucket="escalate",
                chosen_code=None,
                confidence=0.0,
                candidates=[],
                rationale=(
                    f"No COA candidate within prefix distance of '{unknown_code}'."
                ),
            )

        chosen, confidence, rationale = self._select(candidates, context)

        bucket = (
            "auto-accept" if confidence >= self.auto_accept_above
            else "suggest" if confidence >= self.min_confidence
            else "escalate"
        )
        return MapperDecision(
            bucket=bucket,
            chosen_code=chosen,
            confidence=confidence,
            candidates=candidates,
            rationale=rationale,
        )

    def _select(
        self, candidates: list[MappingSuggestion], context: MapperContext
    ) -> tuple[str, float, str]:
        """Production: LLM picks among candidates given context.

        Prototype: returns the top prefix-scored candidate as-is.
        Reviewers reading the production design see where the LLM call goes;
        a deliberate one-line implementation prevents the prototype from
        pretending to be more than it is."""
        top = candidates[0]
        return (
            top.candidate_code,
            top.confidence,
            f"Prefix-similarity selection ({top.reason}). "
            "Production Mapper would refine with name embeddings and LLM judgment.",
        )
