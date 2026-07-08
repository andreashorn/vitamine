#!/usr/bin/env python3
"""Add and prefill manually editable German CV entry fields."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from vitamine.i18n import GERMAN_FIELD_PAIRS, draft_translate_to_german
from vitamine.paths import ROOT, active_db_path

DB = active_db_path()


def ensure_columns(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(cv_entries)").fetchall()}
    for _english, german in GERMAN_FIELD_PAIRS:
        if german not in existing:
            con.execute(f"ALTER TABLE cv_entries ADD COLUMN {german} TEXT")


def prefill(con: sqlite3.Connection, overwrite: bool = False) -> int:
    ensure_columns(con)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM cv_entries").fetchall()
    updated = 0
    for row in rows:
        assignments = []
        values = []
        for english_field, german_field in GERMAN_FIELD_PAIRS:
            current = row[german_field] if german_field in row.keys() else None
            source = row[english_field] if english_field in row.keys() else None
            if source and (overwrite or not current):
                assignments.append(f"{german_field} = ?")
                values.append(draft_translate_to_german(source))
        if assignments:
            values.append(row["id"])
            con.execute(f"UPDATE cv_entries SET {', '.join(assignments)} WHERE id = ?", values)
            updated += 1
    return updated


def main() -> None:
    with sqlite3.connect(DB) as con:
        count = prefill(con)
        con.commit()
    print(f"Prefilled German draft fields for {count} entries in {DB.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
