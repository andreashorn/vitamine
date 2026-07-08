#!/usr/bin/env python3
"""Apply conservative curation defaults for the short CV export."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from vitamine.paths import active_db_path

DB = active_db_path()


HONOR_TITLE_PATTERNS = [
    "%International Brain Stimulation Early Career Award%",
    "%Highly Cited Researchers%",
    "%Global Call Winner%",
    "%Heinz-Maier-Leibnitz%",
    "%Data Reuse Award%",
    "%Best Paper Award%",
    "%Peer Review Award%",
    "%Editor's Choice%",
    "%Emmy Noether Excellence Fellowship%",
    "%Robert Koch Prize%",
    "%Harvard Radcliffe Institute Academic Ventures Grant%",
    "%Max Rubner Prize%",
]

SHORT_PUBLICATION_DOIS = [
    "10.1038/s41593-024-01570-1",
    "10.1038/s41593-024-01572-z",
    "10.1038/s41467-024-48731-1",
    "10.1038/s41467-025-60089-6",
    "10.1038/s41467-020-16734-3",
    "10.1038/s41467-022-34510-3",
    "10.1038/s41582-025-01131-5",
    "10.1126/sciadv.adp0532",
    "10.1073/pnas.2417617122",
    "10.1093/brain/awab258",
]


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def curate() -> dict[str, int]:
    with connect() as con:
        con.execute("UPDATE cv_entries SET include_short=0 WHERE section_key='honors'")
        enabled_honors = 0
        for pattern in HONOR_TITLE_PATTERNS:
            row = con.execute(
                """
                SELECT id
                FROM cv_entries
                WHERE section_key='honors'
                  AND title LIKE ?
                ORDER BY
                  CASE WHEN document_id=2 THEN 0 ELSE 1 END,
                  id DESC
                LIMIT 1
                """,
                (pattern,),
            ).fetchone()
            if row:
                con.execute("UPDATE cv_entries SET include_short=1 WHERE id=?", (row["id"],))
                enabled_honors += 1
        con.execute("UPDATE publications SET include_short=0, short_selected_order=NULL")
        enabled_publications = 0
        for order, doi in enumerate(SHORT_PUBLICATION_DOIS, 1):
            row = con.execute(
                """
                SELECT id
                FROM publications
                WHERE lower(COALESCE(doi, '')) = lower(?)
                  AND COALESCE(suppress_display, 0)=0
                  AND category='peer_reviewed'
                ORDER BY source='zotero' DESC, id
                LIMIT 1
                """,
                (doi,),
            ).fetchone()
            if row:
                con.execute(
                    "UPDATE publications SET include_short=1, short_selected_order=? WHERE id=?",
                    (order, row["id"]),
                )
                enabled_publications += 1
        con.commit()
    return {"short_honors_enabled": enabled_honors, "short_publications_enabled": enabled_publications}


if __name__ == "__main__":
    print(json.dumps(curate(), indent=2, ensure_ascii=False))
