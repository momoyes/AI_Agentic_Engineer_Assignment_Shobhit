"""Input ingestion. Tolerates the seeded defects in the data — does not
sanitize them, but normalizes types so downstream validators can reason
cleanly."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .models import Account, JELine, JournalEntry


def load_coa(path: Path) -> dict[str, Account]:
    """Return a dict keyed by account code. Header rows are included so the
    validator can recognize them."""
    coa: dict[str, Account] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row["account_code"].strip()
            coa[code] = Account(
                code=code,
                name=row["account_name"].strip(),
                account_type=row["account_type"].strip(),
                parent_code=row.get("parent_code", "").strip(),
                statement=row.get("statement", "").strip(),
                cf_category=row.get("cf_category", "").strip(),
                normal_balance=row.get("normal_balance", "").strip(),
            )
    return coa


def load_adjustments(path: Path) -> tuple[str, str, list[JournalEntry]]:
    """Return (period, functional_currency, entries)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    entries: list[JournalEntry] = []
    for raw in data["entries"]:
        lines = tuple(
            JELine(
                account=str(l["account"]).strip(),
                debit=float(l.get("debit") or 0.0),
                credit=float(l.get("credit") or 0.0),
                memo=l.get("memo", "").strip(),
            )
            for l in raw["lines"]
        )
        entries.append(
            JournalEntry(
                id=raw["id"],
                description=raw.get("description", ""),
                date=raw.get("date", ""),
                source=raw.get("source", ""),
                lines=lines,
            )
        )
    return data.get("period", ""), data.get("functional_currency", ""), entries
