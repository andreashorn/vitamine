#!/usr/bin/env python3
"""Local CV database editor."""

from __future__ import annotations

import json
import csv
import filecmp
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .i18n import GERMAN_FIELD_PAIRS, fill_german_drafts
from .paths import (
    BUNDLED_METRICS_CSV,
    DATA,
    EXAMPLE_DB,
    LOGO,
    METRICS_CSV,
    OUTPUT,
    ROOT,
    SCRIPTS,
    STATIC,
    active_db_path,
    create_blank_database,
    sanitize_database_name,
    set_active_db,
    validate_database,
)
from .scripts.maintain_publications import maintain
from .scripts.import_uploaded_cv import import_cv_file


PROJECT = Path(__file__).resolve().parent

SECTION_LABELS = {
    "education": "Education",
    "postdoctoral_training": "Postdoctoral Training",
    "academic_appointments": "Faculty Academic Appointments",
    "hospital_appointments": "Hospital / Affiliated Appointments",
    "professional_positions": "Other Professional Positions",
    "committee_service": "Committee Service",
    "professional_societies": "Professional Societies",
    "grant_review": "Grant Review Activities",
    "editorial_activities": "Editorial Activities",
    "honors": "Honors and Prizes",
    "funding": "Research Funding",
    "teaching": "Teaching",
    "mentoring": "Trainees and Their Successes",
    "invited_presentations": "Invited Teaching and Presentations",
    "clinical_activities": "Clinical Activities and Innovations",
    "education_innovations": "Teaching and Education Innovations",
    "community_service": "Community Service",
}

BIOSKETCH_CONTRIBUTION_LIMIT = 5
BIOSKETCH_PRODUCTS_PER_CONTRIBUTION_LIMIT = 4

OWN_INSTITUTION = {
    "id": "own-institution",
    "name": "University Hospital Cologne",
    "country": "Germany",
    "country_code": "DE",
    "latitude": 50.9242,
    "longitude": 6.9184,
}

ENTRY_FIELDS = [
    "section_key",
    "subcategory",
    "subcategory_de",
    "start_date",
    "end_date",
    "title",
    "title_de",
    "organization",
    "organization_de",
    "location",
    "location_de",
    "role",
    "role_de",
    "amount",
    "amount_de",
    "description",
    "description_de",
    "raw_text",
    "raw_text_de",
    "confidence",
    "include_extended",
    "include_long",
    "include_short",
    "include_biosketch",
    "language",
]

CV_IMPORT_SETTING_FIELDS = {
    "provider": "none",
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "llama3.1:8b",
    "api_base_url": "https://api.openai.com/v1",
    "api_model": "gpt-4.1-mini",
    "bundled_llama_model_path": "",
    "bundled_llama_ctx_size": "4096",
}


app = FastAPI(title="VitaMine")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/logo", StaticFiles(directory=LOGO), name="logo")


@app.middleware("http")
async def no_cache_for_app_shell(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/") or request.url.path.startswith("/logo/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


def connect() -> sqlite3.Connection:
    db_path = active_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    ensure_person_columns(con)
    ensure_publication_columns(con)
    ensure_collaboration_tables(con)
    ensure_biosketch_tables(con)
    ensure_narrative_report_table(con)
    ensure_export_settings_table(con)
    ensure_app_settings_table(con)
    ensure_journal_metrics_table(con)
    return con


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def rows_dict(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def is_own_institution(name: str | None) -> bool:
    text = (name or "").casefold()
    own_tokens = [
        "university hospital cologne",
        "universitätsklinikum köln",
        "universitatsklinikum koln",
        "university of cologne",
        "universität zu köln",
        "universitat zu koln",
    ]
    return any(token in text for token in own_tokens)


def own_institution_from_person(person: dict[str, Any] | None) -> dict[str, Any]:
    person = person or {}
    def number(value: Any, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    return {
        "id": "own-institution",
        "name": str(person.get("own_institution_name") or OWN_INSTITUTION["name"]).strip() or OWN_INSTITUTION["name"],
        "country": str(person.get("own_institution_country") or OWN_INSTITUTION["country"]).strip() or OWN_INSTITUTION["country"],
        "country_code": str(person.get("own_institution_country_code") or OWN_INSTITUTION["country_code"]).strip() or OWN_INSTITUTION["country_code"],
        "latitude": number(person.get("own_institution_latitude"), OWN_INSTITUTION["latitude"]),
        "longitude": number(person.get("own_institution_longitude"), OWN_INSTITUTION["longitude"]),
    }


def unique_database_path(filename: str) -> Path:
    path = DATA / filename
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = DATA / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise HTTPException(status_code=409, detail="Could not choose an unused database filename")


def database_payload(db: Path) -> dict[str, Any]:
    return {
        "ok": True,
        "active": str(db),
        "active_name": db.name,
        "is_example": db.resolve() == EXAMPLE_DB.resolve(),
    }


def display_venue_name(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    known = {
        "brain": "Brain",
        "neuroimage": "NeuroImage",
        "annals of neurology": "Annals of Neurology",
        "movement disorders": "Movement Disorders",
        "clinical neurophysiology": "Clinical Neurophysiology",
        "biological psychiatry": "Biological Psychiatry",
        "nature communications": "Nature Communications",
        "brain communications": "Brain Communications",
        "brain stimulation": "Brain Stimulation",
        "elife": "eLife",
        "eneuro": "eNeuro",
    }
    key = text.casefold()
    if key in known:
        return known[key]
    if text.isupper() or text.islower():
        small = {"and", "of", "in", "the", "for", "on", "with"}
        words = []
        for index, word in enumerate(text.casefold().split()):
            words.append(word if index and word in small else word.capitalize())
        return " ".join(words)
    return text


def ensure_app_settings_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          value TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def ensure_journal_metrics_table(con: sqlite3.Connection) -> None:
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
    count = con.execute("SELECT COUNT(*) FROM journal_metrics").fetchone()[0]
    source_csv = METRICS_CSV if METRICS_CSV.exists() else BUNDLED_METRICS_CSV
    if count or not source_csv.exists():
        return
    with source_csv.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            venue = str(row.get("venue") or "").strip()
            impact_factor = str(row.get("impact_factor") or "").strip()
            if not venue or not impact_factor:
                continue
            con.execute(
                """
                INSERT OR REPLACE INTO journal_metrics
                  (venue, impact_factor, impact_factor_year, metric_source, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (
                    venue,
                    float(impact_factor),
                    str(row.get("impact_factor_year") or "").strip() or None,
                    str(row.get("metric_source") or "").strip() or "manual",
                ),
            )


def get_setting(con: sqlite3.Connection, key: str) -> str:
    row = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return str(row["value"] or "") if row else ""


def set_setting(con: sqlite3.Connection, key: str, value: str | None) -> None:
    con.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET
          value=excluded.value,
          updated_at=excluded.updated_at
        """,
        (key, value),
    )


def zotero_saved_env(con: sqlite3.Connection) -> dict[str, str]:
    api_key = get_setting(con, "zotero_api_key") or os.environ.get("ZOTERO_API_KEY") or ""
    library_type = get_setting(con, "zotero_library_type") or os.environ.get("ZOTERO_LIBRARY_TYPE") or "users"
    library_id = get_setting(con, "zotero_library_id") or os.environ.get("ZOTERO_LIBRARY_ID") or ""
    group_name = get_setting(con, "zotero_group_name") or os.environ.get("ZOTERO_GROUP_NAME") or ""
    collection_key = get_setting(con, "zotero_collection_key") or os.environ.get("ZOTERO_COLLECTION_KEY") or ""
    source_mode = get_setting(con, "zotero_source_mode") or os.environ.get("ZOTERO_SOURCE_MODE") or "my_publications"
    return {
        "api_key": api_key,
        "library_type": library_type.strip("/") or "users",
        "library_id": library_id,
        "group_name": group_name,
        "collection_key": collection_key,
        "source_mode": source_mode,
        "collection_name": get_setting(con, "zotero_collection_name"),
    }


def zotero_api_request(url: str, api_key: str) -> tuple[Any, dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={
            "Zotero-API-Key": api_key,
            "Zotero-API-Version": "3",
            "User-Agent": "vitamine/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8")), {k: v for k, v in response.headers.items()}


def zotero_key_info(api_key: str) -> dict[str, Any]:
    current, _ = zotero_api_request("https://api.zotero.org/keys/current", api_key)
    if not isinstance(current, dict):
        raise HTTPException(status_code=400, detail="Zotero did not return key metadata.")
    return current


def zotero_current_user_id(api_key: str) -> str:
    current = zotero_key_info(api_key)
    user_id = current.get("userID") if isinstance(current, dict) else None
    if not user_id:
        raise HTTPException(status_code=400, detail="Could not read Zotero user ID from this API key.")
    return str(user_id)


def zotero_group_names(api_key: str, user_id: str) -> dict[str, str]:
    params = urllib.parse.urlencode({"format": "json", "limit": 100})
    try:
        groups, _ = zotero_api_request(f"https://api.zotero.org/users/{user_id}/groups?{params}", api_key)
    except Exception:
        return {}
    return {
        str((group.get("data") or {}).get("id")): str((group.get("data") or {}).get("name") or "")
        for group in groups
        if (group.get("data") or {}).get("id")
    }


def zotero_accessible_libraries(api_key: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    info = zotero_key_info(api_key)
    user_id = str(info.get("userID") or "")
    access = info.get("access") if isinstance(info.get("access"), dict) else {}
    libraries: list[dict[str, str]] = []
    user_access = access.get("user") if isinstance(access.get("user"), dict) else {}
    if user_id and user_access.get("library"):
        libraries.append(
            {
                "type": "users",
                "id": user_id,
                "name": f"{info.get('displayName') or info.get('username') or 'Personal'} library",
                "kind": "Personal library",
            }
        )
    groups = access.get("groups") if isinstance(access.get("groups"), dict) else {}
    group_names = zotero_group_names(api_key, user_id) if user_id and groups else {}
    for group_id, permissions in groups.items():
        if isinstance(permissions, dict) and not permissions.get("library"):
            continue
        libraries.append(
            {
                "type": "groups",
                "id": str(group_id),
                "name": group_names.get(str(group_id)) or f"Group {group_id}",
                "kind": "Group library",
            }
        )
    return info, libraries


def choose_zotero_library(env: dict[str, str], libraries: list[dict[str, str]]) -> dict[str, str] | None:
    if not libraries:
        return None
    for library in libraries:
        if env.get("library_id") and library["type"] == env["library_type"] and library["id"] == env["library_id"]:
            return library
    if env.get("group_name"):
        for library in libraries:
            if library["type"] == "groups" and library["name"].casefold() == env["group_name"].casefold():
                return library
    preferred_type = "groups" if env["library_type"] in {"group", "groups"} else "users"
    preferred = [library for library in libraries if library["type"] == preferred_type]
    if len(preferred) == 1:
        return preferred[0]
    if len(libraries) == 1:
        return libraries[0]
    personal = [library for library in libraries if library["type"] == "users"]
    return personal[0] if personal else None


def zotero_resolved_library(env: dict[str, str]) -> tuple[str, str]:
    library_type = env["library_type"]
    library_id = env["library_id"]
    if library_type in {"user", "users"}:
        library_type = "users"
    elif library_type in {"group", "groups"}:
        library_type = "groups"
    else:
        raise HTTPException(status_code=400, detail="Zotero library type must be users or groups")
    if not library_id:
        _, libraries = zotero_accessible_libraries(env["api_key"])
        chosen = choose_zotero_library({**env, "library_type": library_type}, libraries)
        if chosen:
            library_type = chosen["type"]
            library_id = chosen["id"]
    if not library_id and library_type == "users":
        library_id = zotero_current_user_id(env["api_key"])
    if not library_id:
        raise HTTPException(status_code=400, detail="Choose a Zotero library before loading collections.")
    return library_type, library_id


def zotero_fetch_collections(api_key: str, library_type: str, library_id: str) -> list[dict[str, Any]]:
    collections: list[dict[str, Any]] = []
    start = 0
    limit = 100
    while True:
        params = urllib.parse.urlencode({"format": "json", "limit": limit, "start": start, "sort": "title"})
        batch, headers = zotero_api_request(
            f"https://api.zotero.org/{library_type}/{library_id}/collections?{params}",
            api_key,
        )
        collections.extend(batch)
        total = int(headers.get("Total-Results", len(collections)))
        start += limit
        if start >= total or not batch:
            break
    return collections


def journal_metric_count() -> int:
    with connect() as con:
        return int(
            con.execute(
                "SELECT COUNT(*) FROM journal_metrics WHERE venue != '' AND impact_factor IS NOT NULL"
            ).fetchone()[0]
        )


def read_journal_metrics() -> dict[str, dict[str, str]]:
    metrics = {}
    with connect() as con:
        rows = con.execute(
            """
            SELECT venue, impact_factor, impact_factor_year, metric_source
            FROM journal_metrics
            WHERE venue != '' AND impact_factor IS NOT NULL
            """
        ).fetchall()
    for row in rows:
        venue = str(row["venue"] or "").strip()
        if not venue:
            continue
        metrics[venue.casefold()] = {
            "venue": venue,
            "impact_factor": str(row["impact_factor"]),
            "impact_factor_year": str(row["impact_factor_year"] or "").strip(),
            "metric_source": str(row["metric_source"] or "").strip() or "manual",
        }
    return metrics


def write_journal_metrics(rows: list[dict[str, Any]]) -> None:
    with connect() as con:
        for row in rows:
            venue = str(row.get("venue") or "").strip()
            if not venue:
                continue
            impact_factor = str(row.get("impact_factor") or "").strip()
            if not impact_factor:
                con.execute("DELETE FROM journal_metrics WHERE lower(venue) = lower(?)", (venue,))
                continue
            con.execute(
                """
                INSERT INTO journal_metrics
                  (venue, impact_factor, impact_factor_year, metric_source, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(venue) DO UPDATE SET
                  impact_factor=excluded.impact_factor,
                  impact_factor_year=excluded.impact_factor_year,
                  metric_source=excluded.metric_source,
                  updated_at=excluded.updated_at
                """,
                (
                    venue,
                    float(impact_factor),
                    str(row.get("impact_factor_year") or "").strip() or None,
                    str(row.get("metric_source") or "").strip() or "manual",
                ),
            )
        con.commit()


def ensure_person_columns(con: sqlite3.Connection) -> None:
    table = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='person'").fetchone()
    if not table:
        return
    existing = {row[1] for row in con.execute("PRAGMA table_info(person)").fetchall()}
    columns = {
        "orcid_id": "TEXT",
        "own_institution_name": "TEXT",
        "own_institution_country": "TEXT",
        "own_institution_country_code": "TEXT",
        "own_institution_latitude": "REAL",
        "own_institution_longitude": "REAL",
    }
    for column, definition in columns.items():
        if column not in existing:
            con.execute(f"ALTER TABLE person ADD COLUMN {column} {definition}")
    con.execute(
        """
        UPDATE person
        SET own_institution_name=COALESCE(NULLIF(own_institution_name, ''), ?),
            own_institution_country=COALESCE(NULLIF(own_institution_country, ''), ?),
            own_institution_country_code=COALESCE(NULLIF(own_institution_country_code, ''), ?),
            own_institution_latitude=COALESCE(own_institution_latitude, ?),
            own_institution_longitude=COALESCE(own_institution_longitude, ?)
        WHERE id=1
        """,
        (
            OWN_INSTITUTION["name"],
            OWN_INSTITUTION["country"],
            OWN_INSTITUTION["country_code"],
            OWN_INSTITUTION["latitude"],
            OWN_INSTITUTION["longitude"],
        ),
    )
    con.execute(
        """
        INSERT OR IGNORE INTO person (id, full_name, display_name, raw_json)
        VALUES (1, '', '', '{}')
        """
    )
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


def ensure_publication_columns(con: sqlite3.Connection) -> None:
    table = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='publications'").fetchone()
    if not table:
        return
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
        "metadata_source": "TEXT",
        "metadata_enriched_at": "TEXT",
        "openalex_work_id": "TEXT",
        "openalex_cited_by_count": "INTEGER",
    }
    for column, definition in columns.items():
        if column not in existing:
            con.execute(f"ALTER TABLE publications ADD COLUMN {column} {definition}")


def ensure_collaboration_tables(con: sqlite3.Connection) -> None:
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


def ensure_biosketch_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS biosketch_contributions (
          id INTEGER PRIMARY KEY,
          document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
          ordinal INTEGER,
          title TEXT NOT NULL,
          narrative TEXT NOT NULL,
          citations_json TEXT NOT NULL DEFAULT '[]'
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS biosketch_contribution_publications (
          id INTEGER PRIMARY KEY,
          contribution_id INTEGER NOT NULL REFERENCES biosketch_contributions(id) ON DELETE CASCADE,
          citation_label TEXT NOT NULL,
          publication_id INTEGER REFERENCES publications(id) ON DELETE SET NULL,
          raw_citation TEXT NOT NULL,
          pmid TEXT,
          doi TEXT,
          UNIQUE(contribution_id, citation_label)
        )
        """
    )


def ensure_narrative_report_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS narrative_reports (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          title TEXT NOT NULL DEFAULT 'Narrative Report',
          body TEXT NOT NULL DEFAULT '',
          title_de TEXT,
          body_de TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    existing = {row[1] for row in con.execute("PRAGMA table_info(narrative_reports)").fetchall()}
    if "title_de" not in existing:
        con.execute("ALTER TABLE narrative_reports ADD COLUMN title_de TEXT")
    if "body_de" not in existing:
        con.execute("ALTER TABLE narrative_reports ADD COLUMN body_de TEXT")


def ensure_export_settings_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS export_settings (
          profile TEXT PRIMARY KEY,
          publication_limit INTEGER NOT NULL DEFAULT 10,
          authorship_filter TEXT NOT NULL DEFAULT 'first_last'
        )
        """
    )
    for profile in ("short", "ultrashort"):
        con.execute(
            """
            INSERT OR IGNORE INTO export_settings (profile, publication_limit, authorship_filter)
            VALUES (?, 10, 'first_last')
            """,
            (profile,),
        )


def ensure_manual_document(con: sqlite3.Connection) -> int:
    con.execute(
        """
        INSERT INTO documents (slug, title, source_path, source_format, imported_at, notes)
        VALUES ('manual_cv_database', 'Manual CV database edits', 'data/example.vitamine', 'vitamine', datetime('now'), 'Entries created or edited in VitaMine.')
        ON CONFLICT(slug) DO UPDATE SET imported_at=datetime('now')
        """
    )
    return int(con.execute("SELECT id FROM documents WHERE slug='manual_cv_database'").fetchone()[0])


def bool_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    return 1 if str(value).lower() in {"1", "true", "yes", "on"} else 0


def normalize_entry(payload: dict[str, Any]) -> dict[str, Any]:
    data = {field: payload.get(field) for field in ENTRY_FIELDS}
    data["section_key"] = data["section_key"] or "honors"
    data["raw_text"] = data["raw_text"] or data.get("description") or data.get("title") or ""
    data = fill_german_drafts(data)
    data["raw_text_de"] = data["raw_text_de"] or data.get("description_de") or data.get("title_de") or ""
    data["confidence"] = data["confidence"] or "manual"
    data["language"] = data["language"] or "en"
    for field, default in [
        ("include_extended", 1),
        ("include_long", 1),
        ("include_short", 0),
        ("include_biosketch", 0),
    ]:
        data[field] = bool_int(data[field], default)
    return data


def ensure_german_columns(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(cv_entries)").fetchall()}
    for _english, german in GERMAN_FIELD_PAIRS:
        if german not in existing:
            con.execute(f"ALTER TABLE cv_entries ADD COLUMN {german} TEXT")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/summary")
def summary() -> dict[str, Any]:
    with connect() as con:
        entries = rows_dict(
            con.execute(
                """
                SELECT section_key, count(*) AS count
                FROM cv_entries
                WHERE section_key NOT LIKE 'biosketch%'
                GROUP BY section_key
                ORDER BY section_key
                """
            ).fetchall()
        )
        publications = rows_dict(
            con.execute(
                "SELECT source, category, count(*) AS count FROM publications GROUP BY source, category ORDER BY source, category"
            ).fetchall()
        )
        warnings = rows_dict(
            con.execute(
                "SELECT warning_type, count(*) AS count FROM import_warnings GROUP BY warning_type ORDER BY warning_type"
            ).fetchall()
        )
    return {
        "sections": SECTION_LABELS,
        "entries": entries,
        "publications": publications,
        "warnings": warnings,
    }


@app.get("/api/metrics")
def metrics() -> dict[str, Any]:
    with connect() as con:
        publication_metrics = row_dict(
            con.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE WHEN COALESCE(suppress_display, 0) = 0 THEN 1 ELSE 0 END) AS visible,
                  SUM(CASE WHEN category = 'peer_reviewed' AND COALESCE(suppress_display, 0) = 0 THEN 1 ELSE 0 END) AS peer_reviewed,
                  SUM(CASE WHEN include_short = 1 THEN 1 ELSE 0 END) AS selected_short,
                  SUM(CASE WHEN include_ultrashort = 1 THEN 1 ELSE 0 END) AS selected_ultrashort,
                  SUM(CASE WHEN impact_factor IS NOT NULL THEN 1 ELSE 0 END) AS impact_factor_count,
                  SUM(CASE WHEN openalex_cited_by_count IS NOT NULL THEN 1 ELSE 0 END) AS citation_metric_count,
                  SUM(COALESCE(openalex_cited_by_count, 0)) AS openalex_cited_by_total,
                  SUM(CASE WHEN orcid_put_code IS NOT NULL AND orcid_put_code != '' THEN 1 ELSE 0 END) AS orcid_matched,
                  SUM(CASE WHEN COALESCE(suppress_display, 0) = 1 THEN 1 ELSE 0 END) AS suppressed,
                  SUM(CASE WHEN COALESCE(suppress_display, 0) = 0 AND (year IS NULL OR year = '') THEN 1 ELSE 0 END) AS missing_year,
                  SUM(CASE WHEN COALESCE(suppress_display, 0) = 0 AND (venue IS NULL OR venue = '') THEN 1 ELSE 0 END) AS missing_venue,
                  SUM(CASE WHEN COALESCE(suppress_display, 0) = 0 AND (doi IS NULL OR doi = '') THEN 1 ELSE 0 END) AS missing_doi
                FROM publications
                """
            ).fetchone()
        )
        by_year = rows_dict(
            con.execute(
                """
                SELECT year, COUNT(*) AS count
                FROM publications
                WHERE COALESCE(suppress_display, 0) = 0
                  AND year IS NOT NULL
                  AND year != ''
                GROUP BY year
                ORDER BY CAST(year AS INTEGER) DESC
                LIMIT 12
                """
            ).fetchall()
        )
        top_venues = rows_dict(
            con.execute(
                """
                SELECT lower(venue) AS venue_key,
                       MIN(venue) AS venue,
                       COUNT(*) AS count,
                       MAX(impact_factor) AS impact_factor
                FROM publications
                WHERE COALESCE(suppress_display, 0) = 0
                  AND venue IS NOT NULL
                  AND venue != ''
                GROUP BY lower(venue)
                ORDER BY count DESC, lower(venue)
                LIMIT 12
                """
            ).fetchall()
        )
        for row in top_venues:
            row["venue"] = display_venue_name(row["venue"])
        impact_factors = rows_dict(
            con.execute(
                """
                SELECT lower(venue) AS venue_key,
                       MIN(venue) AS venue,
                       COUNT(*) AS count,
                       MAX(impact_factor) AS impact_factor,
                       MAX(impact_factor_year) AS impact_factor_year,
                       MAX(metric_source) AS metric_source
                FROM publications
                WHERE COALESCE(suppress_display, 0) = 0
                  AND impact_factor IS NOT NULL
                  AND venue IS NOT NULL
                  AND venue != ''
                GROUP BY lower(venue)
                ORDER BY lower(venue)
                """
            ).fetchall()
        )
        for row in impact_factors:
            row["venue"] = display_venue_name(row["venue"])
    return {
        "publications": publication_metrics or {},
        "by_year": by_year,
        "top_venues": top_venues,
        "impact_factors": impact_factors,
        "journal_metric_count": journal_metric_count(),
    }


@app.get("/api/collaboration-map")
def collaboration_map() -> dict[str, Any]:
    with connect() as con:
        person = row_dict(con.execute("SELECT * FROM person WHERE id=1").fetchone())
        rows = rows_dict(
            con.execute(
                """
                SELECT
                  ci.institution_id,
                  ci.institution_name,
                  ci.ror,
                  ci.country_code,
                  ci.country,
                  ci.latitude,
                  ci.longitude,
                  COUNT(DISTINCT ci.publication_id) AS publication_count,
                  COUNT(DISTINCT ci.author_name) AS author_count,
                  GROUP_CONCAT(DISTINCT ci.author_name) AS authors,
                  GROUP_CONCAT(DISTINCT ci.publication_year) AS years
                FROM collaboration_institutions ci
                JOIN publications p ON p.id = ci.publication_id
                WHERE ci.latitude IS NOT NULL
                  AND ci.longitude IS NOT NULL
                  AND COALESCE(p.suppress_display, 0) = 0
                GROUP BY ci.institution_id
                ORDER BY publication_count DESC, author_count DESC, institution_name
                LIMIT 250
                """
            ).fetchall()
        )
    own_institution = own_institution_from_person(person)
    nodes = [{**own_institution, "own": True, "publication_count": 0, "author_count": 1, "authors": []}]
    edges = []
    country_counts: dict[str, int] = {}
    publication_total = 0
    for row in rows:
        if is_own_institution(row["institution_name"]):
            continue
        count = int(row["publication_count"] or 0)
        publication_total += count
        country = row["country"] or row["country_code"] or "Unknown"
        country_counts[country] = country_counts.get(country, 0) + count
        node = {
            "id": row["institution_id"],
            "name": row["institution_name"],
            "ror": row["ror"],
            "country": row["country"],
            "country_code": row["country_code"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "publication_count": count,
            "author_count": int(row["author_count"] or 0),
            "authors": sorted({author for author in str(row["authors"] or "").split(",") if author})[:20],
            "years": sorted({year for year in str(row["years"] or "").split(",") if year}, reverse=True),
            "own": False,
        }
        nodes.append(node)
        edges.append({"source": own_institution["id"], "target": node["id"], "weight": count})
    top_countries = [
        {"country": country, "publication_count": count}
        for country, count in sorted(country_counts.items(), key=lambda item: item[1], reverse=True)[:10]
    ]
    return {
        "own": own_institution,
        "nodes": nodes,
        "edges": edges,
        "top_countries": top_countries,
        "institution_count": max(len(nodes) - 1, 0),
        "edge_count": len(edges),
        "publication_links": publication_total,
    }


@app.get("/api/journal-metrics")
def journal_metrics(q: str | None = None, limit: int = 80) -> dict[str, Any]:
    clauses = ["COALESCE(suppress_display, 0) = 0", "venue IS NOT NULL", "venue != ''"]
    params: list[Any] = []
    if q:
        clauses.append("venue LIKE ?")
        params.append(f"%{q}%")
    params.append(limit)
    with connect() as con:
        rows = rows_dict(
            con.execute(
                f"""
                SELECT lower(venue) AS venue_key,
                       MIN(venue) AS venue,
                       COUNT(*) AS count,
                       MAX(impact_factor) AS impact_factor,
                       MAX(impact_factor_year) AS impact_factor_year,
                       MAX(metric_source) AS metric_source
                FROM publications
                WHERE {' AND '.join(clauses)}
                GROUP BY lower(venue)
                ORDER BY count DESC, lower(venue)
                LIMIT ?
                """,
                params,
            ).fetchall()
        )
        for row in rows:
            row["venue"] = display_venue_name(row["venue"])
    return {"metrics": rows}


@app.put("/api/journal-metrics")
async def update_journal_metrics(request: Request) -> JSONResponse:
    payload = await request.json()
    rows = payload.get("metrics", [])
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="metrics must be a list")
    cleared_venues = [
        str(row.get("venue") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("venue") or "").strip() and not str(row.get("impact_factor") or "").strip()
    ]
    write_journal_metrics(rows)
    if cleared_venues:
        with connect() as con:
            for venue in cleared_venues:
                con.execute(
                    """
                    UPDATE publications
                    SET impact_factor=NULL,
                        impact_factor_year=NULL,
                        metric_source=NULL
                    WHERE lower(venue) = lower(?)
                    """,
                    (venue,),
                )
            con.commit()
    result = maintain()
    return JSONResponse({"ok": True, **result, "metric_rows": journal_metric_count()})


@app.get("/api/person")
def get_person() -> dict[str, Any]:
    with connect() as con:
        person = row_dict(con.execute("SELECT * FROM person WHERE id=1").fetchone())
    return person or {}


@app.get("/api/connections")
def get_connections() -> dict[str, Any]:
    with connect() as con:
        person = con.execute("SELECT orcid_id FROM person WHERE id=1").fetchone()
        identifier = con.execute(
            """
            SELECT identifier_value
            FROM person_identifiers
            WHERE person_id = 1 AND lower(platform) = 'orcid'
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
        api_key = get_setting(con, "zotero_api_key")
        library_type = get_setting(con, "zotero_library_type") or "users"
        library_id = get_setting(con, "zotero_library_id")
        return {
            "orcid_id": (identifier["identifier_value"] if identifier else None) or (person["orcid_id"] if person else "") or "",
            "zotero_api_key_set": bool(api_key),
            "zotero_library_type": library_type,
            "zotero_library_id": library_id,
            "zotero_library_value": f"{library_type}:{library_id}" if library_id else "",
            "zotero_group_name": get_setting(con, "zotero_group_name"),
            "zotero_source_mode": get_setting(con, "zotero_source_mode") or "my_publications",
            "zotero_collection_key": get_setting(con, "zotero_collection_key"),
            "zotero_collection_name": get_setting(con, "zotero_collection_name"),
            "publication_source_policy": get_setting(con, "publication_source_policy") or "zotero_primary_orcid_validation",
        }


@app.put("/api/connections")
async def update_connections(request: Request) -> dict[str, Any]:
    payload = await request.json()
    orcid_id = str(payload.get("orcid_id") or "").strip()
    api_key = str(payload.get("zotero_api_key") or "").strip()
    library_value = str(payload.get("zotero_library_value") or "").strip()
    library_type = str(payload.get("zotero_library_type") or "users").strip("/") or "users"
    library_id = str(payload.get("zotero_library_id") or "").strip()
    if ":" in library_value:
        library_type, library_id = library_value.split(":", 1)
        library_type = library_type.strip("/") or "users"
        library_id = library_id.strip()
    if library_type not in {"users", "groups"}:
        raise HTTPException(status_code=400, detail="Zotero library type must be users or groups")
    group_name = str(payload.get("zotero_group_name") or "").strip()
    source_mode = str(payload.get("zotero_source_mode") or "my_publications").strip() or "my_publications"
    if source_mode not in {"my_publications", "collection", "library"}:
        raise HTTPException(status_code=400, detail="Choose My Publications, a collection, or the whole library.")
    collection_key = str(payload.get("zotero_collection_key") or "").strip()
    collection_name = str(payload.get("zotero_collection_name") or "").strip()
    if source_mode == "collection" and not collection_key:
        raise HTTPException(status_code=400, detail="Choose a Zotero collection.")
    source_policy = str(payload.get("publication_source_policy") or "zotero_primary_orcid_validation").strip()
    if source_policy not in {
        "zotero_only",
        "orcid_only",
        "zotero_primary_orcid_validation",
        "orcid_primary_zotero_validation",
    }:
        raise HTTPException(status_code=400, detail="Choose a publication source policy.")
    with connect() as con:
        if orcid_id:
            upsert_person_orcid_identifier(con, orcid_id)
        else:
            con.execute("UPDATE person SET orcid_id='' WHERE id=1")
            con.execute("DELETE FROM person_identifiers WHERE person_id=1 AND lower(platform)='orcid'")
        set_setting(con, "zotero_library_type", library_type)
        set_setting(con, "zotero_library_id", library_id)
        set_setting(con, "zotero_group_name", group_name)
        set_setting(con, "zotero_source_mode", source_mode)
        set_setting(con, "zotero_collection_key", collection_key)
        set_setting(con, "zotero_collection_name", collection_name)
        set_setting(con, "publication_source_policy", source_policy)
        if api_key:
            set_setting(con, "zotero_api_key", api_key)
        effective_key = api_key or get_setting(con, "zotero_api_key")
        if effective_key and not library_id:
            _, libraries = zotero_accessible_libraries(effective_key)
            chosen = choose_zotero_library(
                {
                    "library_type": library_type,
                    "library_id": library_id,
                    "group_name": group_name,
                },
                libraries,
            )
            if chosen:
                library_type = chosen["type"]
                library_id = chosen["id"]
                if chosen["type"] == "groups":
                    group_name = chosen["name"]
                set_setting(con, "zotero_library_type", library_type)
                set_setting(con, "zotero_library_id", library_id)
                set_setting(con, "zotero_group_name", group_name)
        con.commit()
    return {"ok": True}


@app.get("/api/zotero/connect-url")
def zotero_connect_url() -> dict[str, Any]:
    params = urllib.parse.urlencode(
        {
            "name": "VitaMine",
            "library_access": "1",
            "notes_access": "0",
            "write_access": "0",
            "all_groups": "none",
        }
    )
    return {
        "url": f"https://www.zotero.org/settings/keys/new?{params}",
        "oauth_available": bool(os.environ.get("ZOTERO_OAUTH_CLIENT_KEY") and os.environ.get("ZOTERO_OAUTH_CLIENT_SECRET")),
    }


@app.get("/api/zotero/collections")
def zotero_collections() -> dict[str, Any]:
    with connect() as con:
        env = zotero_saved_env(con)
    if not env["api_key"]:
        raise HTTPException(status_code=400, detail="Save a Zotero API key before loading collections.")
    library_type, library_id = zotero_resolved_library(env)
    with connect() as con:
        set_setting(con, "zotero_library_type", library_type)
        set_setting(con, "zotero_library_id", library_id)
        con.commit()
    collections = zotero_fetch_collections(env["api_key"], library_type, library_id)
    options = [
        {
            "mode": "my_publications",
            "key": "",
            "name": "My Publications",
            "level": 0,
            "path": "My Publications",
        },
        {
            "mode": "library",
            "key": "",
            "name": "Whole library",
            "level": 0,
            "path": "Whole library",
        },
    ]
    options.extend(
        {
            "mode": "collection",
            "key": item.get("key") or (item.get("data") or {}).get("key") or "",
            "name": (item.get("data") or {}).get("name") or "Untitled collection",
            "level": 0,
            "path": (item.get("data") or {}).get("name") or "Untitled collection",
        }
        for item in sorted(collections, key=lambda row: ((row.get("data") or {}).get("name") or "").casefold())
    )
    return {
        "library_type": library_type,
        "library_id": library_id,
        "collections": options,
    }


@app.get("/api/zotero/status")
def zotero_status() -> dict[str, Any]:
    with connect() as con:
        env = zotero_saved_env(con)
    if not env["api_key"]:
        return {"ok": False, "message": "No Zotero key saved.", "libraries": []}
    info, libraries = zotero_accessible_libraries(env["api_key"])
    chosen = choose_zotero_library(env, libraries)
    if chosen:
        collection_count = 0
        try:
            collection_count = len(zotero_fetch_collections(env["api_key"], chosen["type"], chosen["id"]))
        except Exception:
            collection_count = 0
        with connect() as con:
            set_setting(con, "zotero_library_type", chosen["type"])
            set_setting(con, "zotero_library_id", chosen["id"])
            if chosen["type"] == "groups":
                set_setting(con, "zotero_group_name", chosen["name"])
            con.commit()
        return {
            "ok": True,
            "message": f"Connected to {chosen['name']}.",
            "user": info.get("displayName") or info.get("username") or "",
            "library": chosen,
            "libraries": libraries,
            "collection_count": collection_count,
        }
    return {
        "ok": bool(libraries),
        "message": "Choose which Zotero library to use." if libraries else "This Zotero key does not grant library access.",
        "user": info.get("displayName") or info.get("username") or "",
        "libraries": libraries,
    }


@app.get("/api/person/identifiers")
def get_person_identifiers() -> dict[str, Any]:
    with connect() as con:
        rows = rows_dict(
            con.execute(
                """
                SELECT id, platform, identifier_type, identifier_value, url, source, verified_at, notes
                FROM person_identifiers
                WHERE person_id = 1
                ORDER BY lower(platform), lower(identifier_type), lower(identifier_value)
                """
            ).fetchall()
        )
    return {"identifiers": rows}


def identifier_payload(payload: dict[str, Any]) -> dict[str, str | None]:
    platform = str(payload.get("platform") or "").strip()
    identifier_type = str(payload.get("identifier_type") or "").strip()
    identifier_value = str(payload.get("identifier_value") or "").strip() or None
    url = str(payload.get("url") or "").strip()
    if not platform:
        raise HTTPException(status_code=400, detail="Platform is required")
    if not identifier_type:
        raise HTTPException(status_code=400, detail="Identifier type is required")
    if not url and platform.casefold() == "orcid" and identifier_value:
        url = f"https://orcid.org/{identifier_value}"
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    return {
        "platform": platform,
        "identifier_type": identifier_type,
        "identifier_value": identifier_value,
        "url": url,
        "source": str(payload.get("source") or "").strip() or "manual",
        "notes": str(payload.get("notes") or "").strip() or None,
    }


def upsert_person_orcid_identifier(
    con: sqlite3.Connection,
    orcid_id: str,
    *,
    source: str = "manual",
    notes: str = "Used for ORCID public-work sync.",
) -> int:
    if not orcid_id:
        raise HTTPException(status_code=400, detail="ORCID iD is required")
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
                source=?,
                verified_at=datetime('now'),
                notes=?
            WHERE id=? AND person_id=1
            """,
            (orcid_id, url, source, notes, row["id"]),
        )
    else:
        con.execute(
            """
            INSERT INTO person_identifiers
              (person_id, platform, identifier_type, identifier_value, url, source, verified_at, notes)
            VALUES (1, 'ORCID', 'ORCID iD', ?, ?, ?, datetime('now'), ?)
            """,
            (orcid_id, url, source, notes),
        )
        row = con.execute("SELECT last_insert_rowid() AS id").fetchone()
    con.execute("UPDATE person SET orcid_id=? WHERE id=1", (orcid_id,))
    return int(row["id"])


def sync_person_orcid_from_identifiers(con: sqlite3.Connection) -> None:
    row = con.execute(
        """
        SELECT identifier_value
        FROM person_identifiers
        WHERE person_id = 1 AND lower(platform) = 'orcid'
        ORDER BY id
        LIMIT 1
        """
    ).fetchone()
    con.execute("UPDATE person SET orcid_id=? WHERE id=1", ((row["identifier_value"] if row else "") or "",))


@app.post("/api/person/identifiers")
async def create_person_identifier(request: Request) -> dict[str, Any]:
    values = identifier_payload(await request.json())
    with connect() as con:
        if values["platform"].casefold() == "orcid":
            identifier_id = upsert_person_orcid_identifier(
                con,
                values["identifier_value"] or "",
                source=values["source"] or "manual",
                notes=values["notes"] or "Used for ORCID public-work sync.",
            )
            con.commit()
            return {"ok": True, "id": identifier_id}
        cursor = con.execute(
            """
            INSERT INTO person_identifiers
              (person_id, platform, identifier_type, identifier_value, url, source, verified_at, notes)
            VALUES (1, ?, ?, ?, ?, ?, datetime('now'), ?)
            """,
            (
                values["platform"],
                values["identifier_type"],
                values["identifier_value"],
                values["url"],
                values["source"],
                values["notes"],
            ),
        )
        sync_person_orcid_from_identifiers(con)
        con.commit()
    return {"ok": True, "id": cursor.lastrowid}


@app.put("/api/person/identifiers/{identifier_id}")
async def update_person_identifier(identifier_id: int, request: Request) -> dict[str, Any]:
    values = identifier_payload(await request.json())
    with connect() as con:
        if values["platform"].casefold() == "orcid":
            exists = con.execute(
                "SELECT id FROM person_identifiers WHERE id=? AND person_id=1",
                (identifier_id,),
            ).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="Identifier not found")
            upsert_person_orcid_identifier(
                con,
                values["identifier_value"] or "",
                source=values["source"] or "manual",
                notes=values["notes"] or "Used for ORCID public-work sync.",
            )
            con.commit()
            return {"ok": True}
        cursor = con.execute(
            """
            UPDATE person_identifiers
            SET platform=?,
                identifier_type=?,
                identifier_value=?,
                url=?,
                source=?,
                verified_at=datetime('now'),
                notes=?
            WHERE id=? AND person_id=1
            """,
            (
                values["platform"],
                values["identifier_type"],
                values["identifier_value"],
                values["url"],
                values["source"],
                values["notes"],
                identifier_id,
            ),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Identifier not found")
        sync_person_orcid_from_identifiers(con)
        con.commit()
    return {"ok": True}


@app.delete("/api/person/identifiers/{identifier_id}")
def delete_person_identifier(identifier_id: int) -> dict[str, Any]:
    with connect() as con:
        cursor = con.execute("DELETE FROM person_identifiers WHERE id=? AND person_id=1", (identifier_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Identifier not found")
        sync_person_orcid_from_identifiers(con)
        con.commit()
    return {"ok": True}


@app.put("/api/person")
async def update_person(request: Request) -> dict[str, Any]:
    payload = await request.json()
    allowed = [
        "full_name",
        "display_name",
        "degrees",
        "position_title",
        "office_address",
        "home_address",
        "work_phone",
        "work_email",
        "place_of_birth",
        "era_commons",
        "own_institution_name",
        "own_institution_country",
        "own_institution_country_code",
        "own_institution_latitude",
        "own_institution_longitude",
    ]
    values = {field: payload.get(field) for field in allowed}
    values["raw_json"] = json.dumps(values, ensure_ascii=False, indent=2)
    with connect() as con:
        con.execute(
            f"""
            INSERT INTO person (id, {', '.join(allowed)}, raw_json)
            VALUES (1, {', '.join('?' for _ in allowed)}, ?)
            ON CONFLICT(id) DO UPDATE SET
            {', '.join(f'{field}=excluded.{field}' for field in allowed)},
            raw_json=excluded.raw_json
            """,
            (*[values[field] for field in allowed], values["raw_json"]),
        )
        con.commit()
    return {"ok": True}


@app.get("/api/narrative-report")
def get_narrative_report() -> dict[str, Any]:
    with connect() as con:
        report = row_dict(con.execute("SELECT * FROM narrative_reports WHERE id=1").fetchone())
    return report or {"id": 1, "title": "Narrative Report", "body": "", "title_de": "Narrativer Bericht", "body_de": ""}


@app.put("/api/narrative-report")
async def update_narrative_report(request: Request) -> dict[str, Any]:
    payload = await request.json()
    title = str(payload.get("title") or "Narrative Report").strip() or "Narrative Report"
    body = str(payload.get("body") or "").strip()
    title_de = str(payload.get("title_de") or "").strip()
    body_de = str(payload.get("body_de") or "").strip()
    with connect() as con:
        con.execute(
            """
            INSERT INTO narrative_reports (id, title, body, title_de, body_de, updated_at)
            VALUES (1, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(id) DO UPDATE SET
              title=excluded.title,
              body=excluded.body,
              title_de=excluded.title_de,
              body_de=excluded.body_de,
              updated_at=excluded.updated_at
            """,
            (title, body, title_de, body_de),
        )
        con.commit()
    return {"ok": True}


@app.get("/api/entries")
def list_entries(section: str | None = None, q: str | None = None, limit: int = 250) -> dict[str, Any]:
    clauses = []
    params: list[Any] = []
    clauses.append("section_key NOT LIKE 'biosketch%'")
    if section:
        clauses.append("section_key = ?")
        params.append(section)
    if q:
        clauses.append("(title LIKE ? OR title_de LIKE ? OR organization LIKE ? OR organization_de LIKE ? OR description LIKE ? OR description_de LIKE ? OR raw_text LIKE ? OR raw_text_de LIKE ?)")
        needle = f"%{q}%"
        params.extend([needle, needle, needle, needle, needle, needle, needle, needle])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with connect() as con:
        rows = rows_dict(
            con.execute(
                f"""
                SELECT * FROM cv_entries
                {where}
                ORDER BY section_key, start_date, id
                LIMIT ?
                """,
                params,
            ).fetchall()
        )
        entry_ids = [row["id"] for row in rows]
        if entry_ids:
            placeholders = ", ".join("?" for _ in entry_ids)
            achievement_rows = rows_dict(
                con.execute(
                    f"""
                    SELECT t.cv_entry_id, a.*
                    FROM trainee_achievements a
                    JOIN trainees t ON t.id = a.trainee_id
                    WHERE t.cv_entry_id IN ({placeholders})
                    ORDER BY a.year, a.id
                    """,
                    entry_ids,
                ).fetchall()
            )
            achievements_by_entry: dict[int, list[dict[str, Any]]] = {}
            for achievement in achievement_rows:
                achievements_by_entry.setdefault(achievement["cv_entry_id"], []).append(achievement)
            for row in rows:
                row["achievements"] = achievements_by_entry.get(row["id"], [])
    return {"entries": rows}


@app.post("/api/entries")
async def create_entry(request: Request) -> dict[str, Any]:
    payload = normalize_entry(await request.json())
    with connect() as con:
        ensure_german_columns(con)
        document_id = ensure_manual_document(con)
        cur = con.execute(
            f"""
            INSERT INTO cv_entries (document_id, {', '.join(ENTRY_FIELDS)})
            VALUES (?, {', '.join('?' for _ in ENTRY_FIELDS)})
            """,
            (document_id, *[payload[field] for field in ENTRY_FIELDS]),
        )
        con.commit()
        return {"ok": True, "id": cur.lastrowid}


@app.put("/api/entries/{entry_id}")
async def update_entry(entry_id: int, request: Request) -> dict[str, Any]:
    payload = normalize_entry(await request.json())
    with connect() as con:
        ensure_german_columns(con)
        ensure_manual_document(con)
        cur = con.execute(
            f"""
            UPDATE cv_entries
            SET {', '.join(f'{field}=?' for field in ENTRY_FIELDS)}
            WHERE id=?
            """,
            (*[payload[field] for field in ENTRY_FIELDS], entry_id),
        )
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}


@app.delete("/api/entries/{entry_id}")
def delete_entry(entry_id: int) -> dict[str, Any]:
    with connect() as con:
        cur = con.execute("DELETE FROM cv_entries WHERE id=?", (entry_id,))
        con.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}


@app.get("/api/publications")
def list_publications(
    q: str | None = None,
    limit: int = 250,
    show_suppressed: int = 0,
    sort: str = "year",
    direction: str = "desc",
) -> dict[str, Any]:
    clauses = []
    params: list[Any] = []
    if not show_suppressed:
        clauses.append("COALESCE(suppress_display, 0) = 0")
    if q:
        clauses.append("(title LIKE ? OR authors LIKE ? OR venue LIKE ? OR doi LIKE ? OR quality_note LIKE ?)")
        needle = f"%{q}%"
        params.extend([needle, needle, needle, needle, needle])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sort_columns = {
        "year": "CAST(year AS INTEGER)",
        "source": "lower(source)",
        "flags": "include_ultrashort + include_short",
        "order": "COALESCE(selected_order, 999999)",
        "title": "lower(title)",
        "venue": "lower(venue)",
        "impact_factor": "COALESCE(impact_factor, -1)",
        "doi": "lower(doi)",
    }
    sort_sql = sort_columns.get(sort, sort_columns["year"])
    direction_sql = "ASC" if direction.lower() == "asc" else "DESC"
    params.append(limit)
    with connect() as con:
        rows = rows_dict(
            con.execute(
                f"""
                SELECT id, source, zotero_key, item_type, category, authors, title, venue, year, doi, pmid,
                       url, abstract, extra, raw_citation, confidence,
                       include_short, include_ultrashort, selected_order, short_selected_order, ultrashort_selected_order, short_citation,
                       impact_factor, impact_factor_year, metric_source, suppress_display, quality_note
                       , orcid_put_code, orcid_source, orcid_last_modified, orcid_path
                       , metadata_source, metadata_enriched_at, openalex_work_id, openalex_cited_by_count
                FROM publications
                {where}
                ORDER BY {sort_sql} {direction_sql}, lower(title)
                LIMIT ?
                """,
                params,
            ).fetchall()
        )
    return {"publications": rows}


PUBLICATION_FIELDS = [
    "item_type",
    "category",
    "authors",
    "title",
    "venue",
    "year",
    "doi",
    "pmid",
    "url",
    "abstract",
    "extra",
    "raw_citation",
    "confidence",
    "include_short",
    "include_ultrashort",
    "short_citation",
    "suppress_display",
    "quality_note",
]


def normalize_publication(payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()
    raw_citation = str(payload.get("raw_citation") or "").strip()
    if not title and not raw_citation:
        raise HTTPException(status_code=400, detail="Title or raw citation is required")
    authors = str(payload.get("authors") or "").strip()
    venue = str(payload.get("venue") or "").strip()
    year = str(payload.get("year") or "").strip()
    category = str(payload.get("category") or "").strip() or "other"
    item_type = str(payload.get("item_type") or "").strip() or "journal-article"
    if not raw_citation:
        raw_citation = ". ".join(part for part in [authors, title, venue, year] if part)
    return {
        "item_type": item_type,
        "category": category,
        "authors": authors,
        "title": title,
        "venue": venue,
        "year": year,
        "doi": str(payload.get("doi") or "").strip(),
        "pmid": str(payload.get("pmid") or "").strip(),
        "url": str(payload.get("url") or "").strip(),
        "abstract": str(payload.get("abstract") or "").strip(),
        "extra": str(payload.get("extra") or "").strip(),
        "raw_citation": raw_citation,
        "confidence": str(payload.get("confidence") or "").strip() or "manual",
        "include_short": 1 if payload.get("include_short") else 0,
        "include_ultrashort": 1 if payload.get("include_ultrashort") else 0,
        "short_citation": str(payload.get("short_citation") or "").strip(),
        "suppress_display": 1 if payload.get("suppress_display") else 0,
        "quality_note": str(payload.get("quality_note") or "").strip(),
    }


@app.post("/api/publications")
async def create_publication(request: Request) -> dict[str, Any]:
    payload = normalize_publication(await request.json())
    with connect() as con:
        document_id = ensure_manual_document(con)
        cursor = con.execute(
            f"""
            INSERT INTO publications (document_id, source, {', '.join(PUBLICATION_FIELDS)})
            VALUES (?, 'manual', {', '.join('?' for _ in PUBLICATION_FIELDS)})
            """,
            (document_id, *[payload[field] for field in PUBLICATION_FIELDS]),
        )
        con.commit()
    return {"ok": True, "id": cursor.lastrowid}


@app.put("/api/publications/{publication_id}")
async def update_publication(publication_id: int, request: Request) -> dict[str, Any]:
    payload = normalize_publication(await request.json())
    with connect() as con:
        cursor = con.execute(
            f"""
            UPDATE publications
            SET {', '.join(f'{field}=?' for field in PUBLICATION_FIELDS)}
            WHERE id=?
            """,
            (*[payload[field] for field in PUBLICATION_FIELDS], publication_id),
        )
        con.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Publication not found")
    return {"ok": True}


@app.delete("/api/publications/{publication_id}")
def delete_publication(publication_id: int) -> dict[str, Any]:
    with connect() as con:
        cursor = con.execute("DELETE FROM publications WHERE id=?", (publication_id,))
        con.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Publication not found")
    return {"ok": True}


EXPORT_PROFILES = {
    "short": {
        "flag": "include_short",
        "order": "short_selected_order",
        "fallback_order": "selected_order",
    },
    "ultrashort": {
        "flag": "include_ultrashort",
        "order": "ultrashort_selected_order",
        "fallback_order": "selected_order",
    },
}


def validate_export_profile(profile: str) -> dict[str, str]:
    if profile not in EXPORT_PROFILES:
        raise HTTPException(status_code=404, detail="Unknown export profile")
    return EXPORT_PROFILES[profile]


def horn_authorship(authors: str | None) -> str:
    parts = [part.strip().lower() for part in (authors or "").split(",") if part.strip()]
    if not parts:
        return "other"
    horn_patterns = ("andreas horn", "a horn", "horn a", "horn, a", "horn andreas")
    first = any(pattern in parts[0] for pattern in horn_patterns)
    last = any(pattern in parts[-1] for pattern in horn_patterns)
    if first and last:
        return "first_last"
    if first:
        return "first"
    if last:
        return "last"
    return "other"


def authorship_matches(kind: str, authorship_filter: str) -> bool:
    if authorship_filter == "all":
        return True
    if authorship_filter == "first_last":
        return kind in {"first", "last", "first_last"}
    return kind == authorship_filter or kind == "first_last"


def publication_score(row: sqlite3.Row, authorship: str) -> float:
    impact = float(row["impact_factor"] or 0)
    try:
        year = int(str(row["year"] or "0")[:4])
    except ValueError:
        year = 0
    recency = max(0, year - 2010) * 0.7
    authorship_bonus = {"first_last": 8, "last": 7, "first": 6, "other": 0}.get(authorship, 0)
    citations = min(float(row["openalex_cited_by_count"] or 0), 500) / 100
    return round((impact * 2.5) + recency + authorship_bonus + citations, 3)


def export_publication_rows(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = rows_dict(
        con.execute(
            """
            SELECT id, authors, title, venue, year, doi,
                   include_short, include_ultrashort, selected_order,
                   short_selected_order, ultrashort_selected_order,
                   impact_factor, impact_factor_year, openalex_cited_by_count,
                   suppress_display, category
            FROM publications
            WHERE COALESCE(suppress_display, 0) = 0
              AND category = 'peer_reviewed'
            """
        ).fetchall()
    )
    for row in rows:
        row["authorship"] = horn_authorship(row.get("authors"))
        row["score"] = publication_score(row, row["authorship"])
    return rows


def eligible_export_publication(row: dict[str, Any]) -> bool:
    title = str(row.get("title") or "").strip().lower()
    if title.startswith(("correction", "erratum", "corrigendum")):
        return False
    return True


def distinct_publications(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    distinct = []
    for row in rows:
        title = " ".join(str(row.get("title") or "").lower().strip(" .,:;").split())
        key = title
        if key in seen:
            continue
        seen.add(key)
        distinct.append(row)
    return distinct


def compact_publication_citation(row: sqlite3.Row | dict[str, Any]) -> str:
    authors = str(row["authors"] or "").strip()
    title = str(row["title"] or "").strip()
    venue = str(row["venue"] or "").strip()
    year = str(row["year"] or "").strip()
    doi = str(row["doi"] or "").strip()
    pmid = str(row["pmid"] or "").strip()
    pieces = [authors, title, venue, year]
    citation = ". ".join(piece.rstrip(".") for piece in pieces if piece)
    if doi:
        citation = f"{citation}. doi:{doi}" if citation else f"doi:{doi}"
    if pmid:
        citation = f"{citation}. PMID: {pmid}" if citation else f"PMID: {pmid}"
    return citation


def biosketch_publications_for(con: sqlite3.Connection, contribution_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not contribution_ids:
        return {}
    placeholders = ", ".join("?" for _ in contribution_ids)
    rows = rows_dict(
        con.execute(
            f"""
            SELECT bp.contribution_id, bp.citation_label, bp.raw_citation,
                   p.id, p.authors, p.title, p.venue, p.year, p.doi, p.pmid,
                   p.impact_factor, p.impact_factor_year, p.openalex_cited_by_count
            FROM biosketch_contribution_publications bp
            LEFT JOIN publications p ON p.id = bp.publication_id
            WHERE bp.contribution_id IN ({placeholders})
            ORDER BY bp.citation_label, bp.id
            """,
            contribution_ids,
        ).fetchall()
    )
    by_contribution: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("id") is None:
            row["title"] = row.get("raw_citation") or "Unlinked citation"
        by_contribution.setdefault(int(row["contribution_id"]), []).append(row)
    return by_contribution


def biosketch_contribution_payload(row: sqlite3.Row, publications: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ordinal": row["ordinal"],
        "title": row["title"],
        "narrative": row["narrative"],
        "publications": publications,
    }


@app.get("/api/biosketch")
def get_biosketch() -> dict[str, Any]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT id, ordinal, title, narrative, citations_json
            FROM biosketch_contributions
            ORDER BY ordinal, id
            """
        ).fetchall()
        publications = biosketch_publications_for(con, [int(row["id"]) for row in rows])
    contributions = [biosketch_contribution_payload(row, publications.get(int(row["id"]), [])) for row in rows]
    return {
        "contributions": contributions,
        "publication_count": sum(len(item["publications"]) for item in contributions),
        "contribution_limit": BIOSKETCH_CONTRIBUTION_LIMIT,
        "products_per_contribution_limit": BIOSKETCH_PRODUCTS_PER_CONTRIBUTION_LIMIT,
        "publication_limit": BIOSKETCH_CONTRIBUTION_LIMIT * BIOSKETCH_PRODUCTS_PER_CONTRIBUTION_LIMIT,
    }


@app.post("/api/biosketch/contributions")
async def create_biosketch_contribution(request: Request) -> dict[str, Any]:
    payload = await request.json()
    with connect() as con:
        document_id = ensure_manual_document(con)
        ordinal = con.execute("SELECT COALESCE(MAX(ordinal), 0) + 1 FROM biosketch_contributions").fetchone()[0]
        cursor = con.execute(
            """
            INSERT INTO biosketch_contributions (document_id, ordinal, title, narrative, citations_json)
            VALUES (?, ?, ?, ?, '[]')
            """,
            (
                document_id,
                ordinal,
                str(payload.get("title") or "New Achievement").strip() or "New Achievement",
                str(payload.get("narrative") or "").strip(),
            ),
        )
        con.commit()
    return {"ok": True, "id": cursor.lastrowid}


@app.put("/api/biosketch/contributions/{contribution_id}")
async def update_biosketch_contribution(contribution_id: int, request: Request) -> dict[str, Any]:
    payload = await request.json()
    title = str(payload.get("title") or "").strip()
    narrative = str(payload.get("narrative") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Achievement title is required")
    with connect() as con:
        cursor = con.execute(
            """
            UPDATE biosketch_contributions
            SET title=?, narrative=?
            WHERE id=?
            """,
            (title, narrative, contribution_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Achievement not found")
        con.commit()
    return {"ok": True}


@app.delete("/api/biosketch/contributions/{contribution_id}")
def delete_biosketch_contribution(contribution_id: int) -> dict[str, Any]:
    with connect() as con:
        cursor = con.execute("DELETE FROM biosketch_contributions WHERE id=?", (contribution_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Achievement not found")
        rows = con.execute("SELECT id FROM biosketch_contributions ORDER BY ordinal, id").fetchall()
        for index, row in enumerate(rows, 1):
            con.execute("UPDATE biosketch_contributions SET ordinal=? WHERE id=?", (index, row["id"]))
        con.commit()
    return {"ok": True}


@app.put("/api/biosketch/contributions/{contribution_id}/publications")
async def update_biosketch_contribution_publications(contribution_id: int, request: Request) -> dict[str, Any]:
    payload = await request.json()
    requested = payload.get("publications") or []
    publication_ids: list[int] = []
    for item in requested:
        try:
            publication_id = int(item["id"] if isinstance(item, dict) else item)
        except (KeyError, TypeError, ValueError):
            continue
        if publication_id not in publication_ids:
            publication_ids.append(publication_id)
    with connect() as con:
        contribution = con.execute("SELECT id FROM biosketch_contributions WHERE id=?", (contribution_id,)).fetchone()
        if not contribution:
            raise HTTPException(status_code=404, detail="Achievement not found")
        con.execute("DELETE FROM biosketch_contribution_publications WHERE contribution_id=?", (contribution_id,))
        citations = []
        for index, publication_id in enumerate(publication_ids):
            pub = con.execute(
                """
                SELECT id, authors, title, venue, year, doi, pmid
                FROM publications
                WHERE id=?
                """,
                (publication_id,),
            ).fetchone()
            if not pub:
                continue
            label = chr(97 + index)
            raw_citation = compact_publication_citation(pub)
            citations.append(f"{label}. {raw_citation}")
            con.execute(
                """
                INSERT INTO biosketch_contribution_publications
                  (contribution_id, citation_label, publication_id, raw_citation, pmid, doi)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (contribution_id, label, publication_id, raw_citation, pub["pmid"], pub["doi"]),
            )
        con.execute(
            "UPDATE biosketch_contributions SET citations_json=? WHERE id=?",
            (json.dumps(citations, ensure_ascii=False), contribution_id),
        )
        con.commit()
    return {"ok": True, "selected": len(citations)}


@app.get("/api/export-profiles/{profile}/publications")
def export_profile_publications(profile: str, q: str | None = None, limit: int = 200) -> dict[str, Any]:
    config = validate_export_profile(profile)
    with connect() as con:
        settings = row_dict(con.execute("SELECT * FROM export_settings WHERE profile=?", (profile,)).fetchone()) or {
            "profile": profile,
            "publication_limit": 10,
            "authorship_filter": "first_last",
        }
        rows = export_publication_rows(con)
    flag = config["flag"]
    order_column = config["order"]
    fallback = config["fallback_order"]
    selected = [row for row in rows if row.get(flag)]
    selected.sort(key=lambda row: (row.get(order_column) or row.get(fallback) or 999, -(int(str(row.get("year") or "0")[:4]) if str(row.get("year") or "").isdigit() else 0)))
    candidates = [row for row in rows if eligible_export_publication(row) and authorship_matches(row["authorship"], settings["authorship_filter"])]
    if q:
        needle = q.casefold()
        candidates = [
            row
            for row in candidates
            if any(needle in str(row.get(field) or "").casefold() for field in ("title", "authors", "venue", "doi", "year"))
        ]
    candidates.sort(key=lambda row: (-row["score"], -(int(str(row.get("year") or "0")[:4]) if str(row.get("year") or "").isdigit() else 0), row.get("title") or ""))
    candidates = distinct_publications(candidates)
    return {"settings": settings, "selected": selected, "candidates": candidates[: max(10, min(limit, 500))], "query": q or ""}


@app.put("/api/export-profiles/{profile}/publications")
async def update_export_profile_publications(profile: str, request: Request) -> dict[str, Any]:
    config = validate_export_profile(profile)
    payload = await request.json()
    limit = max(1, min(50, int(payload.get("publication_limit") or 10)))
    authorship_filter = str(payload.get("authorship_filter") or "first_last")
    if authorship_filter not in {"first_last", "first", "last", "all"}:
        authorship_filter = "first_last"
    publications = payload.get("publications") or []
    selected_ids: list[int] = []
    for item in publications[:limit]:
        try:
            selected_ids.append(int(item["id"] if isinstance(item, dict) else item))
        except (KeyError, TypeError, ValueError):
            continue
    with connect() as con:
        con.execute(
            """
            INSERT INTO export_settings (profile, publication_limit, authorship_filter)
            VALUES (?, ?, ?)
            ON CONFLICT(profile) DO UPDATE SET
              publication_limit=excluded.publication_limit,
              authorship_filter=excluded.authorship_filter
            """,
            (profile, limit, authorship_filter),
        )
        con.execute(f"UPDATE publications SET {config['flag']}=0, {config['order']}=NULL")
        for index, pub_id in enumerate(selected_ids, 1):
            con.execute(
                f"UPDATE publications SET {config['flag']}=1, {config['order']}=? WHERE id=?",
                (index, pub_id),
            )
        con.commit()
    return {"ok": True, "selected": len(selected_ids), "publication_limit": limit}


@app.post("/api/export-profiles/{profile}/suggest")
async def suggest_export_profile_publications(profile: str, request: Request) -> dict[str, Any]:
    config = validate_export_profile(profile)
    payload = await request.json()
    limit = max(1, min(50, int(payload.get("publication_limit") or 10)))
    authorship_filter = str(payload.get("authorship_filter") or "first_last")
    if authorship_filter not in {"first_last", "first", "last", "all"}:
        authorship_filter = "first_last"
    with connect() as con:
        rows = export_publication_rows(con)
        candidates = [row for row in rows if eligible_export_publication(row) and authorship_matches(row["authorship"], authorship_filter)]
        candidates.sort(key=lambda row: (-row["score"], -(int(str(row.get("year") or "0")[:4]) if str(row.get("year") or "").isdigit() else 0), row.get("title") or ""))
        candidates = distinct_publications(candidates)
        selected_ids = [row["id"] for row in candidates[:limit]]
        con.execute(
            """
            INSERT INTO export_settings (profile, publication_limit, authorship_filter)
            VALUES (?, ?, ?)
            ON CONFLICT(profile) DO UPDATE SET
              publication_limit=excluded.publication_limit,
              authorship_filter=excluded.authorship_filter
            """,
            (profile, limit, authorship_filter),
        )
        con.execute(f"UPDATE publications SET {config['flag']}=0, {config['order']}=NULL")
        for index, pub_id in enumerate(selected_ids, 1):
            con.execute(
                f"UPDATE publications SET {config['flag']}=1, {config['order']}=? WHERE id=?",
                (index, pub_id),
            )
        con.commit()
    return {"ok": True, "selected": len(selected_ids)}


def run_script(name: str, *args: str) -> subprocess.CompletedProcess[str]:
    pythonpath = os.pathsep.join(part for part in (str(ROOT), os.environ.get("PYTHONPATH", "")) if part)
    env = {**os.environ, "VITAMINE_DB": str(active_db_path()), "PYTHONPATH": pythonpath}
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--vitamine-script", name, *args]
    else:
        command = [sys.executable, str(SCRIPTS / name), *args]
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def build_response(stdout: str, cache_key: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True, "stdout": stdout}
    for line in stdout.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key in {"html", "markdown", "typst", "pdf", "docx"} and value:
            payload[key] = f"/{value}?v={cache_key}"
            payload[f"{key}_path"] = value
        elif key == "warning" and value:
            payload["warning"] = value
    if extra:
        payload.update(extra)
    return payload


PUBLICATION_SOURCE_POLICIES = {
    "zotero_only": ("zotero",),
    "orcid_only": ("orcid",),
    "zotero_primary_orcid_validation": ("zotero", "orcid"),
    "orcid_primary_zotero_validation": ("orcid", "zotero"),
}


def publication_source_policy(con: sqlite3.Connection) -> str:
    policy = get_setting(con, "publication_source_policy") or "zotero_primary_orcid_validation"
    return policy if policy in PUBLICATION_SOURCE_POLICIES else "zotero_primary_orcid_validation"


def run_publication_source(source: str) -> subprocess.CompletedProcess[str]:
    if source == "zotero":
        return run_script("sync_zotero.py")
    if source == "orcid":
        return run_script("sync_orcid.py")
    raise ValueError(f"Unknown publication source: {source}")


@app.get("/api/export-settings")
def export_settings() -> dict[str, Any]:
    with connect() as con:
        return {
            "home_language_label": get_setting(con, "home_language_label") or "Deutsch",
        }


@app.put("/api/export-settings")
async def update_export_settings(request: Request) -> dict[str, Any]:
    payload = await request.json()
    label = str(payload.get("home_language_label") or "").strip() or "Deutsch"
    with connect() as con:
        set_setting(con, "home_language_label", label[:40])
        con.commit()
    return {"ok": True, "home_language_label": label[:40]}


@app.get("/api/database")
def database_info() -> dict[str, Any]:
    db = active_db_path()
    return {
        "active": str(db),
        "active_name": db.name,
        "example": str(EXAMPLE_DB),
        "is_example": db.resolve() == EXAMPLE_DB.resolve(),
        "exists": db.exists(),
    }


@app.post("/api/database/use-example")
def use_example_database() -> dict[str, Any]:
    if not EXAMPLE_DB.exists():
        raise HTTPException(status_code=404, detail="Example database not found")
    db = set_active_db(EXAMPLE_DB)
    return database_payload(db)


@app.post("/api/database/create")
async def create_database(request: Request) -> dict[str, Any]:
    payload = await request.json()
    filename = sanitize_database_name(payload.get("name"))
    path = DATA / filename
    if path.resolve() == EXAMPLE_DB.resolve():
        raise HTTPException(status_code=400, detail="Choose a different database name")
    try:
        create_blank_database(path)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"Database already exists: {filename}") from None
    db = set_active_db(path)
    return database_payload(db)


@app.post("/api/database/use")
async def use_database(request: Request) -> dict[str, Any]:
    payload = await request.json()
    raw_path = str(payload.get("path") or "").strip()
    if not raw_path:
        raise HTTPException(status_code=400, detail="Choose a database file.")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    try:
        validate_database(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Database not found: {path}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db = set_active_db(path)
    return database_payload(db)


@app.post("/api/database/choose")
def choose_database() -> dict[str, Any]:
    if not shutil.which("osascript"):
        raise HTTPException(status_code=501, detail="Native file chooser is not available on this system.")
    result = subprocess.run(
        [
            "osascript",
            "-e",
            'POSIX path of (choose file with prompt "Choose a VitaMine database")',
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        if "User canceled" in message or result.returncode == 1:
            return {"ok": False, "cancelled": True}
        raise HTTPException(status_code=500, detail=message or "Could not open the file chooser.")
    path = Path(result.stdout.strip()).expanduser()
    try:
        validate_database(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Database not found: {path}") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db = set_active_db(path)
    return database_payload(db)


@app.post("/api/database/import")
async def import_database(file: UploadFile = File(...)) -> dict[str, Any]:
    filename = sanitize_database_name(file.filename or "workspace.vitamine")
    if filename.endswith(".sqlite"):
        filename = f"{Path(filename).stem}.vitamine"
    path = DATA / filename
    upload_path = unique_database_path(f".{Path(filename).stem}.upload{Path(filename).suffix}")
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        with upload_path.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        validate_database(upload_path)
        if path.exists():
            validate_database(path)
            if filecmp.cmp(upload_path, path, shallow=False):
                upload_path.unlink(missing_ok=True)
            else:
                upload_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"A database named {path.name} already exists. "
                        "It was not duplicated; load the existing database or choose a different filename."
                    ),
                )
        else:
            upload_path.replace(path)
    except ValueError as exc:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Could not import database: {exc}") from exc
    finally:
        await file.close()
    db = set_active_db(path)
    return database_payload(db)


def cv_import_settings(con: sqlite3.Connection, include_secret: bool = False) -> dict[str, Any]:
    settings: dict[str, Any] = {
        key: get_setting(con, f"cv_import_{key}") or default
        for key, default in CV_IMPORT_SETTING_FIELDS.items()
    }
    api_key = get_setting(con, "cv_import_api_key") or os.environ.get("OPENAI_API_KEY") or ""
    settings["api_key_set"] = bool(api_key)
    if include_secret:
        settings["api_key"] = api_key
    return settings


def cv_import_upload_name(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem).strip("._-") or "uploaded-cv"
    if suffix not in {".docx", ".pdf", ".txt", ".md"}:
        raise HTTPException(status_code=400, detail="Please upload a DOCX, PDF, TXT, or Markdown CV.")
    return f"{stem}{suffix}"


@app.get("/api/cv-import/settings")
def get_cv_import_settings() -> dict[str, Any]:
    with connect() as con:
        return cv_import_settings(con, include_secret=False)


@app.put("/api/cv-import/settings")
async def update_cv_import_settings(request: Request) -> dict[str, Any]:
    payload = await request.json()
    provider = str(payload.get("provider") or "none").strip()
    if provider not in {"none", "bundled_llama", "ollama", "openai", "openai_compatible"}:
        raise HTTPException(status_code=400, detail="Unsupported CV import provider")
    with connect() as con:
        set_setting(con, "cv_import_provider", provider)
        for key, default in CV_IMPORT_SETTING_FIELDS.items():
            if key == "provider":
                continue
            value = str(payload.get(key) or default).strip()
            set_setting(con, f"cv_import_{key}", value)
        api_key = str(payload.get("api_key") or "").strip()
        if api_key:
            set_setting(con, "cv_import_api_key", api_key)
        con.commit()
        return cv_import_settings(con, include_secret=False)


@app.post("/api/cv-import/upload")
async def upload_cv_import(files: list[UploadFile] = File(...)) -> JSONResponse:
    if not files:
        raise HTTPException(status_code=400, detail="Please choose at least one CV document.")
    upload_dir = DATA / "cv-imports"
    upload_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    saved_paths: list[Path] = []
    results: list[dict[str, Any]] = []
    try:
        with connect() as con:
            settings = cv_import_settings(con, include_secret=True)
            for index, file in enumerate(files, start=1):
                filename = cv_import_upload_name(file.filename or f"uploaded-cv-{index}")
                upload_path = upload_dir / f"{timestamp}-{index}-{filename}"
                with upload_path.open("wb") as handle:
                    shutil.copyfileobj(file.file, handle)
                saved_paths.append(upload_path)
                results.append(import_cv_file(con, upload_path, file.filename or filename, settings))
            con.commit()
    except HTTPException:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise
    except RuntimeError as exc:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        for path in saved_paths:
            path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Could not import CV: {exc}") from exc
    finally:
        for file in files:
            await file.close()
    warnings = [warning for result in results for warning in result.get("warnings", [])]
    return JSONResponse(
        {
            "ok": True,
            "documents_imported": len(results),
            "entries_inserted": sum(int(result.get("entries_inserted") or 0) for result in results),
            "contributions_inserted": sum(int(result.get("contributions_inserted") or 0) for result in results),
            "person_fields": max([int(result.get("person_fields") or 0) for result in results] or [0]),
            "used_llm": any(bool(result.get("used_llm")) for result in results),
            "provider": results[0].get("provider") if results else "none",
            "warnings": warnings,
            "results": results,
        }
    )


@app.post("/api/actions/sync-zotero")
def sync_zotero_action() -> JSONResponse:
    result = run_script("sync_zotero.py")
    if result.returncode != 0:
        return JSONResponse({"ok": False, "stderr": result.stderr[-4000:]}, status_code=500)
    return JSONResponse({"ok": True, "stdout": result.stdout})


@app.post("/api/actions/sync-publication-sources")
def sync_publication_sources_action() -> JSONResponse:
    with connect() as con:
        policy = publication_source_policy(con)
    results: list[dict[str, Any]] = []
    for source in PUBLICATION_SOURCE_POLICIES[policy]:
        result = run_publication_source(source)
        results.append(
            {
                "source": source,
                "ok": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        if result.returncode != 0:
            return JSONResponse(
                {
                    "ok": False,
                    "policy": policy,
                    "results": results,
                    "stderr": result.stderr[-4000:] or f"{source} sync failed.",
                },
                status_code=500,
            )
    return JSONResponse(
        {
            "ok": True,
            "policy": policy,
            "results": results,
            "stdout": "\n".join(
                f"[{row['source']}]\n{row['stdout'].strip()}" for row in results if row["stdout"].strip()
            ),
        }
    )


@app.post("/api/actions/maintain-publications")
def maintain_publications_action() -> JSONResponse:
    result = maintain()
    return JSONResponse({"ok": True, **result})


@app.post("/api/actions/fetch-journal-metrics")
def fetch_journal_metrics_action() -> JSONResponse:
    result = run_script("fetch_journal_metrics.py")
    if result.returncode != 0:
        return JSONResponse({"ok": False, "stderr": result.stderr[-4000:]}, status_code=500)
    return JSONResponse({"ok": True, "stdout": result.stdout})


@app.post("/api/actions/enrich-doi")
def enrich_doi_action() -> JSONResponse:
    result = run_script("enrich_publications_by_doi.py")
    if result.returncode != 0:
        return JSONResponse({"ok": False, "stderr": result.stderr[-4000:]}, status_code=500)
    return JSONResponse({"ok": True, "stdout": result.stdout, "report": "/output/doi_enrichment_report.json"})


@app.post("/api/actions/sync-orcid")
def sync_orcid_action() -> JSONResponse:
    result = run_script("sync_orcid.py")
    if result.returncode != 0:
        return JSONResponse({"ok": False, "stderr": result.stderr[-4000:]}, status_code=500)
    return JSONResponse({"ok": True, "stdout": result.stdout})


@app.post("/api/actions/build-long")
def build_long_action(lang: str = "en") -> JSONResponse:
    lang = "de" if lang == "de" else "en"
    result = run_script("build_long_cv.py", "--lang", lang)
    if result.returncode != 0:
        return JSONResponse({"ok": False, "stderr": result.stderr[-4000:]}, status_code=500)
    cache_key = str(int(time.time()))
    return JSONResponse(build_response(result.stdout, cache_key, {"language": lang}))


@app.post("/api/actions/build-short")
def build_short_action(lang: str = "en") -> JSONResponse:
    lang = "de" if lang == "de" else "en"
    curated = run_script("curate_short_cv.py")
    if curated.returncode != 0:
        return JSONResponse({"ok": False, "stderr": curated.stderr[-4000:]}, status_code=500)
    result = run_script("build_short_cv.py", "--lang", lang)
    if result.returncode != 0:
        return JSONResponse({"ok": False, "stderr": result.stderr[-4000:]}, status_code=500)
    cache_key = str(int(time.time()))
    return JSONResponse(build_response(curated.stdout + result.stdout, cache_key, {"language": lang}))


@app.post("/api/actions/build-ultrashort-tabular")
def build_ultrashort_tabular_action(lang: str = "en") -> JSONResponse:
    lang = "de" if lang == "de" else "en"
    result = run_script("build_ultrashort_tabular_cv.py", "--lang", lang)
    if result.returncode != 0:
        return JSONResponse({"ok": False, "stderr": result.stderr[-4000:]}, status_code=500)
    cache_key = str(int(time.time()))
    return JSONResponse(build_response(result.stdout, cache_key, {"language": lang}))


@app.post("/api/actions/build-biosketch")
def build_biosketch_action(lang: str = "en") -> JSONResponse:
    lang = "de" if lang == "de" else "en"
    imported_stdout = ""
    with connect() as con:
        contribution_count = int(con.execute("SELECT COUNT(*) FROM biosketch_contributions").fetchone()[0])
    if contribution_count == 0:
        imported = run_script("import_biosketch_contributions.py")
        if imported.returncode != 0:
            return JSONResponse({"ok": False, "stderr": imported.stderr[-4000:]}, status_code=500)
        imported_stdout = imported.stdout
    result = run_script("build_biosketch.py", "--lang", lang)
    if result.returncode != 0:
        return JSONResponse({"ok": False, "stderr": result.stderr[-4000:]}, status_code=500)
    cache_key = str(int(time.time()))
    return JSONResponse(build_response(imported_stdout + result.stdout, cache_key, {"language": lang}))


@app.post("/api/actions/build-harvard")
def build_harvard_action() -> JSONResponse:
    return build_long_action()


@app.get("/output/{filename}")
def output_file(filename: str) -> FileResponse:
    path = OUTPUT / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Output not found")
    return FileResponse(path, headers={"Cache-Control": "no-store, max-age=0"})


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": active_db_path().exists(), "app": "vitamine"}
