#!/usr/bin/env python3
"""Import an uploaded CV into the active VitaMine database."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from docx import Document

from vitamine.paths import APP_SUPPORT, ROOT, bundled_model_path, tool_path
from vitamine.scripts.import_background_docs import (
    clean_markup,
    extract_biosketch_contributions,
    extract_entries,
    split_sections,
)


PERSON_FIELDS = [
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
    "orcid_id",
]

ENTRY_FIELDS = [
    "section_key",
    "subcategory",
    "start_date",
    "end_date",
    "title",
    "organization",
    "location",
    "role",
    "amount",
    "description",
    "raw_text",
    "confidence",
    "include_extended",
    "include_long",
    "include_short",
    "include_biosketch",
    "language",
    "source_note",
]

SECTION_KEYS = {
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

PUBLICATION_HINTS = (
    "publication",
    "bibliography",
    "scholarship",
    "peer-reviewed",
    "peer reviewed",
    "journal articles",
    "abstracts",
    "patents",
)

HEADING_HINTS = {
    "education": ("education", "training", "medical school", "graduate"),
    "postdoctoral_training": ("postdoctoral", "post-doctoral", "residency", "fellowship"),
    "academic_appointments": ("academic appointment", "faculty appointment", "appointment"),
    "hospital_appointments": ("hospital", "clinical appointment"),
    "professional_positions": ("employment", "professional position", "experience", "positions"),
    "committee_service": ("committee", "service"),
    "professional_societies": ("societies", "memberships", "professional society"),
    "grant_review": ("grant review", "study section", "review activities"),
    "editorial_activities": ("editorial", "reviewer"),
    "honors": ("honors", "awards", "prizes", "recognition"),
    "funding": ("funding", "grants", "research support"),
    "teaching": ("teaching", "courses", "lectures"),
    "mentoring": ("mentoring", "supervision", "trainees"),
    "invited_presentations": ("presentations", "invited talks", "lectureships"),
    "clinical_activities": ("clinical activities", "clinical innovations"),
    "education_innovations": ("education innovations", "teaching innovations"),
    "community_service": ("community service", "outreach"),
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def run_pandoc(path: Path) -> str | None:
    pandoc = tool_path("pandoc") or shutil.which("pandoc")
    if not pandoc:
        return None
    return subprocess.check_output([str(pandoc), "-t", "markdown", "--wrap=none", str(path)], cwd=ROOT, text=True)


def docx_to_text(path: Path) -> str:
    markdown = run_pandoc(path)
    if markdown:
        return markdown
    doc = Document(path)
    lines: list[str] = []
    for paragraph in doc.paragraphs:
        if paragraph.text.strip():
            lines.append(paragraph.text.strip())
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells if cell.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def pdf_to_text(path: Path) -> str:
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        return subprocess.check_output([pdftotext, "-layout", str(path), "-"], text=True, errors="replace")
    try:
        import fitz  # type: ignore

        with fitz.open(path) as doc:
            return "\n".join(page.get_text("text") for page in doc)
    except Exception:
        pass
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise RuntimeError("Could not extract PDF text. Install Poppler, PyMuPDF, or pypdf for PDF CV import.") from exc


def extract_text(path: Path) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return docx_to_text(path), "docx"
    if suffix == ".pdf":
        return pdf_to_text(path), "pdf"
    if suffix in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace"), suffix.lstrip(".")
    raise RuntimeError("Please upload a DOCX, PDF, TXT, or Markdown CV.")


def classify_heading(line: str) -> str | None:
    clean = clean_markup(line).strip(":- ").casefold()
    if not clean or len(clean) > 90:
        return None
    if any(hint in clean for hint in PUBLICATION_HINTS):
        return "publications"
    for key, hints in HEADING_HINTS.items():
        if any(hint in clean for hint in hints):
            return key
    return None


def generic_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current = {"section_key": "document_header", "title": "Document Header", "raw_markdown": "", "ordinal": 0}
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        key = classify_heading(line)
        looks_like_heading = bool(key) and not re.search(r"\b(19\d{2}|20\d{2})\b", line)
        if looks_like_heading:
            if lines:
                current["raw_markdown"] = "\n".join(lines).strip()
                sections.append(current)
            current = {
                "section_key": key,
                "title": clean_markup(line),
                "raw_markdown": "",
                "ordinal": len(sections),
            }
            lines = []
        else:
            lines.append(raw)
    if lines:
        current["raw_markdown"] = "\n".join(lines).strip()
        sections.append(current)
    return sections


def heuristic_person(text: str) -> dict[str, str]:
    lines = [clean_markup(line) for line in text.splitlines() if clean_markup(line)]
    person: dict[str, str] = {}
    for line in lines[:30]:
        if "@" in line and not person.get("work_email"):
            match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", line)
            if match:
                person["work_email"] = match.group(0)
        if "orcid" in line.casefold() and not person.get("orcid_id"):
            match = re.search(r"\d{4}-\d{4}-\d{4}-[\dX]{4}", line)
            if match:
                person["orcid_id"] = match.group(0)
    for line in lines[:8]:
        if len(line) < 80 and re.search(r"[A-Za-z]", line) and not any(char.isdigit() for char in line):
            if len(line.split()) in {2, 3, 4, 5}:
                person["full_name"] = line
                person["display_name"] = line
                break
    return person


def heuristic_entries(text: str) -> tuple[list[dict[str, Any]], int]:
    sections = split_sections(text)
    if len(sections) <= 1:
        sections = generic_sections(text)
    entries: list[dict[str, Any]] = []
    skipped_publication_sections = 0
    for section in sections:
        if section["section_key"] == "publications":
            skipped_publication_sections += 1
            continue
        entries.extend(extract_entries(section))
    for entry in entries:
        entry.setdefault("include_extended", 1)
        entry.setdefault("include_long", 1)
        entry.setdefault("include_short", 1 if entry.get("section_key") in {"education", "honors", "academic_appointments"} else 0)
        entry.setdefault("include_biosketch", 1 if entry.get("section_key") == "honors" else 0)
        entry.setdefault("language", "en")
        entry.setdefault("source_note", "Imported from uploaded CV by heuristic parser.")
    return entries, skipped_publication_sections


def heuristic_contributions(text: str) -> list[dict[str, Any]]:
    contributions: list[dict[str, Any]] = []
    for section in split_sections(text):
        contributions.extend(extract_biosketch_contributions(section))
    return contributions


def cv_json_schema() -> dict[str, Any]:
    entry_properties = {
        field: {"type": ["string", "null"]}
        for field in [
            "section_key",
            "subcategory",
            "start_date",
            "end_date",
            "title",
            "organization",
            "location",
            "role",
            "amount",
            "description",
            "raw_text",
            "confidence",
            "language",
        ]
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "person": {
                "type": "object",
                "additionalProperties": False,
                "properties": {field: {"type": ["string", "null"]} for field in PERSON_FIELDS},
                "required": PERSON_FIELDS,
            },
            "entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": entry_properties,
                    "required": list(entry_properties.keys()),
                },
            },
            "skipped_publication_count": {"type": "integer"},
            "contributions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": ["string", "null"]},
                        "narrative": {"type": ["string", "null"]},
                        "raw_text": {"type": ["string", "null"]},
                        "confidence": {"type": ["string", "null"]},
                    },
                    "required": ["title", "narrative", "raw_text", "confidence"],
                },
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["person", "entries", "skipped_publication_count", "contributions", "warnings"],
    }


def llm_prompt(text: str) -> str:
    labels = "\n".join(f"- {key}: {label}" for key, label in SECTION_KEYS.items())
    return f"""Extract this academic CV into VitaMine database fields.

Rules:
- Return only data that is clearly present in the CV.
- Use these section_key values only:
{labels}
- Do not import publications as entries. Count publication-looking citations in skipped_publication_count if obvious.
- If the CV contains NIH-style Contributions to Science, extract each contribution title and narrative into contributions. Do not import cited products as publications.
- Dates may be years or date ranges as printed in the CV.
- raw_text should preserve the source line/block for each entry.
- confidence should be high, medium, or low.
- language should usually be en unless the CV content is German.

CV text:
{text[:60000]}
"""


def parse_json_response(data: bytes) -> dict[str, Any]:
    payload = json.loads(data.decode("utf-8"))
    content = ""
    if "message" in payload:
        content = payload.get("message", {}).get("content", "")
    elif "choices" in payload:
        content = payload["choices"][0].get("message", {}).get("content", "")
    else:
        return payload
    if isinstance(content, dict):
        return content
    return json.loads(content)


def call_ollama(text: str, settings: dict[str, str]) -> dict[str, Any]:
    base = (settings.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")
    model = settings.get("ollama_model") or "llama3.1:8b"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": llm_prompt(text)}],
        "stream": False,
        "format": cv_json_schema(),
        "options": {"temperature": 0},
    }
    request = urllib.request.Request(
        f"{base}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return parse_json_response(response.read())


def call_openai_compatible(text: str, settings: dict[str, str]) -> dict[str, Any]:
    base = (settings.get("api_base_url") or "https://api.openai.com/v1").rstrip("/")
    model = settings.get("api_model") or "gpt-4.1-mini"
    api_key = settings.get("api_key") or ""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": llm_prompt(text)}],
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "vitamine_cv_import", "strict": True, "schema": cv_json_schema()},
        },
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return parse_json_response(response.read())


def free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_llama_server(base_url: str, process: subprocess.Popen, timeout: float = 240.0) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Bundled llama-server exited before it was ready (code {process.returncode}).")
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=2) as response:
                if response.status < 500:
                    return
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"Bundled llama-server did not become ready: {last_error}")


def call_bundled_llama(text: str, settings: dict[str, str]) -> dict[str, Any]:
    server = tool_path("llama-server")
    if not server:
        raise RuntimeError("Bundled llama-server was not found. Run scripts/install_export_tools.py --tool llama-server before packaging.")
    configured_model = _text(settings.get("bundled_llama_model_path"))
    model = Path(configured_model).expanduser() if configured_model else bundled_model_path()
    if not model or not model.exists():
        raise RuntimeError("Bundled local LLM model was not found. Add vendor/models/vitamine-import.gguf before packaging.")
    model = cached_runtime_model(model)
    port = free_local_port()
    base_url = f"http://127.0.0.1:{port}"
    command = [
        server,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--model",
        str(model),
        "--ctx-size",
        str(settings.get("bundled_llama_ctx_size") or "4096"),
        "--parallel",
        "1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        wait_for_llama_server(base_url, process)
        return call_openai_compatible(
            text,
            {
                "api_base_url": f"{base_url}/v1",
                "api_model": model.stem,
                "api_key": "",
            },
        )
    finally:
        try:
            os.killpg(process.pid, 15)
        except OSError:
            process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, 9)
            except OSError:
                process.kill()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass


def cached_runtime_model(source: Path) -> Path:
    cache_dir = APP_SUPPORT / "models"
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / source.name
    try:
        source_stat = source.stat()
        target_stat = target.stat() if target.exists() else None
        if target_stat and target_stat.st_size == source_stat.st_size and int(target_stat.st_mtime) >= int(source_stat.st_mtime):
            return target
        tmp = target.with_suffix(f"{target.suffix}.tmp")
        shutil.copy2(source, tmp)
        tmp.replace(target)
        return target
    except OSError:
        return source


def llm_extract(text: str, settings: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    provider = settings.get("provider") or "none"
    if provider == "none":
        return None, None
    try:
        if provider == "ollama":
            return call_ollama(text, settings), None
        if provider == "bundled_llama":
            return call_bundled_llama(text, settings), None
        if provider in {"openai", "openai_compatible"}:
            return call_openai_compatible(text, settings), None
    except (OSError, RuntimeError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, KeyError) as exc:
        return None, str(exc)
    return None, f"Unknown CV import provider: {provider}"


def normalize_llm_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    section_key = _text(entry.get("section_key"))
    if section_key not in SECTION_KEYS:
        section_key = "professional_positions"
    raw_text = _text(entry.get("raw_text") or entry.get("description") or entry.get("title"))
    title = _text(entry.get("title"))
    description = _text(entry.get("description"))
    if not raw_text and not title and not description:
        return None
    confidence = _text(entry.get("confidence")).lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"
    return {
        "section_key": section_key,
        "subcategory": _text(entry.get("subcategory")) or None,
        "start_date": _text(entry.get("start_date")) or None,
        "end_date": _text(entry.get("end_date")) or None,
        "title": title or None,
        "organization": _text(entry.get("organization")) or None,
        "location": _text(entry.get("location")) or None,
        "role": _text(entry.get("role")) or None,
        "amount": _text(entry.get("amount")) or None,
        "description": description or None,
        "raw_text": raw_text or title or description,
        "confidence": confidence,
        "include_extended": 1,
        "include_long": 1,
        "include_short": 1 if section_key in {"education", "honors", "academic_appointments"} else 0,
        "include_biosketch": 1 if section_key == "honors" else 0,
        "language": _text(entry.get("language")) or "en",
        "source_note": "Imported from uploaded CV by LLM.",
    }


def merge_person(heuristic: dict[str, Any], llm: dict[str, Any] | None) -> dict[str, Any]:
    person = dict(heuristic)
    if llm:
        for field in PERSON_FIELDS:
            value = _text(llm.get(field))
            if value:
                person[field] = value
    return person


def insert_person(con: sqlite3.Connection, person: dict[str, Any]) -> None:
    values = {field: _text(person.get(field)) or None for field in PERSON_FIELDS}
    assignments = ", ".join(f"{field}=COALESCE(excluded.{field}, person.{field})" for field in PERSON_FIELDS)
    con.execute(
        f"""
        INSERT INTO person (id, {', '.join(PERSON_FIELDS)}, raw_json)
        VALUES (1, {', '.join('?' for _ in PERSON_FIELDS)}, ?)
        ON CONFLICT(id) DO UPDATE SET
        {assignments},
        raw_json=excluded.raw_json
        """,
        (*[values[field] for field in PERSON_FIELDS], json.dumps(values, ensure_ascii=False, indent=2)),
    )


def insert_entries(con: sqlite3.Connection, document_id: int, entries: list[dict[str, Any]]) -> int:
    count = 0
    for entry in entries:
        normalized = normalize_llm_entry(entry)
        if not normalized:
            continue
        con.execute(
            f"""
            INSERT INTO cv_entries (document_id, {', '.join(ENTRY_FIELDS)})
            VALUES (?, {', '.join('?' for _ in ENTRY_FIELDS)})
            """,
            (document_id, *[normalized.get(field) for field in ENTRY_FIELDS]),
        )
        count += 1
    return count


def normalize_contribution(contribution: dict[str, Any]) -> dict[str, str] | None:
    title = _text(contribution.get("title"))
    narrative = _text(contribution.get("narrative"))
    raw_text = _text(contribution.get("raw_text"))
    if not title and raw_text:
        title = raw_text[:120]
    if not title or not (narrative or raw_text):
        return None
    return {
        "title": title,
        "narrative": narrative or raw_text,
        "citations_json": _text(contribution.get("citations_json")) or "[]",
    }


def insert_contributions(con: sqlite3.Connection, document_id: int, contributions: list[dict[str, Any]]) -> int:
    count = 0
    for index, contribution in enumerate(contributions, start=1):
        normalized = normalize_contribution(contribution)
        if not normalized:
            continue
        con.execute(
            """
            INSERT INTO biosketch_contributions (document_id, ordinal, title, narrative, citations_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                document_id,
                contribution.get("ordinal") or index,
                normalized["title"],
                normalized["narrative"],
                normalized["citations_json"],
            ),
        )
        count += 1
    return count


def import_cv_file(con: sqlite3.Connection, path: Path, original_filename: str, settings: dict[str, str]) -> dict[str, Any]:
    text, source_format = extract_text(path)
    if not text.strip():
        raise RuntimeError("No text could be extracted from this CV.")

    imported_at = dt.datetime.now(dt.timezone.utc).isoformat()
    slug_base = re.sub(r"[^a-z0-9]+", "_", Path(original_filename).stem.casefold()).strip("_") or "uploaded_cv"
    slug = f"uploaded_cv_{slug_base}_{dt.datetime.now().strftime('%Y%m%d%H%M%S')}"
    cursor = con.execute(
        """
        INSERT INTO documents (slug, title, source_path, source_format, imported_at, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (slug, original_filename, str(path), source_format, imported_at, "Imported from user-uploaded CV."),
    )
    document_id = int(cursor.lastrowid)

    heuristic = heuristic_person(text)
    heuristic_rows, skipped_sections = heuristic_entries(text)
    heuristic_biosketch = heuristic_contributions(text)
    llm_data, llm_warning = llm_extract(text, settings)
    llm_rows = [normalize_llm_entry(row) for row in (llm_data or {}).get("entries", []) if isinstance(row, dict)]
    llm_entries = [row for row in llm_rows if row]
    llm_contributions = [
        contribution
        for contribution in (llm_data or {}).get("contributions", [])
        if isinstance(contribution, dict)
    ]
    entries = llm_entries if llm_entries else heuristic_rows
    contributions = llm_contributions if llm_contributions else heuristic_biosketch
    person = merge_person(heuristic, (llm_data or {}).get("person") if isinstance(llm_data, dict) else None)

    insert_person(con, person)
    inserted = insert_entries(con, document_id, entries)
    contributions_inserted = insert_contributions(con, document_id, contributions)

    warnings: list[str] = []
    if llm_warning:
        warnings.append(f"LLM import fell back to heuristic parsing: {llm_warning}")
    if not llm_entries and settings.get("provider") != "none":
        warnings.append("No usable structured entries were returned by the LLM; heuristic entries were imported.")
    skipped_publications = int((llm_data or {}).get("skipped_publication_count") or 0) if isinstance(llm_data, dict) else 0
    skipped_publications += skipped_sections
    if skipped_publications:
        warnings.append(f"Skipped publication-looking sections/items ({skipped_publications}); use Zotero sync for publications.")
    for warning in warnings + [str(item) for item in (llm_data or {}).get("warnings", []) if item]:
        con.execute(
            """
            INSERT INTO import_warnings (document_id, warning_type, message, raw_text)
            VALUES (?, 'cv_import', ?, ?)
            """,
            (document_id, warning[:1000], None),
        )

    return {
        "document_id": document_id,
        "entries_inserted": inserted,
        "contributions_inserted": contributions_inserted,
        "person_fields": len([value for value in person.values() if value]),
        "provider": settings.get("provider") or "none",
        "used_llm": bool(llm_entries),
        "warnings": warnings,
    }
