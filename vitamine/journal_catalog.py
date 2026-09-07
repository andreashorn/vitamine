"""Portable, per-CV journal-title catalog and conservative bulk normalization."""

from __future__ import annotations

import re
import sqlite3
from typing import Any


def journal_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def ensure_journal_catalog_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS journal_catalog (
          id INTEGER PRIMARY KEY,
          canonical_title TEXT NOT NULL,
          canonical_key TEXT NOT NULL UNIQUE,
          issn_l TEXT,
          source TEXT NOT NULL DEFAULT 'manual',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS journal_aliases (
          alias_key TEXT PRIMARY KEY,
          alias_title TEXT NOT NULL,
          journal_id INTEGER NOT NULL REFERENCES journal_catalog(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_journal_aliases_journal ON journal_aliases(journal_id)")


def _journal_for_alias(con: sqlite3.Connection, alias_key: str) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT c.* FROM journal_aliases a
        JOIN journal_catalog c ON c.id=a.journal_id
        WHERE a.alias_key=?
        """,
        (alias_key,),
    ).fetchone()


def _catalog_journal(
    con: sqlite3.Connection,
    original_title: str,
    canonical_title: str,
    *,
    source: str,
    issn_l: str = "",
) -> sqlite3.Row | None:
    original_key = journal_key(original_title)
    canonical_key = journal_key(canonical_title)
    if not canonical_key:
        return None
    journal = _journal_for_alias(con, original_key) if original_key else None
    if journal is None:
        journal = con.execute("SELECT * FROM journal_catalog WHERE canonical_key=?", (canonical_key,)).fetchone()
    if journal is None:
        cursor = con.execute(
            """
            INSERT INTO journal_catalog (canonical_title, canonical_key, issn_l, source)
            VALUES (?, ?, ?, ?)
            """,
            (canonical_title, canonical_key, issn_l or None, source),
        )
        journal = con.execute("SELECT * FROM journal_catalog WHERE id=?", (cursor.lastrowid,)).fetchone()
    else:
        con.execute(
            """
            UPDATE journal_catalog
            SET canonical_title=?, canonical_key=?, issn_l=COALESCE(?, issn_l), source=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (canonical_title, canonical_key, issn_l or None, source, journal["id"]),
        )
        journal = con.execute("SELECT * FROM journal_catalog WHERE id=?", (journal["id"],)).fetchone()
    for title in {original_title, canonical_title}:
        key = journal_key(title)
        if not key:
            continue
        con.execute(
            """
            INSERT INTO journal_aliases (alias_key, alias_title, journal_id, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(alias_key) DO UPDATE SET
              alias_title=excluded.alias_title,
              journal_id=excluded.journal_id,
              updated_at=CURRENT_TIMESTAMP
            """,
            (key, title, journal["id"]),
        )
    return journal


def journal_catalog_preview(con: sqlite3.Connection, publication_id: int, canonical_title: str) -> dict[str, Any]:
    ensure_journal_catalog_tables(con)
    publication = con.execute("SELECT id, venue FROM publications WHERE id=?", (publication_id,)).fetchone()
    if not publication:
        return {"found": False, "matches": 0, "canonical_title": canonical_title}
    original_title = str(publication["venue"] or "").strip()
    original_key = journal_key(original_title)
    if not original_key or not journal_key(canonical_title):
        return {"found": True, "matches": 0, "canonical_title": canonical_title}
    journal = _journal_for_alias(con, original_key)
    alias_keys = {original_key}
    if journal:
        alias_keys.update(
            str(row[0])
            for row in con.execute("SELECT alias_key FROM journal_aliases WHERE journal_id=?", (journal["id"],)).fetchall()
        )
    matches = sum(
        journal_key(row["venue"]) in alias_keys
        for row in con.execute("SELECT venue FROM publications WHERE trim(COALESCE(venue, '')) != ''").fetchall()
    )
    return {"found": True, "matches": matches, "canonical_title": canonical_title}


def remember_journal_title(
    con: sqlite3.Connection,
    original_title: str,
    canonical_title: str,
    *,
    source: str,
    issn_l: str = "",
) -> int | None:
    """Record a verified alias without changing other publication rows."""
    ensure_journal_catalog_tables(con)
    journal = _catalog_journal(con, original_title, canonical_title, source=source, issn_l=issn_l)
    return int(journal["id"]) if journal else None


def apply_journal_canonical_title(
    con: sqlite3.Connection,
    publication_id: int,
    canonical_title: str,
    *,
    source: str = "manual",
    issn_l: str = "",
) -> dict[str, Any]:
    """Update all catalog-linked venue aliases and return the affected records."""
    ensure_journal_catalog_tables(con)
    publication = con.execute("SELECT id, venue FROM publications WHERE id=?", (publication_id,)).fetchone()
    if not publication:
        return {"found": False, "matched": 0, "updated": []}
    original_title = str(publication["venue"] or "").strip()
    canonical_title = str(canonical_title or "").strip()
    if not original_title or not canonical_title:
        return {"found": True, "matched": 0, "updated": []}
    existing = _journal_for_alias(con, journal_key(original_title))
    alias_keys = {journal_key(original_title)}
    if existing:
        alias_keys.update(
            str(row[0])
            for row in con.execute("SELECT alias_key FROM journal_aliases WHERE journal_id=?", (existing["id"],)).fetchall()
        )
    rows = con.execute("SELECT id, venue FROM publications WHERE trim(COALESCE(venue, '')) != ''").fetchall()
    matched = [row for row in rows if journal_key(row["venue"]) in alias_keys]
    journal = _catalog_journal(con, original_title, canonical_title, source=source, issn_l=issn_l)
    if not journal:
        return {"found": True, "matched": 0, "updated": []}
    for row in matched:
        title = str(row["venue"] or "").strip()
        key = journal_key(title)
        if key:
            con.execute(
                """
                INSERT INTO journal_aliases (alias_key, alias_title, journal_id, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(alias_key) DO UPDATE SET journal_id=excluded.journal_id, updated_at=CURRENT_TIMESTAMP
                """,
                (key, title, journal["id"]),
            )
    updated = [{"id": int(row["id"]), "old_venue": str(row["venue"] or "")} for row in matched if str(row["venue"] or "") != canonical_title]
    if updated:
        con.executemany("UPDATE publications SET venue=? WHERE id=?", [(canonical_title, row["id"]) for row in updated])
    return {"found": True, "matched": len(matched), "updated": updated, "journal_id": int(journal["id"])}
