"""CLI entry point for the manual adjustments agent.

Usage:
    python3 main.py --inputs ../inputs --out output [--llm]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adjuster.loader import load_adjustments, load_coa
from adjuster.models import DecisionRecord
from adjuster.deterministic import decide_batch


def main() -> int:
    parser = argparse.ArgumentParser(description="Manual adjustments validator")
    parser.add_argument("--inputs", type=Path, required=True, help="Path to inputs directory")
    parser.add_argument("--out", type=Path, required=True, help="Output directory")
    parser.add_argument(
        "--llm-mode",
        choices=["off", "prose", "graph"],
        default=None,
        help=(
            "off (default) = pure deterministic + template prose; "
            "prose = deterministic decisions + LLM-rewritten explanation; "
            "graph = LangGraph + Chroma RAG, LLM in structural/existence/semantic seats."
        ),
    )
    parser.add_argument(
        "--llm", action="store_true",
        help="Back-compat shortcut for --llm-mode prose.",
    )
    args = parser.parse_args()

    if args.llm_mode is None:
        args.llm_mode = "prose" if args.llm else "off"

    coa = load_coa(args.inputs / "chart_of_accounts.csv")
    period, fcy, entries = load_adjustments(args.inputs / "manual_adjustments.json")
    print(
        f"Loaded {len(coa)} COA accounts; {len(entries)} entries for "
        f"{period} ({fcy}). LLM mode: {args.llm_mode}."
    )

    records = decide_batch(entries, coa, llm_mode=args.llm_mode)

    args.out.mkdir(parents=True, exist_ok=True)
    write_decisions_json(records, args.out / "decisions.json")
    write_audit_log(records, args.out / "audit_log.jsonl")
    write_report(records, args.out / "report.md")

    summary = _summarize(records)
    print(
        f"Done. accept={summary['accept']}  "
        f"quarantine={summary['quarantine']}  reject={summary['reject']}"
    )
    print(f"Output written to {args.out}/")
    return 0


def write_decisions_json(records: list[DecisionRecord], path: Path) -> None:
    path.write_text(
        json.dumps([r.to_json() for r in records], indent=2),
        encoding="utf-8",
    )


def write_audit_log(records: list[DecisionRecord], path: Path) -> None:
    """Append-only event log. Per JE we emit:

    1. one `decision` event, and
    2. one `finding` event per finding, and
    3. for tier-a (AUTO_CORRECTED) findings, an additional structured
       `auto_correction` event carrying the source entry, the delta, and the
       action taken — per Amit Patel email 2026-04-30: "no threshold-based
       auto-fix is silent — every correction must appear in a machine-readable
       audit log with the source entry, the delta, and the action taken."

    The `finding` event for an IMBALANCE_WARN already carries the warn-band
    reconciliation note in its message + detail; no extra event is emitted.
    """
    lines: list[str] = []
    for r in records:
        lines.append(
            json.dumps({"event": "decision", "je_id": r.je_id, "decision": r.decision})
        )
        for f in r.findings:
            lines.append(
                json.dumps(
                    {
                        "event": "finding",
                        "je_id": f.je_id,
                        "code": f.code,
                        "severity": f.severity,
                        "message": f.message,
                        "line_index": f.line_index,
                    }
                )
            )
            if f.code == "AUTO_CORRECTED":
                pf = r.proposed_fix
                lines.append(
                    json.dumps(
                        {
                            "event": "auto_correction",
                            "je_id": f.je_id,
                            "source_entry": f.je_id,
                            "delta": f.detail.get("difference"),
                            "action": f.detail.get("action"),
                            "rounding_account": f.detail.get("rounding_account"),
                            "plug": (
                                {
                                    "line_index": pf.line_index,
                                    "field": pf.field,
                                    "from": pf.current_amount,
                                    "to": pf.proposed_amount,
                                }
                                if pf is not None
                                else None
                            ),
                        }
                    )
                )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(records: list[DecisionRecord], path: Path) -> None:
    s = _summarize(records)
    out: list[str] = []
    out.append("# Adjustments validation report\n")
    out.append(f"- Accepted: **{s['accept']}**")
    out.append(f"- Quarantined: **{s['quarantine']}**")
    out.append(f"- Rejected: **{s['reject']}**\n")
    out.append("## Per-entry decisions\n")
    out.append("| JE | Decision | Description | Notes |")
    out.append("|---|---|---|---|")
    for r in records:
        notes = r.findings[0].message if r.findings else "—"
        out.append(f"| {r.je_id} | **{r.decision}** | {r.description} | {notes} |")
    out.append("")
    out.append("## Plain-English explanations\n")
    for r in records:
        out.append(f"### {r.je_id} — {r.decision}")
        out.append(r.explanation)
        if r.proposed_fix is not None:
            pf = r.proposed_fix
            out.append(
                f"\n**Proposed fix** (confidence {pf.confidence:.0%}): "
                f"adjust line {pf.line_index} {pf.field} from "
                f"{pf.current_amount:,.2f} to {pf.proposed_amount:,.2f}. "
                f"_{pf.rationale}_"
            )
        if r.suggestions:
            out.append("\n**Mapping suggestions:**")
            for sug in r.suggestions:
                out.append(
                    f"- `{sug.candidate_code}` {sug.candidate_name} "
                    f"(confidence {sug.confidence:.0%}) — {sug.reason}"
                )
        out.append("")
    path.write_text("\n".join(out), encoding="utf-8")


def _summarize(records: list[DecisionRecord]) -> dict[str, int]:
    out = {"accept": 0, "quarantine": 0, "reject": 0}
    for r in records:
        out[r.decision] += 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
