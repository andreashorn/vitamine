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
import tempfile
import threading
import time
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .i18n import GERMAN_FIELD_PAIRS, fill_german_drafts
from .citation_styles import CITATION_STYLES, DEFAULT_CITATION_STYLE, validate_citation_style
from .metadata_text import decode_metadata_text, decode_publication_payload
from .identifiers import normalize_identifier
from .profile_resolver import resolve_profiles
from .deployment import (
    llm_policy,
    llm_user_configuration_allowed,
    managed_llm,
    skip_llm_onboarding,
)
from .paths import (
    BUNDLED_METRICS_CSV,
    DATA,
    DEFAULT_DB,
    EXAMPLE_DB,
    LOGO,
    METRICS_CSV,
    OUTPUT,
    ROOT,
    SCRIPTS,
    STATIC,
    active_db_path,
    bundled_model_path,
    create_blank_database,
    read_preferences,
    sanitize_database_name,
    set_active_db,
    output_ref,
    validate_database,
    write_preferences,
)
from .portrait import MAX_PORTRAIT_UPLOAD_BYTES, normalize_portrait_image
from .scripts.maintain_publications import maintain
from .scripts.enrich_publications_by_doi import (
    authoritative_metadata as authoritative_registry_metadata,
    crossref_metadata as registry_crossref_metadata,
    pubmed_metadata as registry_pubmed_metadata,
    pubmed_pmid as registry_pubmed_pmid,
)
from .scripts.import_uploaded_cv import (
    PERSON_FIELDS as CV_PERSON_FIELDS,
    llm_json,
    llm_extract,
    existing_entry_id as existing_cv_entry_id,
    normalize_contribution as normalize_cv_contribution,
    normalize_llm_entry as normalize_cv_entry,
    normalize_publication as normalize_cv_publication,
    stage_import_candidates,
    store_profile_candidates,
    import_cv_file,
)
from .enrichment_guard import (
    ensure_discovery_rejections_table,
    forget_rejection,
    guard_publications,
    remember_rejection,
    review_nonpublications,
)
from .export_quality import run_export_quality_audit
from .custom_docx_templates import (
    MAX_TEMPLATE_BYTES,
    analyze_and_skeletonize,
    render_template as render_custom_docx_template,
    template_sha256,
)
from .cv_dates import cv_entry_sort_key


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

LEGACY_OWN_INSTITUTION = {
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

LLM_POLICY = llm_policy()

CV_IMPORT_SETTING_FIELDS = {
    "provider": str(LLM_POLICY.get("provider") or "bundled_llama"),
    "ollama_url": "http://127.0.0.1:11434",
    "ollama_model": "llama3.1:8b",
    "api_base_url": str(LLM_POLICY.get("api_base_url") or "https://api.openai.com/v1"),
    "api_model": str(LLM_POLICY.get("api_model") or "gpt-4.1-mini"),
    "api_reasoning_effort": str(LLM_POLICY.get("api_reasoning_effort") or ""),
    "api_max_tokens": str(LLM_POLICY.get("api_max_tokens") or "4096"),
    "bundled_llama_model_path": "",
    "bundled_llama_ctx_size": "4096",
}

LONG_CV_PUBLICATION_CATEGORIES = {
    "peer_reviewed": "Peer-reviewed publications",
    "patents": "Patents",
    "books_chapters": "Books / book chapters",
    "preprints": "Preprints",
    "manuscripts_in_preparation": "Manuscripts in preparation",
    "poster_presentations": "Poster presentations",
}

DEFAULT_LONG_CV_PUBLICATION_CATEGORIES = {"peer_reviewed", "patents"}
EXPORT_FORMAT_CATALOG = STATIC / "export-formats.json"
EXPORT_CONTENT_PROFILES = {
    "long": {
        "label": "Long CV",
        "description": "Uses the comprehensive CV content selection.",
    },
    "short": {
        "label": "Short CV",
        "description": "Uses the selected-content Short CV routine.",
    },
    "one_page": {
        "label": "One-page CV",
        "description": "Uses the tightly selected one-page CV routine.",
    },
    "biosketch": {
        "label": "Biosketch",
        "description": "Uses biosketch-specific statements and contributions to science.",
    },
}
EXPORT_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "max_pages",
        "max_publications",
        "authorship_preference",
        "recency_preference",
        "impact_factor_preference",
        "selected_publication_ids",
        "required_publication_ids",
        "section_strategy",
        "interpretation",
        "warnings",
    ],
    "properties": {
        "max_pages": {"type": ["integer", "null"], "minimum": 1, "maximum": 100},
        "max_publications": {"type": "integer", "minimum": 0, "maximum": 50},
        "authorship_preference": {"type": "string", "enum": ["first_last", "first", "last", "all"]},
        "recency_preference": {"type": "string", "enum": ["strong", "moderate", "none"]},
        "impact_factor_preference": {"type": "string", "enum": ["strong", "moderate", "none"]},
        "selected_publication_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 50},
        "required_publication_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 20},
        "section_strategy": {"type": "string", "enum": ["complete", "compact", "publications_focused"]},
        "interpretation": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
    },
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


def ensure_metadata_entities_decoded(con: sqlite3.Connection) -> None:
    marker = con.execute(
        "SELECT value FROM app_settings WHERE key='metadata_entities_decoded_v1'"
    ).fetchone()
    if marker and marker[0] == "1":
        return
    publication_columns = (
        "authors",
        "title",
        "venue",
        "abstract",
        "extra",
        "raw_citation",
        "short_citation",
        "quality_note",
    )
    for row in con.execute(
        f"SELECT id, {', '.join(publication_columns)} FROM publications"
    ).fetchall():
        values = {
            column: decode_metadata_text(row[column], strip_markup=column == "abstract")
            for column in publication_columns
        }
        if any(str(row[column] or "") != values[column] for column in publication_columns):
            con.execute(
                f"UPDATE publications SET {', '.join(f'{column}=?' for column in publication_columns)} WHERE id=?",
                (*[values[column] for column in publication_columns], row["id"]),
            )
    inbox_rows = con.execute(
        """
        SELECT id, payload_json
        FROM import_inbox_items
        WHERE target_type='publication'
        """
    ).fetchall()
    for row in inbox_rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        decoded = decode_publication_payload(payload)
        subtitle = " · ".join(
            str(decoded.get(field) or "").strip()
            for field in ("year", "venue", "category")
            if str(decoded.get(field) or "").strip()
        )
        con.execute(
            """
            UPDATE import_inbox_items
            SET title=?, subtitle=?, raw_text=?, payload_json=?
            WHERE id=?
            """,
            (
                str(decoded.get("title") or "Publication")[:240],
                subtitle[:500],
                str(decoded.get("raw_citation") or "")[:4000],
                json.dumps(decoded, ensure_ascii=False),
                row["id"],
            ),
        )
    con.execute(
        """
        INSERT INTO app_settings (key, value)
        VALUES ('metadata_entities_decoded_v1', '1')
        ON CONFLICT(key) DO UPDATE SET value='1'
        """
    )


def connect() -> sqlite3.Connection:
    db_path = active_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    ensure_person_columns(con)
    ensure_publication_columns(con)
    ensure_collaboration_tables(con)
    ensure_biosketch_tables(con)
    ensure_narrative_report_table(con)
    ensure_import_inbox_table(con)
    ensure_discovery_rejections_table(con)
    ensure_export_settings_table(con)
    ensure_export_templates_table(con)
    ensure_app_settings_table(con)
    ensure_metadata_entities_decoded(con)
    ensure_journal_metrics_table(con)
    con.commit()
    return con


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def rows_dict(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def export_format_catalog() -> list[dict[str, Any]]:
    try:
        payload = json.loads(EXPORT_FORMAT_CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="The bundled export-format catalogue is unavailable.") from exc
    formats = payload.get("formats") if isinstance(payload, dict) else None
    if not isinstance(formats, list):
        raise HTTPException(status_code=500, detail="The bundled export-format catalogue is invalid.")
    valid: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in formats:
        if not isinstance(item, dict):
            continue
        format_id = str(item.get("id") or "").strip()
        if not format_id or format_id in seen:
            continue
        content_profile = str(item.get("content_profile") or "").strip()
        if content_profile not in EXPORT_CONTENT_PROFILES:
            raise HTTPException(
                status_code=500,
                detail=f"Export format {format_id} does not declare one of the four supported content profiles.",
            )
        seen.add(format_id)
        valid.append({
            **item,
            "content_profile": content_profile,
            "content_profile_label": EXPORT_CONTENT_PROFILES[content_profile]["label"],
            "content_profile_description": EXPORT_CONTENT_PROFILES[content_profile]["description"],
        })
    return valid


def installed_export_format_ids(formats: list[dict[str, Any]] | None = None) -> list[str]:
    formats = formats or export_format_catalog()
    allowed = [str(item["id"]) for item in formats]
    prefs = read_preferences()
    stored = prefs.get("installed_export_format_ids")
    if not isinstance(stored, list):
        return [str(item["id"]) for item in formats if item.get("preinstalled")]
    selected = {str(item) for item in stored}
    return [format_id for format_id in allowed if format_id in selected]


def write_installed_export_format_ids(format_ids: list[str], formats: list[dict[str, Any]] | None = None) -> list[str]:
    formats = formats or export_format_catalog()
    requested = set(format_ids)
    ordered = [str(item["id"]) for item in formats if str(item["id"]) in requested]
    prefs = read_preferences()
    prefs["installed_export_format_ids"] = ordered
    write_preferences(prefs)
    return ordered


def export_format_by_id(format_id: str, formats: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    formats = formats or export_format_catalog()
    match = next((item for item in formats if item["id"] == format_id), None)
    if not match:
        raise HTTPException(status_code=404, detail="Unknown export format")
    return match


def parsed_template_blueprint(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def custom_export_format_payload(row: sqlite3.Row) -> dict[str, Any]:
    profile = str(row["content_profile"] or "short")
    blueprint = parsed_template_blueprint(row["blueprint_json"])
    profile_labels = {
        "long": "Long CV",
        "short": "Short CV",
        "one_page": "Ultrashort CV",
        "biosketch": "Biosketch",
    }
    length_labels = {
        "long": "Long · follows the imported Word layout",
        "short": "Short · follows the imported Word layout",
        "one_page": "Ultrashort · usually one page",
        "biosketch": "Biosketch · follows the imported Word layout",
    }
    page_count = blueprint.get("page_count")
    mapped_count = len(blueprint.get("mapped_sections") or [])
    analysis_method = str(blueprint.get("analysis_method") or "deterministic")
    summary = (
        "A private Word-native layout learned from your uploaded CV. "
        f"VitaMine mapped {mapped_count} content section{'s' if mapped_count != 1 else ''} and reuses the original page, table, style, header, and footer structure."
    )
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "summary": summary,
        "length": length_labels.get(profile, length_labels["short"]),
        "focus": ["your Word layout", "current CV content", "editable DOCX"],
        "audience": "Reuse a familiar institutional or personal Word CV design with the current VitaMine data.",
        "preview": "",
        "preinstalled": False,
        "installed": True,
        "custom_template": True,
        "content_profile": profile,
        "content_profile_label": profile_labels.get(profile, "Short CV"),
        "content_profile_description": EXPORT_CONTENT_PROFILES.get(profile, EXPORT_CONTENT_PROFILES["short"])["description"],
        "exporter": "custom_docx",
        "quality": {
            "key": "imported_template",
            "label": "Imported Word template",
            "description": "Word-native layout preserved from a private user upload.",
        },
        "source": {
            "kind": "private_user_docx",
            "title": str(row["source_filename"]),
            "url": "",
            "note": "Stored only inside this private VitaMine CV database.",
        },
        "template_analysis": {
            "method": analysis_method,
            "model": str(blueprint.get("analysis_model") or ""),
            "page_count": page_count,
            "mapped_sections": list(blueprint.get("mapped_sections") or []),
            "classification_reason": str(blueprint.get("classification_reason") or ""),
        },
    }


def custom_export_formats(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT id, name, source_filename, content_profile, blueprint_json, created_at, updated_at
        FROM export_templates
        ORDER BY created_at, lower(name)
        """
    ).fetchall()
    return [custom_export_format_payload(row) for row in rows]


def custom_export_template_row(con: sqlite3.Connection, template_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM export_templates WHERE id=?", (template_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown custom export template")
    return row


def clean_template_name(value: str | None, fallback: str = "My Word CV") -> str:
    name = re.sub(r"\s+", " ", str(value or "")).strip() or fallback
    if len(name) > 100:
        raise HTTPException(status_code=400, detail="Keep the template name below 100 characters.")
    return name



def normalized_institution_name(name: str | None) -> str:
    return " ".join(str(name or "").casefold().split())


def is_own_institution(name: str | None, own_institution: dict[str, Any] | None) -> bool:
    own_name = normalized_institution_name((own_institution or {}).get("name"))
    text = normalized_institution_name(name)
    return bool(own_name and text and (own_name in text or text in own_name))


def own_institution_from_person(person: dict[str, Any] | None) -> dict[str, Any] | None:
    person = person or {}
    def number(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    name = str(person.get("own_institution_name") or "").strip()
    latitude = number(person.get("own_institution_latitude"))
    longitude = number(person.get("own_institution_longitude"))
    if not name or latitude is None or longitude is None:
        return None

    return {
        "id": "own-institution",
        "name": name,
        "country": str(person.get("own_institution_country") or "").strip(),
        "country_code": str(person.get("own_institution_country_code") or "").strip(),
        "latitude": latitude,
        "longitude": longitude,
    }


def clean_orcid_id(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^https?://orcid\.org/", "", text, flags=re.I).strip("/")
    if not re.match(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$", text, flags=re.I):
        return ""
    return text.upper()


def fetch_json_url(url: str, *, timeout: int = 20, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "VitaMine/1.0 (local CV editor)",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"Online lookup failed: {exc}") from exc


def orcid_date_sort_key(value: dict[str, Any] | None) -> tuple[int, int, int]:
    value = value or {}

    def part(name: str, default: int) -> int:
        try:
            return int((value.get(name) or {}).get("value") or default)
        except (TypeError, ValueError):
            return default

    return (part("year", 0), part("month", 0), part("day", 0))


def orcid_affiliation_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for group in payload.get("affiliation-group") or []:
        summaries = group.get("summaries") or []
        for item in summaries:
            summary = item.get("employment-summary") or item.get("education-summary") or item.get("qualification-summary")
            if not isinstance(summary, dict):
                continue
            organization = summary.get("organization") or {}
            address = organization.get("address") or {}
            name = str(organization.get("name") or "").strip()
            city = str(address.get("city") or "").strip()
            country_code = str(address.get("country") or "").strip().upper()
            if not name and not city:
                continue
            disambiguated = organization.get("disambiguated-organization") or {}
            candidates.append(
                {
                    "institution": name,
                    "city": city,
                    "region": str(address.get("region") or "").strip(),
                    "country_code": country_code,
                    "start_date": summary.get("start-date") or {},
                    "end_date": summary.get("end-date"),
                    "ror": (
                        str(disambiguated.get("disambiguated-organization-identifier") or "").strip()
                        if str(disambiguated.get("disambiguation-source") or "").casefold() == "ror"
                        else ""
                    ),
                }
            )
    return candidates


def best_orcid_affiliation(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda row: (
            1 if not row.get("end_date") else 0,
            orcid_date_sort_key(row.get("start_date")),
            row.get("institution") or "",
        ),
        reverse=True,
    )[0]


def geocode_institution(candidate: dict[str, Any]) -> dict[str, Any] | None:
    parts = [
        candidate.get("institution"),
        candidate.get("city"),
        candidate.get("region"),
        candidate.get("country_code"),
    ]
    queries = [", ".join(str(part).strip() for part in parts if str(part or "").strip())]
    if candidate.get("city"):
        queries.append(", ".join(str(part).strip() for part in [candidate.get("city"), candidate.get("country_code")] if str(part or "").strip()))
    for query in dict.fromkeys(q for q in queries if q):
        params = urllib.parse.urlencode({"q": query, "format": "jsonv2", "limit": 1, "addressdetails": 1})
        payload = fetch_json_url(f"https://nominatim.openstreetmap.org/search?{params}", timeout=20)
        if not isinstance(payload, list) or not payload:
            continue
        hit = payload[0]
        try:
            latitude = float(hit.get("lat"))
            longitude = float(hit.get("lon"))
        except (TypeError, ValueError):
            continue
        address = hit.get("address") or {}
        return {
            "latitude": latitude,
            "longitude": longitude,
            "country": address.get("country") or "",
            "country_code": str(address.get("country_code") or candidate.get("country_code") or "").upper(),
            "geocoded_query": query,
        }
    return None


def saved_orcid_id(con: sqlite3.Connection) -> str:
    row = con.execute(
        """
        SELECT COALESCE(
          (SELECT identifier_value FROM person_identifiers WHERE person_id=1 AND lower(platform)='orcid' ORDER BY id LIMIT 1),
          (SELECT orcid_id FROM person WHERE id=1),
          ''
        ) AS orcid_id
        """
    ).fetchone()
    return clean_orcid_id(row["orcid_id"] if row else "")


def save_own_institution(con: sqlite3.Connection, values: dict[str, Any]) -> None:
    row = con.execute("SELECT raw_json FROM person WHERE id=1").fetchone()
    try:
        raw = json.loads((row["raw_json"] if row else "") or "{}")
    except json.JSONDecodeError:
        raw = {}
    raw.update(
        {
            "own_institution_name": values["own_institution_name"],
            "own_institution_country": values["own_institution_country"],
            "own_institution_country_code": values["own_institution_country_code"],
            "own_institution_latitude": values["own_institution_latitude"],
            "own_institution_longitude": values["own_institution_longitude"],
        }
    )
    con.execute(
        """
        INSERT INTO person (
          id, own_institution_name, own_institution_country, own_institution_country_code,
          own_institution_latitude, own_institution_longitude, raw_json
        )
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          own_institution_name=excluded.own_institution_name,
          own_institution_country=excluded.own_institution_country,
          own_institution_country_code=excluded.own_institution_country_code,
          own_institution_latitude=excluded.own_institution_latitude,
          own_institution_longitude=excluded.own_institution_longitude,
          raw_json=excluded.raw_json
        """,
        (
            values["own_institution_name"],
            values["own_institution_country"],
            values["own_institution_country_code"],
            values["own_institution_latitude"],
            values["own_institution_longitude"],
            json.dumps(raw, ensure_ascii=False, indent=2),
        ),
    )


INSTITUTION_MAPPING_FINGERPRINT_SETTING = "own_institution_mapping_fingerprint"
INSTITUTION_MAPPING_STATUS_SETTING = "own_institution_mapping_status"
INSTITUTION_MAPPING_LAST_RUN_SETTING = "own_institution_mapping_last_run"


def institution_mapping_fingerprint(person: dict[str, Any] | sqlite3.Row | None) -> str:
    person = person or {}
    return "|".join(
        normalized_institution_name(person[key] if key in person.keys() else "")
        for key in (
            "own_institution_name",
            "own_institution_country",
            "own_institution_country_code",
        )
    )


def institution_coordinates_complete(person: dict[str, Any] | sqlite3.Row | None) -> bool:
    person = person or {}
    try:
        float(person["own_institution_latitude"])
        float(person["own_institution_longitude"])
    except (KeyError, TypeError, ValueError):
        return False
    return True


def institution_mapping_needed(con: sqlite3.Connection) -> bool:
    person = con.execute(
        """
        SELECT own_institution_name, own_institution_country, own_institution_country_code,
               own_institution_latitude, own_institution_longitude
        FROM person WHERE id=1
        """
    ).fetchone()
    if not person:
        return False
    has_source = bool(str(person["own_institution_name"] or "").strip() or saved_orcid_id(con))
    if not has_source:
        return False
    if not institution_coordinates_complete(person):
        return True
    marker = get_setting(con, INSTITUTION_MAPPING_FINGERPRINT_SETTING)
    if not marker:
        # Existing complete coordinates predate automatic mapping and should be
        # treated as deliberate rather than silently overwritten.
        return False
    _kind, separator, mapped_fingerprint = marker.partition(":")
    return bool(separator and mapped_fingerprint != institution_mapping_fingerprint(person))


def _record_institution_mapping_result(
    db_path: Path,
    *,
    status: str,
    fingerprint: str,
) -> None:
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        ensure_app_settings_table(con)
        set_setting(con, INSTITUTION_MAPPING_STATUS_SETTING, status)
        set_setting(con, INSTITUTION_MAPPING_LAST_RUN_SETTING, timestamp_text())
        set_setting(con, "own_institution_mapping_attempt_fingerprint", fingerprint)
        con.commit()
    finally:
        con.close()


def map_institution_automatically(db_path: Path, *, force: bool = False) -> dict[str, Any]:
    """Map the current institution without overwriting unchanged manual coordinates."""
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        ensure_app_settings_table(con)
        person_row = con.execute(
            """
            SELECT own_institution_name, own_institution_country, own_institution_country_code,
                   own_institution_latitude, own_institution_longitude
            FROM person WHERE id=1
            """
        ).fetchone()
        person = dict(person_row) if person_row else {}
        orcid_id = saved_orcid_id(con)
        fingerprint = institution_mapping_fingerprint(person)
        if not force and not institution_mapping_needed(con):
            return {"ok": True, "mapped": False, "reason": "not_needed"}
    finally:
        con.close()

    institution_name = str(person.get("own_institution_name") or "").strip()
    country = str(person.get("own_institution_country") or "").strip()
    country_code = str(person.get("own_institution_country_code") or "").strip().upper()
    candidate: dict[str, Any] | None = None
    geocoded: dict[str, Any] | None = None
    source = ""

    try:
        if institution_name:
            candidate = {
                "institution": institution_name,
                "city": "",
                "region": country,
                "country_code": country_code,
            }
            geocoded = geocode_institution(candidate)
            source = "saved institution"

        orcid_candidate: dict[str, Any] | None = None
        if not geocoded and orcid_id:
            payload = fetch_json_url(
                f"https://pub.orcid.org/v3.0/{urllib.parse.quote(orcid_id)}/employments"
            )
            orcid_candidate = best_orcid_affiliation(orcid_affiliation_candidates(payload))
            if orcid_candidate:
                if institution_name:
                    candidate = {
                        **orcid_candidate,
                        "institution": institution_name,
                        "country_code": country_code or orcid_candidate.get("country_code") or "",
                    }
                else:
                    candidate = orcid_candidate
                geocoded = geocode_institution(candidate)
                source = "ORCID public employments"
    except Exception as exc:
        _record_institution_mapping_result(
            db_path,
            status=f"error:{type(exc).__name__}",
            fingerprint=fingerprint,
        )
        return {"ok": False, "mapped": False, "reason": "lookup_failed"}

    if not candidate or not geocoded:
        _record_institution_mapping_result(
            db_path,
            status="not_found",
            fingerprint=fingerprint,
        )
        return {"ok": True, "mapped": False, "reason": "not_found"}

    values = {
        "own_institution_name": institution_name or candidate.get("institution") or candidate.get("city") or "",
        "own_institution_country": geocoded.get("country") or country or candidate.get("country_code") or "",
        "own_institution_country_code": geocoded.get("country_code") or country_code or candidate.get("country_code") or "",
        "own_institution_latitude": geocoded["latitude"],
        "own_institution_longitude": geocoded["longitude"],
    }
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        ensure_app_settings_table(con)
        current = con.execute(
            """
            SELECT own_institution_name, own_institution_country, own_institution_country_code
            FROM person WHERE id=1
            """
        ).fetchone()
        if institution_mapping_fingerprint(current) != fingerprint or saved_orcid_id(con) != orcid_id:
            return {"ok": True, "mapped": False, "reason": "source_changed"}
        save_own_institution(con, values)
        mapped_fingerprint = institution_mapping_fingerprint(values)
        set_setting(con, INSTITUTION_MAPPING_FINGERPRINT_SETTING, f"auto:{mapped_fingerprint}")
        set_setting(con, INSTITUTION_MAPPING_STATUS_SETTING, "mapped")
        set_setting(con, INSTITUTION_MAPPING_LAST_RUN_SETTING, timestamp_text())
        set_setting(con, "own_institution_mapping_attempt_fingerprint", mapped_fingerprint)
        con.commit()
    finally:
        con.close()
    return {
        "ok": True,
        "mapped": True,
        "institution": values,
        "source": source,
        "geocoded_query": geocoded.get("geocoded_query") or "",
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


def ensure_import_inbox_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS import_inbox_items (
          id INTEGER PRIMARY KEY,
          document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
          source TEXT NOT NULL,
          target_type TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          confidence TEXT NOT NULL DEFAULT 'medium',
          duplicate_of_type TEXT,
          duplicate_of_id INTEGER,
          title TEXT,
          subtitle TEXT,
          raw_text TEXT,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          reviewed_at TEXT,
          review_note TEXT
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_import_inbox_items_status
        ON import_inbox_items(status, target_type, created_at)
        """
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


def zotero_runtime_api_key(con: sqlite3.Connection) -> str:
    stored_key = get_setting(con, "zotero_api_key")
    environment_key = os.environ.get("ZOTERO_API_KEY") or ""
    if os.environ.get("VITAMINE_CLOUD_WORKER") == "1" and environment_key:
        return environment_key
    return stored_key or environment_key


def zotero_saved_env(con: sqlite3.Connection) -> dict[str, str]:
    api_key = zotero_runtime_api_key(con)
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
        "portrait_image": "BLOB",
        "portrait_mime_type": "TEXT",
        "portrait_filename": "TEXT",
        "portrait_width": "INTEGER",
        "portrait_height": "INTEGER",
    }
    for column, definition in columns.items():
        if column not in existing:
            con.execute(f"ALTER TABLE person ADD COLUMN {column} {definition}")
    row = con.execute(
        """
        SELECT id, raw_json, own_institution_name, own_institution_country, own_institution_country_code,
               own_institution_latitude, own_institution_longitude
        FROM person
        WHERE id=1
        """
    ).fetchone()
    if not row:
        con.execute(
            """
            INSERT OR IGNORE INTO person (
              id, full_name, display_name, raw_json
            )
            VALUES (1, '', '', '{}')
            """
        )
    elif has_legacy_auto_own_institution(row):
        clear_legacy_auto_own_institution(con)
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


def has_legacy_auto_own_institution(row: sqlite3.Row) -> bool:
    try:
        raw = json.loads(row["raw_json"] or "{}")
    except json.JSONDecodeError:
        raw = {}
    if any(str(key).startswith("own_institution_") for key in raw):
        return False
    return (
        row["own_institution_name"] == LEGACY_OWN_INSTITUTION["name"]
        and row["own_institution_country"] == LEGACY_OWN_INSTITUTION["country"]
        and row["own_institution_country_code"] == LEGACY_OWN_INSTITUTION["country_code"]
        and row["own_institution_latitude"] == LEGACY_OWN_INSTITUTION["latitude"]
        and row["own_institution_longitude"] == LEGACY_OWN_INSTITUTION["longitude"]
    )


def clear_legacy_auto_own_institution(con: sqlite3.Connection) -> None:
    con.execute(
        """
        UPDATE person
        SET own_institution_name=NULL,
            own_institution_country=NULL,
            own_institution_country_code=NULL,
            own_institution_latitude=NULL,
            own_institution_longitude=NULL
        WHERE id=1
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
        "openalex_counts_by_year_json": "TEXT",
        "openalex_citation_geography_enriched_at": "TEXT",
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
        """
        CREATE INDEX IF NOT EXISTS idx_citation_institutions_pub
        ON citation_institutions(publication_id)
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_citation_institutions_author
        ON citation_institutions(author_id, author_name)
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


def ensure_export_templates_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS export_templates (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          source_filename TEXT NOT NULL,
          source_docx BLOB NOT NULL,
          content_profile TEXT NOT NULL CHECK (content_profile IN ('long', 'short', 'one_page', 'biosketch')),
          blueprint_json TEXT NOT NULL,
          source_sha256 TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
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


def inbox_payload(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    try:
        item["payload"] = json.loads(item.pop("payload_json") or "{}")
    except json.JSONDecodeError:
        item["payload"] = {}
    return item


def pending_inbox_count(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) FROM import_inbox_items WHERE status='pending'").fetchone()[0])


def h_index(citations: list[int]) -> int:
    score = 0
    for index, count in enumerate(sorted(citations, reverse=True), start=1):
        if count < index:
            break
        score = index
    return score


def mark_inbox_item(con: sqlite3.Connection, item_id: int, status: str, note: str = "") -> None:
    con.execute(
        """
        UPDATE import_inbox_items
        SET status=?, reviewed_at=datetime('now'), review_note=?
        WHERE id=?
        """,
        (status, note, item_id),
    )


def accept_entry_candidate(con: sqlite3.Connection, item: dict[str, Any]) -> tuple[str, int | None]:
    payload = normalize_cv_entry(item["payload"])
    if not payload:
        return "skipped", None
    existing_id = existing_cv_entry_id(con, payload)
    if existing_id:
        return "duplicate", existing_id
    cursor = con.execute(
        f"""
        INSERT INTO cv_entries (document_id, {', '.join(ENTRY_FIELDS)})
        VALUES (?, {', '.join('?' for _ in ENTRY_FIELDS)})
        """,
        (item["document_id"], *[payload.get(field) for field in ENTRY_FIELDS]),
    )
    return "accepted", int(cursor.lastrowid)


def accept_publication_candidate(con: sqlite3.Connection, item: dict[str, Any]) -> tuple[str, int | None]:
    payload = normalize_cv_publication(item["payload"])
    if not payload:
        return "skipped", None
    if item.get("source") == "ai_web_discovery" and not normalize_doi(str(payload.get("doi") or "")):
        return "skipped", None
    if payload.get("doi"):
        existing = con.execute(
            "SELECT id FROM publications WHERE lower(COALESCE(doi, ''))=? LIMIT 1",
            (str(payload["doi"]).casefold(),),
        ).fetchone()
        if existing:
            return "duplicate", int(existing["id"])
    if payload.get("pmid"):
        existing = con.execute("SELECT id FROM publications WHERE pmid=? LIMIT 1", (payload["pmid"],)).fetchone()
        if existing:
            return "duplicate", int(existing["id"])
    existing = con.execute(
        "SELECT id FROM publications WHERE lower(raw_citation)=? LIMIT 1",
        (str(payload["raw_citation"]).casefold(),),
    ).fetchone()
    if existing:
        return "duplicate", int(existing["id"])
    cursor = con.execute(
        f"""
        INSERT INTO publications (document_id, source, {', '.join(PUBLICATION_FIELDS)})
        VALUES (?, ?, {', '.join('?' for _ in PUBLICATION_FIELDS)})
        """,
        (item["document_id"], item["source"], *[payload[field] for field in PUBLICATION_FIELDS]),
    )
    return "accepted", int(cursor.lastrowid)


def inbox_request_ids(payload: dict[str, Any]) -> list[int]:
    ids: list[int] = []
    for value in payload.get("ids") or []:
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            continue
        if item_id > 0:
            ids.append(item_id)
    return ids


def accept_person_candidate(con: sqlite3.Connection, item: dict[str, Any]) -> tuple[str, int | None]:
    payload = item["payload"]
    fields = {field: str(payload.get(field) or "").strip() for field in CV_PERSON_FIELDS if str(payload.get(field) or "").strip()}
    if not fields:
        return "skipped", None
    con.execute("INSERT OR IGNORE INTO person (id, raw_json) VALUES (1, '{}')")
    assignments = ", ".join(f"{field}=?" for field in fields)
    con.execute(f"UPDATE person SET {assignments} WHERE id=1", tuple(fields.values()))
    return "accepted", 1


def accept_identifier_candidate(con: sqlite3.Connection, item: dict[str, Any]) -> tuple[str, int | None]:
    values = identifier_payload(item["payload"])
    existing = con.execute(
        "SELECT id FROM person_identifiers WHERE person_id=1 AND lower(platform)=lower(?) LIMIT 1",
        (values["platform"],),
    ).fetchone()
    if existing:
        return "duplicate", int(existing["id"])
    cursor = con.execute(
        """
        INSERT INTO person_identifiers
          (person_id, platform, identifier_type, identifier_value, url, source, verified_at, notes)
        VALUES (1, ?, ?, ?, ?, ?, datetime('now'), ?)
        """,
        (
            values["platform"], values["identifier_type"], values["identifier_value"], values["url"],
            values["source"] or item.get("source") or "profile resolver", values["notes"],
        ),
    )
    sync_person_orcid_from_identifiers(con)
    return "accepted", int(cursor.lastrowid)


def accept_narrative_candidate(con: sqlite3.Connection, item: dict[str, Any]) -> tuple[str, int | None]:
    payload = item["payload"]
    title = str(payload.get("title") or "Narrative Report").strip() or "Narrative Report"
    body = str(payload.get("body") or "").strip()
    title_de = str(payload.get("title_de") or "").strip()
    body_de = str(payload.get("body_de") or "").strip()
    if not body and not body_de:
        return "skipped", None
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
    return "accepted", 1


def accept_contribution_candidate(con: sqlite3.Connection, item: dict[str, Any]) -> tuple[str, int | None]:
    contribution = dict(item["payload"])
    normalized = normalize_cv_contribution(contribution)
    if not normalized:
        return "skipped", None
    existing = con.execute(
        "SELECT id FROM biosketch_contributions WHERE lower(title)=? AND lower(narrative)=? LIMIT 1",
        (normalized["title"].casefold(), normalized["narrative"].casefold()),
    ).fetchone()
    if existing:
        return "duplicate", int(existing["id"])
    cursor = con.execute(
        """
        INSERT INTO biosketch_contributions (document_id, ordinal, title, narrative, citations_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            item["document_id"],
            contribution.get("ordinal"),
            normalized["title"],
            normalized["narrative"],
            normalized["citations_json"],
        ),
    )
    return "accepted", int(cursor.lastrowid)


def accept_inbox_candidate(con: sqlite3.Connection, item: dict[str, Any]) -> tuple[str, int | None]:
    handlers = {
        "entry": accept_entry_candidate,
        "publication": accept_publication_candidate,
        "person": accept_person_candidate,
        "identifier": accept_identifier_candidate,
        "narrative_report": accept_narrative_candidate,
        "contribution": accept_contribution_candidate,
    }
    handler = handlers.get(str(item["target_type"] or ""))
    if not handler:
        return "skipped", None
    return handler(con, item)


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
        inbox_pending = pending_inbox_count(con)
    return {
        "sections": SECTION_LABELS,
        "entries": entries,
        "publications": publications,
        "warnings": warnings,
        "import_inbox_pending": inbox_pending,
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
                  SUM(CASE WHEN COALESCE(suppress_display, 0) = 0 AND impact_factor IS NOT NULL THEN 1 ELSE 0 END) AS impact_factor_count,
                  SUM(CASE WHEN COALESCE(suppress_display, 0) = 0 AND openalex_cited_by_count IS NOT NULL THEN 1 ELSE 0 END) AS citation_metric_count,
                  SUM(CASE WHEN COALESCE(suppress_display, 0) = 0 THEN COALESCE(openalex_cited_by_count, 0) ELSE 0 END) AS openalex_cited_by_total,
                  SUM(CASE WHEN COALESCE(suppress_display, 0) = 0 AND orcid_put_code IS NOT NULL AND orcid_put_code != '' THEN 1 ELSE 0 END) AS orcid_matched,
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
        citation_rows = rows_dict(
            con.execute(
                """
                SELECT
                  COALESCE(openalex_cited_by_count, 0) AS citations,
                  openalex_counts_by_year_json,
                  authors,
                  year,
                  impact_factor
                FROM publications
                WHERE COALESCE(suppress_display, 0) = 0
                """
            ).fetchall()
        )
        person = con.execute(
            "SELECT full_name, display_name FROM person WHERE id=1"
        ).fetchone()
        name_terms = researcher_name_terms(person)
        citation_counts = [int(row["citations"] or 0) for row in citation_rows]
        first_last_citation_counts: list[int] = []
        since_year = time.localtime().tm_year - 5
        yearly_citations_by_work: list[int] = []
        first_last_yearly_citations_by_work: list[int] = []
        citations_by_year: dict[str, int] = {}
        first_last_citations_by_year: dict[str, int] = {}
        publications_by_year: dict[str, int] = {}
        impact_factor_sum_by_year: dict[str, float] = {}
        impact_factor_count_by_year: dict[str, int] = {}
        for row in citation_rows:
            publication_year = str(row.get("year") or "").strip()[:4]
            if publication_year.isdigit():
                publications_by_year[publication_year] = publications_by_year.get(publication_year, 0) + 1
                if row.get("impact_factor") is not None:
                    impact_factor_sum_by_year[publication_year] = (
                        impact_factor_sum_by_year.get(publication_year, 0.0)
                        + float(row["impact_factor"])
                    )
                    impact_factor_count_by_year[publication_year] = (
                        impact_factor_count_by_year.get(publication_year, 0) + 1
                    )
            first_or_last_author = authorship_matches(
                researcher_authorship(row.get("authors"), name_terms),
                "first_last",
            )
            if first_or_last_author:
                first_last_citation_counts.append(int(row["citations"] or 0))
            yearly_total = 0
            try:
                counts_by_year = json.loads(row.get("openalex_counts_by_year_json") or "[]")
            except json.JSONDecodeError:
                counts_by_year = []
            for item in counts_by_year if isinstance(counts_by_year, list) else []:
                year = str(item.get("year") or "").strip()
                if not year.isdigit():
                    continue
                citations = int(item.get("cited_by_count") or 0)
                citations_by_year[year] = citations_by_year.get(year, 0) + citations
                if first_or_last_author:
                    first_last_citations_by_year[year] = (
                        first_last_citations_by_year.get(year, 0) + citations
                    )
                if int(year) >= since_year:
                    yearly_total += citations
            if yearly_total:
                yearly_citations_by_work.append(yearly_total)
                if first_or_last_author:
                    first_last_yearly_citations_by_work.append(yearly_total)
        citation_years_received = [
            {
                "year": year,
                "citations": citations,
                "first_last_author_citations": first_last_citations_by_year.get(year, 0),
                "publications_published": publications_by_year.get(year, 0),
                "impact_factor_sum": round(impact_factor_sum_by_year.get(year, 0.0), 2),
                "impact_factor_count": impact_factor_count_by_year.get(year, 0),
            }
            for year, citations in sorted(citations_by_year.items(), key=lambda item: int(item[0]))
        ]
    return {
        "publications": publication_metrics or {},
        "by_year": by_year,
        "top_venues": top_venues,
        "impact_factors": impact_factors,
        "journal_metric_count": journal_metric_count(),
        "citation_profile": {
            "since_year": since_year,
            "citation_metric_count": len(citation_counts),
            "all": {
                "citations": sum(citation_counts),
                "h_index": h_index(citation_counts),
                "i10_index": sum(1 for count in citation_counts if count >= 10),
            },
            "since_yearly_citations": {
                "citations": sum(yearly_citations_by_work),
                "h_index": h_index(yearly_citations_by_work),
                "i10_index": sum(1 for count in yearly_citations_by_work if count >= 10),
            },
            "first_last_author": {
                "citations": sum(first_last_citation_counts),
                "h_index": h_index(first_last_citation_counts),
                "i10_index": sum(1 for count in first_last_citation_counts if count >= 10),
            },
            "first_last_author_since_yearly_citations": {
                "citations": sum(first_last_yearly_citations_by_work),
                "h_index": h_index(first_last_yearly_citations_by_work),
                "i10_index": sum(
                    1 for count in first_last_yearly_citations_by_work if count >= 10
                ),
            },
            "by_year": citation_years_received,
        },
    }


@app.get("/api/collaboration-map")
def collaboration_map(mode: str = "collaborations") -> dict[str, Any]:
    mode = str(mode or "collaborations").strip().casefold()
    if mode not in {"collaborations", "citations"}:
        raise HTTPException(status_code=422, detail="Choose collaborations or citations.")
    with connect() as con:
        person = row_dict(con.execute("SELECT * FROM person WHERE id=1").fetchone())
        if mode == "citations":
            citation_rows = rows_dict(con.execute(
                """
                SELECT ci.publication_id, ci.citing_openalex_work_id, ci.citing_work_year,
                       ci.author_id, ci.author_name, ci.institution_id, ci.institution_name,
                       ci.ror, ci.country_code, ci.country, ci.latitude, ci.longitude
                FROM citation_institutions ci
                JOIN publications p ON p.id=ci.publication_id
                WHERE ci.latitude IS NOT NULL AND ci.longitude IS NOT NULL
                  AND COALESCE(p.suppress_display, 0)=0
                ORDER BY ci.institution_name, ci.author_name
                """
            ).fetchall())
        else:
            citation_rows = []
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
    if not own_institution:
        return {
            "own": None,
            "nodes": [],
            "edges": [],
            "top_countries": [],
            "institution_count": 0,
            "edge_count": 0,
            "publication_links": 0,
            "needs_own_institution": True,
            "mode": mode,
        }
    if mode == "citations":
        name_terms = researcher_name_terms(person)
        self_citing_works = {
            str(row["citing_openalex_work_id"] or "")
            for row in citation_rows
            if author_matches_researcher(str(row["author_name"] or ""), name_terms)
        }
        grouped: dict[str, dict[str, Any]] = {}
        all_researchers: set[str] = set()
        sampled_works: set[str] = set()
        for row in citation_rows:
            work_id = str(row["citing_openalex_work_id"] or "")
            if work_id in self_citing_works:
                continue
            institution_id = str(row["institution_id"] or row["institution_name"] or "")
            author_name = str(row["author_name"] or "").strip()
            author_key = str(row["author_id"] or "").strip() or author_name.casefold()
            event = (int(row["publication_id"]), work_id)
            item = grouped.setdefault(institution_id, {
                "id": f"citation:{institution_id}", "name": row["institution_name"],
                "ror": row["ror"], "country": row["country"],
                "country_code": row["country_code"], "latitude": row["latitude"],
                "longitude": row["longitude"], "events": set(), "researchers": {},
            })
            item["events"].add(event)
            item["researchers"].setdefault(author_key, {"name": author_name, "events": set()})["events"].add(event)
            all_researchers.add(author_key)
            sampled_works.add(work_id)
        ranked = sorted(grouped.values(), key=lambda item: (-len(item["events"]), str(item["name"]).casefold()))[:250]
        nodes = [{**own_institution, "own": True}]
        edges = []
        country_counts: dict[str, int] = {}
        citation_total = 0
        for item in ranked:
            count = len(item.pop("events"))
            researchers = sorted(
                ({"name": value["name"], "citation_count": len(value["events"])} for value in item.pop("researchers").values()),
                key=lambda value: (-value["citation_count"], value["name"].casefold()),
            )
            item.update({"researchers": researchers[:20], "researcher_count": len(researchers),
                         "citation_count": count, "own": False})
            nodes.append(item)
            edges.append({"source": own_institution["id"], "target": item["id"], "weight": count})
            citation_total += count
            country = item["country"] or item["country_code"] or "Unknown"
            country_counts[country] = country_counts.get(country, 0) + count
        return {
            "mode": mode, "own": own_institution, "nodes": nodes, "edges": edges,
            "top_countries": [{"country": country, "citation_count": count} for country, count in sorted(country_counts.items(), key=lambda value: value[1], reverse=True)[:10]],
            "institution_count": len(ranked), "researcher_count": len(all_researchers),
            "citation_links": citation_total, "sampled_works": len(sampled_works),
            "needs_own_institution": False,
        }
    nodes = [{**own_institution, "own": True, "publication_count": 0, "author_count": 1, "authors": []}]
    edges = []
    country_counts: dict[str, int] = {}
    publication_total = 0
    for row in rows:
        if is_own_institution(row["institution_name"], own_institution):
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
        "mode": mode,
        "own": own_institution,
        "nodes": nodes,
        "edges": edges,
        "top_countries": top_countries,
        "institution_count": max(len(nodes) - 1, 0),
        "edge_count": len(edges),
        "publication_links": publication_total,
        "needs_own_institution": False,
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
    if not person:
        return {}
    portrait = person.pop("portrait_image", None)
    person["portrait_available"] = bool(portrait)
    return person


@app.get("/api/person/portrait")
def get_person_portrait() -> Response:
    with connect() as con:
        row = con.execute(
            """
            SELECT portrait_image, portrait_mime_type
            FROM person
            WHERE id=1
            """
        ).fetchone()
    if row is None or not row["portrait_image"]:
        raise HTTPException(status_code=404, detail="No profile picture has been added.")
    return Response(
        content=bytes(row["portrait_image"]),
        media_type=str(row["portrait_mime_type"] or "application/octet-stream"),
        headers={"Cache-Control": "no-store"},
    )


@app.put("/api/person/portrait")
async def update_person_portrait(file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read(MAX_PORTRAIT_UPLOAD_BYTES + 1)
    await file.close()
    try:
        normalized = normalize_portrait_image(data, filename=file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    metadata = normalized.metadata
    with connect() as con:
        con.execute("INSERT OR IGNORE INTO person (id, raw_json) VALUES (1, '{}')")
        con.execute(
            """
            UPDATE person
            SET portrait_image=?,
                portrait_mime_type=?,
                portrait_filename=?,
                portrait_width=?,
                portrait_height=?
            WHERE id=1
            """,
            (
                normalized.data,
                metadata.mime_type,
                metadata.filename,
                metadata.width,
                metadata.height,
            ),
        )
        con.commit()
    return {
        "ok": True,
        "portrait_available": True,
        "portrait_mime_type": metadata.mime_type,
        "portrait_filename": metadata.filename,
        "portrait_width": metadata.width,
        "portrait_height": metadata.height,
    }


@app.delete("/api/person/portrait")
def delete_person_portrait() -> dict[str, Any]:
    with connect() as con:
        con.execute(
            """
            UPDATE person
            SET portrait_image=NULL,
                portrait_mime_type=NULL,
                portrait_filename=NULL,
                portrait_width=NULL,
                portrait_height=NULL
            WHERE id=1
            """
        )
        con.commit()
    return {"ok": True, "portrait_available": False}


@app.get("/api/person/institution-mapping-status")
def institution_mapping_status() -> dict[str, Any]:
    with connect() as con:
        person = row_dict(con.execute("SELECT * FROM person WHERE id=1").fetchone()) or {}
        status = get_setting(con, INSTITUTION_MAPPING_STATUS_SETTING)
        pending = institution_mapping_needed(con)
    return {
        "ok": True,
        "status": status,
        "pending": pending,
        "mapped": bool(own_institution_from_person(person)),
    }


@app.post("/api/person/auto-map-institution")
def auto_map_institution() -> dict[str, Any]:
    result = map_institution_automatically(active_db_path().resolve(), force=True)
    if result.get("mapped"):
        return result
    if result.get("reason") == "lookup_failed":
        raise HTTPException(status_code=502, detail="The institution lookup is temporarily unavailable.")
    raise HTTPException(status_code=404, detail="No mappable institution could be found.")


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
        api_key = zotero_runtime_api_key(con)
        library_type = get_setting(con, "zotero_library_type") or "users"
        library_id = get_setting(con, "zotero_library_id")
        stored_policy = get_setting(con, "publication_source_policy") or "zotero_primary_orcid_validation"
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
            "publication_source_policy": stored_policy,
            "effective_publication_source_policy": publication_source_policy(con),
        }


@app.put("/api/connections")
async def update_connections(request: Request) -> dict[str, Any]:
    payload = await request.json()
    orcid_id = str(payload.get("orcid_id") or "").strip()
    api_key = str(payload.get("zotero_api_key") or "").strip()
    if api_key and os.environ.get("VITAMINE_CLOUD_WORKER") == "1":
        raise HTTPException(status_code=403, detail="Connect Zotero securely from the hosted VitaMine workspace.")
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
        effective_key = api_key or zotero_runtime_api_key(con)
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
    if orcid_id:
        schedule_background_refresh(profiles=True)
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
        consolidate_person_identifiers(con)
        con.commit()
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


def identifier_source_rank(source: str) -> int:
    key = (source or "").casefold()
    if "manual" in key or "onboarding" in key:
        return 4
    if "orcid" in key:
        return 3
    if "verified" in key:
        return 2
    return 1


def consolidate_person_identifiers(con: sqlite3.Connection) -> int:
    """Canonicalize profile rows and retain one best record per service.

    A researcher may have only one current profile per canonical service in the
    UI. Prefer explicit values and authoritative/manual sources; fill missing
    values from service URLs when their URL format is unambiguous.
    """
    rows = rows_dict(
        con.execute(
            """
            SELECT id, platform, identifier_type, identifier_value, url, source, verified_at, notes
            FROM person_identifiers WHERE person_id=1 ORDER BY id
            """
        ).fetchall()
    )
    groups: dict[str, list[dict[str, Any]]] = {}
    for raw in rows:
        row = normalize_identifier(raw)
        groups.setdefault(str(row["platform"]).casefold(), []).append(row)
    removed = 0
    for candidates in groups.values():
        winner = max(
            candidates,
            key=lambda row: (
                1 if row.get("identifier_value") else 0,
                identifier_source_rank(str(row.get("source") or "")),
                str(row.get("verified_at") or ""),
                int(row["id"]),
            ),
        )
        losers = [row for row in candidates if row["id"] != winner["id"]]
        if losers:
            con.executemany(
                "DELETE FROM person_identifiers WHERE person_id=1 AND id=?",
                [(row["id"],) for row in losers],
            )
            removed += len(losers)
        con.execute(
            """
            UPDATE person_identifiers
            SET platform=?, identifier_type=?, identifier_value=?, url=?, source=?, notes=?
            WHERE person_id=1 AND id=?
            """,
            (
                winner["platform"],
                winner["identifier_type"],
                winner.get("identifier_value"),
                winner["url"],
                winner["source"],
                winner.get("notes"),
                winner["id"],
            ),
        )
    sync_person_orcid_from_identifiers(con)
    return removed


def identifier_payload(payload: dict[str, Any]) -> dict[str, str | None]:
    normalized = normalize_identifier(payload)
    platform = str(normalized.get("platform") or "").strip()
    identifier_type = str(payload.get("identifier_type") or "").strip()
    identifier_value = str(normalized.get("identifier_value") or "").strip() or None
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
            schedule_background_refresh(profiles=True)
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
            schedule_background_refresh(profiles=True)
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

    def coordinate_pair(person: dict[str, Any] | sqlite3.Row | None) -> tuple[float | None, float | None]:
        person = person or {}
        numbers: list[float | None] = []
        for field in ("own_institution_latitude", "own_institution_longitude"):
            try:
                value = person[field] if field in person.keys() else None
                numbers.append(float(value) if str(value or "").strip() else None)
            except (TypeError, ValueError):
                numbers.append(None)
        return numbers[0], numbers[1]

    with connect() as con:
        previous = con.execute(
            """
            SELECT own_institution_name, own_institution_country, own_institution_country_code,
                   own_institution_latitude, own_institution_longitude
            FROM person WHERE id=1
            """
        ).fetchone()
        previous_marker = get_setting(con, INSTITUTION_MAPPING_FINGERPRINT_SETTING)
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
        current = con.execute(
            """
            SELECT own_institution_name, own_institution_country, own_institution_country_code,
                   own_institution_latitude, own_institution_longitude
            FROM person WHERE id=1
            """
        ).fetchone()
        coordinates_changed = coordinate_pair(previous) != coordinate_pair(current)
        if institution_coordinates_complete(current) and (coordinates_changed or not previous_marker):
            set_setting(
                con,
                INSTITUTION_MAPPING_FINGERPRINT_SETTING,
                f"manual:{institution_mapping_fingerprint(current)}",
            )
        mapping_pending = institution_mapping_needed(con)
        con.commit()
    if mapping_pending:
        schedule_background_refresh(institutions=True)
    return {"ok": True, "institution_mapping_pending": mapping_pending}


@app.get("/api/narrative-report")
def get_narrative_report() -> dict[str, Any]:
    with connect() as con:
        report = row_dict(con.execute("SELECT * FROM narrative_reports WHERE id=1").fetchone())
    return report or {"id": 1, "title": "Narrative Report", "body": "", "title_de": "Freie Stellungname", "body_de": ""}


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
    with connect() as con:
        rows = rows_dict(
            con.execute(
                f"""
                SELECT * FROM cv_entries
                {where}
                """,
                params,
            ).fetchall()
        )
        rows.sort(key=cv_entry_sort_key)
        if limit >= 0:
            rows = rows[:limit]
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


@app.get("/api/import-inbox")
def list_import_inbox(
    status: str = "pending",
    target_type: str = "all",
    limit: int = 500,
) -> dict[str, Any]:
    if status not in {"pending", "accepted", "rejected", "skipped", "all"}:
        raise HTTPException(status_code=400, detail="Unsupported inbox status")
    if target_type not in {"all", "entry", "publication", "person", "identifier", "narrative_report", "contribution"}:
        raise HTTPException(status_code=400, detail="Unsupported inbox type")
    clauses = []
    params: list[Any] = []
    if status != "all":
        clauses.append("i.status=?")
        params.append(status)
    if target_type != "all":
        clauses.append("i.target_type=?")
        params.append(target_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(limit, 2000)))
    with connect() as con:
        rows = rows_dict(
            con.execute(
                f"""
                SELECT i.*, d.title AS document_title
                FROM import_inbox_items i
                LEFT JOIN documents d ON d.id = i.document_id
                {where}
                ORDER BY
                  CASE i.confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                  i.target_type,
                  i.id
                LIMIT ?
                """,
                params,
            ).fetchall()
        )
        counts = rows_dict(
            con.execute(
                """
                SELECT status, target_type, COUNT(*) AS count
                FROM import_inbox_items
                GROUP BY status, target_type
                ORDER BY status, target_type
                """
            ).fetchall()
        )
    return {"items": [inbox_payload(row) for row in rows], "counts": counts}


@app.post("/api/import-inbox/accept")
async def accept_import_inbox(request: Request) -> dict[str, Any]:
    payload = await request.json()
    ids = inbox_request_ids(payload)
    if not ids:
        raise HTTPException(status_code=400, detail="Choose at least one inbox item.")
    accepted = 0
    publications_accepted = 0
    person_accepted = False
    orcid_accepted = False
    skipped = 0
    duplicates = 0
    with connect() as con:
        placeholders = ", ".join("?" for _ in ids)
        rows = rows_dict(
            con.execute(
                f"SELECT * FROM import_inbox_items WHERE status IN ('pending', 'rejected') AND id IN ({placeholders}) ORDER BY id",
                ids,
            ).fetchall()
        )
        for row in rows:
            item = inbox_payload(row)
            status, target_id = accept_inbox_candidate(con, item)
            if status == "accepted":
                accepted += 1
                if item["target_type"] == "publication":
                    publications_accepted += 1
                elif item["target_type"] == "person":
                    person_accepted = True
                    orcid_accepted = orcid_accepted or bool(clean_orcid_id(item["payload"].get("orcid_id")))
                elif item["target_type"] == "identifier":
                    orcid_accepted = orcid_accepted or str(item["payload"].get("platform") or "").casefold() == "orcid"
                mark_inbox_item(con, int(item["id"]), "accepted", f"Imported as {item['target_type']} {target_id}.")
            elif status == "duplicate":
                duplicates += 1
                mark_inbox_item(con, int(item["id"]), "skipped", f"Skipped duplicate of existing record {target_id}.")
                if item.get("source") == "ai_web_discovery":
                    remember_rejection(con, item["target_type"], item["payload"], item["source"], f"Duplicate of existing record {target_id}.")
            else:
                skipped += 1
                mark_inbox_item(con, int(item["id"]), "skipped", "Skipped; candidate did not contain enough usable data.")
                if item.get("source") == "ai_web_discovery":
                    remember_rejection(con, item["target_type"], item["payload"], item["source"], "Candidate did not contain enough usable data.")
        con.commit()
        pending = pending_inbox_count(con)
    if publications_accepted or person_accepted:
        schedule_background_refresh(
            publications_changed=bool(publications_accepted),
            profiles=orcid_accepted,
            institutions=person_accepted,
        )
    return {"ok": True, "accepted": accepted, "duplicates": duplicates, "skipped": skipped, "pending": pending}


@app.post("/api/import-inbox/reject")
async def reject_import_inbox(request: Request) -> dict[str, Any]:
    payload = await request.json()
    ids = inbox_request_ids(payload)
    if not ids:
        raise HTTPException(status_code=400, detail="Choose at least one inbox item.")
    with connect() as con:
        placeholders = ", ".join("?" for _ in ids)
        rows = con.execute(
            f"SELECT * FROM import_inbox_items WHERE status='pending' AND id IN ({placeholders})",
            ids,
        ).fetchall()
        for row in rows:
            item = inbox_payload(dict(row))
            if item["target_type"] == "publication":
                remember_rejection(con, item["target_type"], item["payload"], item["source"], "Rejected by user.")
        cursor = con.execute(
            f"""
            UPDATE import_inbox_items
            SET status='rejected', reviewed_at=datetime('now'), review_note='Rejected by user.'
            WHERE status='pending' AND id IN ({placeholders})
            """,
            ids,
        )
        con.commit()
        pending = pending_inbox_count(con)
    return {"ok": True, "rejected": cursor.rowcount, "pending": pending}


@app.post("/api/import-inbox/restore")
async def restore_import_inbox(request: Request) -> dict[str, Any]:
    payload = await request.json()
    ids = inbox_request_ids(payload)
    if not ids:
        raise HTTPException(status_code=400, detail="Choose at least one inbox item.")
    with connect() as con:
        placeholders = ", ".join("?" for _ in ids)
        rows = con.execute(
            f"SELECT * FROM import_inbox_items WHERE status='rejected' AND id IN ({placeholders})",
            ids,
        ).fetchall()
        for row in rows:
            item = inbox_payload(dict(row))
            if item["target_type"] == "publication":
                forget_rejection(con, item["target_type"], item["payload"])
        cursor = con.execute(
            f"""
            UPDATE import_inbox_items
            SET status='pending', reviewed_at=NULL, review_note='Restored by user.'
            WHERE status='rejected' AND id IN ({placeholders})
            """,
            ids,
        )
        con.commit()
        pending = pending_inbox_count(con)
    return {"ok": True, "restored": cursor.rowcount, "pending": pending}


@app.post("/api/import-inbox/resolve-publications")
async def resolve_import_inbox_publications(request: Request) -> dict[str, Any]:
    payload = await request.json()
    ids = inbox_request_ids(payload)
    if not ids:
        raise HTTPException(status_code=400, detail="Choose at least one publication candidate.")
    resolved = 0
    existing_count = 0
    unresolved = 0
    results: list[dict[str, Any]] = []
    with connect() as con:
        placeholders = ", ".join("?" for _ in ids)
        rows = rows_dict(
            con.execute(
                f"""
                SELECT *
                FROM import_inbox_items
                WHERE target_type='publication'
                  AND status IN ('pending', 'rejected')
                  AND id IN ({placeholders})
                ORDER BY id
                """,
                ids,
            ).fetchall()
        )
        for row in rows:
            item = inbox_payload(row)
            metadata, lookup_kind, lookup_value = resolve_publication_candidate_metadata(item)
            if not metadata.get("title") and not metadata.get("raw_citation"):
                unresolved += 1
                results.append({"id": item["id"], "status": "unresolved", "lookup": lookup_kind, "value": lookup_value})
                continue
            normalized = normalize_publication(metadata)
            duplicate = existing_publication_for_identifier(con, doi=normalized.get("doi") or "", pmid=normalized.get("pmid") or "")
            duplicate_id = int(duplicate["id"]) if duplicate else None
            existing_count += 1 if duplicate_id else 0
            con.execute(
                """
                UPDATE import_inbox_items
                SET status='pending',
                    confidence='high',
                    duplicate_of_type=?,
                    duplicate_of_id=?,
                    title=?,
                    subtitle=?,
                    raw_text=?,
                    payload_json=?,
                    reviewed_at=NULL,
                    review_note=?
                WHERE id=?
                """,
                (
                    "publication" if duplicate_id else None,
                    duplicate_id,
                    (normalized.get("title") or "Publication")[:240],
                    " · ".join(
                        part
                        for part in [
                            str(normalized.get("year") or "").strip(),
                            str(normalized.get("venue") or "").strip(),
                            str(normalized.get("category") or "").strip(),
                        ]
                        if part
                    )[:500],
                    str(normalized.get("raw_citation") or "")[:4000],
                    json.dumps(normalized, ensure_ascii=False),
                    f"Resolved from {lookup_kind}: {lookup_value}"[:1000],
                    item["id"],
                ),
            )
            resolved += 1
            results.append(
                {
                    "id": item["id"],
                    "status": "resolved",
                    "lookup": lookup_kind,
                    "value": lookup_value,
                    "title": normalized.get("title"),
                    "duplicate_of_id": duplicate_id,
                }
            )
            time.sleep(0.05)
        con.commit()
        pending = pending_inbox_count(con)
    return {"ok": True, "resolved": resolved, "existing": existing_count, "unresolved": unresolved, "pending": pending, "results": results}


@app.post("/api/import-inbox/reject-duplicates")
def reject_duplicate_import_inbox() -> dict[str, Any]:
    with connect() as con:
        rows = con.execute(
            """
            SELECT * FROM import_inbox_items
            WHERE status='pending' AND duplicate_of_id IS NOT NULL AND source='ai_web_discovery'
            """
        ).fetchall()
        for row in rows:
            item = inbox_payload(dict(row))
            remember_rejection(
                con,
                item["target_type"],
                item["payload"],
                item["source"],
                f"Rejected as duplicate of existing record {item.get('duplicate_of_id')}.",
            )
        cursor = con.execute(
            """
            UPDATE import_inbox_items
            SET status='rejected', reviewed_at=datetime('now'), review_note='Rejected as duplicate-looking candidate.'
            WHERE status='pending' AND duplicate_of_id IS NOT NULL
            """
        )
        con.commit()
        pending = pending_inbox_count(con)
    return {"ok": True, "rejected": cursor.rowcount, "pending": pending}


@app.post("/api/import-inbox/accept-high-confidence")
def accept_high_confidence_import_inbox() -> dict[str, Any]:
    accepted = 0
    publications_accepted = 0
    person_accepted = False
    orcid_accepted = False
    skipped = 0
    duplicates = 0
    with connect() as con:
        rows = rows_dict(
            con.execute(
                """
                SELECT *
                FROM import_inbox_items
                WHERE status='pending'
                  AND confidence='high'
                  AND duplicate_of_id IS NULL
                ORDER BY id
                """
            ).fetchall()
        )
        for row in rows:
            item = inbox_payload(row)
            if item["target_type"] == "entry" and item["payload"].get("section_key") == "honors":
                # Honors and prizes are intentionally never swept in by the bulk
                # action; they require an explicit per-item user selection.
                continue
            status, target_id = accept_inbox_candidate(con, item)
            if status == "accepted":
                accepted += 1
                if item["target_type"] == "publication":
                    publications_accepted += 1
                elif item["target_type"] == "person":
                    person_accepted = True
                    orcid_accepted = orcid_accepted or bool(clean_orcid_id(item["payload"].get("orcid_id")))
                elif item["target_type"] == "identifier":
                    orcid_accepted = orcid_accepted or str(item["payload"].get("platform") or "").casefold() == "orcid"
                mark_inbox_item(con, int(item["id"]), "accepted", f"Imported as {item['target_type']} {target_id}.")
            elif status == "duplicate":
                duplicates += 1
                mark_inbox_item(con, int(item["id"]), "skipped", f"Skipped duplicate of existing record {target_id}.")
            else:
                skipped += 1
                mark_inbox_item(con, int(item["id"]), "skipped", "Skipped; candidate did not contain enough usable data.")
        con.commit()
        pending = pending_inbox_count(con)
    if publications_accepted or person_accepted:
        schedule_background_refresh(
            publications_changed=bool(publications_accepted),
            profiles=orcid_accepted,
            institutions=person_accepted,
        )
    return {"ok": True, "accepted": accepted, "duplicates": duplicates, "skipped": skipped, "pending": pending}


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
        clauses.append(
            """(
              COALESCE(suppress_display, 0) = 0
              OR (
                category = 'preprints'
                AND COALESCE(quality_note, '') =
                    'Suppressed preprint; not exported to CV publication lists.'
              )
            )"""
        )
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

DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
PMID_RE = re.compile(r"\b(?:pmid|pubmed)\s*:?\s*(\d{5,10})\b", re.IGNORECASE)
BARE_PMID_RE = re.compile(r"^\d{5,10}$")
IDENTIFIER_USER_AGENT = "vitamine/0.1 (publication identifier import)"


def clean_metadata_text(value: Any) -> str:
    return decode_metadata_text(value, strip_markup=True)


def normalize_doi(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(".,;)")


def year_from_date_parts(parts: Any) -> str:
    try:
        year = parts[0][0]
    except (TypeError, IndexError):
        return ""
    return str(year) if year else ""


def metadata_json(url: str) -> dict[str, Any] | None:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": IDENTIFIER_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status >= 400:
                return None
            return json.loads(response.read().decode("utf-8"))
    except (TimeoutError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return None


def crossref_publication_metadata(doi: str) -> dict[str, Any]:
    crossref = registry_crossref_metadata(normalize_doi(doi))
    if not crossref:
        return {}
    doi_value = normalize_doi(crossref.get("doi") or doi)
    pmid = registry_pubmed_pmid(doi_value) if doi_value else ""
    pubmed = registry_pubmed_metadata(pmid) if pmid else {}
    metadata = authoritative_registry_metadata(
        {
            "title": crossref.get("title") or "",
            "authors": "",
            "venue": crossref.get("venue") or "",
            "year": crossref.get("year") or "",
            "doi": doi_value,
            "raw_citation": "",
        },
        crossref,
        pubmed,
    )
    values = {
        "item_type": clean_metadata_text(metadata.get("crossref_type")) or "journal-article",
        "category": "peer_reviewed",
        "authors": clean_metadata_text(metadata.get("authors")),
        "title": clean_metadata_text(metadata.get("title")),
        "venue": clean_metadata_text(metadata.get("venue")),
        "year": clean_metadata_text(metadata.get("year")),
        "doi": doi_value,
        "pmid": pmid,
        "url": clean_metadata_text(metadata.get("url")) or (f"https://doi.org/{doi_value}" if doi_value else ""),
        "abstract": clean_metadata_text(crossref.get("abstract")),
        "extra": "",
        "confidence": "high",
        "include_short": 0,
        "include_ultrashort": 0,
        "short_citation": "",
        "suppress_display": 0,
        "quality_note": "",
    }
    values["raw_citation"] = publication_raw_citation(values)
    return values


def pubmed_pmid_for_doi(doi: str) -> str:
    term = urllib.parse.quote(f"{doi}[AID]")
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&retmode=json&tool=vitamine&term={term}"
    payload = metadata_json(url)
    ids = (((payload or {}).get("esearchresult") or {}).get("idlist") or [])
    return str(ids[0]) if ids else ""


def pubmed_publication_metadata(pmid: str) -> dict[str, Any]:
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&tool=vitamine&id={urllib.parse.quote(pmid)}"
    payload = metadata_json(url)
    summary = ((payload or {}).get("result") or {}).get(str(pmid)) or {}
    if not summary:
        return {}
    authors = [author.get("name") for author in summary.get("authors") or [] if author.get("name")]
    doi = ""
    for article_id in summary.get("articleids") or []:
        if str(article_id.get("idtype") or "").lower() == "doi":
            doi = normalize_doi(article_id.get("value"))
            break
    pubdate = clean_metadata_text(summary.get("pubdate"))
    year_match = re.search(r"\b(19|20)\d{2}\b", pubdate)
    values = {
        "item_type": "journal-article",
        "category": "peer_reviewed",
        "authors": ", ".join(authors),
        "title": clean_metadata_text(summary.get("title")),
        "venue": clean_metadata_text(summary.get("fulljournalname") or summary.get("source")),
        "year": year_match.group(0) if year_match else "",
        "doi": doi,
        "pmid": str(pmid),
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "abstract": "",
        "extra": "",
        "confidence": "high",
        "include_short": 0,
        "include_ultrashort": 0,
        "short_citation": "",
        "suppress_display": 0,
        "quality_note": "",
    }
    values["raw_citation"] = publication_raw_citation(values)
    return values


def publication_raw_citation(values: dict[str, Any]) -> str:
    citation = ". ".join(str(values.get(field) or "").strip() for field in ("authors", "title", "venue", "year") if str(values.get(field) or "").strip())
    if values.get("doi"):
        citation = f"{citation}. doi:{values['doi']}" if citation else f"doi:{values['doi']}"
    elif values.get("pmid"):
        citation = f"{citation}. PMID:{values['pmid']}" if citation else f"PMID:{values['pmid']}"
    return citation


def parse_publication_identifiers(text: str) -> list[dict[str, str]]:
    identifiers: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in DOI_RE.finditer(text):
        value = normalize_doi(match.group(0))
        key = ("doi", value)
        if value and key not in seen:
            identifiers.append({"kind": "doi", "value": value})
            seen.add(key)
    for match in PMID_RE.finditer(text):
        value = match.group(1)
        key = ("pmid", value)
        if key not in seen:
            identifiers.append({"kind": "pmid", "value": value})
            seen.add(key)
    for line in text.splitlines():
        value = line.strip()
        if BARE_PMID_RE.fullmatch(value):
            key = ("pmid", value)
            if key not in seen:
                identifiers.append({"kind": "pmid", "value": value})
                seen.add(key)
    return identifiers


def crossref_title_publication_metadata(title: str) -> dict[str, Any]:
    clean_title = re.sub(r"\s+", " ", title).strip()
    if len(clean_title) < 8:
        return {}
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode({"query.title": clean_title, "rows": "5"})
    payload = metadata_json(url)
    items = (((payload or {}).get("message") or {}).get("items") or [])
    best: dict[str, Any] | None = None
    best_score = 0.0
    title_key = re.sub(r"[^a-z0-9]+", " ", clean_title.casefold()).strip()
    title_tokens = set(title_key.split())
    for item in items:
        candidate_title = clean_metadata_text(item.get("title"))
        candidate_key = re.sub(r"[^a-z0-9]+", " ", candidate_title.casefold()).strip()
        candidate_tokens = set(candidate_key.split())
        if not candidate_tokens:
            continue
        overlap = len(title_tokens & candidate_tokens)
        score = overlap / max(len(title_tokens), len(candidate_tokens), 1)
        if candidate_key == title_key:
            score = 1.0
        if score > best_score:
            best = item
            best_score = score
    if not best or best_score < 0.55:
        return {}
    doi = normalize_doi(best.get("DOI"))
    return crossref_publication_metadata(doi) if doi else {}


def publication_candidate_lookup_text(item: dict[str, Any]) -> str:
    payload = item.get("payload") or {}
    parts = [
        item.get("title"),
        item.get("subtitle"),
        item.get("raw_text"),
        payload.get("doi"),
        payload.get("pmid"),
        payload.get("title"),
        payload.get("raw_citation"),
    ]
    return "\n".join(str(part) for part in parts if part)


def resolve_publication_candidate_metadata(item: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    lookup_text = publication_candidate_lookup_text(item)
    for identifier in parse_publication_identifiers(lookup_text):
        kind = identifier["kind"]
        value = identifier["value"]
        metadata = crossref_publication_metadata(value) if kind == "doi" else pubmed_publication_metadata(value)
        if metadata.get("title") or metadata.get("raw_citation"):
            return metadata, kind, value
    payload = item.get("payload") or {}
    title = str(payload.get("title") or item.get("title") or "").strip()
    if not title:
        raw = str(payload.get("raw_citation") or item.get("raw_text") or "").strip()
        title = raw.split(". ")[0][:240]
    metadata = crossref_title_publication_metadata(title)
    if metadata.get("title") or metadata.get("raw_citation"):
        return metadata, "title", title
    return {}, "title", title


def existing_publication_for_identifier(con: sqlite3.Connection, doi: str = "", pmid: str = "") -> sqlite3.Row | None:
    doi_key = normalize_doi(doi)
    if doi_key:
        for row in con.execute("SELECT id, title, doi, pmid FROM publications WHERE doi IS NOT NULL AND doi != '' ORDER BY id").fetchall():
            if normalize_doi(row["doi"]) == doi_key:
                return row
    if pmid:
        return con.execute("SELECT id, title, doi, pmid FROM publications WHERE pmid=? ORDER BY id LIMIT 1", (pmid,)).fetchone()
    return None


def normalize_publication(payload: dict[str, Any]) -> dict[str, Any]:
    payload = decode_publication_payload(payload)
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
    schedule_background_refresh(publications_changed=True)
    return {"ok": True, "id": cursor.lastrowid}


@app.post("/api/publications/import-identifiers")
async def import_publication_identifiers(request: Request) -> dict[str, Any]:
    payload = await request.json()
    identifiers = parse_publication_identifiers(str(payload.get("text") or ""))
    if not identifiers:
        raise HTTPException(status_code=400, detail="Paste at least one DOI or PubMed ID.")
    results: list[dict[str, Any]] = []
    imported = 0
    skipped = 0
    unresolved = 0
    with connect() as con:
        document_id = ensure_manual_document(con)
        for identifier in identifiers:
            kind = identifier["kind"]
            value = identifier["value"]
            existing = existing_publication_for_identifier(con, doi=value if kind == "doi" else "", pmid=value if kind == "pmid" else "")
            if existing:
                skipped += 1
                results.append(
                    {
                        "identifier": value,
                        "kind": kind,
                        "status": "existing",
                        "id": existing["id"],
                        "title": existing["title"],
                    }
                )
                continue
            metadata = crossref_publication_metadata(value) if kind == "doi" else pubmed_publication_metadata(value)
            if metadata.get("doi") or metadata.get("pmid"):
                duplicate = existing_publication_for_identifier(con, doi=metadata.get("doi") or "", pmid=metadata.get("pmid") or "")
                if duplicate:
                    skipped += 1
                    results.append(
                        {
                            "identifier": value,
                            "kind": kind,
                            "status": "existing",
                            "id": duplicate["id"],
                            "title": duplicate["title"],
                        }
                    )
                    continue
            if not metadata.get("title") and not metadata.get("raw_citation"):
                unresolved += 1
                results.append({"identifier": value, "kind": kind, "status": "unresolved", "title": ""})
                continue
            normalized = normalize_publication(metadata)
            cursor = con.execute(
                f"""
                INSERT INTO publications (document_id, source, {', '.join(PUBLICATION_FIELDS)})
                VALUES (?, 'identifier_import', {', '.join('?' for _ in PUBLICATION_FIELDS)})
                """,
                (document_id, *[normalized[field] for field in PUBLICATION_FIELDS]),
            )
            imported += 1
            results.append(
                {
                    "identifier": value,
                    "kind": kind,
                    "status": "imported",
                    "id": cursor.lastrowid,
                    "title": normalized["title"],
                }
            )
            time.sleep(0.05)
        con.commit()
    if imported:
        schedule_background_refresh(publications_changed=True)
    return {
        "ok": True,
        "requested": len(identifiers),
        "imported": imported,
        "skipped": skipped,
        "unresolved": unresolved,
        "results": results,
    }


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
    schedule_background_refresh(publications_changed=True)
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


def researcher_name_terms(person: dict[str, Any] | sqlite3.Row | None) -> list[str]:
    if not person:
        return []
    def person_value(key: str) -> str:
        try:
            return str(person[key] or "").strip()
        except (KeyError, IndexError):
            return ""

    names = [person_value("display_name"), person_value("full_name")]
    terms: set[str] = set()
    for name in names:
        parts = [part for part in re.split(r"\s+", name.casefold()) if part]
        if not parts:
            continue
        terms.add(" ".join(parts))
        first = parts[0]
        last = parts[-1]
        if first and last and first != last:
            terms.add(f"{first} {last}")
            terms.add(f"{last} {first}")
            terms.add(f"{first[0]} {last}")
            terms.add(f"{last} {first[0]}")
        if len(last) > 3:
            terms.add(last)
    return sorted(terms, key=len, reverse=True)


def author_matches_researcher(author: str, terms: list[str]) -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", author.casefold()).strip()
    padded = f" {text} "
    return any(f" {re.sub(r'[^a-z0-9]+', ' ', term).strip()} " in padded for term in terms)


def researcher_authorship(authors: str | None, terms: list[str]) -> str:
    parts = [part.strip() for part in (authors or "").split(",") if part.strip()]
    if not parts:
        return "other"
    first = author_matches_researcher(parts[0], terms)
    last = author_matches_researcher(parts[-1], terms)
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
    person = con.execute("SELECT full_name, display_name FROM person WHERE id=1").fetchone()
    terms = researcher_name_terms(person)
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
        row["authorship"] = researcher_authorship(row.get("authors"), terms)
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


def export_plan_setting_key(format_id: str) -> str:
    return f"export_prompt_plan:{format_id}"


def validate_export_plan(
    raw: dict[str, Any],
    candidates: list[dict[str, Any]],
    prompt: str,
    format_id: str,
) -> dict[str, Any]:
    candidate_by_id = {int(row["id"]): row for row in candidates}
    try:
        maximum = max(0, min(50, int(raw.get("max_publications", 10))))
    except (TypeError, ValueError):
        maximum = 10
    authorship_preference = (
        raw.get("authorship_preference")
        if raw.get("authorship_preference") in {"first_last", "first", "last", "all"}
        else "all"
    )
    selected: list[int] = []
    for value in raw.get("selected_publication_ids") or []:
        try:
            publication_id = int(value)
        except (TypeError, ValueError):
            continue
        if publication_id in candidate_by_id and publication_id not in selected:
            selected.append(publication_id)
    required: list[int] = []
    for value in raw.get("required_publication_ids") or []:
        try:
            publication_id = int(value)
        except (TypeError, ValueError):
            continue
        if publication_id in candidate_by_id and publication_id not in required:
            required.append(publication_id)
    selected = [
        publication_id
        for publication_id in selected
        if publication_id in required
        or authorship_matches(str(candidate_by_id[publication_id].get("authorship") or "other"), authorship_preference)
    ]
    if maximum:
        selected = required + [publication_id for publication_id in selected if publication_id not in required]
        ranked = sorted(
            candidates,
            key=lambda row: (
                -float(row.get("score") or 0),
                -(int(re.search(r"\d{4}", str(row.get("year") or "")).group(0)) if re.search(r"\d{4}", str(row.get("year") or "")) else 0),
            ),
        )
        for row in ranked:
            publication_id = int(row["id"])
            if len(selected) >= maximum:
                break
            if publication_id in selected:
                continue
            if authorship_matches(str(row.get("authorship") or "other"), authorship_preference):
                selected.append(publication_id)
        selected = selected[:maximum]
    else:
        selected = []
        required = []
    if not selected and maximum:
        def safe_year(row: dict[str, Any]) -> int:
            match = re.search(r"\d{4}", str(row.get("year") or ""))
            return int(match.group(0)) if match else 0

        ranked = sorted(candidates, key=lambda row: (-float(row.get("score") or 0), -safe_year(row)))
        selected = [int(row["id"]) for row in ranked[:maximum]]
    try:
        max_pages_value = raw.get("max_pages")
        max_pages = max(1, min(100, int(max_pages_value))) if max_pages_value is not None else None
    except (TypeError, ValueError):
        max_pages = None
    warnings = [str(item).strip() for item in raw.get("warnings") or [] if str(item).strip()]
    if required and all(
        authorship_matches(str(candidate_by_id[publication_id].get("authorship") or "other"), "first_last")
        for publication_id in required
    ):
        warnings = [
            warning
            for warning in warnings
            if not ("required" in warning.casefold() and "not first/last author" in warning.casefold())
        ]
    if max_pages:
        warnings.append(
            "The page limit is a layout target, not a guarantee: final Word pagination depends on fonts, Word version, and manual edits."
        )
    selected_rows = [candidate_by_id[publication_id] for publication_id in selected]
    return {
        "format_id": format_id,
        "prompt": prompt.strip(),
        "max_pages": max_pages,
        "max_publications": maximum,
        "authorship_preference": authorship_preference,
        "recency_preference": raw.get("recency_preference") if raw.get("recency_preference") in {"strong", "moderate", "none"} else "moderate",
        "impact_factor_preference": raw.get("impact_factor_preference") if raw.get("impact_factor_preference") in {"strong", "moderate", "none"} else "moderate",
        "section_strategy": raw.get("section_strategy") if raw.get("section_strategy") in {"complete", "compact", "publications_focused"} else "compact",
        "interpretation": str(raw.get("interpretation") or "").strip(),
        "warnings": list(dict.fromkeys(warnings)),
        "selected_publication_ids": selected,
        "required_publication_ids": [publication_id for publication_id in required if publication_id in selected],
        "selected_publications": [
            {
                "id": row["id"],
                "title": row.get("title"),
                "authors": row.get("authors"),
                "year": row.get("year"),
                "venue": row.get("venue"),
                "impact_factor": row.get("impact_factor"),
                "authorship": row.get("authorship"),
            }
            for row in selected_rows
        ],
    }


def apply_export_plan(con: sqlite3.Connection, plan: dict[str, Any], content_profile: str) -> None:
    selected = [int(value) for value in plan.get("selected_publication_ids") or []]
    limit = max(1, min(50, int(plan.get("max_publications") or len(selected) or 10)))
    profile = "ultrashort" if content_profile == "one_page" else "short"
    if content_profile in {"short", "one_page"}:
        config = validate_export_profile(profile)
        con.execute(
            """
            INSERT INTO export_settings (profile, publication_limit, authorship_filter)
            VALUES (?, ?, ?)
            ON CONFLICT(profile) DO UPDATE SET
              publication_limit=excluded.publication_limit,
              authorship_filter=excluded.authorship_filter
            """,
            (profile, limit, plan.get("authorship_preference") or "all"),
        )
        con.execute(f"UPDATE publications SET {config['flag']}=0, {config['order']}=NULL")
        for index, publication_id in enumerate(selected[:limit], 1):
            con.execute(
                f"UPDATE publications SET {config['flag']}=1, {config['order']}=? WHERE id=?",
                (index, publication_id),
            )


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
                str(payload.get("title") or "New Contributions to Science Item").strip() or "New Contributions to Science Item",
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
        raise HTTPException(status_code=400, detail="Contributions to Science title is required")
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
            raise HTTPException(status_code=404, detail="Contributions to Science item not found")
        con.commit()
    return {"ok": True}


@app.delete("/api/biosketch/contributions/{contribution_id}")
def delete_biosketch_contribution(contribution_id: int) -> dict[str, Any]:
    with connect() as con:
        cursor = con.execute("DELETE FROM biosketch_contributions WHERE id=?", (contribution_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Contributions to Science item not found")
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
            raise HTTPException(status_code=404, detail="Contributions to Science item not found")
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


def run_script(name: str, *args: str, db_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    pythonpath = os.pathsep.join(part for part in (str(ROOT), os.environ.get("PYTHONPATH", "")) if part)
    env = {**os.environ, "VITAMINE_DB": str(db_path or active_db_path()), "PYTHONPATH": pythonpath}
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


BACKGROUND_REFRESH_LOCK = threading.Lock()
BACKGROUND_REFRESH_PENDING: dict[Path, dict[str, bool]] = {}
BACKGROUND_METRICS_MAX_AGE_DAYS = 7
BACKGROUND_PROFILE_MAX_AGE_DAYS = 30


def setting_is_due(con: sqlite3.Connection, key: str, max_age_days: int) -> bool:
    raw = get_setting(con, key)
    if not raw:
        return True
    try:
        last_run = time.mktime(time.strptime(raw, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return True
    return time.time() - last_run >= max_age_days * 86400


def run_background_refresh(db_path: Path) -> None:
    """Drain refresh requests for one database in a single daemon thread."""
    while True:
        with BACKGROUND_REFRESH_LOCK:
            requested = BACKGROUND_REFRESH_PENDING.pop(db_path, None)
        if not requested:
            return
        profile_due = bool(requested.get("profiles"))
        publications_changed = bool(requested.get("publications"))
        institution_requested = bool(requested.get("institutions") or profile_due)
        profile_succeeded = False
        metrics_succeeded = False
        # ORCID's public person record is an authoritative source for URLs and
        # external identifiers belonging to that ORCID. It is intentionally the
        # only unattended identifier source; guessed web-search matches belong
        # in review unless independently corroborated by papers + institution.
        if institution_requested:
            # Mapping is fast and directly visible to the user, so do it before
            # slower publication/profile maintenance in this shared worker.
            map_institution_automatically(db_path)
        if profile_due:
            profile_result = run_script("sync_orcid.py", db_path=db_path)
            profile_succeeded = profile_result.returncode == 0
            publications_changed = publications_changed or profile_succeeded
        if publications_changed:
            citation_result = run_script("enrich_publications_by_doi.py", "--resolve-missing", db_path=db_path)
            journal_result = run_script("fetch_journal_metrics.py", db_path=db_path)
            metrics_succeeded = citation_result.returncode == 0 and journal_result.returncode == 0
        con = sqlite3.connect(db_path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                  key TEXT PRIMARY KEY, value TEXT,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            now = timestamp_text()
            if profile_succeeded:
                consolidate_person_identifiers(con)
                con.execute(
                    """
                    INSERT INTO app_settings(key, value, updated_at)
                    VALUES ('profile_identifiers_last_run', ?, datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (now,),
                )
            if metrics_succeeded:
                con.execute(
                    """
                    INSERT INTO app_settings(key, value, updated_at)
                    VALUES ('publication_metrics_last_run', ?, datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                    """,
                    (now,),
                )
            con.commit()
        finally:
            con.close()


def schedule_background_refresh(
    *,
    publications_changed: bool = False,
    profiles: bool = False,
    institutions: bool = False,
) -> bool:
    """Coalesce lightweight maintenance without delaying the user's request."""
    db_path = active_db_path().resolve()
    with BACKGROUND_REFRESH_LOCK:
        existing = BACKGROUND_REFRESH_PENDING.setdefault(
            db_path,
            {"publications": False, "profiles": False, "institutions": False},
        )
        existing["publications"] = existing["publications"] or publications_changed
        existing["profiles"] = existing["profiles"] or profiles
        existing["institutions"] = existing["institutions"] or institutions
        already_running = any(
            thread.name == f"vitamine-refresh:{db_path}" and thread.is_alive()
            for thread in threading.enumerate()
        )
        if already_running:
            return False
        worker = threading.Thread(
            target=run_background_refresh,
            args=(db_path,),
            name=f"vitamine-refresh:{db_path}",
            daemon=True,
        )
        worker.start()
    return True


@app.on_event("startup")
def schedule_due_background_maintenance() -> None:
    with connect() as con:
        institution_due = institution_mapping_needed(con)
        if os.environ.get("VITAMINE_CLOUD_WORKER") == "1":
            metrics_due = False
            profiles_due = False
        else:
            has_publications = bool(con.execute("SELECT 1 FROM publications LIMIT 1").fetchone())
            has_identity = bool(
                con.execute(
                    """
                    SELECT 1 FROM person
                    WHERE id=1 AND COALESCE(orcid_id, '') != ''
                    """
                ).fetchone()
            )
            metrics_due = has_publications and setting_is_due(
                con, "publication_metrics_last_run", BACKGROUND_METRICS_MAX_AGE_DAYS
            )
            profiles_due = has_identity and setting_is_due(
                con, "profile_identifiers_last_run", BACKGROUND_PROFILE_MAX_AGE_DAYS
            )
    if metrics_due or profiles_due or institution_due:
        schedule_background_refresh(
            publications_changed=metrics_due,
            profiles=profiles_due,
            institutions=institution_due,
        )


def build_response(stdout: str, cache_key: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": True, "stdout": stdout}
    for line in stdout.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key == "docx" and value:
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
    if policy not in PUBLICATION_SOURCE_POLICIES:
        policy = "zotero_primary_orcid_validation"
    zotero_key = get_setting(con, "zotero_api_key") or os.environ.get("ZOTERO_API_KEY") or ""
    orcid_row = con.execute(
        """
        SELECT
          COALESCE(
            (SELECT identifier_value FROM person_identifiers WHERE person_id=1 AND lower(platform)='orcid' ORDER BY id LIMIT 1),
            (SELECT orcid_id FROM person WHERE id=1),
            ''
          ) AS orcid_id
        """
    ).fetchone()
    has_orcid = bool(orcid_row and str(orcid_row["orcid_id"] or "").strip())
    if not zotero_key and has_orcid and "orcid" in PUBLICATION_SOURCE_POLICIES[policy]:
        return "orcid_only"
    if not zotero_key and has_orcid and policy == "zotero_only":
        return "orcid_only"
    return policy


def run_publication_source(source: str, db_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    if source == "zotero":
        return run_script("sync_zotero.py", db_path=db_path)
    if source == "orcid":
        return run_script("sync_orcid.py", db_path=db_path)
    raise ValueError(f"Unknown publication source: {source}")


def publication_inbox_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = {field: row[field] if field in row.keys() else "" for field in PUBLICATION_FIELDS}
    for field in ("orcid_put_code", "orcid_source", "orcid_last_modified", "orcid_path"):
        if field in row.keys():
            payload[field] = row[field]
    return decode_publication_payload(payload)


def ensure_source_review_document(con: sqlite3.Connection, source: str) -> int:
    slug = f"{source}_review_candidates"
    title = f"{source.title()} review candidates"
    con.execute(
        """
        INSERT INTO documents (slug, title, source_path, source_format, imported_at, notes)
        VALUES (?, ?, ?, ?, datetime('now'), ?)
        ON CONFLICT(slug) DO UPDATE SET imported_at=datetime('now'), notes=excluded.notes
        """,
        (slug, title, source, f"{source}-review", f"Candidates staged from {source.title()} enrichment."),
    )
    return int(con.execute("SELECT id FROM documents WHERE slug=?", (slug,)).fetchone()[0])


def stage_publication_payload_from_source(con: sqlite3.Connection, document_id: int, payload: dict[str, Any], source: str) -> None:
    payload = decode_publication_payload(payload)
    title = str(payload.get("title") or "Publication")
    subtitle = " · ".join(
        part
        for part in [
            str(payload.get("year") or "").strip(),
            str(payload.get("venue") or "").strip(),
            str(payload.get("category") or "").strip(),
        ]
        if part
    )
    raw_text = str(payload.get("raw_citation") or "")
    con.execute(
        """
        INSERT INTO import_inbox_items
          (document_id, source, target_type, status, confidence, title, subtitle, raw_text, payload_json)
        VALUES (?, ?, 'publication', 'pending', ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            source,
            str(payload.get("confidence") or "high"),
            title[:240],
            subtitle[:500],
            raw_text[:4000],
            json.dumps(payload, ensure_ascii=False),
        ),
    )


def publication_exists_in_curated_db(con: sqlite3.Connection, payload: dict[str, Any]) -> bool:
    doi = normalize_doi(str(payload.get("doi") or ""))
    pmid = str(payload.get("pmid") or "").strip()
    raw_citation = str(payload.get("raw_citation") or "").strip().casefold()
    title = str(payload.get("title") or "").strip().casefold()
    if doi:
        row = con.execute("SELECT id FROM publications WHERE lower(COALESCE(doi, ''))=? LIMIT 1", (doi,)).fetchone()
        if row:
            return True
    if pmid:
        row = con.execute("SELECT id FROM publications WHERE pmid=? LIMIT 1", (pmid,)).fetchone()
        if row:
            return True
    if raw_citation:
        row = con.execute("SELECT id FROM publications WHERE lower(COALESCE(raw_citation, ''))=? LIMIT 1", (raw_citation,)).fetchone()
        if row:
            return True
    if title:
        row = con.execute("SELECT id FROM publications WHERE lower(COALESCE(title, ''))=? LIMIT 1", (title,)).fetchone()
        if row:
            return True
    return False


def publication_inbox_candidate_exists(con: sqlite3.Connection, payload: dict[str, Any]) -> bool:
    doi = normalize_doi(str(payload.get("doi") or ""))
    pmid = str(payload.get("pmid") or "").strip()
    raw_citation = str(payload.get("raw_citation") or "").strip().casefold()
    title = str(payload.get("title") or "").strip().casefold()
    rows = con.execute(
        """
        SELECT payload_json, raw_text, title
        FROM import_inbox_items
        WHERE target_type='publication'
          AND status IN ('pending', 'rejected')
        """
    ).fetchall()
    for row in rows:
        try:
            candidate = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            candidate = {}
        candidate_doi = normalize_doi(str(candidate.get("doi") or ""))
        candidate_pmid = str(candidate.get("pmid") or "").strip()
        candidate_raw = str(candidate.get("raw_citation") or row["raw_text"] or "").strip().casefold()
        candidate_title = str(candidate.get("title") or row["title"] or "").strip().casefold()
        if doi and candidate_doi == doi:
            return True
        if pmid and candidate_pmid == pmid:
            return True
        if raw_citation and candidate_raw == raw_citation:
            return True
        if title and candidate_title == title:
            return True
    return False


def persist_discovered_identifiers(con: sqlite3.Connection, identifiers: list[dict[str, Any]]) -> int:
    """Copy identifier discoveries out of the isolated source-sync database."""
    consolidate_person_identifiers(con)
    added = 0
    for raw_identifier in identifiers:
        identifier = normalize_identifier(raw_identifier)
        platform = str(identifier.get("platform") or "").strip()
        identifier_type = str(identifier.get("identifier_type") or "").strip()
        identifier_value = str(identifier.get("identifier_value") or "").strip() or None
        url = str(identifier.get("url") or "").strip()
        if not platform or not identifier_type or not url:
            continue
        exists = con.execute(
            """
            SELECT id, identifier_value, source
            FROM person_identifiers
            WHERE person_id=1
              AND lower(platform)=lower(?)
            LIMIT 1
            """,
            (platform,),
        ).fetchone()
        if exists:
            # A canonical service is a single card. Enrich a URL-only row with a
            # parsed/explicit value, but never overwrite a stronger manual value.
            if identifier_value and (
                not str(exists["identifier_value"] or "").strip()
                or identifier_source_rank(str(identifier.get("source") or ""))
                > identifier_source_rank(str(exists["source"] or ""))
            ):
                con.execute(
                    """
                    UPDATE person_identifiers
                    SET platform=?, identifier_type=?, identifier_value=?, url=?,
                        source=?, verified_at=COALESCE(?, datetime('now')), notes=COALESCE(?, notes)
                    WHERE id=? AND person_id=1
                    """,
                    (
                        platform,
                        identifier_type,
                        identifier_value,
                        url,
                        str(identifier.get("source") or "").strip() or "orcid",
                        str(identifier.get("verified_at") or "").strip() or None,
                        str(identifier.get("notes") or "").strip() or None,
                        exists["id"],
                    ),
                )
            continue
        con.execute(
            """
            INSERT INTO person_identifiers
              (person_id, platform, identifier_type, identifier_value, url, source, verified_at, notes)
            VALUES (1, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')), ?)
            """,
            (
                platform,
                identifier_type,
                identifier_value,
                url,
                str(identifier.get("source") or "").strip() or "orcid",
                str(identifier.get("verified_at") or "").strip() or None,
                str(identifier.get("notes") or "").strip() or None,
            ),
        )
        added += 1
    sync_person_orcid_from_identifiers(con)
    return added


def run_publication_source_to_inbox(source: str) -> dict[str, Any]:
    active_db = active_db_path()
    temp_identifiers: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix=f"vitamine-{source}-") as tmpdir:
        temp_db = Path(tmpdir) / active_db.name
        shutil.copy2(active_db, temp_db)
        result = run_publication_source(source, db_path=temp_db)
        temp_rows: list[dict[str, Any]] = []
        if result.returncode == 0:
            temp_con = sqlite3.connect(temp_db)
            temp_con.row_factory = sqlite3.Row
            try:
                temp_rows = [
                    publication_inbox_payload(row)
                    for row in temp_con.execute(
                        f"""
                        SELECT {', '.join(PUBLICATION_FIELDS)},
                               orcid_put_code, orcid_source, orcid_last_modified, orcid_path
                        FROM publications
                        WHERE source=?
                        ORDER BY id
                        """,
                        (source,),
                    ).fetchall()
                ]
                identifier_table = temp_con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='person_identifiers'"
                ).fetchone()
                if identifier_table:
                    temp_identifiers = [
                        dict(row)
                        for row in temp_con.execute(
                            """
                            SELECT platform, identifier_type, identifier_value, url, source, verified_at, notes
                            FROM person_identifiers
                            WHERE person_id=1
                            ORDER BY id
                            """
                        ).fetchall()
                    ]
            finally:
                temp_con.close()
    staged = 0
    identifiers_added = 0
    guard_stats: dict[str, int] = {}
    if result.returncode == 0:
        with connect() as con:
            approved_rows, guard_stats = guard_publications(con, temp_rows, source)
            document_id = ensure_source_review_document(con, source)
            for payload in approved_rows:
                if publication_exists_in_curated_db(con, payload):
                    continue
                if not publication_inbox_candidate_exists(con, payload):
                    stage_publication_payload_from_source(con, document_id, payload, source)
                    staged += 1
            identifiers_added = persist_discovered_identifiers(con, temp_identifiers)
            con.commit()
    return {
        "source": source,
        "ok": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "staged_new_publications": staged,
        "publication_guard": guard_stats,
        "identifiers_added": identifiers_added,
    }


AI_DISCOVERY_MAX_PAGES = 5
AI_DISCOVERY_MAX_CHARS_PER_PAGE = 45000
AI_DISCOVERY_MAX_BYTES = 2_000_000


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg", "canvas"}:
            self.hidden_depth += 1
        if tag.lower() in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg", "canvas"} and self.hidden_depth:
            self.hidden_depth -= 1
        if tag.lower() in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)

    def text(self) -> str:
        text = " ".join(part.strip() for part in self.parts if part.strip())
        text = re.sub(r"\s*\n\s*", "\n", text)
        return re.sub(r"[ \t]{2,}", " ", text).strip()


def html_to_visible_text(html: str) -> str:
    parser = VisibleTextParser()
    parser.feed(html)
    return parser.text()


def fetch_profile_text(url: str) -> tuple[str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "vitamine/0.1 (AI profile discovery)",
            "Accept": "text/html,text/plain;q=0.9,*/*;q=0.5",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        content_type = response.headers.get("Content-Type", "")
        raw = response.read(AI_DISCOVERY_MAX_BYTES + 1)
    if len(raw) > AI_DISCOVERY_MAX_BYTES:
        raise RuntimeError("profile page was larger than the discovery limit")
    charset_match = re.search(r"charset=([^;\s]+)", content_type, flags=re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    decoded = raw.decode(charset, errors="replace")
    if "html" in content_type.lower() or "<html" in decoded[:1000].lower():
        return html_to_visible_text(decoded), content_type
    return re.sub(r"\s+", " ", decoded).strip(), content_type


def timestamp_text(timestamp: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp or time.time()))


def ai_web_discovery_enabled(con: sqlite3.Connection) -> bool:
    return get_setting(con, "ai_web_discovery_enabled") == "1"


def ai_discovery_source_urls(con: sqlite3.Connection, limit: int = AI_DISCOVERY_MAX_PAGES) -> list[dict[str, str]]:
    rows = rows_dict(
        con.execute(
            """
            SELECT platform, identifier_type, identifier_value, url
            FROM person_identifiers
            WHERE url IS NOT NULL AND url != ''
            ORDER BY lower(platform), id
            """
        ).fetchall()
    )
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    skip_platforms = {"orcid", "zotero", "pubmed", "crossref", "doi"}
    for row in rows:
        platform = str(row.get("platform") or "").strip()
        url = str(row.get("url") or "").strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if platform.casefold() in skip_platforms:
            continue
        normalized_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path, "", parsed.query, ""))
        if normalized_url in seen:
            continue
        seen.add(normalized_url)
        sources.append(
            {
                "platform": platform or parsed.netloc,
                "identifier_type": str(row.get("identifier_type") or ""),
                "identifier_value": str(row.get("identifier_value") or ""),
                "url": normalized_url,
            }
        )
        if len(sources) >= limit:
            break
    return sources


def ensure_discovery_document(con: sqlite3.Connection, source: dict[str, str]) -> int:
    slug_base = re.sub(r"[^a-z0-9]+", "-", f"ai-discovery-{source.get('platform')}-{source.get('url')}".casefold()).strip("-")
    slug = slug_base[:140] or "ai-discovery-profile"
    title = f"AI profile discovery: {source.get('platform') or source.get('url')}"
    con.execute(
        """
        INSERT INTO documents (slug, title, source_path, source_format, imported_at, notes)
        VALUES (?, ?, ?, 'web-profile', datetime('now'), ?)
        ON CONFLICT(slug) DO UPDATE SET imported_at=datetime('now'), notes=excluded.notes
        """,
        (slug, title[:240], source.get("url") or "", "Profile page fetched for AI-assisted candidate discovery."),
    )
    return int(con.execute("SELECT id FROM documents WHERE slug=?", (slug,)).fetchone()[0])


def discover_researcher_profiles() -> dict[str, Any]:
    """Resolve researcher IDs from identity and publication evidence."""
    with connect() as con:
        if not ai_web_discovery_enabled(con):
            return {"ok": True, "enabled": False, "accepted": 0, "staged": 0, "warnings": []}
        person = row_dict(con.execute("SELECT * FROM person WHERE id=1").fetchone())
        publications = rows_dict(
            con.execute(
                "SELECT title, year, doi, authors, venue FROM publications "
                "WHERE COALESCE(suppress_display, 0)=0 ORDER BY year DESC, id DESC"
            ).fetchall()
        )
        candidates, warnings = resolve_profiles("", person, publications, search_web=True)
        con.execute(
            """
            INSERT INTO documents (slug, title, source_path, source_format, imported_at, notes)
            VALUES ('researcher-profile-resolution', 'Researcher profile resolution', '',
                    'online-profile-index', datetime('now'),
                    'Profile identifiers resolved from corroborated public identity evidence.')
            ON CONFLICT(slug) DO UPDATE SET imported_at=datetime('now'), notes=excluded.notes
            """
        )
        document_id = int(
            con.execute("SELECT id FROM documents WHERE slug='researcher-profile-resolution'").fetchone()[0]
        )
        counts = store_profile_candidates(con, document_id, candidates)
        con.commit()
    return {"ok": True, "enabled": True, **counts, "warnings": warnings}


def discover_ai_profile_candidates() -> dict[str, Any]:
    with connect() as con:
        if not ai_web_discovery_enabled(con):
            return {"ok": True, "enabled": False, "sources_checked": 0, "candidates_staged": 0, "warnings": []}
        settings = cv_import_settings(con, include_secret=True)
        sources = ai_discovery_source_urls(con)
    if not sources:
        return {"ok": True, "enabled": True, "sources_checked": 0, "candidates_staged": 0, "warnings": ["No profile URLs available for AI discovery."]}
    if settings.get("provider") == "none":
        return {"ok": True, "enabled": True, "sources_checked": 0, "candidates_staged": 0, "warnings": ["AI web discovery is enabled, but CV import LLM provider is set to none."]}

    total_staged = 0
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    with connect() as con:
        for source in sources:
            url = source["url"]
            try:
                text, content_type = fetch_profile_text(url)
            except (OSError, RuntimeError, urllib.error.URLError, urllib.error.HTTPError, UnicodeError) as exc:
                warnings.append(f"{url}: {exc}")
                results.append({"url": url, "ok": False, "warning": str(exc)})
                continue
            if len(text) < 300:
                warnings.append(f"{url}: profile page did not contain enough visible text.")
                results.append({"url": url, "ok": False, "warning": "not enough visible text"})
                continue
            prompt_text = (
                "This is a researcher profile page, not necessarily a full CV. "
                "Extract only concrete CV/publication facts that are explicitly present. "
                "Treat uncertain items as low confidence.\n\n"
                f"Source URL: {url}\nContent type: {content_type}\n\n{text[:AI_DISCOVERY_MAX_CHARS_PER_PAGE]}"
            )
            llm_data, llm_warning = llm_extract(prompt_text, settings)
            if llm_warning or not isinstance(llm_data, dict):
                warning = llm_warning or "LLM returned no structured discovery data."
                warnings.append(f"{url}: {warning}")
                results.append({"url": url, "ok": False, "warning": warning})
                continue
            document_id = ensure_discovery_document(con, source)
            guarded_publications, publication_review = guard_publications(
                con,
                [row for row in llm_data.get("publications", []) if isinstance(row, dict)],
                "ai_web_discovery",
            )
            guarded_other, other_review = review_nonpublications(
                con,
                [row for row in llm_data.get("entries", []) if isinstance(row, dict)],
                [row for row in llm_data.get("contributions", []) if isinstance(row, dict)],
                llm_data.get("person") if isinstance(llm_data.get("person"), dict) else {},
                llm_data.get("narrative_report") if isinstance(llm_data.get("narrative_report"), dict) else None,
                "ai_web_discovery",
                settings,
            )
            staged = stage_import_candidates(
                con,
                document_id,
                guarded_other["entries"],
                guarded_publications,
                guarded_other["contributions"],
                guarded_other["person"],
                guarded_other["narrative_report"],
                "ai_web_discovery",
            )
            staged_count = sum(int(staged.get(key) or 0) for key in ("entries", "publications", "contributions", "person", "narrative"))
            total_staged += staged_count
            results.append(
                {
                    "url": url,
                    "ok": True,
                    "staged": staged,
                    "publication_review": publication_review,
                    "other_review": other_review,
                    "candidates_staged": staged_count,
                    "remembered_rejections": int(staged.get("remembered_rejections") or 0),
                }
            )
            for warning in llm_data.get("warnings") or []:
                if warning:
                    warnings.append(f"{url}: {warning}")
        con.commit()
        pending = pending_inbox_count(con)
    return {
        "ok": True,
        "enabled": True,
        "sources_checked": len(sources),
        "candidates_staged": total_staged,
        "inbox_pending": pending,
        "results": results,
        "warnings": warnings[:20],
    }


def enrich_cv_job(
    update_last_run: bool = True,
    progress_callback: Callable[[str, str, int], None] | None = None,
) -> dict[str, Any]:
    def report_progress(phase: str, message: str, percent: int) -> None:
        if progress_callback is not None:
            progress_callback(phase, message, percent)

    with connect() as con:
        policy = publication_source_policy(con)
    doi_result = run_script("enrich_publications_by_doi.py", "--resolve-missing")
    if doi_result.returncode != 0:
        raise RuntimeError(doi_result.stderr[-4000:] or "DOI enrichment failed.")
    report_progress("sources", "Checking connected publication sources", 62)
    source_results: list[dict[str, Any]] = []
    sources = PUBLICATION_SOURCE_POLICIES[policy]
    for index, source in enumerate(sources, start=1):
        source_label = source.replace("_", " ").title()
        report_progress(
            "sources",
            f"Checking {source_label} ({index} of {len(sources)})",
            62 + round(((index - 1) / max(1, len(sources))) * 16),
        )
        source_result = run_publication_source_to_inbox(source)
        source_results.append(source_result)
        if not source_result["ok"]:
            raise RuntimeError(str(source_result.get("stderr") or "")[-4000:] or f"{source} sync failed.")
    report_progress("maintenance", "Consolidating publication records", 80)
    maintenance = maintain()
    report_progress("discovery", "Checking additional researcher profiles", 84)
    profile_resolution = discover_researcher_profiles()
    ai_discovery = discover_ai_profile_candidates()
    with connect() as con:
        if update_last_run:
            set_setting(con, "enrichment_last_run", timestamp_text())
        con.commit()
        inbox_pending = pending_inbox_count(con)
        citation_coverage = row_dict(
            con.execute(
                """
                SELECT
                  SUM(CASE WHEN COALESCE(suppress_display, 0)=0 THEN 1 ELSE 0 END) AS visible_publications,
                  SUM(CASE WHEN COALESCE(suppress_display, 0)=0
                            AND openalex_cited_by_count IS NOT NULL THEN 1 ELSE 0 END) AS publications_with_citations,
                  SUM(CASE WHEN COALESCE(suppress_display, 0)=0
                           THEN COALESCE(openalex_cited_by_count, 0) ELSE 0 END) AS citation_total
                FROM publications
                """
            ).fetchone()
        )
    source_summary: list[dict[str, Any]] = []
    for row in source_results:
        try:
            script_stats = json.loads(str(row.get("stdout") or "{}"))
        except json.JSONDecodeError:
            script_stats = {}
        guard = row.get("publication_guard") or {}
        source_summary.append(
            {
                "source": row.get("source"),
                "fetched": int(script_stats.get("fetched") or 0),
                "matched": int(script_stats.get("matched") or 0),
                "source_upserted": int(script_stats.get("upserted") or 0),
                "duplicates": int(guard.get("duplicates") or 0),
                "backfilled": int(guard.get("backfilled") or 0),
                "rejected": sum(
                    int(guard.get(key) or 0)
                    for key in ("remembered", "unresolved", "not_author")
                ),
                "identity_review": int(guard.get("identity_review") or 0),
                "staged": int(row.get("staged_new_publications") or 0),
            }
        )
    enrichment_summary = {
        "sources": source_summary,
        "source_records_fetched": sum(row["fetched"] for row in source_summary),
        "matched_at_source": sum(row["matched"] for row in source_summary),
        "duplicates": sum(row["duplicates"] for row in source_summary),
        "backfilled": sum(row["backfilled"] for row in source_summary),
        "rejected": sum(row["rejected"] for row in source_summary),
        "identity_review": sum(row["identity_review"] for row in source_summary),
        "staged_from_sources": sum(row["staged"] for row in source_summary),
        "profiles_accepted": int(profile_resolution.get("accepted") or 0),
        "profiles_staged": int(profile_resolution.get("staged") or 0),
        "staged_from_web": int(ai_discovery.get("candidates_staged") or 0),
        "inbox_pending": inbox_pending,
        "citation_coverage": {
            key: int((citation_coverage or {}).get(key) or 0)
            for key in ("visible_publications", "publications_with_citations", "citation_total")
        },
    }
    stdout_parts = [
        *(f"[{row['source']}]\n{row['stdout'].strip()}" for row in source_results if row["stdout"].strip()),
        f"[doi]\n{doi_result.stdout.strip()}",
        f"[maintenance]\n{json.dumps(maintenance, indent=2)}",
        f"[profile-resolution]\n{json.dumps(profile_resolution, indent=2)}",
        f"[ai-web-discovery]\n{json.dumps(ai_discovery, indent=2)}",
    ]
    return {
        "ok": True,
        "policy": policy,
        "results": source_results,
        "doi_stdout": doi_result.stdout,
        "maintenance": maintenance,
        "profile_resolution": profile_resolution,
        "ai_web_discovery": ai_discovery,
        "enrichment_summary": enrichment_summary,
        "inbox_pending": inbox_pending,
        "stdout": "\n\n".join(part for part in stdout_parts if part.strip()),
    }


@app.get("/api/export-formats")
def export_formats() -> dict[str, Any]:
    formats = export_format_catalog()
    installed = set(installed_export_format_ids(formats))
    bundled = [{**item, "installed": item["id"] in installed} for item in formats]
    with connect() as con:
        custom = custom_export_formats(con)
    return {
        "schema_version": 2,
        "formats": [*custom, *bundled],
    }


@app.post("/api/export-templates")
async def create_export_template(
    file: UploadFile = File(...),
    name: str = Form(""),
) -> dict[str, Any]:
    filename = Path(file.filename or "template.docx").name
    if Path(filename).suffix.casefold() != ".docx":
        raise HTTPException(status_code=400, detail="Please upload a Word .docx document.")
    data = await file.read(MAX_TEMPLATE_BYTES + 1)
    fallback_name = re.sub(r"[_-]+", " ", Path(filename).stem).strip() or "My Word CV"
    template_name = clean_template_name(name, fallback_name)
    template_id = f"custom.{uuid.uuid4().hex}"
    try:
        with connect() as con:
            settings = cv_import_settings(con, include_secret=True)
            skeleton, blueprint = analyze_and_skeletonize(
                data,
                template_name,
                con,
                llm_json=llm_json,
                settings=settings,
            )
            blueprint["template_name"] = template_name
            con.execute(
                """
                INSERT INTO export_templates
                  (id, name, source_filename, source_docx, content_profile,
                   blueprint_json, source_sha256, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    template_id,
                    template_name,
                    filename[:240],
                    skeleton,
                    blueprint["content_profile"],
                    json.dumps(blueprint, ensure_ascii=False),
                    template_sha256(data),
                ),
            )
            con.commit()
            row = custom_export_template_row(con, template_id)
            payload = custom_export_format_payload(row)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "format": payload}


@app.put("/api/export-templates/{template_id}")
async def rename_export_template(template_id: str, request: Request) -> dict[str, Any]:
    payload = await request.json()
    name = clean_template_name(payload.get("name"))
    with connect() as con:
        row = custom_export_template_row(con, template_id)
        blueprint = parsed_template_blueprint(row["blueprint_json"])
        blueprint["template_name"] = name
        con.execute(
            """
            UPDATE export_templates
            SET name=?, blueprint_json=?, updated_at=datetime('now')
            WHERE id=?
            """,
            (name, json.dumps(blueprint, ensure_ascii=False), template_id),
        )
        con.commit()
        updated = custom_export_format_payload(custom_export_template_row(con, template_id))
    return {"ok": True, "format": updated}


@app.delete("/api/export-templates/{template_id}")
def delete_export_template(template_id: str) -> dict[str, Any]:
    with connect() as con:
        custom_export_template_row(con, template_id)
        con.execute("DELETE FROM export_templates WHERE id=?", (template_id,))
        con.commit()
    return {"ok": True, "template_id": template_id}


@app.get("/api/export-formats/{format_id}/prompt-plan")
def get_export_prompt_plan(format_id: str) -> dict[str, Any]:
    export_format_by_id(format_id, export_format_catalog())
    with connect() as con:
        raw = get_setting(con, export_plan_setting_key(format_id))
    if not raw:
        return {"active": False, "plan": None}
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError:
        plan = None
    return {"active": bool(plan), "plan": plan}


@app.post("/api/export-formats/{format_id}/prompt-plan")
async def create_export_prompt_plan(format_id: str, request: Request) -> dict[str, Any]:
    item = export_format_by_id(format_id, export_format_catalog())
    exporter = str(item.get("exporter") or "")
    content_profile = str(item["content_profile"])
    if not exporter or content_profile not in {"long", "short", "one_page"}:
        raise HTTPException(status_code=422, detail="Prompt planning is currently available for the formal, short, and one-page Word exporters.")
    payload = await request.json()
    prompt = str(payload.get("prompt") or "").strip()
    if len(prompt) < 10:
        raise HTTPException(status_code=400, detail="Describe the export you want in a little more detail.")
    if len(prompt) > 12000:
        raise HTTPException(status_code=400, detail="The export instructions are too long; keep them below 12,000 characters.")
    with connect() as con:
        candidates = distinct_publications([row for row in export_publication_rows(con) if eligible_export_publication(row)])
        settings = cv_import_settings(con, include_secret=True)
    if not candidates:
        raise HTTPException(status_code=422, detail="There are no eligible peer-reviewed publications to plan with.")
    compact_candidates = [
        {
            "id": row["id"],
            "title": row.get("title"),
            "authors": row.get("authors"),
            "venue": row.get("venue"),
            "year": row.get("year"),
            "impact_factor": row.get("impact_factor"),
            "citations": row.get("openalex_cited_by_count"),
            "authorship": row.get("authorship"),
            "baseline_score": row.get("score"),
        }
        for row in candidates
    ]
    model_prompt = f"""
You are planning an academic CV export for a scientist.
Convert the user's natural-language instructions into the strict JSON export plan schema.

Safety and fidelity rules:
- Select only publication IDs present in CANDIDATE_PUBLICATIONS.
- Respect explicit maximums as hard limits.
- Put every publication the user explicitly requires in required_publication_ids and selected_publication_ids.
- Interpret "first or last author" using the supplied authorship value.
- Use impact_factor when present. Missing impact factor is unknown, not zero-quality.
- Balance recency and impact exactly as the user requests; do not invent metadata.
- Choose a practical section_strategy, but do not claim exact Word pagination.
- Briefly explain the interpretation and disclose ambiguity in warnings.

FORMAT:
{json.dumps({"id": item["id"], "name": item["name"], "content_profile": content_profile, "length": item.get("length"), "focus": item.get("focus")}, ensure_ascii=False)}

USER_INSTRUCTIONS:
{prompt}

CANDIDATE_PUBLICATIONS:
{json.dumps(compact_candidates, ensure_ascii=False)}
""".strip()
    plan_raw, warning = llm_json(model_prompt, EXPORT_PLAN_SCHEMA, settings)
    if warning or not isinstance(plan_raw, dict):
        raise HTTPException(status_code=502, detail=f"The configured LLM could not create an export plan. {warning or ''}".strip())
    plan = validate_export_plan(plan_raw, candidates, prompt, format_id)
    if warning:
        plan["warnings"].append(warning)
    with connect() as con:
        if content_profile in {"short", "one_page"}:
            profile = "ultrashort" if content_profile == "one_page" else "short"
            config = validate_export_profile(profile)
            previous_settings = row_dict(
                con.execute("SELECT publication_limit, authorship_filter FROM export_settings WHERE profile=?", (profile,)).fetchone()
            ) or {"publication_limit": 10, "authorship_filter": "first_last"}
            previous_rows = con.execute(
                f"""
                SELECT id
                FROM publications
                WHERE {config['flag']}=1
                ORDER BY COALESCE({config['order']}, {config['fallback_order']}, 999), id
                """
            ).fetchall()
            existing_raw = get_setting(con, export_plan_setting_key(format_id))
            try:
                existing_plan = json.loads(existing_raw) if existing_raw else {}
            except json.JSONDecodeError:
                existing_plan = {}
            original_previous = existing_plan.get("previous_selection") if isinstance(existing_plan, dict) else None
            plan["previous_selection"] = original_previous if isinstance(original_previous, dict) else {
                **previous_settings,
                "publication_ids": [int(row["id"]) for row in previous_rows],
            }
        apply_export_plan(con, plan, content_profile)
        set_setting(con, export_plan_setting_key(format_id), json.dumps(plan, ensure_ascii=False))
        con.commit()
    return {"ok": True, "active": True, "provider": settings.get("provider"), "plan": plan}


@app.delete("/api/export-formats/{format_id}/prompt-plan")
def clear_export_prompt_plan(format_id: str) -> dict[str, Any]:
    item = export_format_by_id(format_id, export_format_catalog())
    with connect() as con:
        raw = get_setting(con, export_plan_setting_key(format_id))
        try:
            plan = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            plan = {}
        content_profile = str(item["content_profile"])
        previous = plan.get("previous_selection") if isinstance(plan, dict) else None
        if content_profile in {"short", "one_page"} and isinstance(previous, dict):
            profile = "ultrashort" if content_profile == "one_page" else "short"
            config = validate_export_profile(profile)
            con.execute(f"UPDATE publications SET {config['flag']}=0, {config['order']}=NULL")
            for index, publication_id in enumerate(previous.get("publication_ids") or [], 1):
                if str(publication_id).isdigit():
                    con.execute(
                        f"UPDATE publications SET {config['flag']}=1, {config['order']}=? WHERE id=?",
                        (index, int(publication_id)),
                    )
            con.execute(
                """
                INSERT INTO export_settings (profile, publication_limit, authorship_filter)
                VALUES (?, ?, ?)
                ON CONFLICT(profile) DO UPDATE SET
                  publication_limit=excluded.publication_limit,
                  authorship_filter=excluded.authorship_filter
                """,
                (
                    profile,
                    max(1, min(50, int(previous.get("publication_limit") or 10))),
                    previous.get("authorship_filter") if previous.get("authorship_filter") in {"first_last", "first", "last", "all"} else "first_last",
                ),
            )
        con.execute("DELETE FROM app_settings WHERE key=?", (export_plan_setting_key(format_id),))
        con.commit()
    return {"ok": True, "active": False}


@app.post("/api/export-formats/{format_id}/install")
def install_export_format(format_id: str) -> dict[str, Any]:
    formats = export_format_catalog()
    item = export_format_by_id(format_id, formats)
    installed = installed_export_format_ids(formats)
    if format_id not in installed:
        installed.append(format_id)
    write_installed_export_format_ids(installed, formats)
    return {"ok": True, "format_id": item["id"], "installed": True}


@app.delete("/api/export-formats/{format_id}/install")
def uninstall_export_format(format_id: str) -> dict[str, Any]:
    # Compatibility for clients that loaded the export-format UI before
    # custom templates gained their dedicated Delete action. Those clients
    # send the generic Remove request for every installed card.
    if format_id.startswith("custom."):
        delete_export_template(format_id)
        return {
            "ok": True,
            "format_id": format_id,
            "installed": False,
            "deleted": True,
        }
    formats = export_format_catalog()
    item = export_format_by_id(format_id, formats)
    installed = [item_id for item_id in installed_export_format_ids(formats) if item_id != format_id]
    write_installed_export_format_ids(installed, formats)
    return {"ok": True, "format_id": item["id"], "installed": False}


@app.get("/api/export-settings")
def export_settings() -> dict[str, Any]:
    with connect() as con:
        selected = get_setting(con, "long_cv_publication_categories")
        categories = [item for item in (selected or "").split(",") if item in LONG_CV_PUBLICATION_CATEGORIES]
        if not categories:
            categories = [key for key in LONG_CV_PUBLICATION_CATEGORIES if key in DEFAULT_LONG_CV_PUBLICATION_CATEGORIES]
        return {
            "home_language_label": get_setting(con, "home_language_label") or "Deutsch",
            "citation_style": validate_citation_style(get_setting(con, "export_citation_style")),
            "citation_style_options": CITATION_STYLES,
            "long_cv_publication_categories": categories,
            "long_cv_publication_category_options": [
                {"key": key, "label": label, "default": key in DEFAULT_LONG_CV_PUBLICATION_CATEGORIES}
                for key, label in LONG_CV_PUBLICATION_CATEGORIES.items()
            ],
        }


@app.put("/api/export-settings")
async def update_export_settings(request: Request) -> dict[str, Any]:
    payload = await request.json()
    label = str(payload.get("home_language_label") or "").strip() or "Deutsch"
    requested_categories = payload.get("long_cv_publication_categories")
    citation_style = validate_citation_style(str(payload.get("citation_style") or DEFAULT_CITATION_STYLE))
    categories: list[str] = []
    if isinstance(requested_categories, list):
        categories = [str(item) for item in requested_categories if str(item) in LONG_CV_PUBLICATION_CATEGORIES]
    with connect() as con:
        set_setting(con, "home_language_label", label[:40])
        set_setting(con, "export_citation_style", citation_style)
        if isinstance(requested_categories, list):
            set_setting(con, "long_cv_publication_categories", ",".join(categories))
        con.commit()
    return {
        "ok": True,
        "home_language_label": label[:40],
        "citation_style": citation_style,
        "long_cv_publication_categories": categories,
    }


@app.get("/api/enrichment-settings")
def enrichment_settings() -> dict[str, Any]:
    with connect() as con:
        return {
            "ai_web_discovery_enabled": ai_web_discovery_enabled(con),
            "enrichment_last_run": get_setting(con, "enrichment_last_run") or get_setting(con, "background_enrichment_last_run"),
        }


@app.put("/api/enrichment-settings")
async def update_enrichment_settings(request: Request) -> dict[str, Any]:
    payload = await request.json()
    ai_discovery_enabled = "1" if payload.get("ai_web_discovery_enabled") else "0"
    with connect() as con:
        set_setting(con, "ai_web_discovery_enabled", ai_discovery_enabled)
        con.commit()
    return {
        "ok": True,
        "ai_web_discovery_enabled": ai_discovery_enabled == "1",
    }


def database_population_state(con: sqlite3.Connection) -> dict[str, Any]:
    person = con.execute("SELECT full_name, display_name, work_email FROM person WHERE id=1").fetchone()
    has_person = bool(person and any(str(person[key] or "").strip() for key in person.keys()))
    counts = {
        "entries": int(con.execute("SELECT COUNT(*) FROM cv_entries").fetchone()[0]),
        "publications": int(con.execute("SELECT COUNT(*) FROM publications").fetchone()[0]),
        "identifiers": int(con.execute("SELECT COUNT(*) FROM person_identifiers").fetchone()[0]),
        "inbox": pending_inbox_count(con),
    }
    return {"is_empty": not has_person and not any(counts.values()), "has_person": has_person, "counts": counts}


def onboarding_payload(con: sqlite3.Connection) -> dict[str, Any]:
    population = database_population_state(con)
    enabled = get_setting(con, "onboarding_enabled") == "1"
    if population["is_empty"] and not enabled:
        set_setting(con, "onboarding_enabled", "1")
        enabled = True
    skipped = {item for item in get_setting(con, "onboarding_skipped_steps").split(",") if item}
    completed = get_setting(con, "onboarding_completed") == "1"
    orcid_id = saved_orcid_id(con)
    zotero_connected = bool(get_setting(con, "zotero_api_key"))
    enriched = bool(get_setting(con, "enrichment_last_run") or get_setting(con, "background_enrichment_last_run"))
    step = ""
    if enabled and not completed:
        if population["is_empty"] and "import_cv" not in skipped:
            step = "import_cv"
        elif not orcid_id and "orcid" not in skipped:
            step = "orcid"
        elif not zotero_connected and "zotero" not in skipped:
            step = "zotero"
        elif not enriched and "enrich" not in skipped:
            step = "enrich"
        elif population["counts"]["inbox"] and "inbox" not in skipped:
            step = "inbox"
    return {
        "step": step,
        "enabled": enabled,
        "completed": completed or not step,
        "skipped": sorted(skipped),
        "population": population,
        "orcid_id": orcid_id,
        "zotero_connected": zotero_connected,
        "enriched": enriched,
    }


@app.get("/api/onboarding")
def get_onboarding() -> dict[str, Any]:
    with connect() as con:
        settings = cv_import_settings(con, include_secret=False)
        prefs = cv_import_preferences()
        configuration_skipped = skip_llm_onboarding()
        return {
            **onboarding_payload(con),
            "llm_configured": configuration_skipped or bool(str(prefs.get("provider") or "").strip()),
            "llm_provider": settings.get("provider"),
            "api_key_set": settings.get("api_key_set", False),
            "llm_managed": managed_llm(),
            "skip_llm_configuration": configuration_skipped,
        }


@app.post("/api/onboarding/step")
async def update_onboarding_step(request: Request) -> dict[str, Any]:
    payload = await request.json()
    step = str(payload.get("step") or "").strip()
    action = str(payload.get("action") or "skip").strip()
    allowed = {"import_cv", "orcid", "zotero", "enrich", "inbox"}
    if step not in allowed:
        raise HTTPException(status_code=400, detail="Unknown onboarding step.")
    with connect() as con:
        skipped = {item for item in get_setting(con, "onboarding_skipped_steps").split(",") if item}
        if action == "skip":
            skipped.add(step)
        elif action == "restore":
            skipped.discard(step)
        elif action == "complete":
            set_setting(con, "onboarding_completed", "1")
        else:
            raise HTTPException(status_code=400, detail="Unknown onboarding action.")
        set_setting(con, "onboarding_skipped_steps", ",".join(sorted(skipped)))
        con.commit()
        return {"ok": True, **onboarding_payload(con)}


def compact_person_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


@app.post("/api/orcid/discover")
def discover_orcid() -> dict[str, Any]:
    with connect() as con:
        existing = saved_orcid_id(con)
        if existing:
            return {"ok": True, "auto_linked": False, "orcid_id": existing, "candidates": []}
        person = con.execute(
            "SELECT full_name, display_name, own_institution_name FROM person WHERE id=1"
        ).fetchone()
        name = str((person["full_name"] if person else "") or (person["display_name"] if person else "") or "").strip()
        institution = str((person["own_institution_name"] if person else "") or "").strip()
        institutions = [
            str(row["organization"] or "").strip()
            for row in con.execute(
                """
                SELECT organization FROM cv_entries
                WHERE section_key IN ('academic_appointments', 'hospital_appointments', 'professional_positions', 'education')
                  AND COALESCE(organization, '') != ''
                ORDER BY
                  CASE WHEN COALESCE(end_date, '')='' OR lower(end_date) IN ('present', 'current', 'ongoing') THEN 0 ELSE 1 END,
                  start_date DESC,
                  id DESC
                LIMIT 8
                """
            ).fetchall()
        ]
        if len(name.split()) < 2:
            return {"ok": True, "auto_linked": False, "orcid_id": "", "candidates": [], "warning": "Import or enter the person’s full name first."}
    parts = name.split()
    query = f'given-names:"{parts[0]}" AND family-name:"{parts[-1]}"'
    url = (
        "https://pub.orcid.org/v3.0/expanded-search/?"
        + urllib.parse.urlencode({"q": query, "start": 0, "rows": 5})
    )
    payload = fetch_json_url(url)
    candidates: list[dict[str, Any]] = []
    expected_name_parts = compact_person_name(name).split()
    expected_institutions = [
        compact_person_name(value)
        for value in [institution, *institutions]
        if compact_person_name(value)
    ]
    for row in payload.get("expanded-result") or []:
        orcid_id = clean_orcid_id(str(row.get("orcid-id") or ""))
        candidate_name = " ".join(
            part for part in [str(row.get("given-names") or "").strip(), str(row.get("family-names") or "").strip()] if part
        )
        institutions = [str(item or "").strip() for item in row.get("institution-name") or [] if str(item or "").strip()]
        candidate_name_parts = compact_person_name(candidate_name).split()
        exact_name = bool(
            expected_name_parts
            and candidate_name_parts
            and expected_name_parts[0] == candidate_name_parts[0]
            and expected_name_parts[-1] == candidate_name_parts[-1]
        )
        institution_match = False
        for expected in expected_institutions:
            expected_tokens = set(expected.split())
            for item in institutions:
                actual = compact_person_name(item)
                actual_tokens = set(actual.split())
                overlap = len(expected_tokens & actual_tokens) / min(len(expected_tokens), len(actual_tokens)) if expected_tokens and actual_tokens else 0
                if expected in actual or actual in expected or overlap >= 0.60:
                    institution_match = True
                    break
            if institution_match:
                break
        if orcid_id:
            candidates.append(
                {
                    "orcid_id": orcid_id,
                    "name": candidate_name,
                    "institutions": institutions,
                    "exact_name": exact_name,
                    "institution_match": institution_match,
                }
            )
    exact = [row for row in candidates if row["exact_name"]]
    high_confidence = [row for row in exact if row["institution_match"]]
    chosen = high_confidence[0] if len(high_confidence) == 1 else (exact[0] if len(exact) == 1 and len(candidates) == 1 else None)
    if chosen:
        with connect() as con:
            upsert_person_orcid_identifier(
                con,
                chosen["orcid_id"],
                source="ORCID public search",
                notes="Automatically linked after exact-name high-confidence onboarding match.",
            )
            con.commit()
        schedule_background_refresh(profiles=True)
        return {"ok": True, "auto_linked": True, "orcid_id": chosen["orcid_id"], "candidates": candidates}
    return {"ok": True, "auto_linked": False, "orcid_id": "", "candidates": candidates}


@app.post("/api/orcid/link")
async def link_orcid(request: Request) -> dict[str, Any]:
    payload = await request.json()
    orcid_id = clean_orcid_id(str(payload.get("orcid_id") or ""))
    if not re.fullmatch(r"\d{4}-\d{4}-\d{4}-[\dX]{4}", orcid_id, flags=re.I):
        raise HTTPException(status_code=400, detail="Enter a valid ORCID iD.")
    with connect() as con:
        upsert_person_orcid_identifier(con, orcid_id, source="onboarding", notes="Linked during onboarding.")
        con.commit()
    schedule_background_refresh(profiles=True)
    return {"ok": True, "orcid_id": orcid_id}


@app.get("/api/database")
def database_info() -> dict[str, Any]:
    db = active_db_path()
    with connect() as con:
        counts = {
            "entries": int(con.execute("SELECT COUNT(*) FROM cv_entries").fetchone()[0]),
            "publications": int(con.execute("SELECT COUNT(*) FROM publications").fetchone()[0]),
            "identifiers": int(con.execute("SELECT COUNT(*) FROM person_identifiers").fetchone()[0]),
        }
        person = con.execute("SELECT * FROM person WHERE id=1").fetchone()
        has_person = bool(person and any(str(person[key] or "").strip() for key in ("full_name", "display_name", "work_email")))
    return {
        "active": str(db),
        "active_name": db.name,
        "example": str(EXAMPLE_DB),
        "default": str(DEFAULT_DB),
        "is_example": db.resolve() == EXAMPLE_DB.resolve(),
        "is_default": db.resolve() == DEFAULT_DB.resolve(),
        "is_empty": not has_person and not any(counts.values()),
        "counts": counts,
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
        raise HTTPException(status_code=400, detail="The example database name is reserved. Choose a different database name.")
    try:
        create_blank_database(path)
    except FileExistsError:
        raise HTTPException(status_code=409, detail=f"A database with this name already exists: {filename}") from None
    db = set_active_db(path)
    return database_payload(db)


@app.post("/api/database/rename")
async def rename_database(request: Request) -> dict[str, Any]:
    payload = await request.json()
    current = active_db_path()
    filename = sanitize_database_name(payload.get("name"))
    target = current.with_name(filename)
    if current.resolve() == EXAMPLE_DB.resolve():
        raise HTTPException(status_code=400, detail="The bundled example database cannot be renamed.")
    if target.resolve() == EXAMPLE_DB.resolve():
        raise HTTPException(status_code=400, detail="The example database name is reserved.")
    if target.resolve() == current.resolve():
        return database_payload(current)
    if target.exists():
        raise HTTPException(status_code=409, detail=f"A database with this name already exists: {filename}")
    try:
        current.replace(target)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not rename database: {exc}") from exc
    if os.environ.get("VITAMINE_DB"):
        os.environ["VITAMINE_DB"] = str(target)
    db = set_active_db(target)
    with connect() as con:
        con.execute(
            "UPDATE documents SET source_path=? WHERE slug='manual_cv_database' AND source_path=?",
            (str(db), str(current)),
        )
        con.commit()
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


def cv_import_preferences() -> dict[str, Any]:
    raw = read_preferences().get("cv_import")
    return raw if isinstance(raw, dict) else {}


def write_cv_import_preferences(values: dict[str, Any]) -> None:
    prefs = read_preferences()
    current = prefs.get("cv_import")
    if not isinstance(current, dict):
        current = {}
    current.update(values)
    prefs["cv_import"] = current
    write_preferences(prefs)


def migrate_cv_import_preferences(con: sqlite3.Connection) -> None:
    if isinstance(read_preferences().get("cv_import"), dict):
        return
    migrated: dict[str, Any] = {}
    for key in CV_IMPORT_SETTING_FIELDS:
        value = get_setting(con, f"cv_import_{key}")
        if value:
            migrated[key] = value
    api_key = get_setting(con, "cv_import_api_key")
    if api_key:
        migrated["api_key"] = api_key
    if migrated:
        write_cv_import_preferences(migrated)
        con.execute("DELETE FROM app_settings WHERE key='cv_import_api_key'")
        con.commit()


def cv_import_settings(con: sqlite3.Connection, include_secret: bool = False) -> dict[str, Any]:
    migrate_cv_import_preferences(con)
    prefs = cv_import_preferences()
    if managed_llm():
        settings = {key: str(default) for key, default in CV_IMPORT_SETTING_FIELDS.items()}
    else:
        settings = {
            key: str(prefs.get(key) or get_setting(con, f"cv_import_{key}") or default)
            for key, default in CV_IMPORT_SETTING_FIELDS.items()
        }
    shared_openai_key = os.environ.get("OPENAI_API_KEY") if settings["provider"] == "openai" else ""
    api_key = str(
        shared_openai_key
        if managed_llm()
        else (prefs.get("api_key") or shared_openai_key or get_setting(con, "cv_import_api_key") or "")
    )
    api_key = normalize_api_key(api_key)
    settings["api_key_set"] = bool(api_key)
    settings["managed"] = managed_llm()
    settings["configuration_allowed"] = llm_user_configuration_allowed()
    if include_secret:
        settings["api_key"] = api_key
    return settings


def cv_import_upload_name(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem).strip("._-") or "uploaded-cv"
    if suffix not in {".docx", ".pdf", ".txt", ".md"}:
        raise HTTPException(status_code=400, detail="Please upload a DOCX, PDF, TXT, or Markdown CV.")
    return f"{stem}{suffix}"


def api_http_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        detail = exc.read().decode("utf-8", errors="replace")
    except OSError:
        detail = ""
    return re.sub(r"\s+", " ", detail).strip()[-1200:] or str(exc.reason)


def normalize_api_key(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip().strip("\"'"))
    if text.lower().startswith("bearer"):
        text = text[6:].strip()
    return text


def looks_like_openai_api_key(value: str) -> bool:
    return bool(re.fullmatch(r"sk-[A-Za-z0-9_-]+", normalize_api_key(value)))


def test_openai_api_connection(settings: dict[str, Any]) -> None:
    provider = str(settings.get("provider") or "")
    if provider not in {"openai", "openai_compatible"}:
        return
    base = str(settings.get("api_base_url") or "https://api.openai.com/v1").rstrip("/")
    model = str(settings.get("api_model") or "gpt-4.1-mini").strip()
    api_key = normalize_api_key(str(settings.get("api_key") or ""))
    if provider == "openai":
        base = "https://api.openai.com/v1"
    if provider == "openai" and not api_key:
        raise HTTPException(status_code=400, detail="Paste an OpenAI API key before saving OpenAI API settings.")
    if provider == "openai" and not looks_like_openai_api_key(api_key):
        raise HTTPException(status_code=400, detail="OpenAI API keys should start with sk-. Clear the key field or paste a valid OpenAI API key.")
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if provider == "openai":
        request = urllib.request.Request(
            f"{base}/models/{urllib.parse.quote(model, safe='')}",
            headers=headers,
            method="GET",
        )
    else:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "temperature": 0,
            "max_tokens": 8,
        }
        request = urllib.request.Request(
            f"{base}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
    try:
        with urllib.request.urlopen(request, timeout=12):
            return
    except urllib.error.HTTPError as exc:
        detail = api_http_error_detail(exc)
        if exc.code in {401, 403}:
            raise HTTPException(status_code=400, detail=f"OpenAI API authentication failed ({exc.code}). Check the key and model access. {detail}") from exc
        raise HTTPException(status_code=400, detail=f"OpenAI API connection test failed ({exc.code}). {detail}") from exc
    except urllib.error.URLError as exc:
        raise HTTPException(status_code=400, detail=f"OpenAI API connection test failed: {exc}") from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=400, detail="OpenAI API connection test timed out.") from exc


@app.post("/api/cv-import/test-connection")
async def test_cv_import_connection(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if managed_llm():
        with connect() as con:
            settings = cv_import_settings(con, include_secret=True)
        test_openai_api_connection(settings)
        return {"ok": True, "message": "Managed API connection successful."}
    provider = str(payload.get("provider") or "none").strip()
    if provider not in {"none", "bundled_llama", "ollama", "openai", "openai_compatible"}:
        raise HTTPException(status_code=400, detail="Unsupported CV import provider")
    with connect() as con:
        current = cv_import_settings(con, include_secret=True)
    settings = {
        **current,
        **{
            key: str(payload.get(key) or current.get(key) or default).strip()
            for key, default in CV_IMPORT_SETTING_FIELDS.items()
        },
    }
    api_key = normalize_api_key(str(payload.get("api_key") or current.get("api_key") or ""))
    if os.environ.get("VITAMINE_CLOUD_WORKER") == "1" and provider == "openai_compatible":
        api_key = normalize_api_key(str(payload.get("api_key") or ""))
    settings["api_key"] = api_key
    if provider in {"openai", "openai_compatible"}:
        test_openai_api_connection(settings)
        return {"ok": True, "message": "API connection successful."}
    if provider == "ollama":
        base = str(settings.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")
        try:
            with urllib.request.urlopen(f"{base}/api/tags", timeout=5):
                return {"ok": True, "message": "Ollama is reachable."}
        except (urllib.error.URLError, TimeoutError) as exc:
            raise HTTPException(status_code=400, detail=f"Could not connect to Ollama at {base}: {exc}") from exc
    if provider == "bundled_llama":
        configured = str(settings.get("bundled_llama_model_path") or "").strip()
        model_path = Path(configured).expanduser() if configured else bundled_model_path()
        if model_path and model_path.exists():
            return {"ok": True, "message": "Bundled local model is ready."}
        return {"ok": True, "message": "Bundled local model selected; it will be prepared when the import starts."}
    return {"ok": True, "message": "Heuristic extraction does not require a connection."}


@app.get("/api/cv-import/settings")
def get_cv_import_settings() -> dict[str, Any]:
    with connect() as con:
        return cv_import_settings(con, include_secret=False)


@app.put("/api/cv-import/settings")
async def update_cv_import_settings(request: Request) -> dict[str, Any]:
    if not llm_user_configuration_allowed():
        raise HTTPException(status_code=403, detail="Language-model settings are managed by this VitaMine deployment.")
    payload = await request.json()
    provider = str(payload.get("provider") or "none").strip()
    if provider not in {"none", "bundled_llama", "ollama", "openai", "openai_compatible"}:
        raise HTTPException(status_code=400, detail="Unsupported CV import provider")
    with connect() as con:
        current_settings = cv_import_settings(con, include_secret=True)
        prefs_update: dict[str, Any] = {"provider": provider}
        for key, default in CV_IMPORT_SETTING_FIELDS.items():
            if key == "provider":
                continue
            value = str(payload.get(key) or default).strip()
            prefs_update[key] = value
        if provider == "openai":
            prefs_update["api_base_url"] = "https://api.openai.com/v1"
        api_key = normalize_api_key(str(payload.get("api_key") or ""))
        test_settings = {**current_settings, **prefs_update}
        if api_key:
            if provider == "openai" and not looks_like_openai_api_key(api_key):
                raise HTTPException(status_code=400, detail="OpenAI API keys should start with sk-. Clear the key field or paste a valid OpenAI API key.")
            prefs_update["api_key"] = api_key
            test_settings["api_key"] = api_key
        elif provider == "openai":
            test_settings["api_key"] = current_settings.get("api_key") or ""
        if provider == "openai" or (provider == "openai_compatible" and api_key):
            test_openai_api_connection(test_settings)
        write_cv_import_preferences(prefs_update)
        con.execute("DELETE FROM app_settings WHERE key='cv_import_api_key'")
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
            settings["review_mode"] = "inbox"
            settings["profile_search_enabled"] = ai_web_discovery_enabled(con)
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
            "publications_inserted": sum(int(result.get("publications_inserted") or 0) for result in results),
            "narratives_imported": sum(int(result.get("narrative_imported") or 0) for result in results),
            "candidates_staged": sum(int(result.get("candidates_staged") or 0) for result in results),
            "staged": {
                "entries": sum(int((result.get("staged") or {}).get("entries") or 0) for result in results),
                "publications": sum(int((result.get("staged") or {}).get("publications") or 0) for result in results),
                "contributions": sum(int((result.get("staged") or {}).get("contributions") or 0) for result in results),
                "person": sum(int((result.get("staged") or {}).get("person") or 0) for result in results),
                "identifiers": sum(int((result.get("staged") or {}).get("identifiers") or 0) for result in results),
                "narrative": sum(int((result.get("staged") or {}).get("narrative") or 0) for result in results),
                "remembered_rejections": sum(int((result.get("staged") or {}).get("remembered_rejections") or 0) for result in results),
            },
            "person_fields": max([int(result.get("person_fields") or 0) for result in results] or [0]),
            "used_llm": any(bool(result.get("used_llm")) for result in results),
            "provider": results[0].get("provider") if results else "none",
            "review_mode": any(bool(result.get("review_mode")) for result in results),
            "warnings": warnings,
            "results": results,
        }
    )


@app.post("/api/actions/sync-zotero")
def sync_zotero_action() -> JSONResponse:
    result = run_script("sync_zotero.py")
    if result.returncode != 0:
        return JSONResponse({"ok": False, "stderr": result.stderr[-4000:]}, status_code=500)
    schedule_background_refresh(publications_changed=True)
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
    schedule_background_refresh(publications_changed=True)
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
    result = run_script("enrich_publications_by_doi.py", "--resolve-missing")
    if result.returncode != 0:
        return JSONResponse({"ok": False, "stderr": result.stderr[-4000:]}, status_code=500)
    return JSONResponse({"ok": True, "stdout": result.stdout, "report": "/output/doi_enrichment_report.json"})


@app.post("/api/actions/enrich-cv")
def enrich_cv_action() -> JSONResponse:
    try:
        payload = enrich_cv_job(update_last_run=True)
        schedule_background_refresh(publications_changed=True)
        return JSONResponse(payload)
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "stderr": str(exc)[-4000:]}, status_code=500)


@app.post("/api/actions/sync-orcid")
def sync_orcid_action() -> JSONResponse:
    result = run_script("sync_orcid.py")
    if result.returncode != 0:
        return JSONResponse({"ok": False, "stderr": result.stderr[-4000:]}, status_code=500)
    schedule_background_refresh(publications_changed=True, institutions=True)
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
    with connect() as con:
        has_prompt_plan = bool(get_setting(con, export_plan_setting_key("vitamine.short-academic")))
    curated = None if has_prompt_plan else run_script("curate_short_cv.py")
    if curated is not None and curated.returncode != 0:
        return JSONResponse({"ok": False, "stderr": curated.stderr[-4000:]}, status_code=500)
    result = run_script("build_short_cv.py", "--lang", lang)
    if result.returncode != 0:
        return JSONResponse({"ok": False, "stderr": result.stderr[-4000:]}, status_code=500)
    cache_key = str(int(time.time()))
    plan_note = "[prompt-plan]\nPreserved prompt-selected publications.\n" if has_prompt_plan else ""
    return JSONResponse(build_response(plan_note + (curated.stdout if curated else "") + result.stdout, cache_key, {"language": lang}))


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


def built_docx_path(relative_docx: str) -> Path:
    relative = Path(str(relative_docx or ""))
    if relative.is_absolute():
        return relative.resolve()
    if relative.parts and relative.parts[0] == "output":
        return (OUTPUT / Path(*relative.parts[1:])).resolve()
    return (ROOT / relative).resolve()


def build_custom_export_template(template_id: str, lang: str = "en") -> JSONResponse:
    lang = "de" if lang == "de" else "en"
    with connect() as con:
        row = custom_export_template_row(con, template_id)
        source_docx = bytes(row["source_docx"])
        blueprint = parsed_template_blueprint(row["blueprint_json"])
        profile = str(row["content_profile"])
        template_name = str(row["name"])
    builders = {
        "one_page": build_ultrashort_tabular_action,
        "short": build_short_action,
        "long": build_long_action,
        "biosketch": build_biosketch_action,
    }
    builder = builders.get(profile)
    if builder is None:
        raise HTTPException(status_code=422, detail="This custom template has an unsupported content classification.")
    canonical_response = builder(lang)
    if canonical_response.status_code >= 400:
        return canonical_response
    canonical_payload = json.loads(canonical_response.body)
    canonical_path = built_docx_path(str(canonical_payload.get("docx_path") or ""))
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", template_name).strip("._-") or "custom_cv"
    suffix = "_de" if lang == "de" else ""
    output_path = OUTPUT / f"{safe_stem[:80]}_{template_id.rsplit('.', 1)[-1][:8]}{suffix}.docx"
    try:
        with connect() as con:
            render_report = render_custom_docx_template(source_docx, blueprint, canonical_path, output_path, con)
            settings = cv_import_settings(con, include_secret=True)
            quality_audit = run_export_quality_audit(con, output_path, llm_json, settings)
        if quality_audit["applied_count"]:
            canonical_response = builder(lang)
            if canonical_response.status_code >= 400:
                return canonical_response
            canonical_payload = json.loads(canonical_response.body)
            canonical_path = built_docx_path(str(canonical_payload.get("docx_path") or ""))
            with connect() as con:
                render_report = render_custom_docx_template(source_docx, blueprint, canonical_path, output_path, con)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return JSONResponse({"ok": False, "stderr": str(exc)[:1000]}, status_code=500)
    cache_key = str(int(time.time()))
    payload = build_response(
        f"docx: output/{output_ref(output_path)}",
        cache_key,
        {
            "language": lang,
            "template_id": template_id,
            "template_render": render_report,
            "quality_audit": quality_audit,
        },
    )
    return JSONResponse(payload)


@app.post("/api/actions/export/{format_id}")
def build_installed_export_format(format_id: str, lang: str = "en") -> JSONResponse:
    if format_id.startswith("custom."):
        return build_custom_export_template(format_id, lang)
    formats = export_format_catalog()
    item = export_format_by_id(format_id, formats)
    if format_id not in installed_export_format_ids(formats):
        raise HTTPException(status_code=409, detail="Install this format before exporting it.")
    exporter = item.get("exporter")
    content_profile = item["content_profile"]
    builders = {
        "one_page": build_ultrashort_tabular_action,
        "short": build_short_action,
        "long": build_long_action,
        "biosketch": build_biosketch_action,
    }
    if not exporter:
        raise HTTPException(status_code=422, detail="This local format is a preview package; its Word exporter is not implemented yet.")
    response = builders[content_profile](lang)
    if response.status_code >= 400:
        return response
    payload = json.loads(response.body)
    relative_docx = str(payload.get("docx_path") or "")
    docx_path = built_docx_path(relative_docx) if relative_docx else Path()
    try:
        with connect() as con:
            settings = cv_import_settings(con, include_secret=True)
            quality_audit = run_export_quality_audit(con, docx_path, llm_json, settings)
        if quality_audit["applied_count"]:
            rebuilt = builders[content_profile](lang)
            if rebuilt.status_code >= 400:
                return rebuilt
            payload = json.loads(rebuilt.body)
        payload["quality_audit"] = quality_audit
    except Exception as exc:
        # A quality check must never prevent delivery of an otherwise valid CV.
        payload["quality_audit"] = {
            "status": "failed",
            "applied_count": 0,
            "review_count": 0,
            "warning": str(exc)[:500],
            "issues": [],
        }
    return JSONResponse(payload)


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
