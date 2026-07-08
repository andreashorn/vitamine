#!/usr/bin/env python3
"""Conservatively enrich local publication rows from DOI-based metadata."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from maintain_publications import maintain
from vitamine.paths import OUTPUT, ROOT, active_db_path, output_ref


DB = active_db_path()
REPORT = OUTPUT / "doi_enrichment_report.json"
USER_AGENT = "vitamine/0.1"
INSTITUTION_CACHE: dict[str, dict[str, Any]] = {}


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ensure_columns(con)
    return con


def ensure_columns(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(publications)").fetchall()}
    columns = {
        "metadata_source": "TEXT",
        "metadata_enriched_at": "TEXT",
        "openalex_work_id": "TEXT",
        "openalex_cited_by_count": "INTEGER",
    }
    for column, definition in columns.items():
        if column not in existing:
            con.execute(f"ALTER TABLE publications ADD COLUMN {column} {definition}")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS collaboration_institutions (
          id INTEGER PRIMARY KEY,
          publication_id INTEGER NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
          openalex_work_id TEXT,
          publication_title TEXT,
          publication_year TEXT,
          author_name TEXT,
          author_position TEXT,
          institution_id TEXT NOT NULL,
          institution_name TEXT NOT NULL,
          ror TEXT,
          country_code TEXT,
          country TEXT,
          latitude REAL,
          longitude REAL,
          source TEXT NOT NULL DEFAULT 'openalex',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(publication_id, author_name, institution_id)
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_collaboration_institutions_pub
        ON collaboration_institutions(publication_id)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_collaboration_institutions_inst
        ON collaboration_institutions(institution_id)
        """
    )


def normalize_doi(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(".")


def clean_text(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    text = str(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def request_json(url: str) -> dict[str, Any] | None:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status >= 400:
                return None
            return json.load(response)
    except (TimeoutError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return None


def openalex_institution(institution_id: str) -> dict[str, Any]:
    if not institution_id:
        return {}
    if institution_id in INSTITUTION_CACHE:
        return INSTITUTION_CACHE[institution_id]
    if institution_id.startswith("https://openalex.org/"):
        api_id = institution_id.rstrip("/").split("/")[-1]
        url = f"https://api.openalex.org/institutions/{urllib.parse.quote(api_id, safe='')}"
    else:
        url = institution_id
    payload = request_json(url)
    INSTITUTION_CACHE[institution_id] = payload or {}
    return INSTITUTION_CACHE[institution_id]


def institution_geo(institution: dict[str, Any]) -> dict[str, Any]:
    full = openalex_institution(str(institution.get("id") or ""))
    geo = full.get("geo") or {}
    return {
        "country_code": geo.get("country_code") or full.get("country_code") or institution.get("country_code") or "",
        "country": geo.get("country") or "",
        "latitude": geo.get("latitude"),
        "longitude": geo.get("longitude"),
    }


def openalex_collaboration_rows(publication_id: int, row: sqlite3.Row, payload: dict[str, Any]) -> list[dict[str, Any]]:
    work_id = payload.get("id") or ""
    publication_title = clean_text(payload.get("display_name")) or clean_text(row["title"])
    publication_year = str(payload.get("publication_year") or row["year"] or "")
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for authorship in payload.get("authorships") or []:
        author = authorship.get("author") or {}
        author_name = clean_text(author.get("display_name") or authorship.get("raw_author_name"))
        author_position = clean_text(authorship.get("author_position"))
        for institution in authorship.get("institutions") or []:
            institution_id = str(institution.get("id") or "").strip()
            institution_name = clean_text(institution.get("display_name"))
            if not institution_id or not institution_name:
                continue
            key = (author_name, institution_id, work_id)
            if key in seen:
                continue
            seen.add(key)
            geo = institution_geo(institution)
            records.append(
                {
                    "publication_id": publication_id,
                    "openalex_work_id": work_id,
                    "publication_title": publication_title,
                    "publication_year": publication_year,
                    "author_name": author_name,
                    "author_position": author_position,
                    "institution_id": institution_id,
                    "institution_name": institution_name,
                    "ror": institution.get("ror") or "",
                    "country_code": geo["country_code"],
                    "country": geo["country"],
                    "latitude": geo["latitude"],
                    "longitude": geo["longitude"],
                }
            )
    return records


def year_from_date_parts(parts: Any) -> str:
    try:
        year = parts[0][0]
    except (TypeError, IndexError):
        return ""
    return str(year) if year else ""


def crossref_metadata(doi: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(doi, safe="")
    payload = request_json(f"https://api.crossref.org/works/{encoded}")
    message = (payload or {}).get("message") or {}
    if not message:
        return {}
    authors = []
    for author in message.get("author") or []:
        name = " ".join(part for part in [author.get("given"), author.get("family")] if part)
        if name:
            authors.append(name)
    year = (
        year_from_date_parts((message.get("published-print") or {}).get("date-parts"))
        or year_from_date_parts((message.get("published-online") or {}).get("date-parts"))
        or year_from_date_parts((message.get("published") or {}).get("date-parts"))
        or year_from_date_parts((message.get("issued") or {}).get("date-parts"))
    )
    return {
        "title": clean_text(message.get("title")),
        "venue": clean_text(message.get("container-title")),
        "year": year,
        "authors": ", ".join(authors),
        "doi": normalize_doi(message.get("DOI") or doi),
        "url": clean_text(message.get("URL")),
        "crossref_type": clean_text(message.get("type")),
    }


def pubmed_pmid(doi: str) -> str:
    term = urllib.parse.quote(f"{doi}[AID]")
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&retmode=json&tool=hornacademic_cv&email=andreas.horn%40uk-koeln.de&term={term}"
    payload = request_json(url)
    ids = (((payload or {}).get("esearchresult") or {}).get("idlist") or [])
    return str(ids[0]) if ids else ""


def openalex_metadata(doi: str) -> dict[str, Any]:
    candidates = [
        f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi, safe='')}",
        f"https://api.openalex.org/works/{urllib.parse.quote('https://doi.org/' + doi, safe='')}",
    ]
    payload = None
    for url in candidates:
        payload = request_json(url)
        if payload and payload.get("id"):
            break
    if not payload or not payload.get("id"):
        return {}
    primary_location = payload.get("primary_location") or {}
    source = primary_location.get("source") or {}
    authorships = payload.get("authorships") or []
    authors = []
    for authorship in authorships:
        author = authorship.get("author") or {}
        name = author.get("display_name")
        if name:
            authors.append(name)
    return {
        "openalex_work_id": payload.get("id") or "",
        "openalex_cited_by_count": payload.get("cited_by_count"),
        "title": clean_text(payload.get("display_name")),
        "venue": clean_text(source.get("display_name")),
        "year": str(payload.get("publication_year") or ""),
        "authors": ", ".join(authors),
        "_payload": payload,
    }


def raw_citation(values: dict[str, Any]) -> str:
    parts = [values.get("authors"), values.get("title"), values.get("venue"), values.get("year")]
    citation = ". ".join(str(part).strip() for part in parts if str(part or "").strip())
    doi = values.get("doi")
    if doi:
        citation = f"{citation}. doi:{doi}" if citation else f"doi:{doi}"
    return citation


def merged_metadata(row: sqlite3.Row, crossref: dict[str, Any], openalex: dict[str, Any], pmid: str) -> dict[str, Any]:
    merged = dict(row)
    for field in ("title", "venue", "year", "authors", "doi", "url"):
        current = str(merged.get(field) or "").strip()
        candidate = str(crossref.get(field) or openalex.get(field) or "").strip()
        if not current and candidate:
            merged[field] = candidate
    if not str(merged.get("pmid") or "").strip() and pmid:
        merged["pmid"] = pmid
    if openalex.get("openalex_work_id"):
        merged["openalex_work_id"] = openalex["openalex_work_id"]
    if openalex.get("openalex_cited_by_count") is not None:
        merged["openalex_cited_by_count"] = int(openalex["openalex_cited_by_count"])
    if not str(merged.get("raw_citation") or "").strip():
        merged["raw_citation"] = raw_citation(merged)
    return merged


def changes_for_row(row: sqlite3.Row, values: dict[str, Any]) -> dict[str, Any]:
    changes = {}
    fields = [
        "title",
        "venue",
        "year",
        "authors",
        "doi",
        "pmid",
        "url",
        "raw_citation",
        "openalex_work_id",
        "openalex_cited_by_count",
    ]
    for field in fields:
        old = row[field] if field in row.keys() else None
        new = values.get(field)
        if str(old or "") != str(new or ""):
            changes[field] = {"old": old, "new": new}
    return changes


def update_row(con: sqlite3.Connection, row_id: int, values: dict[str, Any], source: str) -> None:
    con.execute(
        """
        UPDATE publications
        SET title=?,
            venue=?,
            year=?,
            authors=?,
            doi=?,
            pmid=?,
            url=?,
            raw_citation=?,
            openalex_work_id=?,
            openalex_cited_by_count=?,
            metadata_source=?,
            metadata_enriched_at=?
        WHERE id=?
        """,
        (
            values.get("title"),
            values.get("venue"),
            values.get("year"),
            values.get("authors"),
            values.get("doi"),
            values.get("pmid"),
            values.get("url"),
            values.get("raw_citation") or raw_citation(values),
            values.get("openalex_work_id"),
            values.get("openalex_cited_by_count"),
            source,
            dt.datetime.now(dt.timezone.utc).isoformat(),
            row_id,
        ),
    )


def upsert_collaboration_rows(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        con.execute(
            """
            INSERT INTO collaboration_institutions (
              publication_id, openalex_work_id, publication_title, publication_year,
              author_name, author_position, institution_id, institution_name, ror,
              country_code, country, latitude, longitude, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'openalex', datetime('now'))
            ON CONFLICT(publication_id, author_name, institution_id) DO UPDATE SET
              openalex_work_id=excluded.openalex_work_id,
              publication_title=excluded.publication_title,
              publication_year=excluded.publication_year,
              author_position=excluded.author_position,
              institution_name=excluded.institution_name,
              ror=excluded.ror,
              country_code=excluded.country_code,
              country=excluded.country,
              latitude=excluded.latitude,
              longitude=excluded.longitude,
              source='openalex',
              updated_at=datetime('now')
            """,
            (
                row["publication_id"],
                row["openalex_work_id"],
                row["publication_title"],
                row["publication_year"],
                row["author_name"],
                row["author_position"],
                row["institution_id"],
                row["institution_name"],
                row["ror"],
                row["country_code"],
                row["country"],
                row["latitude"],
                row["longitude"],
            ),
        )
        count += 1
    return count


def enrich(
    limit: int | None = None,
    include_suppressed: bool = False,
    dry_run: bool = False,
    refresh: bool = False,
) -> dict[str, Any]:
    where = "WHERE doi IS NOT NULL AND doi != ''"
    if not include_suppressed:
        where += " AND COALESCE(suppress_display, 0) = 0"
    if not refresh:
        where += " AND metadata_enriched_at IS NULL"
    sql_limit = f" LIMIT {int(limit)}" if limit else ""
    report: list[dict[str, Any]] = []
    fetched = 0
    updated = 0
    institutions = 0
    with connect() as con:
        rows = con.execute(
            f"""
            SELECT *
            FROM publications
            {where}
            ORDER BY year DESC, id DESC
            {sql_limit}
            """
        ).fetchall()
        for row in rows:
            doi = normalize_doi(row["doi"])
            if not doi:
                continue
            time.sleep(0.05)
            crossref = crossref_metadata(doi)
            openalex = openalex_metadata(doi)
            pmid = pubmed_pmid(doi)
            fetched += 1
            values = merged_metadata(row, crossref, openalex, pmid)
            changes = changes_for_row(row, values)
            sources = [name for name, data in [("crossref", crossref), ("openalex", openalex), ("pubmed", pmid)] if data]
            collaboration_rows = []
            if openalex.get("_payload"):
                collaboration_rows = openalex_collaboration_rows(row["id"], row, openalex["_payload"])
                if collaboration_rows and not dry_run:
                    institutions += upsert_collaboration_rows(con, collaboration_rows)
            if changes:
                updated += 1
                if not dry_run:
                    update_row(con, row["id"], values, "+".join(sources) or "doi")
                report.append(
                    {
                        "id": row["id"],
                        "doi": doi,
                        "title": row["title"],
                        "sources": sources,
                        "changes": changes,
                        "institutions": len(collaboration_rows),
                    }
                )
        if not dry_run:
            con.commit()
    maintenance = maintain() if not dry_run else {}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "checked": fetched,
        "updated": updated,
        "institutions": institutions,
        "dry_run": dry_run,
        "refresh": refresh,
        "report": f"output/{output_ref(REPORT)}",
        **maintenance,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-suppressed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    print(json.dumps(enrich(args.limit, args.include_suppressed, args.dry_run, args.refresh), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
