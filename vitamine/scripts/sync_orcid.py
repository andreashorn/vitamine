#!/usr/bin/env python3
"""Sync public ORCID works into the local CV publication table."""

from __future__ import annotations

import json
import re
import sqlite3
import urllib.request

from maintain_publications import ensure_columns, maintain
from vitamine.paths import active_db_path


DB = active_db_path()
ORCID_TYPE_CATEGORIES = {
    "journal-article": "peer_reviewed",
    "book": "books_chapters",
    "book-chapter": "books_chapters",
    "conference-paper": "conference_presentations",
    "conference-abstract": "posters",
    "conference-poster": "posters",
    "preprint": "preprints",
    "dissertation": "theses",
    "patent": "patents",
}


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ensure_person_columns(con)
    ensure_columns(con)
    return con


def ensure_person_columns(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(person)").fetchall()}
    if "orcid_id" not in existing:
        con.execute("ALTER TABLE person ADD COLUMN orcid_id TEXT")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS person_identifiers (
          id INTEGER PRIMARY KEY,
          person_id INTEGER NOT NULL DEFAULT 1 REFERENCES person(id) ON DELETE CASCADE,
          platform TEXT NOT NULL,
          identifier_type TEXT NOT NULL,
          identifier_value TEXT,
          url TEXT NOT NULL,
          source TEXT NOT NULL,
          verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          notes TEXT,
          UNIQUE(person_id, platform, identifier_type, identifier_value)
        )
        """
    )


def upsert_person_orcid_identifier(con: sqlite3.Connection, orcid_id: str) -> None:
    row = con.execute(
        """
        SELECT id
        FROM person_identifiers
        WHERE person_id = 1 AND lower(platform) = 'orcid'
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()
    url = f"https://orcid.org/{orcid_id}"
    if row:
        con.execute(
            """
            DELETE FROM person_identifiers
            WHERE person_id=1 AND lower(platform)='orcid' AND id != ?
            """,
            (row["id"],),
        )
        con.execute(
            """
            UPDATE person_identifiers
            SET platform='ORCID',
                identifier_type='ORCID iD',
                identifier_value=?,
                url=?,
                source='orcid-sync',
                verified_at=datetime('now'),
                notes='Primary persistent researcher identifier.'
            WHERE id=? AND person_id=1
            """,
            (orcid_id, url, row["id"]),
        )
    else:
        con.execute(
            """
            INSERT INTO person_identifiers
              (person_id, platform, identifier_type, identifier_value, url, source, verified_at, notes)
            VALUES (1, 'ORCID', 'ORCID iD', ?, ?, 'orcid-sync', datetime('now'), 'Primary persistent researcher identifier.')
            """,
            (orcid_id, url),
        )
    con.execute("UPDATE person SET orcid_id=? WHERE id=1", (orcid_id,))


def normalize_doi(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^https?://(dx\\.)?doi\\.org/", "", text)
    text = re.sub(r"^doi:\\s*", "", text)
    return text.rstrip(".")


def normalize_title(value: str | None) -> str:
    text = (value or "").casefold()
    text = text.replace("‐", "-").replace("‑", "-").replace("–", "-")
    return re.sub(r"\\W+", " ", text).strip()


def text_value(node: dict | None, *keys: str) -> str:
    current = node or {}
    for key in keys:
        current = current.get(key) if isinstance(current, dict) else None
    if isinstance(current, dict):
        return str(current.get("value") or "")
    return str(current or "")


def external_ids(summary: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    ids = (summary.get("external-ids") or {}).get("external-id") or []
    for item in ids:
        key = (item.get("external-id-type") or "").lower()
        value = item.get("external-id-value") or text_value(item, "external-id-normalized")
        if key and value and key not in out:
            out[key] = value
    return out


def publication_year(summary: dict) -> str:
    return text_value(summary, "publication-date", "year")


def source_name(summary: dict) -> str:
    return text_value(summary, "source", "source-name")


def work_category(summary: dict) -> str:
    work_type = (summary.get("type") or "").lower()
    return ORCID_TYPE_CATEGORIES.get(work_type, "other")


def fetch_orcid_works(orcid_id: str) -> dict:
    request = urllib.request.Request(
        f"https://pub.orcid.org/v3.0/{orcid_id}/works",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def work_summaries(payload: dict) -> list[dict]:
    summaries: list[dict] = []
    for group in payload.get("group", []):
        group_summaries = group.get("work-summary") or []
        if group_summaries:
            summaries.append(group_summaries[0])
    return summaries


def find_existing(con: sqlite3.Connection, summary: dict) -> sqlite3.Row | None:
    put_code = str(summary.get("put-code") or "")
    if put_code:
        row = con.execute("SELECT * FROM publications WHERE orcid_put_code = ? ORDER BY id LIMIT 1", (put_code,)).fetchone()
        if row:
            return row
    ids = external_ids(summary)
    doi = normalize_doi(ids.get("doi"))
    if doi:
        row = con.execute(
            "SELECT * FROM publications WHERE lower(COALESCE(doi, '')) = ? ORDER BY id LIMIT 1",
            (doi,),
        ).fetchone()
        if row:
            return row
    pmid = ids.get("pmid")
    if pmid:
        row = con.execute("SELECT * FROM publications WHERE pmid = ? ORDER BY id LIMIT 1", (pmid,)).fetchone()
        if row:
            return row
    title_key = normalize_title(text_value(summary, "title", "title"))
    year = publication_year(summary)
    if title_key and year:
        for row in con.execute("SELECT * FROM publications WHERE year = ?", (year,)).fetchall():
            if normalize_title(row["title"]) == title_key:
                return row
    return None


def ensure_orcid_document(con: sqlite3.Connection, orcid_id: str) -> int:
    con.execute(
        """
        INSERT INTO documents (slug, title, source_path, source_format, imported_at, notes)
        VALUES ('orcid_public_record', 'ORCID public record', ?, 'orcid-api', datetime('now'), 'Synced from the public ORCID API.')
        ON CONFLICT(slug) DO UPDATE SET imported_at=datetime('now'), source_path=excluded.source_path
        """,
        (f"https://orcid.org/{orcid_id}",),
    )
    return int(con.execute("SELECT id FROM documents WHERE slug='orcid_public_record'").fetchone()[0])


def update_existing(con: sqlite3.Connection, row: sqlite3.Row, summary: dict) -> None:
    ids = external_ids(summary)
    doi = normalize_doi(ids.get("doi")) or row["doi"]
    pmid = ids.get("pmid") or row["pmid"]
    venue = text_value(summary, "journal-title") or row["venue"]
    year = publication_year(summary) or row["year"]
    con.execute(
        """
        UPDATE publications
        SET doi=?, pmid=?, venue=?, year=?,
            orcid_put_code=?, orcid_source=?, orcid_last_modified=?, orcid_path=?,
            quality_note=CASE
              WHEN COALESCE(quality_note, '') LIKE 'Suppressed incomplete%' THEN NULL
              ELSE quality_note
            END
        WHERE id=?
        """,
        (
            doi,
            pmid,
            venue,
            year,
            str(summary.get("put-code") or ""),
            source_name(summary),
            text_value(summary, "last-modified-date"),
            summary.get("path"),
            row["id"],
        ),
    )


def insert_work(con: sqlite3.Connection, document_id: int, summary: dict) -> None:
    ids = external_ids(summary)
    title = text_value(summary, "title", "title")
    year = publication_year(summary)
    venue = text_value(summary, "journal-title")
    doi = normalize_doi(ids.get("doi"))
    pmid = ids.get("pmid")
    con.execute(
        """
        INSERT INTO publications (
          document_id, source, item_type, category, authors, title, venue, year, doi, pmid, url,
          raw_citation, confidence, orcid_put_code, orcid_source, orcid_last_modified, orcid_path
        ) VALUES (?, 'orcid', ?, ?, '', ?, ?, ?, ?, ?, ?, ?, 'medium', ?, ?, ?, ?)
        """,
        (
            document_id,
            summary.get("type") or "journal-article",
            work_category(summary),
            title,
            venue,
            year,
            doi,
            pmid,
            text_value(summary, "url"),
            ". ".join(part for part in [title, venue, year] if part),
            str(summary.get("put-code") or ""),
            source_name(summary),
            text_value(summary, "last-modified-date"),
            summary.get("path"),
        ),
    )


def sync_orcid(orcid_id: str | None = None) -> dict[str, int | str]:
    with connect() as con:
        person = con.execute("SELECT * FROM person WHERE id=1").fetchone()
        identifier = con.execute(
            """
            SELECT identifier_value
            FROM person_identifiers
            WHERE person_id = 1 AND lower(platform) = 'orcid'
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
        orcid_id = (
            orcid_id
            or (identifier["identifier_value"] if identifier else None)
            or (person["orcid_id"] if person and "orcid_id" in person.keys() else None)
        )
        if not orcid_id:
            raise RuntimeError("Add an ORCID iD in Connections or Person > Identifiers before syncing ORCID.")
        upsert_person_orcid_identifier(con, orcid_id)
        payload = fetch_orcid_works(orcid_id)
        summaries = work_summaries(payload)
        document_id = ensure_orcid_document(con, orcid_id)
        matched = 0
        inserted = 0
        skipped = 0
        for summary in summaries:
            if not text_value(summary, "title", "title"):
                continue
            row = find_existing(con, summary)
            if row:
                update_existing(con, row, summary)
                matched += 1
            else:
                insert_work(con, document_id, summary)
                inserted += 1
        con.commit()
    maintenance = maintain()
    return {"orcid_id": orcid_id, "fetched": len(summaries), "matched": matched, "inserted": inserted, "skipped_new": skipped, **maintenance}


if __name__ == "__main__":
    print(json.dumps(sync_orcid(), indent=2, ensure_ascii=False))
