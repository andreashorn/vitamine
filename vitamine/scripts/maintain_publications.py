#!/usr/bin/env python3
"""Maintain publication display quality and curated journal metrics."""

from __future__ import annotations

import re
import sqlite3

from vitamine.paths import active_db_path

PREPRINT_VENUES = {
    "arxiv",
    "biorxiv",
    "chemrxiv",
    "medrxiv",
    "osf",
    "preprints.org",
    "psyarxiv",
    "research square",
    "ssrn",
}
PREPRINT_DOI_PREFIXES = (
    "10.1101/",
    "10.21203/",
    "10.31234/",
    "10.48550/arxiv.",
    "10.2139/ssrn.",
    "10.20944/preprints",
    "10.22541/",
    "10.64898/",
)
POSTER_TERMS = ("poster", "conference poster", "meeting abstract")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(active_db_path())
    con.row_factory = sqlite3.Row
    ensure_columns(con)
    return con


def ensure_columns(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(publications)").fetchall()}
    columns = {
        "include_short": "INTEGER NOT NULL DEFAULT 0",
        "include_ultrashort": "INTEGER NOT NULL DEFAULT 0",
        "selected_order": "INTEGER",
        "short_selected_order": "INTEGER",
        "ultrashort_selected_order": "INTEGER",
        "short_citation": "TEXT",
        "impact_factor": "REAL",
        "impact_factor_year": "TEXT",
        "metric_source": "TEXT",
        "suppress_display": "INTEGER NOT NULL DEFAULT 0",
        "quality_note": "TEXT",
        "orcid_put_code": "TEXT",
        "orcid_source": "TEXT",
        "orcid_last_modified": "TEXT",
        "orcid_path": "TEXT",
    }
    for column, definition in columns.items():
        if column not in existing:
            con.execute(f"ALTER TABLE publications ADD COLUMN {column} {definition}")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS journal_metrics (
          venue TEXT PRIMARY KEY,
          impact_factor REAL,
          impact_factor_year TEXT,
          metric_source TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def normalize_title(value: str | None) -> str:
    return (value or "").casefold().replace("‐", "-").replace("‑", "-").replace("–", "-").strip()


def normalize_doi(value: str | None) -> str:
    text = (value or "").strip().casefold()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(".")


def compact_title(value: str | None) -> str:
    return re.sub(r"\W+", " ", normalize_title(value)).strip()


def is_preprint(row: sqlite3.Row) -> bool:
    item_type = (row["item_type"] or "").casefold()
    category = (row["category"] or "").casefold()
    venue = (row["venue"] or "").casefold()
    title = (row["title"] or "").casefold()
    doi = normalize_doi(row["doi"])
    if item_type == "preprint" or category == "preprints":
        return True
    if any(server in venue for server in PREPRINT_VENUES):
        return True
    if any(doi.startswith(prefix) for prefix in PREPRINT_DOI_PREFIXES):
        return True
    return "preprint" in title


def is_poster(row: sqlite3.Row) -> bool:
    item_type = (row["item_type"] or "").casefold()
    category = (row["category"] or "").casefold()
    haystack = " ".join(str(row[key] or "").casefold() for key in ("title", "venue", "raw_citation", "extra"))
    if item_type in {"poster", "presentation"} or category == "posters":
        return True
    return any(term in haystack for term in POSTER_TERMS)


def is_correction(row: sqlite3.Row) -> bool:
    title = (row["title"] or "").strip().casefold()
    return title.startswith(("correction", "publisher correction", "erratum", "corrigendum"))


def suppress_incomplete_duplicates(con: sqlite3.Connection) -> int:
    rows = con.execute(
        """
        SELECT id, title, year, venue, doi, include_short, include_ultrashort
        FROM publications
        WHERE COALESCE(suppress_display, 0) = 0
        """
    ).fetchall()
    complete_titles = {
        normalize_title(row["title"])
        for row in rows
        if row["title"] and row["year"] and row["venue"] and row["doi"]
    }
    suppressed = 0
    for row in rows:
        title_key = normalize_title(row["title"])
        if row["include_short"] or row["include_ultrashort"]:
            continue
        incomplete = not row["year"] or not row["venue"] or not row["doi"]
        if title_key in complete_titles and incomplete:
            con.execute(
                """
                UPDATE publications
                SET suppress_display=1,
                    quality_note=COALESCE(NULLIF(quality_note, ''), 'Suppressed incomplete duplicate; complete Zotero record exists.')
                WHERE id=?
                """,
                (row["id"],),
            )
            suppressed += 1
    con.execute(
        """
        UPDATE publications
        SET suppress_display=1,
            quality_note=COALESCE(NULLIF(quality_note, ''), 'Suppressed incomplete Zotero record; missing year/venue/DOI.')
        WHERE COALESCE(suppress_display, 0) = 0
          AND (year IS NULL OR year = '')
          AND (doi IS NULL OR doi = '')
          AND COALESCE(include_short, 0) = 0
          AND COALESCE(include_ultrashort, 0) = 0
        """
    )
    suppressed += con.total_changes
    return suppressed


def classify_and_suppress_non_cv_publications(con: sqlite3.Connection) -> int:
    rows = con.execute(
        """
        SELECT id, item_type, category, title, venue, doi, raw_citation, extra
        FROM publications
        """
    ).fetchall()
    changed = 0
    for row in rows:
        category = None
        note = None
        if is_poster(row):
            category = "posters"
            note = "Suppressed poster / conference abstract; not exported to CV publication lists."
        elif is_preprint(row):
            category = "preprints"
            note = "Suppressed preprint; not exported to CV publication lists."
        elif is_correction(row):
            category = row["category"]
            note = "Suppressed correction/erratum; not exported to CV publication lists."
        if not category:
            continue
        cursor = con.execute(
            """
            UPDATE publications
            SET category=?,
                suppress_display=1,
                include_short=0,
                include_ultrashort=0,
                selected_order=NULL,
                quality_note=?
            WHERE id=?
              AND (
                category IS NOT ?
                OR COALESCE(suppress_display, 0) != 1
                OR COALESCE(include_short, 0) != 0
                OR COALESCE(include_ultrashort, 0) != 0
                OR COALESCE(quality_note, '') != ?
              )
            """,
            (category, note, row["id"], category, note),
        )
        changed += cursor.rowcount
    return changed


def suppress_orcid_duplicates(con: sqlite3.Connection) -> int:
    rows = con.execute(
        """
        SELECT id, source, category, title, year, doi, suppress_display
        FROM publications
        """
    ).fetchall()
    zotero_by_doi = {
        normalize_doi(row["doi"])
        for row in rows
        if row["source"] == "zotero" and normalize_doi(row["doi"])
    }
    zotero_by_title_year = {
        (compact_title(row["title"]), row["year"] or "")
        for row in rows
        if row["source"] == "zotero" and compact_title(row["title"]) and row["year"]
    }
    changed = 0
    for row in rows:
        if row["source"] != "orcid":
            continue
        if row["category"] in {"preprints", "posters"}:
            continue
        doi_match = normalize_doi(row["doi"]) in zotero_by_doi if normalize_doi(row["doi"]) else False
        title_match = (compact_title(row["title"]), row["year"] or "") in zotero_by_title_year
        if not doi_match and not title_match:
            continue
        cursor = con.execute(
            """
            UPDATE publications
            SET suppress_display=1,
                include_short=0,
                include_ultrashort=0,
                selected_order=NULL,
                quality_note='Suppressed ORCID duplicate; Zotero record is authoritative.'
            WHERE id=?
              AND (
                COALESCE(suppress_display, 0) != 1
                OR COALESCE(include_short, 0) != 0
                OR COALESCE(include_ultrashort, 0) != 0
                OR COALESCE(quality_note, '') != 'Suppressed ORCID duplicate; Zotero record is authoritative.'
              )
            """,
            (row["id"],),
        )
        changed += cursor.rowcount
    return changed


def suppress_unverified_wos_profile_imports(con: sqlite3.Connection) -> int:
    note = "Suppressed unverified Web of Science/Publons ORCID import; review before showing in CV."
    cursor = con.execute(
        """
        UPDATE publications
        SET suppress_display=1,
            include_short=0,
            include_ultrashort=0,
            selected_order=NULL,
            short_selected_order=NULL,
            ultrashort_selected_order=NULL,
            quality_note=?
        WHERE source='orcid'
          AND orcid_source='Web of Science Researcher Profile Sync'
          AND (
            COALESCE(suppress_display, 0) != 1
            OR COALESCE(include_short, 0) != 0
            OR COALESCE(include_ultrashort, 0) != 0
            OR COALESCE(quality_note, '') != ?
          )
        """,
        (note, note),
    )
    return cursor.rowcount


def suppress_orcid_without_horn_authorship(con: sqlite3.Connection) -> int:
    note = "Suppressed ORCID-only record without clear Horn authorship; review before showing in CV."
    cursor = con.execute(
        """
        UPDATE publications
        SET suppress_display=1,
            include_short=0,
            include_ultrashort=0,
            selected_order=NULL,
            short_selected_order=NULL,
            ultrashort_selected_order=NULL,
            quality_note=?
        WHERE source='orcid'
          AND COALESCE(suppress_display, 0)=0
          AND category='peer_reviewed'
          AND (
            authors IS NULL
            OR authors=''
            OR lower(authors) NOT LIKE '%horn%'
          )
          AND (
            COALESCE(suppress_display, 0) != 1
            OR COALESCE(include_short, 0) != 0
            OR COALESCE(include_ultrashort, 0) != 0
            OR COALESCE(quality_note, '') != ?
          )
        """,
        (note, note),
    )
    return cursor.rowcount


def suppress_orcid_only_records(con: sqlite3.Connection) -> int:
    note = "Suppressed ORCID-only record; use Zotero/manual record as authoritative CV source."
    cursor = con.execute(
        """
        UPDATE publications
        SET suppress_display=1,
            include_short=0,
            include_ultrashort=0,
            selected_order=NULL,
            short_selected_order=NULL,
            ultrashort_selected_order=NULL,
            quality_note=COALESCE(NULLIF(quality_note, ''), ?)
        WHERE source='orcid'
          AND COALESCE(suppress_display, 0)=0
        """,
        (note,),
    )
    return cursor.rowcount


def apply_journal_metrics(con: sqlite3.Connection) -> int:
    updated = 0
    for row in con.execute(
        """
        SELECT venue, impact_factor, impact_factor_year, metric_source
        FROM journal_metrics
        WHERE venue != '' AND impact_factor IS NOT NULL
        """
    ).fetchall():
        cursor = con.execute(
            """
            UPDATE publications
            SET impact_factor=?,
                impact_factor_year=?,
                metric_source=?
            WHERE lower(venue) = lower(?)
            """,
            (
                row["impact_factor"],
                row["impact_factor_year"],
                row["metric_source"],
                row["venue"],
            ),
        )
        updated += cursor.rowcount
    return updated


def maintain() -> dict[str, int]:
    with connect() as con:
        non_cv_changed = classify_and_suppress_non_cv_publications(con)
        orcid_duplicates = suppress_orcid_duplicates(con)
        wos_profile_imports = suppress_unverified_wos_profile_imports(con)
        orcid_no_horn = suppress_orcid_without_horn_authorship(con)
        orcid_only = suppress_orcid_only_records(con)
        before = con.total_changes
        suppress_incomplete_duplicates(con)
        suppressed = con.total_changes - before
        metric_before = con.total_changes
        apply_journal_metrics(con)
        metrics_updated = con.total_changes - metric_before
        con.commit()
    return {
        "suppressed": suppressed,
        "non_cv_suppressed": non_cv_changed,
        "orcid_duplicates_suppressed": orcid_duplicates,
        "wos_profile_imports_suppressed": wos_profile_imports,
        "orcid_without_horn_suppressed": orcid_no_horn,
        "orcid_only_suppressed": orcid_only,
        "metrics_updated": metrics_updated,
    }


if __name__ == "__main__":
    print(maintain())
