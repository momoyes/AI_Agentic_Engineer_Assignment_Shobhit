"""Single source of truth for thresholds and policy.

Every value here is a lever a reviewer can flip without touching validation
logic. Defaults reflect the policy confirmed by Amit Patel via email
2026-04-30 (see ../../ASSUMPTIONS.md A1).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Materiality:
    je_line_tolerance: float = 0.0           # JE lines must balance exactly
    fx_rounding_abs: float = 500.0           # tier-a: ≤ this auto-correct + log
    je_warn_band_max: float = 10_000.0       # tier-b: warn band ceiling (accept + reconciliation note)
    je_halt_threshold: float = 10_000.0      # tier-c: > this halts/rejects
    bs_tieout_abs: float = 1.0               # BS must foot exactly; otherwise HALT
    rounding_account: str = "7300"           # plug account for tier-a auto-corrections


@dataclass(frozen=True)
class Period:
    label: str = "2024-Q4"
    start: str = "2024-10-01"
    end: str = "2024-12-31"
    functional_currency: str = "USD"


@dataclass(frozen=True)
class Config:
    materiality: Materiality = Materiality()
    period: Period = Period()
    mapping_top_k: int = 3                   # candidates surfaced to humans
    min_mapping_confidence: float = 0.55     # below this, escalate
    openai_model: str = "gpt-4o-2024-08-06"   # supports strict json_schema
    anthropic_model: str = "claude-opus-4-7"


CONFIG = Config()
