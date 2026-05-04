"""Deterministic validators. Each returns a list of Findings.

These checks never call an LLM. They produce structured machine-readable
findings; humans (or the LLM, in the explainer) read the findings and
generate prose.

Mapping suggestions use **pure mathematics** — no third-party fuzzy-string
library. The score for each candidate blends three classical signals,
implemented from scratch below:

  1. Prefix similarity on the numeric account code.
  2. Levenshtein (edit-distance) ratio between the JE memo and the
     candidate's COA name.
  3. Jaccard similarity over case-folded word tokens.

All three are deterministic, side-effect-free, and visible in the source.
A reviewer can audit the math line-by-line; nothing happens behind a
library call."""
from __future__ import annotations

from .config import CONFIG
from .models import Account, Finding, JournalEntry, MappingSuggestion, ProposedFix


def check_balanced(je: JournalEntry) -> list[Finding]:
    """Sum of debits must equal sum of credits within tolerance."""
    diff = round(je.total_debit - je.total_credit, 2)
    if abs(diff) <= CONFIG.materiality.je_line_tolerance:
        return []
    return [
        Finding(
            code="UNBALANCED",
            severity="error",
            message=(
                f"Total debits {je.total_debit:,.2f} ≠ total credits "
                f"{je.total_credit:,.2f} (diff {diff:,.2f})"
            ),
            je_id=je.id,
            detail={
                "total_debit": je.total_debit,
                "total_credit": je.total_credit,
                "difference": diff,
            },
        )
    ]


def check_line_signs(je: JournalEntry) -> list[Finding]:
    """A single line cannot have both a non-zero debit and credit, and
    amounts must be non-negative."""
    findings: list[Finding] = []
    for i, line in enumerate(je.lines):
        if line.debit < 0 or line.credit < 0:
            findings.append(
                Finding(
                    code="NEGATIVE_AMOUNT",
                    severity="error",
                    message=f"Line {i} has a negative amount",
                    je_id=je.id,
                    line_index=i,
                )
            )
        if line.debit > 0 and line.credit > 0:
            findings.append(
                Finding(
                    code="DEBIT_AND_CREDIT_ON_SAME_LINE",
                    severity="error",
                    message=f"Line {i} has both debit and credit set",
                    je_id=je.id,
                    line_index=i,
                )
            )
    return findings


def check_accounts_exist(je: JournalEntry, coa: dict[str, Account]) -> list[Finding]:
    """Every referenced account must exist in the COA and must be a postable
    leaf (not a Header)."""
    findings: list[Finding] = []
    for i, line in enumerate(je.lines):
        acct = coa.get(line.account)
        if acct is None:
            findings.append(
                Finding(
                    code="UNMAPPED_ACCOUNT",
                    severity="error",
                    message=f"Line {i}: account '{line.account}' not in COA",
                    je_id=je.id,
                    line_index=i,
                    detail={"account": line.account},
                )
            )
        elif acct.account_type == "Header":
            findings.append(
                Finding(
                    code="POSTING_TO_HEADER",
                    severity="error",
                    message=(
                        f"Line {i}: account '{line.account}' is a Header — "
                        "postings must hit leaf accounts"
                    ),
                    je_id=je.id,
                    line_index=i,
                )
            )
    return findings


def check_circular_intercompany(je: JournalEntry) -> list[Finding]:
    """A JE that debits and credits the same account with offsetting amounts
    has zero economic effect. Technically valid double-entry; substantively
    meaningless. Quarantine for human review."""
    by_account: dict[str, float] = {}
    for line in je.lines:
        by_account[line.account] = (
            by_account.get(line.account, 0.0) + line.debit - line.credit
        )
    null_accounts = [a for a, net in by_account.items() if abs(net) < 0.01]
    has_traffic = {
        a for a in null_accounts
        if any(l.account == a and (l.debit or l.credit) for l in je.lines)
    }
    if has_traffic and len(by_account) == 1:
        # Entire JE nets to zero against one account
        a = next(iter(has_traffic))
        return [
            Finding(
                code="CIRCULAR_NET_ZERO",
                severity="warning",
                message=(
                    f"Entry debits and credits the same account '{a}' with "
                    "offsetting amounts; net economic effect is zero."
                ),
                je_id=je.id,
                detail={"account": a},
            )
        ]
    return []


def check_date_in_period(je: JournalEntry) -> list[Finding]:
    if not (CONFIG.period.start <= je.date <= CONFIG.period.end):
        return [
            Finding(
                code="DATE_OUT_OF_PERIOD",
                severity="error",
                message=(
                    f"JE date {je.date} is outside reporting period "
                    f"{CONFIG.period.start}..{CONFIG.period.end}"
                ),
                je_id=je.id,
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Mapping suggestions for unmapped accounts. Code-generated candidate set;
# the LLM (if used) only chooses and explains. The agent cannot mint a code.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Pure-math scorers. Hand-rolled so the reviewer can audit every line — no
# third-party fuzzy-string dependency.
# ---------------------------------------------------------------------------

def _prefix_score(unknown_code: str, candidate_code: str) -> tuple[float, int]:
    """Longest common prefix length, normalised by the longer code.

    Math: |LCP(a,b)| / max(|a|, |b|)  ∈ [0, 1].
    """
    common = 0
    for x, y in zip(unknown_code, candidate_code):
        if x == y:
            common += 1
        else:
            break
    denom = max(len(unknown_code), len(candidate_code))
    return (common / denom if denom else 0.0), common


def _levenshtein_distance(a: str, b: str) -> int:
    """Classical Wagner–Fischer DP for edit distance.

    Math: d(i,j) = min(
        d(i-1, j)   + 1,                  # deletion
        d(i, j-1)   + 1,                  # insertion
        d(i-1, j-1) + (0 if a[i]==b[j] else 1)   # substitution
    )

    Time O(|a|·|b|), space O(min(|a|,|b|)) thanks to the rolling row.
    """
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a   # ensure b is the shorter, so the rolling row is small
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            insert = curr[j - 1] + 1
            delete = prev[j] + 1
            substitute = prev[j - 1] + (0 if ca == cb else 1)
            curr.append(min(insert, delete, substitute))
        prev = curr
    return prev[-1]


def _levenshtein_ratio(a: str, b: str) -> float:
    """Edit-distance similarity normalised to [0, 1].

    Math: 1 - d(a,b) / max(|a|, |b|).
    Identical strings → 1.0; entirely-different strings → 0.0.
    """
    if not a and not b:
        return 1.0
    max_len = max(len(a), len(b))
    return 1 - _levenshtein_distance(a, b) / max_len


def _jaccard_tokens(a: str, b: str) -> float:
    """Set Jaccard over case-folded whitespace tokens.

    Math: |A ∩ B| / |A ∪ B|  where A, B are token sets.
    Word-order invariant; ignores punctuation differences once split.
    """
    ta = {t for t in a.lower().split() if t}
    tb = {t for t in b.lower().split() if t}
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def suggest_mappings(
    unknown_code: str,
    coa: dict[str, Account],
    *,
    name_hint: str | None = None,
) -> list[MappingSuggestion]:
    """Rank COA candidates with a deterministic, pure-math blend.

    score = 0.7 · prefix_score(code, candidate.code)
          + 0.3 · name_sim(memo, candidate.name)

    where  name_sim = (levenshtein_ratio + jaccard_tokens) / 2.

    The 70/30 weighting reflects domain reality: the numeric code carries
    most of the signal (a 3-of-4 digit prefix match is rarely accidental),
    and the memo is a noisy-but-useful tiebreaker. With no memo, the score
    reduces to the prefix term alone.

    Header rows are excluded because postings cannot hit a header. Returns
    the top-K per `config.mapping_top_k`.
    """
    leaves = [a for a in coa.values() if a.account_type != "Header"]
    use_name = bool(name_hint and name_hint.strip())

    scored: list[tuple[float, Account, str]] = []
    for acct in leaves:
        prefix, common = _prefix_score(unknown_code, acct.code)

        if use_name:
            lev = _levenshtein_ratio(name_hint.lower(), acct.name.lower())
            jac = _jaccard_tokens(name_hint, acct.name)
            name_sim = (lev + jac) / 2
            score = 0.7 * prefix + 0.3 * name_sim
            reason = (
                f"prefix {prefix:.0%}, levenshtein {lev:.0%}, "
                f"jaccard {jac:.0%} → blended {score:.0%} "
                f"(memo '{name_hint}' vs name '{acct.name}')"
            )
        else:
            score = prefix
            reason = f"shares {common}-digit prefix with {acct.code}"

        if score > 0:
            scored.append((score, acct, reason))

    scored.sort(key=lambda t: t[0], reverse=True)
    return [
        MappingSuggestion(
            candidate_code=acct.code,
            candidate_name=acct.name,
            confidence=round(score, 2),
            reason=reason,
        )
        for score, acct, reason in scored[: CONFIG.mapping_top_k]
    ]


# ---------------------------------------------------------------------------
# Tier-b proposal: for unbalanced JEs within materiality, propose which
# existing line to adjust and the target amount. Code-generated; the LLM (if
# used) only writes the rationale prose around it.
# ---------------------------------------------------------------------------

def propose_balance_fix(je: JournalEntry) -> ProposedFix | None:
    """Return a proposed fix for an unbalanced JE, or None if no single-line
    adjustment can resolve the imbalance.

    Heuristic: if debits exceed credits, the credit side is short by `diff`;
    propose increasing the largest credit line to absorb it. Symmetric for the
    debit side. We never invent new lines, never change account codes, and
    never adjust the long side — those are decisions for a human, not a
    proposal."""
    diff = round(je.total_debit - je.total_credit, 2)
    if diff == 0:
        return None

    if diff > 0:
        # Credits are short by `diff`. Find the credit line with the largest amount.
        candidates = [(i, l.credit) for i, l in enumerate(je.lines) if l.credit > 0]
        field = "credit"
    else:
        candidates = [(i, l.debit) for i, l in enumerate(je.lines) if l.debit > 0]
        field = "debit"

    if not candidates:
        return None

    target_idx, current_amount = max(candidates, key=lambda t: t[1])
    proposed = round(current_amount + abs(diff), 2)

    # Confidence heuristic: if there's exactly one line on the short side, the
    # proposal is unambiguous; if there are several, lower confidence and let a
    # human pick.
    confidence = 0.85 if len(candidates) == 1 else 0.55

    rationale = (
        f"Total debits and credits differ by {abs(diff):,.2f}. Adjusting line "
        f"{target_idx} ({field} {current_amount:,.2f} → {proposed:,.2f}) "
        "would balance the entry without changing any account code."
    )

    return ProposedFix(
        line_index=target_idx,
        field=field,
        current_amount=current_amount,
        proposed_amount=proposed,
        rationale=rationale,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_all_checks(je: JournalEntry, coa: dict[str, Account]) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(check_line_signs(je))
    findings.extend(check_balanced(je))
    findings.extend(check_accounts_exist(je, coa))
    findings.extend(check_circular_intercompany(je))
    findings.extend(check_date_in_period(je))
    return findings
