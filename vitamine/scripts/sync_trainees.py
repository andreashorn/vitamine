#!/usr/bin/env python3
"""Normalize mentoring rows into trainees and trainee-level achievements."""

from __future__ import annotations

import sqlite3
import sys
import re
from pathlib import Path

from vitamine.i18n import draft_translate_to_german
from vitamine.paths import ROOT, SCHEMA, active_db_path

DB = active_db_path()


LUKAS_GOODE_ACHIEVEMENTS = [
    {
        "achievement_type": "prize/funding",
        "achievement_type_de": "Preis/Förderung",
        "title": "Clinician Scientist Funding",
        "title_de": "Clinician Scientist Funding",
        "organization": "Berlin Institute of Health",
        "organization_de": "Berlin Institute of Health",
        "amount": "75k €",
        "amount_de": "75 Tsd. €",
        "description": "Clinician Scientist Funding (Berlin Institute of Health, 75k €)",
        "description_de": "Clinician Scientist Funding (Berlin Institute of Health, 75 Tsd. €)",
    },
    {
        "achievement_type": "prize",
        "achievement_type_de": "Preis",
        "title": "Helga Freyberg-Rüßmann-Stiftungspreis für Nachwuchswissenschaftler",
        "title_de": "Helga Freyberg-Rüßmann-Stiftungspreis für Nachwuchswissenschaftler",
        "organization": "Helga Freyberg-Rüßmann-Stiftung",
        "organization_de": "Helga Freyberg-Rüßmann-Stiftung",
        "amount": "25k €",
        "amount_de": "25 Tsd. €",
        "description": "Helga Freyberg-Rüßmann-Stiftungspreis für Nachwuchswissenschaftler (25k €)",
        "description_de": "Helga Freyberg-Rüßmann-Stiftungspreis für Nachwuchswissenschaftler (25 Tsd. €)",
    },
]


def ensure_tables(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA.read_text(encoding="utf-8"))


def split_mentee_title(title: str | None) -> tuple[str, str | None, str | None]:
    parts = [part.strip() for part in (title or "").split("/") if part.strip()]
    if not parts:
        return "", None, None
    name = parts[0]
    degree = parts[1] if len(parts) > 1 else None
    institution = parts[-1] if len(parts) > 2 else None
    return name, degree, institution


def clean_cell(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\\", "")
    text = text.replace("*", "")
    text = text.replace(" ", " ")
    text = re.sub(r"\{\.underline\}", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def table_cells(line: str) -> list[str]:
    if not line.strip().startswith("|"):
        return []
    cells = [clean_cell(cell) for cell in line.strip().strip("|").split("|")]
    if not cells or all(not cell for cell in cells):
        return []
    if all(set(cell) <= {"-", "=", ":", " "} for cell in cells):
        return []
    return cells


def parse_date_range(value: str) -> tuple[str | None, str | None]:
    value = value.replace("−", "-").strip()
    if "-" not in value:
        return value or None, None
    start, end = value.split("-", 1)
    return start.strip() or None, end.strip() or None


def source_mentoring_records(con: sqlite3.Connection) -> list[dict]:
    section = con.execute("SELECT raw_markdown FROM sections WHERE section_key = 'mentoring'").fetchone()
    if not section:
        return []
    records: list[dict] = []
    current: dict | None = None
    for line in section["raw_markdown"].splitlines():
        cells = table_cells(line)
        if not cells:
            continue
        first = cells[0].replace("−", "-")
        if first and re.match(r"^\d{4}", first):
            if current:
                records.append(current)
            start, end = parse_date_range(first)
            current = {
                "start_date": start,
                "end_date": end,
                "title": cells[1] if len(cells) > 1 else "",
                "lines": [],
            }
            remainder = " ".join(cell for cell in cells[2:] if cell)
            if remainder:
                current["lines"].append(remainder)
        elif current:
            text = " ".join(cell for cell in cells if cell)
            if text:
                current["lines"].append(text)
    if current:
        records.append(current)

    parsed = []
    for record in records:
        name, degree, institution = split_mentee_title(record["title"])
        if not name or "/" not in record["title"]:
            continue
        details = " ".join(record["lines"])
        career_stage = extract_labeled_value(details, "Career Stage")
        mentoring_role = extract_labeled_value(details, "Mentoring Role") or extract_labeled_value(details, "Role")
        accomplishments = extract_labeled_value(details, "Accomplishments")
        parsed.append(
            {
                **record,
                "name": name,
                "degree": degree,
                "institution": institution,
                "career_stage": career_stage,
                "mentoring_role": mentoring_role,
                "accomplishments": accomplishments,
            }
        )
    return parsed


def extract_labeled_value(text: str, label: str) -> str | None:
    labels = ["Career Stage", "Mentoring Role", "Role", "Accomplishments"]
    alternatives = "|".join(re.escape(item) for item in labels if item != label)
    pattern = rf"{re.escape(label)}:\s*(.*?)(?:;\s*(?:{alternatives}):|$)"
    match = re.search(pattern, text)
    return clean_cell(match.group(1)) if match else None


def split_accomplishments(text: str | None) -> list[str]:
    if not text:
        return []
    text = clean_cell(text).replace(";Junior", "; Junior")
    if not text or text.upper() == "TBD":
        return []
    items: list[str] = []
    for segment in [part.strip(" ,") for part in text.split(";") if part.strip(" ,")]:
        items.extend(split_accomplishment_segment(segment))
    return [item for item in items if item and item.upper() != "TBD"]


def split_accomplishment_segment(segment: str) -> list[str]:
    segment = segment.strip(" ,")
    if not segment:
        return []
    splitters = [
        r"(?<=\))\s*,\s*(?=Paper of the Month)",
        r"(?<=Foundation)\s*,\s*(?=Now Full Professor)",
        r",\s*(?=Interview at Neurology Podcast)",
    ]
    for splitter in splitters:
        parts = re.split(splitter, segment, maxsplit=1)
        if len(parts) > 1:
            out: list[str] = []
            for part in parts:
                out.extend(split_accomplishment_segment(part))
            return out

    initial_patterns = [
        r"^(\d+\s+papers?)\s*,\s*(.+)$",
        r"^(1\s+paper)\s*,\s*(.+)$",
        r"^(\d+\s+Poster)\s*,\s*(.+)$",
        r"^(1\s+Poster)\s*,\s*(.+)$",
        r"^('summa cum laude')\s*,\s*(.+)$",
        r"^('cum laude')\s*,\s*(.+)$",
    ]
    for pattern in initial_patterns:
        match = re.match(pattern, segment, flags=re.I)
        if match:
            return [match.group(1)] + split_accomplishment_segment(match.group(2))

    return [segment]


def achievement_type(title: str) -> str:
    lower = title.lower()
    if "award" in lower or "prize" in lower or "preis" in lower:
        return "prize/award"
    if re.fullmatch(r"\d+\s+papers?", lower) or re.fullmatch(r"\d+\s+poster", lower):
        return "publication/output"
    if "summa cum laude" in lower or "cum laude" in lower:
        return "degree honor"
    if "fellowship" in lower or "stipend" in lower:
        return "fellowship"
    if "grant" in lower or "funding" in lower or "förderung" in lower:
        return "grant/funding"
    if "program" in lower or "phd program" in lower:
        return "program/admission"
    if "interview" in lower or "podcast" in lower:
        return "media"
    if "full professor" in lower:
        return "career outcome"
    if "usmle" in lower:
        return "exam"
    return "achievement"


def split_outside_parentheses(text: str, delimiter: str = ",") -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif char == delimiter and depth == 0:
            if delimiter == "," and index > 0 and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit():
                continue
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return parts


def achievement_organization(title: str) -> str | None:
    for paren in re.findall(r"\(([^)]*(?:EUR|USD|\$|€|k)[^)]*)\)", title):
        parts = split_outside_parentheses(paren)
        if len(parts) > 1 and re.search(r"(?:EUR|USD|\$|€|k)", parts[-1]):
            return ", ".join(parts[:-1]).strip() or None
    title_without_amounts = re.sub(r"\s*\([^)]*(?:EUR|USD|\$|€|k)[^)]*\)", "", title).strip()
    if "," not in title_without_amounts:
        return None
    parts = split_outside_parentheses(title_without_amounts)
    if len(parts) < 2:
        return None
    tail = ", ".join(parts[1:])
    return tail or None


def achievement_amount(title: str) -> str | None:
    parenthetical = re.findall(r"\(([^)]*(?:EUR|USD|\$|€|k)[^)]*)\)", title)
    values = []
    for value in parenthetical:
        parts = split_outside_parentheses(value)
        if len(parts) > 1 and re.search(r"(?:EUR|USD|\$|€|k)", parts[-1]):
            values.append(parts[-1])
        else:
            values.append(value)
    if not values:
        values.extend(re.findall(r"(?<![\w])(\$\d+\s*k?|\d+(?:,\d{3})*(?:\.\d+)?\s*(?:EUR|USD|€)|\d+\s*k€)", title))
    deduped = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return "; ".join(deduped) if deduped else None


def title_without_amount(title: str) -> str:
    return re.sub(r"\s*\([^)]*(?:EUR|USD|\$|€|k)[^)]*\)", "", title).strip(" ,")


def upsert_trainees(con: sqlite3.Connection) -> dict[str, int]:
    con.row_factory = sqlite3.Row
    source_records = {record["name"].lower(): record for record in source_mentoring_records(con)}
    rows = con.execute(
        """
        SELECT *
        FROM cv_entries
        WHERE section_key = 'mentoring'
          AND title IS NOT NULL
          AND title LIKE '%/%'
        ORDER BY id
        """
    ).fetchall()
    ids_by_name: dict[str, int] = {}
    for row in rows:
        name, degree, institution = split_mentee_title(row["title"])
        if not name:
            continue
        source_record = source_records.get(name.lower(), {})
        career_stage = source_record.get("career_stage")
        mentoring_role = source_record.get("mentoring_role") or row["role"]
        con.execute(
            """
            INSERT INTO trainees (
              cv_entry_id, name, name_de, degree, degree_de, career_stage, career_stage_de,
              institution, institution_de, start_date, end_date, mentoring_role, mentoring_role_de
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cv_entry_id) DO UPDATE SET
              name=excluded.name,
              name_de=excluded.name_de,
              degree=excluded.degree,
              degree_de=excluded.degree_de,
              career_stage=excluded.career_stage,
              career_stage_de=excluded.career_stage_de,
              institution=excluded.institution,
              institution_de=excluded.institution_de,
              start_date=excluded.start_date,
              end_date=excluded.end_date,
              mentoring_role=excluded.mentoring_role,
              mentoring_role_de=excluded.mentoring_role_de
            """,
            (
                row["id"],
                name,
                name,
                degree,
                draft_translate_to_german(degree),
                career_stage,
                draft_translate_to_german(career_stage),
                institution,
                draft_translate_to_german(institution),
                row["start_date"],
                row["end_date"],
                mentoring_role,
                draft_translate_to_german(mentoring_role),
            ),
        )
        trainee_id = con.execute("SELECT id FROM trainees WHERE cv_entry_id = ?", (row["id"],)).fetchone()["id"]
        ids_by_name[name.lower()] = int(trainee_id)
    return ids_by_name


def add_source_accomplishments(con: sqlite3.Connection, ids_by_name: dict[str, int]) -> int:
    added = 0
    for record in source_mentoring_records(con):
        trainee_id = ids_by_name.get(record["name"].lower())
        if not trainee_id:
            continue
        for raw_title in split_accomplishments(record.get("accomplishments")):
            full_title = clean_cell(raw_title)
            amount = achievement_amount(full_title)
            organization = achievement_organization(full_title)
            display_title = title_without_amount(full_title)
            kind = achievement_type(full_title)
            con.execute(
                """
                INSERT OR IGNORE INTO trainee_achievements (
                  trainee_id, achievement_type, achievement_type_de, title, title_de,
                  organization, organization_de, amount, amount_de, description, description_de, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'source_cv')
                """,
                (
                    trainee_id,
                    kind,
                    draft_translate_to_german(kind),
                    display_title,
                    draft_translate_to_german(display_title),
                    organization,
                    draft_translate_to_german(organization),
                    amount,
                    draft_translate_to_german(amount),
                    full_title,
                    draft_translate_to_german(full_title),
                ),
            )
            added += con.execute("SELECT changes()").fetchone()[0]
    return added


def add_lukas_goede_achievements(con: sqlite3.Connection, ids_by_name: dict[str, int]) -> int:
    trainee_id = ids_by_name.get("lukas goede")
    if not trainee_id:
        raise RuntimeError("Could not find trainee row for Lukas Goede")
    added = 0
    for item in LUKAS_GOODE_ACHIEVEMENTS:
        con.execute(
            """
            INSERT OR IGNORE INTO trainee_achievements (
              trainee_id, achievement_type, achievement_type_de, title, title_de,
              organization, organization_de, amount, amount_de, description, description_de, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual')
            """,
            (
                trainee_id,
                item["achievement_type"],
                item["achievement_type_de"],
                item["title"],
                item["title_de"],
                item["organization"],
                item["organization_de"],
                item["amount"],
                item["amount_de"],
                item["description"],
                item["description_de"],
            ),
        )
        added += con.execute("SELECT changes()").fetchone()[0]
    return added


def main() -> None:
    with sqlite3.connect(DB) as con:
        ensure_tables(con)
        ids_by_name = upsert_trainees(con)
        source_added = add_source_accomplishments(con, ids_by_name)
        manual_added = add_lukas_goede_achievements(con, ids_by_name)
        con.commit()
    print(
        f"Synced {len(ids_by_name)} trainees; added {source_added} source accomplishments and {manual_added} manual Lukas Goede achievement records."
    )


if __name__ == "__main__":
    main()
