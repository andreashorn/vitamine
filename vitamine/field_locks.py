"""Field-level protection for user-approved curated CV values."""

from __future__ import annotations

import sqlite3
from typing import Any


def ensure_field_locks_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS field_locks (
          id INTEGER PRIMARY KEY,
          target_type TEXT NOT NULL,
          target_id INTEGER NOT NULL,
          field_name TEXT NOT NULL,
          locked_value TEXT NOT NULL,
          source TEXT NOT NULL,
          source_inbox_item_id INTEGER REFERENCES import_inbox_items(id) ON DELETE SET NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(target_type, target_id, field_name)
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_field_locks_target "
        "ON field_locks(target_type, target_id)"
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS enrichment_change_candidates (
          id INTEGER PRIMARY KEY,
          publication_id INTEGER NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
          field_name TEXT NOT NULL,
          old_value TEXT NOT NULL,
          new_value TEXT NOT NULL,
          source TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          rationale TEXT,
          confidence TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(publication_id, field_name, source, new_value)
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_enrichment_change_candidates_pending "
        "ON enrichment_change_candidates(status, created_at)"
    )


def lock_field(
    con: sqlite3.Connection,
    *,
    target_type: str,
    target_id: int,
    field_name: str,
    value: Any,
    source: str,
    source_inbox_item_id: int | None = None,
) -> None:
    ensure_field_locks_table(con)
    con.execute(
        """
        INSERT INTO field_locks
          (target_type, target_id, field_name, locked_value, source, source_inbox_item_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(target_type, target_id, field_name) DO UPDATE SET
          locked_value=excluded.locked_value,
          source=excluded.source,
          source_inbox_item_id=excluded.source_inbox_item_id,
          updated_at=CURRENT_TIMESTAMP
        """,
        (target_type, target_id, field_name, str(value or ""), source, source_inbox_item_id),
    )


def locked_field_values(
    con: sqlite3.Connection, target_type: str, target_id: int
) -> dict[str, str]:
    ensure_field_locks_table(con)
    return {
        str(row["field_name"]): str(row["locked_value"] or "")
        for row in con.execute(
            "SELECT field_name, locked_value FROM field_locks WHERE target_type=? AND target_id=?",
            (target_type, target_id),
        ).fetchall()
    }


def stage_enrichment_change(
    con: sqlite3.Connection,
    *,
    publication_id: int,
    field_name: str,
    old_value: Any,
    new_value: Any,
    source: str,
) -> None:
    """Remember one nonlocked source disagreement for later LLM reconciliation."""
    ensure_field_locks_table(con)
    con.execute(
        """
        INSERT INTO enrichment_change_candidates
          (publication_id, field_name, old_value, new_value, source, status, updated_at)
        VALUES (?, ?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
        ON CONFLICT(publication_id, field_name, source, new_value) DO UPDATE SET
          old_value=excluded.old_value,
          status=CASE WHEN enrichment_change_candidates.status='applied' THEN 'pending'
                      ELSE enrichment_change_candidates.status END,
          updated_at=CURRENT_TIMESTAMP
        """,
        (publication_id, field_name, str(old_value or ""), str(new_value or ""), source),
    )
