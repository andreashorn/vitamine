"""Build deliberately limited public-profile snapshots from VitaMine databases."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote


PROFILE_BLOCK_KEYS = ("bio", "metrics", "publications", "collaborators")
PUBLIC_PROFILE_SCHEMA_VERSION = 3
PROFILE_BLOCK_LABELS = {
    "bio": "About",
    "metrics": "Citations",
    "publications": "Publications",
    "collaborators": "Collaborators",
}
DEFAULT_PROFILE_BLOCKS = [
    {"key": key, "visible": True}
    for key in PROFILE_BLOCK_KEYS
]


def normalize_profile_blocks(raw: Any) -> list[dict[str, Any]]:
    requested: dict[str, bool] = {}
    order: list[str] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip().lower()
        if key not in PROFILE_BLOCK_KEYS or key in requested:
            continue
        requested[key] = bool(item.get("visible", True))
        order.append(key)
    for key in PROFILE_BLOCK_KEYS:
        if key not in requested:
            requested[key] = True
            order.append(key)
    return [{"key": key, "visible": requested[key]} for key in order]


def _tables(con: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in con.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _text(value: Any, limit: int = 5000) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or "")).strip()
    return text[:limit]


def _profile_title(display_name: str, degrees: str) -> str:
    clean_name = _text(display_name, 200) or "Academic profile"
    clean_degrees = _text(degrees, 200)
    if not clean_degrees or clean_degrees.casefold() in clean_name.casefold():
        return clean_name
    return _text(f"{clean_name}, {clean_degrees}", 200)


def _safe_public_url(value: Any) -> str:
    url = _text(value, 1000)
    return url if re.match(r"^https?://", url, flags=re.I) else ""


def _doi_url(value: Any) -> str:
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", _text(value, 300), flags=re.I)
    return f"https://doi.org/{quote(doi, safe='/().;:-_')}" if doi else ""


def _h_index(citations: list[int]) -> int:
    score = 0
    for index, count in enumerate(sorted(citations, reverse=True), start=1):
        if count < index:
            break
        score = index
    return score


def _researcher_name_terms(person: sqlite3.Row | None) -> list[str]:
    if person is None:
        return []
    names = [_text(person["display_name"], 200), _text(person["full_name"], 200)]
    terms: set[str] = set()
    for name in names:
        parts = [part for part in re.split(r"\s+", name.casefold()) if part]
        if not parts:
            continue
        terms.add(" ".join(parts))
        first, last = parts[0], parts[-1]
        if first and last and first != last:
            terms.update({f"{first} {last}", f"{last} {first}", f"{first[0]} {last}", f"{last} {first[0]}"})
        if len(last) > 3:
            terms.add(last)
    return sorted(terms, key=len, reverse=True)


def _first_or_last_author(authors: Any, terms: list[str]) -> bool:
    parts = [part.strip() for part in _text(authors, 10_000).split(",") if part.strip()]
    if not parts:
        return False

    def matches(author: str) -> bool:
        text = f" {re.sub(r'[^a-z0-9]+', ' ', author.casefold()).strip()} "
        return any(
            f" {re.sub(r'[^a-z0-9]+', ' ', term).strip()} " in text
            for term in terms
        )

    return matches(parts[0]) or matches(parts[-1])


def _profile_publications(
    con: sqlite3.Connection,
    publication_columns: set[str],
) -> tuple[list[dict[str, Any]], list[sqlite3.Row]]:
    def column(name: str, fallback: str = "NULL") -> str:
        return name if name in publication_columns else fallback

    suppressed = column("suppress_display", "0")
    rows = con.execute(
        f"""
        SELECT id,
               {column('category', "''")} AS category,
               {column('authors', "''")} AS authors,
               {column('title', "''")} AS title,
               {column('venue', "''")} AS venue,
               {column('year', "''")} AS year,
               {column('doi', "''")} AS doi,
               {column('url', "''")} AS url,
               {column('impact_factor')} AS impact_factor,
               {column('openalex_cited_by_count')} AS openalex_cited_by_count,
               {column('openalex_counts_by_year_json', "'[]'")} AS openalex_counts_by_year_json
        FROM publications
        WHERE COALESCE({suppressed}, 0)=0
        ORDER BY
          CASE WHEN {column('year', "''")} GLOB '[0-9][0-9][0-9][0-9]' THEN CAST({column('year', "''")} AS INTEGER) ELSE 0 END DESC,
          id DESC
        LIMIT 500
        """
    ).fetchall()
    publications = []
    for row in rows:
        doi_url = _doi_url(row["doi"])
        publications.append(
            {
                "id": int(row["id"]),
                "category": _text(row["category"], 80),
                "authors": _text(row["authors"], 3000),
                "title": _text(row["title"], 1000) or "Untitled publication",
                "venue": _text(row["venue"], 500),
                "year": _text(row["year"], 20),
                "doi": _text(row["doi"], 300),
                "url": doi_url or _safe_public_url(row["url"]),
                "citations": int(row["openalex_cited_by_count"] or 0),
            }
        )
    return publications, rows


def _profile_metrics(
    publication_rows: list[sqlite3.Row],
    person: sqlite3.Row | None,
) -> dict[str, Any]:
    citations = [int(row["openalex_cited_by_count"] or 0) for row in publication_rows]
    citation_coverage = sum(row["openalex_cited_by_count"] is not None for row in publication_rows)
    peer_reviewed = sum(_text(row["category"], 80) == "peer_reviewed" for row in publication_rows)
    name_terms = _researcher_name_terms(person)
    since_year = time.localtime().tm_year - 5
    recent_by_work: list[int] = []
    citations_by_year: dict[str, int] = {}
    first_last_by_year: dict[str, int] = {}
    publications_by_year: dict[str, int] = {}
    impact_sum_by_year: dict[str, float] = {}
    impact_count_by_year: dict[str, int] = {}
    for row in publication_rows:
        publication_year = _text(row["year"], 4)
        if publication_year.isdigit():
            publications_by_year[publication_year] = publications_by_year.get(publication_year, 0) + 1
            if row["impact_factor"] is not None:
                impact_sum_by_year[publication_year] = (
                    impact_sum_by_year.get(publication_year, 0.0)
                    + float(row["impact_factor"])
                )
                impact_count_by_year[publication_year] = impact_count_by_year.get(publication_year, 0) + 1
        first_or_last = _first_or_last_author(row["authors"], name_terms)
        recent_total = 0
        try:
            yearly = json.loads(row["openalex_counts_by_year_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            yearly = []
        for item in yearly if isinstance(yearly, list) else []:
            if not isinstance(item, dict):
                continue
            year = _text(item.get("year"), 4)
            if not year.isdigit():
                continue
            count = max(0, int(item.get("cited_by_count") or 0))
            citations_by_year[year] = citations_by_year.get(year, 0) + count
            if first_or_last:
                first_last_by_year[year] = first_last_by_year.get(year, 0) + count
            if int(year) >= since_year:
                recent_total += count
        if recent_total:
            recent_by_work.append(recent_total)
    by_year = [
        {
            "year": year,
            "citations": count,
            "first_last_author_citations": first_last_by_year.get(year, 0),
            "publications_published": publications_by_year.get(year, 0),
            "impact_factor_sum": round(impact_sum_by_year.get(year, 0.0), 2),
            "impact_factor_count": impact_count_by_year.get(year, 0),
        }
        for year, count in sorted(citations_by_year.items(), key=lambda item: int(item[0]))
    ]
    return {
        "total_publications": len(publication_rows),
        "peer_reviewed_publications": peer_reviewed,
        "total_citations": sum(citations),
        "citation_metric_count": citation_coverage,
        "since_year": since_year,
        "all": {
            "citations": sum(citations),
            "h_index": _h_index(citations),
            "i10_index": sum(count >= 10 for count in citations),
        },
        "recent": {
            "citations": sum(recent_by_work),
            "h_index": _h_index(recent_by_work),
            "i10_index": sum(count >= 10 for count in recent_by_work),
        },
        "by_year": by_year,
    }


def _normalized_institution(value: Any) -> str:
    return " ".join(_text(value, 500).casefold().split())


def _profile_collaborators(
    con: sqlite3.Connection,
    person: sqlite3.Row | None,
    tables: set[str],
    publication_columns: set[str],
) -> dict[str, Any]:
    if person is None:
        return {"own": None, "nodes": [], "edges": [], "institution_count": 0, "publication_links": 0}
    own_name = _text(person["own_institution_name"], 500)
    try:
        own_latitude = float(person["own_institution_latitude"])
        own_longitude = float(person["own_institution_longitude"])
    except (TypeError, ValueError):
        own_latitude = own_longitude = None
    if (
        "collaboration_institutions" not in tables
        or not own_name
        or own_latitude is None
        or own_longitude is None
    ):
        return {"own": None, "nodes": [], "edges": [], "institution_count": 0, "publication_links": 0}
    suppressed_clause = (
        "AND COALESCE(p.suppress_display, 0)=0"
        if "suppress_display" in publication_columns
        else ""
    )
    rows = con.execute(
        f"""
        SELECT ci.institution_id, ci.institution_name, ci.ror, ci.country_code, ci.country,
               ci.latitude, ci.longitude, ci.publication_id, ci.author_name, ci.publication_year
        FROM collaboration_institutions ci
        JOIN publications p ON p.id=ci.publication_id
        WHERE ci.latitude IS NOT NULL AND ci.longitude IS NOT NULL
          {suppressed_clause}
        ORDER BY ci.institution_name, ci.publication_id
        """
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    own_normalized = _normalized_institution(own_name)
    for row in rows:
        institution_name = _text(row["institution_name"], 500)
        institution_normalized = _normalized_institution(institution_name)
        if own_normalized and institution_normalized and (
            own_normalized in institution_normalized
            or institution_normalized in own_normalized
        ):
            continue
        key = _text(row["institution_id"], 300) or institution_normalized
        item = grouped.setdefault(
            key,
            {
                "id": key,
                "name": institution_name,
                "ror": _safe_public_url(row["ror"]),
                "country": _text(row["country"], 200),
                "country_code": _text(row["country_code"], 10),
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "publication_ids": set(),
                "authors": set(),
                "years": set(),
            },
        )
        item["publication_ids"].add(int(row["publication_id"]))
        author = _text(row["author_name"], 300)
        if author:
            item["authors"].add(author)
        year = _text(row["publication_year"], 20)
        if year:
            item["years"].add(year)
    ranked = sorted(
        grouped.values(),
        key=lambda item: (-len(item["publication_ids"]), item["name"].casefold()),
    )[:160]
    own = {
        "id": "own-institution",
        "name": own_name,
        "country": _text(person["own_institution_country"], 200),
        "country_code": _text(person["own_institution_country_code"], 10),
        "latitude": own_latitude,
        "longitude": own_longitude,
        "own": True,
    }
    nodes = [own]
    edges = []
    publication_links = 0
    for item in ranked:
        count = len(item.pop("publication_ids"))
        item["authors"] = sorted(item["authors"])[:20]
        item["years"] = sorted(item["years"], reverse=True)
        item["publication_count"] = count
        item["author_count"] = len(item["authors"])
        item["own"] = False
        nodes.append(item)
        edges.append({"source": own["id"], "target": item["id"], "weight": count})
        publication_links += count
    return {
        "own": own,
        "nodes": nodes,
        "edges": edges,
        "institution_count": len(ranked),
        "publication_links": publication_links,
    }


def _profile_citations(
    con: sqlite3.Connection,
    person: sqlite3.Row | None,
    tables: set[str],
    publication_columns: set[str],
) -> dict[str, Any]:
    empty = {
        "own": None, "nodes": [], "edges": [], "institution_count": 0,
        "researcher_count": 0, "citation_links": 0, "sampled_works": 0,
    }
    if person is None or "citation_institutions" not in tables:
        return empty
    own_name = _text(person["own_institution_name"], 500)
    try:
        own_latitude = float(person["own_institution_latitude"])
        own_longitude = float(person["own_institution_longitude"])
    except (TypeError, ValueError):
        return empty
    if not own_name:
        return empty
    suppressed_clause = (
        "AND COALESCE(p.suppress_display, 0)=0"
        if "suppress_display" in publication_columns else ""
    )
    rows = con.execute(
        f"""
        SELECT ci.publication_id, ci.citing_openalex_work_id, ci.citing_work_year,
               ci.author_id, ci.author_name, ci.institution_id, ci.institution_name,
               ci.ror, ci.country_code, ci.country, ci.latitude, ci.longitude
        FROM citation_institutions ci
        JOIN publications p ON p.id=ci.publication_id
        WHERE ci.latitude IS NOT NULL AND ci.longitude IS NOT NULL {suppressed_clause}
        ORDER BY ci.institution_name, ci.author_name
        """
    ).fetchall()
    name_terms = _researcher_name_terms(person)
    self_citing_works = {
        _text(row["citing_openalex_work_id"], 300)
        for row in rows
        if _first_or_last_author(row["author_name"], name_terms)
    }
    grouped: dict[str, dict[str, Any]] = {}
    all_researchers: set[str] = set()
    sampled_works: set[str] = set()
    for row in rows:
        citing_work_id = _text(row["citing_openalex_work_id"], 300)
        if citing_work_id in self_citing_works:
            continue
        institution_id = _text(row["institution_id"], 300)
        institution_name = _text(row["institution_name"], 500)
        key = institution_id or _normalized_institution(institution_name)
        author_name = _text(row["author_name"], 300)
        author_key = _text(row["author_id"], 300) or author_name.casefold()
        event = (int(row["publication_id"]), citing_work_id)
        item = grouped.setdefault(key, {
            "id": f"citation:{key}", "name": institution_name,
            "ror": _safe_public_url(row["ror"]), "country": _text(row["country"], 200),
            "country_code": _text(row["country_code"], 10),
            "latitude": float(row["latitude"]), "longitude": float(row["longitude"]),
            "events": set(), "researchers": {}, "years": set(),
        })
        item["events"].add(event)
        researcher = item["researchers"].setdefault(
            author_key, {"name": author_name, "events": set()}
        )
        researcher["events"].add(event)
        year = _text(row["citing_work_year"], 20)
        if year:
            item["years"].add(year)
        all_researchers.add(author_key)
        sampled_works.add(citing_work_id)
    ranked = sorted(
        grouped.values(), key=lambda item: (-len(item["events"]), item["name"].casefold())
    )[:160]
    own = {
        "id": "own-institution", "name": own_name,
        "country": _text(person["own_institution_country"], 200),
        "country_code": _text(person["own_institution_country_code"], 10),
        "latitude": own_latitude, "longitude": own_longitude, "own": True,
    }
    nodes = [own]
    edges = []
    citation_links = 0
    for item in ranked:
        count = len(item.pop("events"))
        researchers = sorted(
            ({"name": value["name"], "citation_count": len(value["events"])}
             for value in item.pop("researchers").values()),
            key=lambda value: (-value["citation_count"], value["name"].casefold()),
        )
        item["researchers"] = researchers[:20]
        item["researcher_count"] = len(researchers)
        item["years"] = sorted(item["years"], reverse=True)
        item["citation_count"] = count
        item["own"] = False
        nodes.append(item)
        edges.append({"source": own["id"], "target": item["id"], "weight": count})
        citation_links += count
    return {
        "own": own, "nodes": nodes, "edges": edges,
        "institution_count": len(ranked), "researcher_count": len(all_researchers),
        "citation_links": citation_links, "sampled_works": len(sampled_works),
    }


def build_public_profile_snapshot(
    database_path: Path,
    *,
    database_id: str,
    blocks: Any = None,
) -> dict[str, Any]:
    with sqlite3.connect(f"file:{database_path.resolve()}?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        tables = _tables(con)
        person = con.execute("SELECT * FROM person WHERE id=1").fetchone()
        publication_columns = _columns(con, "publications")
        publications, publication_rows = _profile_publications(con, publication_columns)
        narrative = ""
        if "narrative_reports" in tables:
            report = con.execute("SELECT body FROM narrative_reports WHERE id=1").fetchone()
            narrative = _text(report["body"], 5000) if report else ""
        display_name = _text(
            (person["display_name"] if person else "") or (person["full_name"] if person else ""),
            200,
        ) or "Academic profile"
        degrees = _text(person["degrees"] if person else "", 300)
        profile_title = _profile_title(display_name, degrees)
        position = _text(person["position_title"] if person else "", 300)
        institution = _text(person["own_institution_name"] if person else "", 500)
        country = _text(person["own_institution_country"] if person else "", 200)
        headline = " · ".join(value for value in (position, institution) if value)
        location = country
        orcid = _text(person["orcid_id"] if person else "", 80)
        person_columns = set(person.keys()) if person else set()
        portrait_blob = (
            bytes(person["portrait_image"])
            if person
            and "portrait_image" in person_columns
            and person["portrait_image"]
            else None
        )
        portrait_mime_type = (
            _text(person["portrait_mime_type"], 80)
            if person and "portrait_mime_type" in person_columns
            else ""
        )
        if portrait_mime_type not in {"image/jpeg", "image/png"}:
            portrait_blob = None
            portrait_mime_type = ""
        try:
            portrait_width = int(person["portrait_width"] or 0) if "portrait_width" in person_columns else 0
            portrait_height = int(person["portrait_height"] or 0) if "portrait_height" in person_columns else 0
        except (TypeError, ValueError):
            portrait_width = 0
            portrait_height = 0
        bio = {
            "display_name": display_name,
            "degrees": degrees,
            "position_title": position,
            "institution": institution,
            "country": country,
            "orcid": orcid,
            "biography": narrative,
        }
        snapshot = {
            "schema_version": PUBLIC_PROFILE_SCHEMA_VERSION,
            "source_database_id": database_id,
            "display_name": display_name,
            "profile_title": profile_title,
            "headline": headline,
            "location": location,
            "biography": narrative,
            "orcid": orcid,
            "website": "",
            "blocks": normalize_profile_blocks(blocks),
            "bio": bio,
            "publications": publications,
            "metrics": _profile_metrics(publication_rows, person),
            "collaborators": _profile_collaborators(
                con,
                person,
                tables,
                publication_columns,
            ),
            "citations": _profile_citations(
                con,
                person,
                tables,
                publication_columns,
            ),
        }
        if portrait_blob and portrait_width > 0 and portrait_height > 0:
            snapshot["portrait_width"] = portrait_width
            snapshot["portrait_height"] = portrait_height
        snapshot["_portrait_blob"] = portrait_blob
        snapshot["_portrait_mime_type"] = portrait_mime_type or None
        return snapshot
