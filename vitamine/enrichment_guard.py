"""Conservative gates for web-discovered CV facts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from .scripts.enrich_publications_by_doi import (
    authoritative_metadata,
    best_metadata_match,
    crossref_metadata,
    metadata_match_is_safe,
    metadata_resolution_is_confident,
    normalize_doi,
    normalize_orcid,
    pubmed_metadata,
    pubmed_pmid,
)
from .scripts.import_uploaded_cv import llm_json, normalize_publication


PUBLICATION_TITLE_THRESHOLD = 0.90
PUBLICATION_AUTHOR_THRESHOLD = 0.60
PUBLICATION_RESOLUTION_THRESHOLD = 0.86


def ensure_discovery_rejections_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS discovery_rejections (
          id INTEGER PRIMARY KEY,
          fingerprint TEXT NOT NULL UNIQUE,
          target_type TEXT NOT NULL,
          normalized_key TEXT NOT NULL,
          source TEXT NOT NULL,
          reason TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          seen_count INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_discovery_rejections_type_key
        ON discovery_rejections(target_type, normalized_key)
        """
    )


def normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def title_similarity(left: Any, right: Any) -> float:
    a = normalized_text(left)
    b = normalized_text(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def candidate_key(target_type: str, payload: dict[str, Any]) -> str:
    if target_type == "publication":
        doi = normalize_doi(str(payload.get("doi") or ""))
        if doi:
            return f"doi:{doi}"
        return "|".join(
            [
                normalized_text(payload.get("title") or payload.get("raw_citation")),
                normalized_text(payload.get("year")),
            ]
        )
    if target_type == "entry":
        return "|".join(
            [
                normalized_text(payload.get("section_key")),
                normalized_text(payload.get("title") or payload.get("raw_text")),
                normalized_text(payload.get("organization")),
                normalized_text(payload.get("start_date")),
                normalized_text(payload.get("end_date")),
            ]
        )
    if target_type == "person":
        return "|".join(f"{key}:{normalized_text(value)}" for key, value in sorted(payload.items()))
    if target_type == "contribution":
        return "|".join([normalized_text(payload.get("title")), normalized_text(payload.get("narrative"))])
    if target_type == "narrative_report":
        return normalized_text(payload.get("body") or payload.get("body_de"))
    return normalized_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def candidate_fingerprint(target_type: str, payload: dict[str, Any]) -> tuple[str, str]:
    key = candidate_key(target_type, payload)
    digest = hashlib.sha256(f"{target_type}\0{key}".encode("utf-8")).hexdigest()
    return digest, key


def remembered_rejection(con: sqlite3.Connection, target_type: str, payload: dict[str, Any]) -> bool:
    fingerprint, key = candidate_fingerprint(target_type, payload)
    row = con.execute("SELECT id FROM discovery_rejections WHERE fingerprint=?", (fingerprint,)).fetchone()
    if not row and target_type in {"publication", "entry", "contribution", "narrative_report"}:
        for candidate in con.execute(
            "SELECT id, normalized_key FROM discovery_rejections WHERE target_type=?",
            (target_type,),
        ).fetchall():
            stored_key = str(candidate["normalized_key"] or "")
            if target_type == "publication":
                if key.startswith("doi:") or stored_key.startswith("doi:"):
                    continue
                title, _, year = key.partition("|")
                stored_title, _, stored_year = stored_key.partition("|")
                if year == stored_year and title_similarity(title, stored_title) >= PUBLICATION_TITLE_THRESHOLD:
                    row = candidate
                    break
            elif title_similarity(key, stored_key) >= 0.96:
                row = candidate
                break
    if not row:
        return False
    con.execute(
        """
        UPDATE discovery_rejections
        SET last_seen_at=datetime('now'), seen_count=seen_count+1
        WHERE id=?
        """,
        (int(row["id"]),),
    )
    return True


def remember_rejection(
    con: sqlite3.Connection,
    target_type: str,
    payload: dict[str, Any],
    source: str,
    reason: str,
) -> None:
    fingerprint, key = candidate_fingerprint(target_type, payload)
    con.execute(
        """
        INSERT INTO discovery_rejections
          (fingerprint, target_type, normalized_key, source, reason, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
          source=excluded.source,
          reason=excluded.reason,
          payload_json=excluded.payload_json,
          last_seen_at=datetime('now'),
          seen_count=discovery_rejections.seen_count+1
        """,
        (fingerprint, target_type, key, source, reason[:1000], json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )


def forget_rejection(con: sqlite3.Connection, target_type: str, payload: dict[str, Any]) -> None:
    fingerprint, _ = candidate_fingerprint(target_type, payload)
    con.execute("DELETE FROM discovery_rejections WHERE fingerprint=?", (fingerprint,))


def author_tokens(value: Any) -> set[str]:
    stop = {"and", "et", "al"}
    return {token for token in normalized_text(value).split() if len(token) > 1 and token not in stop}


def author_similarity(left: Any, right: Any) -> float:
    a = author_tokens(left)
    b = author_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def researcher_names(con: sqlite3.Connection) -> list[str]:
    row = con.execute("SELECT full_name, display_name FROM person WHERE id=1").fetchone()
    if not row:
        return []
    return [str(row[key] or "").strip() for key in ("full_name", "display_name") if str(row[key] or "").strip()]


def researcher_is_author(names: list[str], authors: Any) -> bool:
    normalized_authors = normalized_text(authors)
    tokens = set(normalized_authors.split())
    for name in names:
        parts = normalized_text(name).split()
        if not parts:
            continue
        surname = parts[-1]
        given = parts[0]
        if surname in tokens and (given in tokens or any(token.startswith(given[:1]) for token in tokens if token != surname)):
            return True
    return False


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        return set()
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def researcher_orcids(con: sqlite3.Connection) -> set[str]:
    values: set[str] = set()
    if "orcid_id" in _table_columns(con, "person"):
        row = con.execute("SELECT orcid_id FROM person WHERE id=1").fetchone()
        if row and normalize_orcid(row["orcid_id"]):
            values.add(normalize_orcid(row["orcid_id"]))
    if _table_columns(con, "person_identifiers"):
        for row in con.execute(
            """
            SELECT identifier_value, url
            FROM person_identifiers
            WHERE person_id=1 AND lower(platform)='orcid'
            """
        ).fetchall():
            value = normalize_orcid(row["identifier_value"] or row["url"])
            if value:
                values.add(value)
    return values


def known_institutions(con: sqlite3.Connection) -> list[str]:
    values: list[str] = []
    person_columns = _table_columns(con, "person")
    if "own_institution_name" in person_columns:
        row = con.execute("SELECT own_institution_name FROM person WHERE id=1").fetchone()
        if row and str(row["own_institution_name"] or "").strip():
            values.append(str(row["own_institution_name"]).strip())
    entry_columns = _table_columns(con, "cv_entries")
    if "organization" in entry_columns:
        values.extend(
            str(row["organization"]).strip()
            for row in con.execute(
                "SELECT DISTINCT organization FROM cv_entries WHERE COALESCE(organization, '') != ''"
            ).fetchall()
            if str(row["organization"] or "").strip()
        )
    return values


def institution_similarity(left: Any, right: Any) -> bool:
    generic = {
        "and", "center", "centre", "department", "faculty", "hospital", "institute",
        "medical", "medicine", "of", "school", "the", "university",
    }
    a = {token for token in normalized_text(left).split() if token not in generic}
    b = {token for token in normalized_text(right).split() if token not in generic}
    if not a or not b:
        return False
    overlap = a & b
    if len(overlap) >= 2:
        return True
    return any(len(token) >= 7 for token in overlap)


def author_record_match_strength(names: list[str], record: dict[str, Any]) -> str:
    """Return ``full``, ``initial``, or an empty string for a researcher match."""
    raw_record_name = str(record.get("name") or "").strip()
    record_tokens = re.findall(r"[A-Za-zÀ-ž]+", raw_record_name)
    if not record_tokens:
        return ""
    normalized_record_tokens = [normalized_text(token) for token in record_tokens]
    for researcher_name in names:
        researcher_parts = normalized_text(researcher_name).split()
        if len(researcher_parts) < 2:
            continue
        researcher_given = researcher_parts[0]
        researcher_surname = researcher_parts[-1]
        if normalized_record_tokens[-1] == researcher_surname:
            raw_given = record_tokens[:-1]
            normalized_given = normalized_record_tokens[:-1]
        elif normalized_record_tokens[0] == researcher_surname:
            raw_given = record_tokens[1:]
            normalized_given = normalized_record_tokens[1:]
        else:
            continue
        if not normalized_given:
            continue
        full_given = [
            normalized
            for raw, normalized in zip(raw_given, normalized_given)
            if len(raw) > 1 and not (raw.isupper() and len(raw) <= 4)
        ]
        if full_given:
            if full_given[0] == researcher_given:
                return "full"
            # A registry-supplied full given name that conflicts with the CV name
            # is stronger evidence than a shared initial and surname.
            continue
        if normalized_given[0].startswith(researcher_given[0]):
            return "initial"
    return ""


def author_record_matches(names: list[str], record: dict[str, Any]) -> bool:
    return bool(author_record_match_strength(names, record))


def coauthor_surnames(value: Any) -> set[str]:
    names = re.split(r"\s*(?:,|;|\band\b)\s*", str(value or ""), flags=re.I)
    surnames = set()
    for name in names:
        parts = normalized_text(name).split()
        if parts:
            surnames.add(parts[-1])
    return surnames


def known_coauthor_surnames(con: sqlite3.Connection, researcher: list[str]) -> set[str]:
    if "authors" not in _table_columns(con, "publications"):
        return set()
    own_surnames = {
        parts[-1]
        for name in researcher
        if (parts := normalized_text(name).split())
    }
    known: set[str] = set()
    for row in con.execute(
        "SELECT authors FROM publications WHERE COALESCE(authors, '') != ''"
    ).fetchall():
        known.update(coauthor_surnames(row["authors"]))
    return {surname for surname in known if surname not in own_surnames and len(surname) > 2}


def researcher_identity_evidence(
    con: sqlite3.Connection,
    names: list[str],
    metadata: dict[str, Any],
) -> tuple[bool, str]:
    records = [
        record
        for record in metadata.get("_author_records") or []
        if isinstance(record, dict)
    ]
    # Compatibility for older/imported metadata without structured author records.
    # Real registry results always carry records and therefore use the stronger path.
    if not records:
        if researcher_is_author(names, metadata.get("authors")):
            return True, "name-only legacy metadata"
        return False, "Researcher was not present in authoritative author metadata."

    matching = [record for record in records if author_record_matches(names, record)]
    if not matching:
        return False, "Researcher was not present in structured registry author metadata."

    pubmed_records = [
        record
        for record in metadata.get("_pubmed_author_records") or []
        if isinstance(record, dict)
    ]
    if pubmed_records and not any(author_record_matches(names, record) for record in pubmed_records):
        return False, "PubMed did not confirm the researcher in its author list."

    saved_orcids = researcher_orcids(con)
    if saved_orcids and any(normalize_orcid(record.get("orcid")) in saved_orcids for record in matching):
        return True, "matching ORCID"

    institutions = known_institutions(con)
    matching_affiliations = [
        affiliation
        for record in matching
        for affiliation in record.get("affiliations") or []
        if str(affiliation or "").strip()
    ]
    for record in matching:
        for affiliation in record.get("affiliations") or []:
            if any(institution_similarity(affiliation, institution) for institution in institutions):
                return True, "matching affiliation"

    # A stated but non-matching affiliation is identity evidence, not missing data.
    # Never let a shared coauthor network overrule it.
    if matching_affiliations:
        return False, (
            "The abbreviated researcher name had an affiliation, but it did not match "
            "the researcher's known institutions."
        )

    known_coauthors = known_coauthor_surnames(con, names)
    candidate_coauthors = {
        surname
        for record in records
        if not author_record_matches(names, record)
        for surname in coauthor_surnames(record.get("name"))
    }
    # Coauthors can corroborate an explicit given name, but cannot disambiguate
    # initials such as "Horn A": homonyms in one specialty often share colleagues.
    has_full_given_name = any(
        author_record_match_strength(names, record) == "full" for record in matching
    )
    if has_full_given_name and len(known_coauthors & candidate_coauthors) >= 2:
        return True, "established coauthor network"

    return False, (
        "The name matched, but ORCID, affiliation, and established coauthors did not "
        "disambiguate this publication from a homonym."
    )


def identity_is_ambiguous(names: list[str], metadata: dict[str, Any]) -> bool:
    """Whether registry evidence contains a plausible initials-only researcher."""
    records = [
        record
        for record in metadata.get("_author_records") or []
        if isinstance(record, dict)
    ]
    return any(author_record_match_strength(names, record) == "initial" for record in records)


def resolved_publication(
    payload: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    normalized = normalize_publication(payload)
    if not normalized:
        return None, "Publication did not contain usable metadata."
    doi = normalize_doi(normalized.get("doi"))
    metadata: dict[str, Any] = {}
    score = 0.0
    parts: dict[str, float] = {}
    if doi:
        crossref = crossref_metadata(doi)
        pmid = pubmed_pmid(doi)
        pubmed = pubmed_metadata(pmid) if pmid else {}
        metadata = authoritative_metadata(normalized, crossref, pubmed)
        if not metadata:
            return None, "DOI could not be verified against Crossref or PubMed."
        safe, score, parts, reason = metadata_match_is_safe(
            normalized,
            metadata,
            require_author_overlap=bool(normalized.get("authors")),
        )
        if not safe:
            return None, reason
    else:
        metadata, _, _, _ = best_metadata_match(normalized)  # type: ignore[arg-type]
        doi = normalize_doi(metadata.get("doi") if metadata else "")
        if metadata:
            score, parts = _metadata_score(normalized, metadata)
    if not metadata or not doi:
        return None, "No authoritative DOI could be resolved."
    if (
        parts.get("title", 0.0) < PUBLICATION_TITLE_THRESHOLD
        or (
            normalized.get("authors")
            and parts.get("authors", 0.0) < PUBLICATION_AUTHOR_THRESHOLD
        )
        or (
            not normalize_doi(normalized.get("doi"))
            and not metadata_resolution_is_confident(score, parts)
        )
    ):
        return None, (
            "DOI resolution was not sufficiently certain "
            f"(score={score:.3f}, title={parts.get('title', 0.0):.3f}, authors={parts.get('authors', 0.0):.3f})."
        )
    resolved = dict(normalized)
    for field in ("title", "authors", "venue", "year", "url"):
        if metadata.get(field):
            resolved[field] = metadata[field]
    resolved["doi"] = doi
    resolved["pmid"] = str(metadata.get("pmid") or pubmed_pmid(doi) or resolved.get("pmid") or "")
    resolved["raw_citation"] = ". ".join(
        str(resolved.get(field) or "").strip() for field in ("authors", "title", "venue", "year") if str(resolved.get(field) or "").strip()
    )
    resolved["raw_citation"] += f". doi:{doi}"
    resolved["confidence"] = "high"
    for key, value in metadata.items():
        if key.startswith("_"):
            resolved[key] = value
    # Keep the originating profile item so a rejection can later be reconciled
    # with that profile. These values are never used as identity evidence.
    for key in ("orcid_put_code", "orcid_source", "orcid_last_modified", "orcid_path"):
        if payload.get(key) not in (None, ""):
            resolved[key] = payload[key]
    return resolved, ""


def _metadata_score(payload: dict[str, Any], metadata: dict[str, Any]) -> tuple[float, dict[str, float]]:
    title = title_similarity(payload.get("title"), metadata.get("title"))
    authors = author_similarity(payload.get("authors"), metadata.get("authors"))
    year_left = re.search(r"\b(?:19|20)\d{2}\b", str(payload.get("year") or payload.get("raw_citation") or ""))
    year_right = re.search(r"\b(?:19|20)\d{2}\b", str(metadata.get("year") or ""))
    year = 1.0 if year_left and year_right and year_left.group(0) == year_right.group(0) else 0.0
    score = 0.68 * title + 0.24 * authors + 0.08 * year
    return score, {"title": title, "authors": authors, "year": year}


def publication_year_distance(left: Any, right: Any) -> int | None:
    a = re.search(r"\b(?:19|20)\d{2}\b", str(left or ""))
    b = re.search(r"\b(?:19|20)\d{2}\b", str(right or ""))
    if not a or not b:
        return None
    return abs(int(a.group(0)) - int(b.group(0)))


def matching_curated_publication(
    rows: list[dict[str, Any]],
    publication: dict[str, Any],
) -> tuple[dict[str, Any] | None, float]:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for row in rows:
        similarity = title_similarity(publication.get("title"), row.get("title"))
        if similarity < 0.97:
            continue
        year_distance = publication_year_distance(publication.get("year"), row.get("year"))
        if year_distance is not None and year_distance > 2:
            continue
        if similarity > best_score or (
            similarity == best_score
            and best is not None
            and not normalize_doi(row.get("doi"))
            and normalize_doi(best.get("doi"))
        ):
            best = row
            best_score = similarity
    return best, best_score


def backfill_curated_publication(
    con: sqlite3.Connection,
    row_id: int,
    publication: dict[str, Any],
    source: str,
) -> None:
    columns = _table_columns(con, "publications")
    values: dict[str, Any] = {}
    for field in ("authors", "title", "venue", "year", "doi", "pmid", "url", "raw_citation"):
        value = publication.get(field)
        if field in columns and str(value or "").strip():
            values[field] = value
    if "metadata_source" in columns:
        values["metadata_source"] = f"{source}+registry"
    if "metadata_enriched_at" in columns:
        values["metadata_enriched_at"] = "CURRENT_TIMESTAMP"
    assignments = []
    parameters: list[Any] = []
    for field, value in values.items():
        if value == "CURRENT_TIMESTAMP":
            assignments.append(f"{field}=CURRENT_TIMESTAMP")
        else:
            assignments.append(f"{field}=?")
            parameters.append(value)
    if "quality_note" in columns:
        assignments.append(
            "quality_note=CASE WHEN COALESCE(quality_note, '') LIKE 'Suppressed incomplete%' "
            "THEN NULL ELSE quality_note END"
        )
    if not assignments:
        return
    con.execute(
        f"UPDATE publications SET {', '.join(assignments)} WHERE id=?",
        (*parameters, row_id),
    )


def guard_publications(
    con: sqlite3.Connection,
    publications: list[dict[str, Any]],
    source: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    names = researcher_names(con)
    accepted: list[dict[str, Any]] = []
    stats = {
        "received": len(publications),
        "remembered": 0,
        "unresolved": 0,
        "not_author": 0,
        "identity_review": 0,
        "duplicates": 0,
        "backfilled": 0,
        "approved": 0,
    }
    existing_dois = {
        normalize_doi(row["doi"])
        for row in con.execute("SELECT doi FROM publications WHERE COALESCE(doi, '') != ''").fetchall()
        if normalize_doi(row["doi"])
    }
    publication_columns = _table_columns(con, "publications")
    year_expression = "year" if "year" in publication_columns else "'' AS year"
    existing_rows = [
        dict(row)
        for row in con.execute(
            f"SELECT id, title, {year_expression}, doi "
            "FROM publications WHERE COALESCE(title, '')!=''"
        ).fetchall()
    ]
    for candidate in publications:
        if remembered_rejection(con, "publication", candidate):
            stats["remembered"] += 1
            continue
        candidate_doi = normalize_doi(candidate.get("doi"))
        if candidate_doi and candidate_doi in existing_dois:
            remember_rejection(
                con,
                "publication",
                candidate,
                source,
                f"DOI already exists in publications: {candidate_doi}",
            )
            stats["duplicates"] += 1
            continue
        resolved, reason = resolved_publication(candidate)
        if not resolved:
            remember_rejection(con, "publication", candidate, source, reason)
            stats["unresolved"] += 1
            continue
        identity_ok, identity_reason = researcher_identity_evidence(con, names, resolved)
        if not identity_ok:
            if identity_is_ambiguous(names, resolved):
                resolved["confidence"] = "low"
                resolved["_identity_review_required"] = True
                resolved["_identity_review_reason"] = identity_reason
                accepted.append(resolved)
                existing_dois.add(normalize_doi(resolved.get("doi")))
                stats["identity_review"] += 1
                continue
            remember_rejection(con, "publication", candidate, source, identity_reason)
            stats["not_author"] += 1
            continue
        doi = normalize_doi(resolved.get("doi"))
        duplicate_reason = ""
        if doi in existing_dois:
            duplicate_reason = f"DOI already exists in publications: {doi}"
        else:
            matched, similarity = matching_curated_publication(existing_rows, resolved)
            if matched and not normalize_doi(matched.get("doi")):
                backfill_curated_publication(con, int(matched["id"]), resolved, source)
                matched.update(
                    {
                        "title": resolved.get("title"),
                        "year": resolved.get("year"),
                        "doi": doi,
                    }
                )
                existing_dois.add(doi)
                stats["backfilled"] += 1
                continue
            if matched:
                duplicate_reason = (
                    f"Matches curated publication {matched['id']} by title "
                    f"({similarity:.3f}); no second Inbox record was created."
                )
        if duplicate_reason:
            remember_rejection(con, "publication", candidate, source, duplicate_reason)
            stats["duplicates"] += 1
            continue
        accepted.append(resolved)
        existing_dois.add(doi)
        stats["approved"] += 1
    return accepted, stats


def review_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "understood": {"type": "boolean"},
                        "complete": {"type": "boolean"},
                        "certainly_novel": {"type": "boolean"},
                        "approve": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["candidate_id", "understood", "complete", "certainly_novel", "approve", "reason"],
                },
            }
        },
        "required": ["decisions"],
    }


def database_context(con: sqlite3.Connection) -> dict[str, Any]:
    person = dict(con.execute("SELECT * FROM person WHERE id=1").fetchone() or {})
    entries = [
        dict(row)
        for row in con.execute(
            """
            SELECT section_key, start_date, end_date, title, organization, role, amount, description, raw_text
            FROM cv_entries ORDER BY section_key, start_date, id
            """
        ).fetchall()
    ]
    contributions = [
        dict(row)
        for row in con.execute("SELECT title, narrative FROM biosketch_contributions ORDER BY ordinal, id").fetchall()
    ]
    narrative = dict(con.execute("SELECT title, body, title_de, body_de FROM narrative_reports WHERE id=1").fetchone() or {})
    return {"person": person, "entries": entries, "contributions": contributions, "narrative_report": narrative}


def review_nonpublications(
    con: sqlite3.Connection,
    entries: list[dict[str, Any]],
    contributions: list[dict[str, Any]],
    person: dict[str, Any],
    report: dict[str, Any] | None,
    source: str,
    settings: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    originals: dict[str, tuple[str, dict[str, Any]]] = {}
    for index, payload in enumerate(entries):
        candidate_id = f"entry:{index}"
        originals[candidate_id] = ("entry", payload)
    for index, payload in enumerate(contributions):
        candidate_id = f"contribution:{index}"
        originals[candidate_id] = ("contribution", payload)
    for field, value in person.items():
        if str(value or "").strip():
            candidate_id = f"person:{field}"
            originals[candidate_id] = ("person", {field: value})
    if isinstance(report, dict) and (str(report.get("body") or "").strip() or str(report.get("body_de") or "").strip()):
        originals["narrative_report:0"] = ("narrative_report", report)
    remembered = 0
    for candidate_id, (target_type, payload) in originals.items():
        if remembered_rejection(con, target_type, payload):
            remembered += 1
            continue
        candidates.append({"candidate_id": candidate_id, "target_type": target_type, "payload": payload})
    empty = {"entries": [], "contributions": [], "person": {}, "narrative_report": None}
    if not candidates:
        return empty, {"received": len(originals), "remembered": remembered, "approved": 0, "rejected": 0}
    prompt = f"""Act as an extremely conservative academic-CV curator.

For every candidate, decide whether it may enter a human review inbox.
Approve only when all of these are true:
1. You clearly understand the fact and its CV category.
2. It is complete enough to be useful. Honors/prizes require a date and award name. Jobs and appointments require a title, organization, and start date; an end date or an explicit indication that the role is current is also required. Education requires degree/program, institution, and date. Other facts require the analogous essential dates and context.
3. After semantic comparison with the full current database, you are absolutely certain the same fact is not already present in another wording or field.

Reject incomplete, ambiguous, biographical prose, summaries, field-value variants, and anything possibly duplicative. Do not use string equality alone. `approve` must equal understood AND complete AND certainly_novel.

CURRENT DATABASE:
{json.dumps(database_context(con), ensure_ascii=False)}

CANDIDATES:
{json.dumps(candidates, ensure_ascii=False)}
"""
    result, warning = llm_json(prompt, review_schema(), settings)
    if warning or not isinstance(result, dict):
        return empty, {
            "received": len(originals),
            "remembered": remembered,
            "approved": 0,
            "rejected": 0,
            "deferred": len(candidates),
            "warning": warning or "LLM returned no structured review.",
        }
    decisions = {
        str(row.get("candidate_id") or ""): row
        for row in result.get("decisions") or []
        if isinstance(row, dict)
    }
    approved = dict(empty)
    rejected = 0
    deferred = 0
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        target_type, payload = originals[candidate_id]
        decision = decisions.get(candidate_id)
        if not decision:
            deferred += 1
            continue
        allow = all(bool(decision.get(key)) for key in ("understood", "complete", "certainly_novel", "approve"))
        if not allow:
            remember_rejection(con, target_type, payload, source, str(decision.get("reason") or "Rejected by LLM review."))
            rejected += 1
            continue
        if target_type == "entry":
            approved["entries"].append(payload)
        elif target_type == "contribution":
            approved["contributions"].append(payload)
        elif target_type == "person":
            approved["person"].update(payload)
        elif target_type == "narrative_report":
            approved["narrative_report"] = payload
    approved_count = len(approved["entries"]) + len(approved["contributions"]) + len(approved["person"]) + int(bool(approved["narrative_report"]))
    return approved, {
        "received": len(originals),
        "remembered": remembered,
        "approved": approved_count,
        "rejected": rejected,
        "deferred": deferred,
    }
