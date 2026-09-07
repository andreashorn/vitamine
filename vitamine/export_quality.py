"""Conservative final quality audit for generated CV documents.

The LLM is an auditor, not a database editor.  Every proposed automatic change
is independently checked to ensure that it only decodes markup residue or
normalizes whitespace.
"""

from __future__ import annotations

import html
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree


AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "record_type": {"type": "string", "enum": ["person", "cv_entry", "publication", "document"]},
                    "record_id": {"type": ["integer", "null"]},
                    "field": {"type": ["string", "null"]},
                    "old_value": {"type": ["string", "null"]},
                    "new_value": {"type": ["string", "null"]},
                    "category": {
                        "type": "string",
                        "enum": ["encoding_error", "whitespace", "formatting", "possible_factual_error", "other"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
                "required": ["record_type", "record_id", "field", "old_value", "new_value", "category", "confidence", "reason"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["issues", "summary"],
    "additionalProperties": False,
}

AUDIT_FIELDS = {
    "person": (
        "full_name", "display_name", "degrees", "position_title", "office_address",
        "home_address", "work_phone", "work_email", "place_of_birth", "own_institution_name",
    ),
    "cv_entries": (
        "subcategory", "subcategory_de", "start_date", "end_date", "title", "title_de",
        "organization", "organization_de", "location", "location_de", "role", "role_de",
        "amount", "amount_de", "description", "description_de", "raw_text", "raw_text_de",
    ),
    "publications": ("authors", "title", "venue", "year", "doi", "url", "raw_citation", "short_citation"),
}

RECORD_TABLES = {"person": "person", "cv_entry": "cv_entries", "publication": "publications"}
BAD_ENTITY_RE = re.compile(r"(?:&(?:amp;)*#?\w{1,12};|(?:Ã.|Â.|Ä(?:apos|quot|amp)))", re.I)
SPACE_BEFORE_PUNCTUATION_RE = re.compile(r"[ \t]+([,.;:!?])")


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}


def conservative_cleanup(value: str) -> str:
    """Return a meaning-preserving cleanup suitable for automatic persistence."""
    cleaned = value.replace("\u00a0", " ").replace("\u200b", "")
    # Common mojibake emitted by broken HTML/UTF-8 conversions.
    replacements = {
        "Äapos;": "'", "Äapos": "'", "Ã¢â‚¬â„¢": "’", "â€™": "’",
        "Ã¢â‚¬Å“": "“", "Ã¢â‚¬Â": "”", "â€“": "–", "â€”": "—",
    }
    for broken, replacement in replacements.items():
        cleaned = cleaned.replace(broken, replacement)
    for _ in range(3):
        decoded = html.unescape(cleaned)
        if decoded == cleaned:
            break
        cleaned = decoded
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = SPACE_BEFORE_PUNCTUATION_RE.sub(r"\1", cleaned)
    return cleaned.strip()


def is_safe_automatic_change(old_value: str, new_value: str, category: str) -> bool:
    if category not in {"encoding_error", "whitespace"} or old_value == new_value:
        return False
    return conservative_cleanup(old_value) == new_value


def deterministic_corrections(con: sqlite3.Connection) -> list[dict[str, Any]]:
    corrections: list[dict[str, Any]] = []
    for table, requested_fields in AUDIT_FIELDS.items():
        columns = _table_columns(con, table)
        fields = [field for field in requested_fields if field in columns]
        if "id" not in columns or not fields:
            continue
        for row in con.execute(f"SELECT id, {', '.join(fields)} FROM {table}"):
            for field in fields:
                value = row[field]
                if not isinstance(value, str) or not value:
                    continue
                cleaned = conservative_cleanup(value)
                if cleaned == value:
                    continue
                category = "encoding_error" if BAD_ENTITY_RE.search(value) else "whitespace"
                corrections.append({
                    "record_type": "cv_entry" if table == "cv_entries" else table.rstrip("s"),
                    "record_id": int(row["id"]), "field": field, "old_value": value,
                    "new_value": cleaned, "category": category, "confidence": 1.0,
                    "reason": "Deterministic entity/whitespace cleanup.", "applied": True,
                })
    return corrections


def apply_corrections(con: sqlite3.Connection, corrections: list[dict[str, Any]]) -> int:
    applied = 0
    for issue in corrections:
        table = RECORD_TABLES.get(str(issue.get("record_type")))
        field = str(issue.get("field") or "")
        record_id = issue.get("record_id")
        old_value = issue.get("old_value")
        new_value = issue.get("new_value")
        category = str(issue.get("category") or "")
        if not table or not isinstance(record_id, int) or field not in _table_columns(con, table):
            issue["applied"] = False
            continue
        if not isinstance(old_value, str) or not isinstance(new_value, str) or not is_safe_automatic_change(old_value, new_value, category):
            issue["applied"] = False
            continue
        cursor = con.execute(
            f"UPDATE {table} SET {field}=? WHERE id=? AND {field}=?",
            (new_value, record_id, old_value),
        )
        issue["applied"] = cursor.rowcount == 1
        applied += int(issue["applied"])
    return applied


def extract_docx_text(path: Path, max_chars: int = 50000) -> str:
    if not path.exists() or path.suffix.lower() != ".docx":
        return ""
    chunks: list[str] = []
    with zipfile.ZipFile(path) as package:
        for name in sorted(package.namelist()):
            if name == "word/document.xml" or name.startswith("word/header") or name.startswith("word/footer"):
                root = ElementTree.fromstring(package.read(name))
                for paragraph in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                    text = "".join(node.text or "" for node in paragraph.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"))
                    if text.strip():
                        chunks.append(text)
    return "\n".join(chunks)[:max_chars]


def audit_records(con: sqlite3.Connection, max_records: int = 250) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for table, requested_fields in AUDIT_FIELDS.items():
        columns = _table_columns(con, table)
        fields = [field for field in requested_fields if field in columns]
        if "id" not in columns or not fields:
            continue
        for row in con.execute(f"SELECT id, {', '.join(fields)} FROM {table} ORDER BY id"):
            values = {field: row[field] for field in fields if isinstance(row[field], str) and row[field].strip()}
            if values:
                records.append({"record_type": "cv_entry" if table == "cv_entries" else table.rstrip("s"), "record_id": int(row["id"]), "fields": values})
            if len(records) >= max_records:
                return records
    return records


def run_export_quality_audit(
    con: sqlite3.Connection,
    docx_path: Path,
    llm_call: Callable[[str, dict[str, Any], dict[str, str]], tuple[dict[str, Any] | None, str | None]],
    settings: dict[str, str],
) -> dict[str, Any]:
    con.row_factory = sqlite3.Row
    deterministic = deterministic_corrections(con)
    deterministic_applied = apply_corrections(con, deterministic)
    con.commit()

    records = audit_records(con)
    document_text = extract_docx_text(docx_path)
    prompt = f"""
You are the final quality auditor for an academic CV. Report only obvious defects.
Do not rewrite prose, improve style, change facts, infer missing facts, or standardize legitimate names.
Encoding residue and accidental whitespace may be corrected. Anything involving names, dates,
roles, organizations, publication metadata, or meaning must be category possible_factual_error
and is a suggestion only. For a database-backed issue, copy the exact record_type, record_id,
field, and old_value from SOURCE_RECORDS. Use document issues only for layout/formatting findings.
Return no issue when uncertain.

GENERATED_DOCUMENT_TEXT:
{document_text}

SOURCE_RECORDS:
{records}
""".strip()
    model_result, warning = llm_call(prompt, AUDIT_SCHEMA, settings)
    model_issues = model_result.get("issues", []) if isinstance(model_result, dict) else []
    if not isinstance(model_issues, list):
        model_issues = []
    model_issues = [issue for issue in model_issues if isinstance(issue, dict)][:100]
    model_applied = apply_corrections(con, model_issues)
    con.commit()
    suggestions = [{**issue, "applied": bool(issue.get("applied"))} for issue in model_issues]
    return {
        "status": "completed" if model_result is not None else "deterministic_only",
        "summary": str(model_result.get("summary") or "") if isinstance(model_result, dict) else "",
        "applied_count": deterministic_applied + model_applied,
        "deterministic_count": deterministic_applied,
        "llm_applied_count": model_applied,
        "issues": [*deterministic, *suggestions],
        "review_count": sum(1 for issue in suggestions if not issue.get("applied")),
        "warning": warning,
    }
