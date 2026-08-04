"""Bounded, review-only LLM cleanup suggestions for an existing CV."""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from collections import Counter
from typing import Any, Callable


CLEANUP_CSV_COLUMNS = (
    "operation",
    "record_type",
    "record_id",
    "field",
    "old_text",
    "new_text",
    "related_record_type",
    "related_record_id",
    "rationale",
    "confidence",
)
ALLOWED_OPERATIONS = {"edit", "merge", "delete"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
RECORD_FIELDS = {
    "person": {"full_name", "display_name", "degrees", "position_title", "work_email", "orcid_id", "own_institution_name"},
    "entry": {"section_key", "start_date", "end_date", "title", "organization", "location", "role", "amount", "description", "raw_text"},
    "publication": {"authors", "title", "venue", "year", "doi", "pmid", "url", "raw_citation", "quality_note"},
    "contribution": {"ordinal", "title", "narrative"},
}
MAX_RECORDS_PER_BATCH = 32
MAX_BATCHES = 24


def _text(value: Any, limit: int = 2400) -> str:
    return str(value or "").strip()[:limit]


def _same_text(left: str, right: str) -> bool:
    return re.sub(r"\s+", " ", left).strip() == re.sub(r"\s+", " ", right).strip()


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def _select_records(con: sqlite3.Connection, record_type: str) -> list[dict[str, Any]]:
    columns = _table_columns(con, {"entry": "cv_entries", "publication": "publications", "contribution": "biosketch_contributions"}[record_type])
    fields = sorted(RECORD_FIELDS[record_type] & columns)
    table = {"entry": "cv_entries", "publication": "publications", "contribution": "biosketch_contributions"}[record_type]
    rows = con.execute(f"SELECT id, {', '.join(fields)} FROM {table} ORDER BY id").fetchall()
    return [
        {
            "record_type": record_type,
            "record_id": str(row["id"]),
            "fields": {field: _text(row[field]) for field in fields if _text(row[field])},
        }
        for row in rows
    ]


def cleanup_batches(con: sqlite3.Connection) -> tuple[list[dict[str, Any]], int]:
    """Return small, section-aware review batches and their total record count."""
    batches: list[dict[str, Any]] = []
    person_columns = _table_columns(con, "person")
    person_fields = sorted(RECORD_FIELDS["person"] & person_columns)
    person = con.execute(f"SELECT {', '.join(person_fields)} FROM person WHERE id=1").fetchone() if person_fields else None
    person_context = {field: _text(person[field], 500) for field in person_fields if person and _text(person[field], 500)}
    all_records: list[dict[str, Any]] = []
    for record_type in ("entry", "publication", "contribution"):
        all_records.extend(_select_records(con, record_type))
    total = len(all_records) + (1 if person_context else 0)
    if person_context:
        batches.append({"scope": "person", "context": person_context, "records": [{"record_type": "person", "record_id": "1", "fields": person_context}]})

    entries = [record for record in all_records if record["record_type"] == "entry"]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in entries:
        grouped.setdefault(record["fields"].get("section_key") or "other", []).append(record)
    for section, records in grouped.items():
        for start in range(0, len(records), MAX_RECORDS_PER_BATCH):
            batches.append({"scope": f"entries: {section}", "context": person_context, "records": records[start : start + MAX_RECORDS_PER_BATCH]})
    for record_type in ("publication", "contribution"):
        records = [record for record in all_records if record["record_type"] == record_type]
        for start in range(0, len(records), MAX_RECORDS_PER_BATCH):
            batches.append({"scope": record_type, "context": person_context, "records": records[start : start + MAX_RECORDS_PER_BATCH]})
    return batches[:MAX_BATCHES], total


def cleanup_prompt(batch: dict[str, Any], summary: dict[str, Any]) -> str:
    """Ask for one strict CSV, with locators that can be safely verified later."""
    return f"""Review this bounded portion of an academic CV for conservative cleanup suggestions.

The researcher context is {json.dumps({**summary, 'basic_profile': batch.get('context') or {}}, ensure_ascii=False)}. This batch is {batch['scope']}.
Suggest only clear corrections, duplicate merges, or unmistakable junk records. Include objective consistency
fixes such as journal-title casing (for example, ALL CAPS venue names normalized to title case), but preserve
legitimate acronyms, identifiers, titles, and author names. Do not invent facts, rewrite for style alone, or
suggest changes outside the supplied records. For an edit, old_text must
exactly reproduce the current value of the named field. For a merge, record_id is the record to merge
away and related_record_id is the record to keep. For delete, leave field/new_text/related fields empty.

Put the suggestions in the csv string. Its first line and only allowed columns must be:
{','.join(CLEANUP_CSV_COLUMNS)}
Use CSV quoting correctly. Allowed operations: edit, merge, delete. Allowed record_type values: person,
entry, publication, contribution. Confidence is low, medium, or high. Return the header with no data rows
when there is no clear suggestion.

Records:
{json.dumps(batch['records'], ensure_ascii=False)}
"""


CLEANUP_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["csv"],
    "properties": {"csv": {"type": "string", "maxLength": 64000}},
}


def parse_cleanup_csv(raw_csv: str, records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Parse only suggestions which point exactly at a record in this LLM batch."""
    if not raw_csv.strip():
        return []
    reader = csv.DictReader(io.StringIO(raw_csv))
    if tuple(reader.fieldnames or ()) != CLEANUP_CSV_COLUMNS:
        return []
    lookup = {(item["record_type"], item["record_id"]): item for item in records}
    suggestions: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for row in reader:
        item = {column: _text(row.get(column), 8000 if column in {"old_text", "new_text"} else 1200) for column in CLEANUP_CSV_COLUMNS}
        key = (item["record_type"], item["record_id"])
        record = lookup.get(key)
        if not record or item["operation"] not in ALLOWED_OPERATIONS or item["confidence"] not in ALLOWED_CONFIDENCE:
            continue
        if item["record_type"] not in RECORD_FIELDS:
            continue
        if item["operation"] == "edit":
            if item["field"] not in RECORD_FIELDS[item["record_type"]] or not item["new_text"]:
                continue
            current = str(record["fields"].get(item["field"]) or "")
            if not current or not _same_text(item["old_text"], current) or _same_text(item["new_text"], current):
                continue
        elif item["operation"] == "merge":
            related = lookup.get((item["related_record_type"], item["related_record_id"]))
            if item["record_type"] == "person" or not related or item["related_record_type"] != item["record_type"] or item["related_record_id"] == item["record_id"]:
                continue
        else:
            if item["record_type"] == "person" or item["field"] or item["new_text"] or item["related_record_id"]:
                continue
        fingerprint = tuple(item[column] for column in CLEANUP_CSV_COLUMNS)
        if fingerprint not in seen:
            seen.add(fingerprint)
            suggestions.append(item)
    return suggestions


def stage_cleanup_suggestions(con: sqlite3.Connection, suggestions: list[dict[str, str]]) -> int:
    """Keep cleanup review items in the existing inbox; no CV data is changed here."""
    con.execute("DELETE FROM import_inbox_items WHERE source='cv_cleanup' AND status='pending'")
    for suggestion in suggestions:
        operation = suggestion["operation"]
        record_type = suggestion["record_type"]
        record_id = suggestion["record_id"]
        if operation == "edit":
            title = f"Edit {record_type} #{record_id}: {suggestion['field']}"
            raw_text = f"{suggestion['old_text']} → {suggestion['new_text']}"
        elif operation == "merge":
            title = f"Merge duplicate {record_type} #{record_id} into #{suggestion['related_record_id']}"
            raw_text = suggestion["rationale"]
        else:
            title = f"Delete junk {record_type} #{record_id}"
            raw_text = suggestion["rationale"]
        con.execute(
            """
            INSERT INTO import_inbox_items
              (source, target_type, status, confidence, title, subtitle, raw_text, payload_json)
            VALUES ('cv_cleanup', 'cleanup_suggestion', 'pending', ?, ?, ?, ?, ?)
            """,
            (
                suggestion["confidence"], title[:240],
                f"{operation.title()} suggestion · review only"[:500], raw_text[:4000],
                json.dumps({"cleanup_csv": suggestion, "locator": {"record_type": record_type, "record_id": record_id, "field": suggestion["field"]}}, ensure_ascii=False),
            ),
        )
    return len(suggestions)


def run_cleanup_review(
    con: sqlite3.Connection,
    llm_json: Callable[[str, dict[str, Any], dict[str, Any]], tuple[dict[str, Any] | None, str | None]],
    settings: dict[str, Any],
    progress: Callable[[str, str, int], None] | None = None,
) -> dict[str, Any]:
    batches, total_records = cleanup_batches(con)
    summary = {"record_counts": dict(Counter(record["record_type"] for batch in batches for record in batch["records"]))}
    suggestions: list[dict[str, str]] = []
    warnings: list[str] = []
    for index, batch in enumerate(batches, start=1):
        if progress:
            progress("cleanup", f"Reviewing {batch['scope']} ({index} of {len(batches)})", 8 + round(78 * index / max(1, len(batches))))
        response, warning = llm_json(cleanup_prompt(batch, summary), CLEANUP_RESPONSE_SCHEMA, settings)
        if warning:
            warnings.append(f"{batch['scope']}: {warning}"[:600])
            continue
        if not isinstance(response, dict):
            warnings.append(f"{batch['scope']}: the cleanup response was empty.")
            continue
        suggestions.extend(parse_cleanup_csv(str(response.get("csv") or ""), batch["records"]))
    staged = stage_cleanup_suggestions(con, suggestions)
    reviewed = sum(len(batch["records"]) for batch in batches)
    if reviewed < total_records:
        warnings.append(f"Reviewed {reviewed} of {total_records} records in this pass to keep the request bounded.")
    return {"ok": True, "job_kind": "cleanup_cv", "batches_reviewed": len(batches), "records_reviewed": reviewed, "records_total": total_records, "suggestions_staged": staged, "warnings": warnings[:20]}
