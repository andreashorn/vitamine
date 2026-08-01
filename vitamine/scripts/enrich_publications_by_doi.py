#!/usr/bin/env python3
"""Conservatively enrich local publication rows from DOI-based metadata."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .maintain_publications import maintain
except ImportError:
    from maintain_publications import maintain
from vitamine.paths import OUTPUT, ROOT, active_db_path, output_ref
from vitamine.metadata_text import decode_metadata_text


DB = active_db_path()
REPORT = OUTPUT / "doi_enrichment_report.json"
USER_AGENT = "vitamine/0.1"
INSTITUTION_CACHE: dict[str, dict[str, Any]] = {}
REQUEST_CACHE: dict[str, dict[str, Any] | None] = {}
ROR_AFFILIATION_CACHE: dict[str, dict[str, Any]] = {}


def write_job_progress(message: str, percent: int) -> None:
    raw_path = os.environ.get("VITAMINE_JOB_PROGRESS_PATH", "").strip()
    if not raw_path:
        return
    path = Path(raw_path)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    payload = {
        "phase": "doi_enrichment",
        "message": message,
        "percent": max(8, min(60, int(percent))),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
    except OSError:
        # Progress reporting must never interrupt metadata enrichment.
        temporary.unlink(missing_ok=True)


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
        "openalex_counts_by_year_json": "TEXT",
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
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS citation_institutions (
          id INTEGER PRIMARY KEY,
          publication_id INTEGER NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
          cited_openalex_work_id TEXT,
          citing_openalex_work_id TEXT NOT NULL,
          citing_work_title TEXT,
          citing_work_year TEXT,
          author_id TEXT,
          author_name TEXT NOT NULL,
          institution_id TEXT NOT NULL,
          institution_name TEXT NOT NULL,
          ror TEXT,
          country_code TEXT,
          country TEXT,
          latitude REAL,
          longitude REAL,
          source TEXT NOT NULL DEFAULT 'openalex',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(publication_id, citing_openalex_work_id, author_name, institution_id)
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_citation_institutions_pub ON citation_institutions(publication_id)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_citation_institutions_author ON citation_institutions(author_id, author_name)"
    )


def normalize_doi(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(".")


def clean_text(value: Any) -> str:
    return decode_metadata_text(value, strip_markup=True)


def compact_text(value: Any) -> str:
    text = clean_text(value).casefold()
    text = text.replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def token_set(value: Any) -> set[str]:
    stop = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}
    return {token for token in compact_text(value).split() if token and token not in stop}


def token_similarity(left: Any, right: Any) -> float:
    a = token_set(left)
    b = token_set(right)
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


def year_int(value: Any) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", str(value or ""))
    return int(match.group(0)) if match else None


def year_score(left: Any, right: Any) -> float:
    a = year_int(left)
    b = year_int(right)
    if a is None or b is None:
        return 0.0
    diff = abs(a - b)
    if diff == 0:
        return 1.0
    if diff == 1:
        return 0.65
    if diff == 2:
        return 0.35
    return -0.5


def author_overlap_score(row: sqlite3.Row, metadata: dict[str, Any]) -> float:
    row_authors = token_set(row["authors"] or row["raw_citation"])
    meta_authors = token_set(metadata.get("authors"))
    if not row_authors or not meta_authors:
        return 0.0
    token_overlap = len(row_authors & meta_authors) / min(len(row_authors), len(meta_authors))
    row_surnames = author_surnames(row["authors"])
    metadata_surnames = author_surnames(metadata.get("authors"))
    surname_overlap = 0.0
    if row_surnames and metadata_surnames:
        surname_overlap = len(row_surnames & metadata_surnames) / min(len(row_surnames), len(metadata_surnames))
    return max(token_overlap, surname_overlap)


def author_surnames(value: Any) -> set[str]:
    surnames: set[str] = set()
    for author in re.split(r"\s*(?:,|;|\band\b)\s*", clean_text(value), flags=re.I):
        raw_tokens = re.findall(r"[A-Za-zÀ-ž][A-Za-zÀ-ž'’-]*", author)
        if not raw_tokens:
            continue
        normalized = [compact_text(token) for token in raw_tokens if compact_text(token)]
        if not normalized:
            continue
        last_raw = raw_tokens[-1].replace(".", "")
        inverted_initials = len(last_raw) <= 4 and last_raw.isupper()
        surname = normalized[0] if inverted_initials and len(normalized) > 1 else normalized[-1]
        if len(surname) > 1:
            surnames.add(surname)
    return surnames


def venue_score(row: sqlite3.Row, metadata: dict[str, Any]) -> float:
    row_venue = row["venue"] or row["raw_citation"]
    meta_venue = metadata.get("venue")
    if not row_venue or not meta_venue:
        return 0.0
    return token_similarity(row_venue, meta_venue)


def resolution_score(row: sqlite3.Row, metadata: dict[str, Any]) -> tuple[float, dict[str, float]]:
    title = token_similarity(row["title"], metadata.get("title"))
    year = year_score(row["year"] or row["raw_citation"], metadata.get("year"))
    authors = author_overlap_score(row, metadata)
    venue = venue_score(row, metadata)
    pages = 0.0
    if row["raw_citation"] and metadata.get("raw_citation"):
        pages = token_similarity(row["raw_citation"], metadata["raw_citation"])
    score = 0.62 * title + 0.16 * max(year, 0) + 0.12 * authors + 0.07 * venue + 0.03 * pages
    if year < 0:
        score += year
    return score, {"title": title, "year": year, "authors": authors, "venue": venue, "raw": pages}


def request_json(url: str) -> dict[str, Any] | None:
    if url in REQUEST_CACHE:
        return REQUEST_CACHE[url]
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status >= 400:
                REQUEST_CACHE[url] = None
                return None
            payload = json.load(response)
            REQUEST_CACHE[url] = payload
            return payload
    except (TimeoutError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        REQUEST_CACHE[url] = None
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


def ror_affiliation_match(raw_affiliation: str) -> dict[str, Any]:
    affiliation = clean_text(raw_affiliation)
    cache_key = compact_text(
        re.sub(r"\bElectronic address:\s*\S+@\S+\b", "", affiliation, flags=re.I)
    )
    if not cache_key:
        return {}
    if cache_key in ROR_AFFILIATION_CACHE:
        return ROR_AFFILIATION_CACHE[cache_key]
    params = urllib.parse.urlencode({"affiliation": affiliation})
    payload = request_json(f"https://api.ror.org/v2/organizations?{params}") or {}
    match = next(
        (
            item.get("organization") or {}
            for item in payload.get("items") or []
            if item.get("chosen") is True
        ),
        {},
    )
    ROR_AFFILIATION_CACHE[cache_key] = match
    return match


def ror_display_name(organization: dict[str, Any]) -> str:
    names = organization.get("names") or []
    for preferred_type in ("ror_display", "label", "alias"):
        for name in names:
            if preferred_type in (name.get("types") or []):
                value = clean_text(name.get("value"))
                if value:
                    return value
    return clean_text(organization.get("name"))


def ror_institution_record(organization: dict[str, Any]) -> dict[str, Any]:
    institution_id = str(organization.get("id") or "").strip()
    name = ror_display_name(organization)
    locations = organization.get("locations") or []
    geonames = (locations[0].get("geonames_details") or {}) if locations else {}
    if not institution_id or not name:
        return {}
    return {
        "institution_id": institution_id,
        "institution_name": name,
        "ror": institution_id,
        "country_code": str(geonames.get("country_code") or ""),
        "country": clean_text(geonames.get("country_name")),
        "latitude": geonames.get("lat"),
        "longitude": geonames.get("lng"),
        "source": "ror",
    }


GENERIC_INSTITUTION_TOKENS = {
    "academy",
    "center",
    "centre",
    "clinic",
    "college",
    "company",
    "department",
    "foundation",
    "health",
    "hospital",
    "institute",
    "institution",
    "laboratory",
    "medical",
    "medicine",
    "organization",
    "research",
    "school",
    "university",
}


def institution_matches_raw_affiliation(
    institution: dict[str, Any],
    raw_affiliation: str,
) -> bool:
    raw_compact = compact_text(raw_affiliation)
    if not raw_compact:
        return False
    full = openalex_institution(str(institution.get("id") or ""))
    names = [
        institution.get("display_name"),
        full.get("display_name"),
        *(full.get("display_name_alternatives") or []),
    ]
    acronyms = {
        compact_text(value)
        for value in full.get("display_name_acronyms") or []
        if compact_text(value)
    }
    raw_tokens = set(raw_compact.split())
    for value in names:
        normalized = compact_text(value)
        if not normalized:
            continue
        if re.search(rf"\b{re.escape(normalized)}\b", raw_compact):
            return True
        tokens = {
            token
            for token in normalized.split()
            if token not in GENERIC_INSTITUTION_TOKENS
        }
        if len(tokens) >= 2 and tokens.issubset(raw_tokens):
            return True
    return any(
        re.search(rf"\b{re.escape(acronym)}\b", raw_compact)
        for acronym in acronyms
    )


def openalex_institution_record(institution: dict[str, Any]) -> dict[str, Any]:
    full = openalex_institution(str(institution.get("id") or ""))
    geo = full.get("geo") or {}
    ror = str(institution.get("ror") or full.get("ror") or "").strip()
    institution_id = ror or str(institution.get("id") or "").strip()
    name = clean_text(institution.get("display_name") or full.get("display_name"))
    if not institution_id or not name:
        return {}
    return {
        "institution_id": institution_id,
        "institution_name": name,
        "ror": ror,
        "country_code": str(
            geo.get("country_code")
            or full.get("country_code")
            or institution.get("country_code")
            or ""
        ),
        "country": clean_text(geo.get("country")),
        "latitude": geo.get("latitude"),
        "longitude": geo.get("longitude"),
        "source": "openalex",
    }


def openalex_collaboration_rows(publication_id: int, row: sqlite3.Row, payload: dict[str, Any]) -> list[dict[str, Any]]:
    work_id = payload.get("id") or ""
    publication_title = clean_text(payload.get("display_name")) or clean_text(row["title"])
    publication_year = str(payload.get("publication_year") or row["year"] or "")
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for authorship in payload.get("authorships") or []:
        author = authorship.get("author") or {}
        author_id = str(author.get("id") or "").strip()
        author_name = clean_text(author.get("display_name") or authorship.get("raw_author_name"))
        author_position = clean_text(authorship.get("author_position"))
        institutions = authorship.get("institutions") or []
        institutions_by_id = {
            str(institution.get("id") or "").strip(): institution
            for institution in institutions
            if str(institution.get("id") or "").strip()
        }
        affiliation_groups = []
        for affiliation in authorship.get("affiliations") or []:
            raw_affiliation = clean_text(affiliation.get("raw_affiliation_string"))
            candidates = [
                institutions_by_id[institution_id]
                for institution_id in affiliation.get("institution_ids") or []
                if institution_id in institutions_by_id
            ]
            affiliation_groups.append((raw_affiliation, candidates))
        if not affiliation_groups:
            raw_affiliations = [
                clean_text(value)
                for value in authorship.get("raw_affiliation_strings") or []
                if clean_text(value)
            ]
            affiliation_groups = (
                [(raw_affiliation, institutions) for raw_affiliation in raw_affiliations]
                if raw_affiliations
                else [("", institutions)]
            )

        selected: list[dict[str, Any]] = []
        for raw_affiliation, candidates in affiliation_groups:
            if not raw_affiliation:
                selected.extend(
                    record
                    for record in map(openalex_institution_record, candidates)
                    if record
                )
                continue
            direct_matches = [
                institution
                for institution in candidates
                if institution_matches_raw_affiliation(institution, raw_affiliation)
            ]
            if direct_matches:
                selected.extend(
                    record
                    for record in map(openalex_institution_record, direct_matches)
                    if record
                )
                continue
            ror_match = ror_institution_record(ror_affiliation_match(raw_affiliation))
            if ror_match:
                selected.append(ror_match)

        for institution in selected:
            institution_id = institution["institution_id"]
            key = (author_name, institution_id, work_id)
            if key in seen:
                continue
            seen.add(key)
            records.append(
                {
                    "publication_id": publication_id,
                    "openalex_work_id": work_id,
                    "publication_title": publication_title,
                    "publication_year": publication_year,
                    "author_name": author_name,
                    "author_id": author_id,
                    "author_position": author_position,
                    "institution_id": institution_id,
                    **institution,
                }
            )
    return records


def citation_sample_limit(cited_by_count: Any) -> int:
    """Keep citation geography useful without making refresh cost unbounded."""
    try:
        count = max(0, int(cited_by_count or 0))
    except (TypeError, ValueError):
        count = 0
    if count <= 25:
        return count
    if count <= 250:
        return 50
    return 100


def openalex_citation_rows(
    publication_id: int,
    cited_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    cited_work_id = str(cited_payload.get("id") or "").strip()
    limit = citation_sample_limit(cited_payload.get("cited_by_count"))
    if not cited_work_id or not limit:
        return []
    short_id = cited_work_id.rsplit("/", 1)[-1]
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
        {
            "filter": f"cites:{short_id}",
            "sort": "cited_by_count:desc",
            "per-page": limit,
        }
    )
    payload = request_json(url) or {}
    records: list[dict[str, Any]] = []
    for citing_work in payload.get("results") or []:
        proxy = {
            "id": citing_work.get("id"),
            "display_name": citing_work.get("display_name"),
            "publication_year": citing_work.get("publication_year"),
            "authorships": citing_work.get("authorships") or [],
        }
        for row in openalex_collaboration_rows(publication_id, cited_payload, proxy):
            records.append(
                {
                    **row,
                    "cited_openalex_work_id": cited_work_id,
                    "citing_openalex_work_id": row["openalex_work_id"],
                    "citing_work_title": row["publication_title"],
                    "citing_work_year": row["publication_year"],
                }
            )
    return records


def year_from_date_parts(parts: Any) -> str:
    try:
        year = parts[0][0]
    except (TypeError, IndexError):
        return ""
    return str(year) if year else ""


def normalize_orcid(value: Any) -> str:
    text = clean_text(value).casefold()
    text = re.sub(r"^https?://orcid\.org/", "", text)
    return text.strip().upper()


def crossref_metadata(doi: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(doi, safe="")
    payload = request_json(f"https://api.crossref.org/works/{encoded}")
    message = (payload or {}).get("message") or {}
    if not message:
        return {}
    authors = []
    author_records: list[dict[str, Any]] = []
    for author in message.get("author") or []:
        personal_name = " ".join(
            clean_text(part)
            for part in [author.get("given"), author.get("family")]
            if clean_text(part)
        )
        collective_name = clean_text(author.get("name"))
        name = personal_name or collective_name
        if name:
            authors.append(name)
            author_records.append(
                {
                    "name": name,
                    "orcid": normalize_orcid(author.get("ORCID")),
                    "affiliations": [
                        clean_text(affiliation.get("name"))
                        for affiliation in author.get("affiliation") or []
                        if clean_text(affiliation.get("name"))
                    ],
                    "collective": bool(collective_name and not personal_name),
                }
            )
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
        "abstract": clean_text(message.get("abstract")),
        "crossref_type": clean_text(message.get("type")),
        "_author_records": author_records,
        "_author_count": len(author_records),
        "_has_collective_author": any(record["collective"] for record in author_records),
    }


def crossref_query_candidates(text: str, year: str = "", rows: int = 5, bibliographic: bool = False) -> list[dict[str, Any]]:
    clean_title = clean_text(text)
    if len(token_set(clean_title)) < 2:
        return []
    params = {"query.bibliographic" if bibliographic else "query.title": clean_title, "rows": str(rows)}
    if year_int(year):
        params["filter"] = f"from-pub-date:{year_int(year)-1},until-pub-date:{year_int(year)+1}"
    payload = request_json(f"https://api.crossref.org/works?{urllib.parse.urlencode(params)}")
    items = (((payload or {}).get("message") or {}).get("items") or [])
    candidates = []
    for item in items:
        doi = normalize_doi(item.get("DOI"))
        if not doi:
            continue
        metadata = crossref_metadata(doi)
        if metadata:
            metadata["_resolver"] = "crossref-bibliographic" if bibliographic else "crossref-title"
            candidates.append(metadata)
        time.sleep(0.03)
    return candidates


def pubmed_pmid(doi: str) -> str:
    term = urllib.parse.quote(f"{doi}[AID]")
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&retmode=json&tool=hornacademic_cv&email=andreas.horn%40uk-koeln.de&term={term}"
    payload = request_json(url)
    ids = (((payload or {}).get("esearchresult") or {}).get("idlist") or [])
    return str(ids[0]) if ids else ""


def pubmed_metadata(pmid: str) -> dict[str, Any]:
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?"
        + urllib.parse.urlencode({"db": "pubmed", "retmode": "json", "tool": "hornacademic_cv", "email": "andreas.horn@uk-koeln.de", "id": pmid})
    )
    payload = request_json(url)
    summary = ((payload or {}).get("result") or {}).get(str(pmid)) or {}
    if not summary:
        return {}
    author_records = []
    for author in summary.get("authors") or []:
        name = clean_text(author.get("name"))
        if not name:
            continue
        author_records.append(
            {
                "name": name,
                "orcid": "",
                "affiliations": [],
                "collective": str(author.get("authtype") or "").casefold() == "collectivename",
            }
        )
    authors = [record["name"] for record in author_records]
    doi = ""
    for article_id in summary.get("articleids") or []:
        if str(article_id.get("idtype") or "").casefold() == "doi":
            doi = normalize_doi(article_id.get("value"))
            break
    pubdate = clean_text(summary.get("pubdate"))
    return {
        "title": clean_text(summary.get("title")),
        "venue": clean_text(summary.get("fulljournalname") or summary.get("source")),
        "year": str(year_int(pubdate) or ""),
        "authors": ", ".join(authors),
        "doi": doi,
        "pmid": str(pmid),
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "crossref_type": "journal-article",
        "_resolver": "pubmed-title",
        "_author_records": author_records,
        "_author_count": len(author_records),
        "_has_collective_author": any(record["collective"] for record in author_records),
    }


def suspicious_author_expansion(current_authors: Any, metadata: dict[str, Any]) -> bool:
    """Detect registry author lists that likely flattened a collaborative group."""
    candidate = clean_text(metadata.get("authors"))
    current = clean_text(current_authors)
    count = int(metadata.get("_author_count") or 0)
    if count > 100:
        return True
    if metadata.get("_has_collective_author") and count > 25:
        return True
    return bool(current and len(candidate) > 500 and len(candidate) > max(3 * len(current), len(current) + 400))


def authoritative_metadata(
    current: Any,
    crossref: dict[str, Any],
    pubmed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine registries without allowing Crossref to explode consortium authors."""
    current_values = dict(current)
    pubmed = pubmed or {}
    metadata: dict[str, Any] = {}
    for field in ("title", "venue", "year", "doi", "url", "crossref_type"):
        value = crossref.get(field) or pubmed.get(field)
        if value:
            metadata[field] = value

    pubmed_agrees = bool(
        pubmed.get("authors")
        and pubmed.get("title")
        and token_similarity(metadata.get("title") or current_values.get("title"), pubmed.get("title")) >= 0.90
    )
    if pubmed_agrees:
        metadata["authors"] = pubmed["authors"]
        metadata["_author_source"] = "pubmed"
    elif crossref.get("authors") and not suspicious_author_expansion(current_values.get("authors"), crossref):
        metadata["authors"] = crossref["authors"]
        metadata["_author_source"] = "crossref"
    elif current_values.get("authors"):
        metadata["authors"] = current_values["authors"]
        metadata["_author_source"] = "manual-preserved"

    # Crossref's structured records carry ORCID and affiliation evidence. PubMed's
    # compact list remains available separately for consortium-safe rendering.
    metadata["_author_records"] = crossref.get("_author_records") or pubmed.get("_author_records") or []
    metadata["_author_count"] = len(metadata["_author_records"])
    metadata["_has_collective_author"] = bool(
        crossref.get("_has_collective_author") or pubmed.get("_has_collective_author")
    )
    metadata["_pubmed_author_records"] = pubmed.get("_author_records") or []
    metadata["_pubmed_checked"] = bool(pubmed)
    if pubmed.get("pmid"):
        metadata["pmid"] = pubmed["pmid"]
    return metadata


def metadata_match_is_safe(
    row: Any,
    metadata: dict[str, Any],
    *,
    require_author_overlap: bool | None = None,
) -> tuple[bool, float, dict[str, float], str]:
    """Require independent bibliographic agreement before replacing manual data."""
    current = dict(row)
    current_doi = normalize_doi(current.get("doi"))
    metadata_doi = normalize_doi(metadata.get("doi"))
    if current_doi and metadata_doi and current_doi != metadata_doi:
        return False, 0.0, {"doi": 0.0}, "Resolved DOI differs from the stored DOI."
    score, parts = resolution_score(row, metadata)
    if clean_text(current.get("title")) and parts.get("title", 0.0) < 0.90:
        return False, score, parts, "Registry title does not safely match the stored title."
    if parts.get("year", 0.0) < 0:
        return False, score, parts, "Registry year conflicts with the stored year."
    if require_author_overlap is None:
        require_author_overlap = bool(clean_text(current.get("authors")) and clean_text(metadata.get("authors")))
    if require_author_overlap and parts.get("authors", 0.0) < 0.60:
        return False, score, parts, "Registry authors do not safely match the stored authors."
    if not clean_text(current.get("title")):
        return False, score, parts, "Stored publication has no title to verify against the registry."
    return True, score, parts, ""


def metadata_resolution_is_confident(score: float, parts: dict[str, float]) -> bool:
    """Allow exact title+author matches even when the local row lacks year/venue."""
    strong_complete_match = (
        score >= 0.86
        and parts.get("title", 0.0) >= 0.90
        and parts.get("authors", 0.0) >= 0.60
    )
    exact_sparse_match = (
        parts.get("title", 0.0) >= 0.97
        and parts.get("authors", 0.0) >= 0.80
        and parts.get("year", 0.0) >= 0.0
    )
    return strong_complete_match or exact_sparse_match


def pubmed_query_candidates(title: str, year: str = "", rows: int = 5) -> list[dict[str, Any]]:
    clean_title = clean_text(title)
    if len(token_set(clean_title)) < 3:
        return []
    term = f'"{clean_title}"[Title]'
    if year_int(year):
        year_value = year_int(year)
        term += f" AND {year_value - 1}:{year_value + 1}[DP]"
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?"
        + urllib.parse.urlencode({"db": "pubmed", "retmode": "json", "tool": "hornacademic_cv", "email": "andreas.horn@uk-koeln.de", "retmax": rows, "term": term})
    )
    payload = request_json(url)
    ids = (((payload or {}).get("esearchresult") or {}).get("idlist") or [])
    candidates = []
    for pmid in ids:
        metadata = pubmed_metadata(str(pmid))
        if metadata:
            candidates.append(metadata)
        time.sleep(0.03)
    return candidates


def best_metadata_match(row: sqlite3.Row) -> tuple[dict[str, Any], float, dict[str, float], list[dict[str, Any]]]:
    if normalize_doi(row["doi"]):
        doi = normalize_doi(row["doi"])
        crossref = crossref_metadata(doi)
        pmid = pubmed_pmid(doi)
        pubmed = pubmed_metadata(pmid) if pmid else {}
        if crossref or pubmed:
            metadata = authoritative_metadata(row, crossref, pubmed)
            score, parts = resolution_score(row, metadata)
            return metadata, score, parts, [metadata]
    candidates: list[dict[str, Any]] = []
    title = clean_text(row["title"])
    if title:
        candidates.extend(pubmed_query_candidates(title, row["year"] or row["raw_citation"]))
        candidates.extend(crossref_query_candidates(title, row["year"] or row["raw_citation"]))
    raw_citation = clean_text(row["raw_citation"])
    if raw_citation and raw_citation != title:
        candidates.extend(crossref_query_candidates(raw_citation[:500], row["year"] or raw_citation, bibliographic=True))
    best: dict[str, Any] = {}
    best_score = -math.inf
    best_parts: dict[str, float] = {}
    seen: set[str] = set()
    unique_candidates = []
    for candidate in candidates:
        key = normalize_doi(candidate.get("doi")) or str(candidate.get("pmid") or "") or compact_text(candidate.get("title"))
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(candidate)
        score, parts = resolution_score(row, candidate)
        if score > best_score:
            best = candidate
            best_score = score
            best_parts = parts
    return best, best_score, best_parts, unique_candidates


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
        "openalex_counts_by_year_json": json.dumps(payload.get("counts_by_year") or [], ensure_ascii=False),
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


def merged_metadata(row: sqlite3.Row, registry: dict[str, Any], openalex: dict[str, Any], pmid: str) -> dict[str, Any]:
    merged = dict(row)
    for field in ("title", "venue", "year", "authors", "doi", "url"):
        current = str(merged.get(field) or "").strip()
        candidate = str(registry.get(field) or openalex.get(field) or "").strip()
        if candidate and (not current or field in {"title", "venue", "authors", "doi", "url"}):
            merged[field] = candidate
    if not str(merged.get("pmid") or "").strip() and pmid:
        merged["pmid"] = pmid
    if registry.get("pmid") and not str(merged.get("pmid") or "").strip():
        merged["pmid"] = registry["pmid"]
    if openalex.get("openalex_work_id"):
        merged["openalex_work_id"] = openalex["openalex_work_id"]
    if openalex.get("openalex_cited_by_count") is not None:
        merged["openalex_cited_by_count"] = int(openalex["openalex_cited_by_count"])
    if openalex.get("openalex_counts_by_year_json"):
        merged["openalex_counts_by_year_json"] = openalex["openalex_counts_by_year_json"]
    registry_type = str(registry.get("crossref_type") or "").strip().casefold()
    if registry_type == "journal-article":
        merged["item_type"] = "journalArticle"
        merged["category"] = "peer_reviewed"
    elif registry_type == "posted-content":
        merged["item_type"] = "preprint"
        merged["category"] = "preprints"
    if not str(merged.get("raw_citation") or "").strip():
        merged["raw_citation"] = raw_citation(merged)
    elif registry.get("doi"):
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
        "openalex_counts_by_year_json",
        "item_type",
        "category",
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
            openalex_counts_by_year_json=?,
            item_type=?,
            category=?,
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
            values.get("openalex_counts_by_year_json"),
            values.get("item_type"),
            values.get("category"),
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
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
              source=excluded.source,
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
                row.get("source") or "openalex",
            ),
        )
        count += 1
    return count


def upsert_citation_rows(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        con.execute(
            """
            INSERT INTO citation_institutions (
              publication_id, cited_openalex_work_id, citing_openalex_work_id,
              citing_work_title, citing_work_year, author_id, author_name,
              institution_id, institution_name, ror, country_code, country,
              latitude, longitude, source, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(publication_id, citing_openalex_work_id, author_name, institution_id)
            DO UPDATE SET
              cited_openalex_work_id=excluded.cited_openalex_work_id,
              citing_work_title=excluded.citing_work_title,
              citing_work_year=excluded.citing_work_year,
              author_id=excluded.author_id,
              institution_name=excluded.institution_name,
              ror=excluded.ror,
              country_code=excluded.country_code,
              country=excluded.country,
              latitude=excluded.latitude,
              longitude=excluded.longitude,
              source=excluded.source,
              updated_at=datetime('now')
            """,
            (
                row["publication_id"], row["cited_openalex_work_id"],
                row["citing_openalex_work_id"], row["citing_work_title"],
                row["citing_work_year"], row.get("author_id") or "",
                row["author_name"], row["institution_id"], row["institution_name"],
                row["ror"], row["country_code"], row["country"], row["latitude"],
                row["longitude"], row.get("source") or "openalex",
            ),
        )
        count += 1
    return count


def enrich(
    limit: int | None = None,
    include_suppressed: bool = False,
    dry_run: bool = False,
    refresh: bool = False,
    resolve_missing: bool = False,
) -> dict[str, Any]:
    where = "WHERE (COALESCE(doi, '') != '')"
    if refresh or resolve_missing:
        where = "WHERE (COALESCE(title, '') != '' OR COALESCE(doi, '') != '' OR COALESCE(pmid, '') != '')"
    if not include_suppressed:
        if resolve_missing:
            # Incomplete DOI-less rows are often suppressed before enrichment.
            # They still need one conservative resolution attempt so suppression
            # does not permanently block metadata repair.
            where += " AND (COALESCE(suppress_display, 0) = 0 OR COALESCE(doi, '') = '')"
        else:
            where += " AND COALESCE(suppress_display, 0) = 0"
    if not refresh:
        where += " AND (metadata_enriched_at IS NULL OR COALESCE(doi, '') = '' OR COALESCE(openalex_counts_by_year_json, '') = '')"
    sql_limit = f" LIMIT {int(limit)}" if limit else ""
    report: list[dict[str, Any]] = []
    fetched = 0
    updated = 0
    institutions = 0
    citation_affiliations = 0
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
        total_rows = len(rows)
        for index, row in enumerate(rows, start=1):
            percent = 8 + round(((index - 1) / max(1, total_rows)) * 52)
            write_job_progress(
                f"Enriching publication {index} of {total_rows}",
                percent,
            )
            doi = normalize_doi(row["doi"])
            time.sleep(0.05)
            resolver_score = 0.0
            resolver_parts: dict[str, float] = {}
            candidates_checked = 0
            if doi:
                crossref = crossref_metadata(doi)
                pmid = pubmed_pmid(doi)
                pubmed = pubmed_metadata(pmid) if pmid else {}
                registry = authoritative_metadata(row, crossref, pubmed)
            else:
                candidate, resolver_score, resolver_parts, candidates = best_metadata_match(row)
                candidates_checked = len(candidates)
                resolved_doi = normalize_doi(candidate.get("doi") if candidate else "")
                if (
                    not candidate
                    or not resolved_doi
                    or not metadata_resolution_is_confident(resolver_score, resolver_parts)
                ):
                    note = "Publication resolver: no high-confidence metadata match found."
                    if not dry_run:
                        con.execute(
                            """
                            UPDATE publications
                            SET quality_note=COALESCE(NULLIF(quality_note, ''), ?),
                                metadata_enriched_at=COALESCE(metadata_enriched_at, ?)
                            WHERE id=?
                            """,
                            (note, dt.datetime.now(dt.timezone.utc).isoformat(), row["id"]),
                        )
                        con.commit()
                    report.append(
                        {
                            "id": row["id"],
                            "title": row["title"],
                            "resolved": False,
                            "score": round(resolver_score, 3) if resolver_score != -math.inf else None,
                            "score_parts": resolver_parts,
                            "candidates": candidates_checked,
                            "quality_note": note,
                        }
                    )
                    continue
                doi = resolved_doi
                crossref = crossref_metadata(doi)
                pmid = str(candidate.get("pmid") or pubmed_pmid(doi) or "")
                pubmed = pubmed_metadata(pmid) if pmid else {}
                registry = authoritative_metadata(row, crossref or candidate, pubmed)

            safe, resolver_score, resolver_parts, unsafe_reason = metadata_match_is_safe(row, registry)
            if not registry or not doi or not safe:
                note = f"Publication resolver: {unsafe_reason or 'no authoritative metadata match found.'}"
                if not dry_run:
                    con.execute(
                        """
                        UPDATE publications
                        SET quality_note=COALESCE(NULLIF(quality_note, ''), ?),
                            metadata_enriched_at=COALESCE(metadata_enriched_at, ?)
                        WHERE id=?
                        """,
                        (note, dt.datetime.now(dt.timezone.utc).isoformat(), row["id"]),
                    )
                    con.commit()
                report.append(
                    {
                        "id": row["id"],
                        "doi": doi,
                        "title": row["title"],
                        "resolved": False,
                        "score": round(resolver_score, 3),
                        "score_parts": resolver_parts,
                        "candidates": candidates_checked,
                        "quality_note": note,
                    }
                )
                continue
            openalex = openalex_metadata(doi) if doi else {}
            fetched += 1
            values = merged_metadata(row, registry, openalex, pmid)
            changes = changes_for_row(row, values)
            sources = [name for name, data in [("crossref", crossref), ("openalex", openalex), ("pubmed", pubmed)] if data]
            collaboration_rows = []
            citation_rows = []
            if openalex.get("_payload"):
                collaboration_rows = openalex_collaboration_rows(row["id"], row, openalex["_payload"])
                citation_rows = openalex_citation_rows(row["id"], openalex["_payload"])
                if not dry_run:
                    con.execute(
                        "DELETE FROM collaboration_institutions WHERE publication_id=?",
                        (row["id"],),
                    )
                    institutions += upsert_collaboration_rows(con, collaboration_rows)
                    con.execute(
                        "DELETE FROM citation_institutions WHERE publication_id=?",
                        (row["id"],),
                    )
                    citation_affiliations += upsert_citation_rows(con, citation_rows)
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
                        "score": round(resolver_score, 3),
                        "score_parts": resolver_parts,
                        "candidates": candidates_checked,
                        "changes": changes,
                        "institutions": len(collaboration_rows),
                        "citation_affiliations": len(citation_rows),
                    }
                )
            if not dry_run:
                con.commit()
        if total_rows:
            write_job_progress(
                f"Enriched metadata for {total_rows} publications",
                60,
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
        "citation_affiliations": citation_affiliations,
        "dry_run": dry_run,
        "refresh": refresh,
        "resolve_missing": resolve_missing,
        "report": f"output/{output_ref(REPORT)}",
        **maintenance,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--include-suppressed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--resolve-missing", action="store_true", help="Resolve rows without DOI/PMID using title/year/author metadata search.")
    args = parser.parse_args()
    print(json.dumps(enrich(args.limit, args.include_suppressed, args.dry_run, args.refresh, args.resolve_missing), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
