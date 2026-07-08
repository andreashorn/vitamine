#!/usr/bin/env python3
"""Build the one-page tabular CV from the SQLite database."""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
from pathlib import Path

from docx import Document

from vitamine.paths import OUTPUT, PACKAGE, active_db_path

DB = active_db_path()
DEFAULT_TEMPLATE = PACKAGE / "onepage_tabular" / "ultrashort_tabular_template.docx"
DEFAULT_OUTPUT = OUTPUT / "ultrashort_tabular_cv.docx"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ensure_publication_columns(con)
    return con


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
    }
    for column, definition in columns.items():
        if column not in existing:
            con.execute(f"ALTER TABLE publications ADD COLUMN {column} {definition}")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS export_settings (
          profile TEXT PRIMARY KEY,
          publication_limit INTEGER NOT NULL DEFAULT 10,
          authorship_filter TEXT NOT NULL DEFAULT 'first_last'
        )
        """
    )
    con.execute(
        """
        INSERT OR IGNORE INTO export_settings (profile, publication_limit, authorship_filter)
        VALUES ('ultrashort', 10, 'first_last')
        """
    )


def clean(value: str | None) -> str:
    return (value or "").strip()


def year(value: str | None) -> str:
    text = clean(value)
    if not text:
        return ""
    slash_match = re.match(r"^\d{1,2}/\d{1,2}/(\d{2}|\d{4})$", text)
    if slash_match:
        value_int = int(slash_match.group(1))
        if value_int < 100:
            value_int += 2000 if value_int < 40 else 1900
        return str(value_int)
    match = re.search(r"\d{4}", text)
    return match.group(0) if match else text


def range_years(start: str | None, end: str | None) -> str:
    start_year = year(start)
    end_year = year(end)
    if start_year and end_year:
        return f"{start_year}–{end_year}"
    if start_year:
        return f"{start_year}–present"
    return ""


def clear_paragraph(paragraph) -> None:
    for run in list(paragraph.runs):
        paragraph._p.remove(run._r)


def add_text(paragraph, parts: list[tuple[str, bool]]) -> None:
    clear_paragraph(paragraph)
    for text, bold in parts:
        run = paragraph.add_run(text)
        run.bold = bold


def set_simple_paragraph(paragraph, text: str, *, bold: bool = False) -> None:
    add_text(paragraph, [(text, bold)])


def split_year_prefix(text: str) -> list[tuple[str, bool]]:
    match = re.match(r"^([0-9]{4}(?:[–-][0-9]{4}|[–-]present)?)(.*)$", text)
    if not match:
        return [(text, False)]
    return [(match.group(1), True), (match.group(2), False)]


def set_cell(cell, text: str, *, bold: bool = False) -> None:
    paragraph = cell.paragraphs[0]
    set_simple_paragraph(paragraph, text, bold=bold)
    for extra in cell.paragraphs[1:]:
        clear_paragraph(extra)


def citation_parts(citation: str, bold_terms: tuple[str, ...]) -> list[tuple[str, bool]]:
    parts: list[tuple[str, bool]] = []
    cursor = 0
    pattern = re.compile("|".join(re.escape(term) for term in bold_terms if term))
    if not pattern.pattern:
        return [(citation, False)]
    for match in pattern.finditer(citation):
        if match.start() > cursor:
            parts.append((citation[cursor : match.start()], False))
        parts.append((match.group(0), True))
        cursor = match.end()
    if cursor < len(citation):
        parts.append((citation[cursor:], False))
    return parts


def parse_description_parts(row: sqlite3.Row) -> list[str]:
    return [part.strip() for part in clean(row["description"]).split("|") if part.strip()]


def row_by_title(con: sqlite3.Connection, section_key: str, title: str) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT *
        FROM cv_entries
        WHERE section_key = ? AND title = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (section_key, title),
    ).fetchone()


def education_rows(con: sqlite3.Connection) -> list[tuple[str, str, str, str]]:
    current_prof = row_by_title(con, "academic_appointments", "Professor")
    postdoc = row_by_title(con, "postdoctoral_training", "Postdoctoral Fellow")
    junior = row_by_title(con, "postdoctoral_training", "Junior Group Leader")
    phd = row_by_title(con, "education", "PhD")
    md = row_by_title(con, "education", "MD")

    rows = [("Years", "Qualification", "Institution", "Field")]
    if current_prof:
        rows.append(
            (
                range_years(current_prof["start_date"], current_prof["end_date"]),
                "Schilling Professor / Director",
                "Institute for Network Stimulation, University Hospital Cologne",
                "Computational Neurology, DBS, Connectomics",
            )
        )
    if postdoc or junior:
        rows.append(
            (
                "2016–2018",
                "Postdoctoral Fellow / Junior Group Leader",
                "Harvard Medical School & Charité Berlin",
                "Medical Neurosciences",
            )
        )
    if phd:
        parts = parse_description_parts(phd)
        rows.append(("PhD", "PhD", clean(phd["organization"]) or (parts[2] if len(parts) > 2 else ""), parts[1] if len(parts) > 1 else "Medical Neurosciences"))
    if md:
        parts = parse_description_parts(md)
        rows.append(("MD", "MD", clean(md["organization"]) or (parts[2] if len(parts) > 2 else ""), parts[1] if len(parts) > 1 else "Medicine"))
    return rows[:5]


def position_rows(con: sqlite3.Connection) -> list[tuple[str, str]]:
    desired = [
        ("academic_appointments", "Professor", "Professor of Computational Neurology and Director, Network Stimulation Institute, University of Cologne"),
        ("hospital_appointments", "Investigator", "Investigator, Department of Stereotaxy and Functional Neurosurgery, University Hospital Cologne"),
        ("academic_appointments", "Associate Professor", "Associate Professor, Harvard Medical School"),
        ("academic_appointments", "Director, Deep Brain Stimulation Research", "Director, Deep Brain Stimulation Research, Brigham & Women's Hospital"),
        ("academic_appointments", "Director, Connectomic Neuromodulation Research", "Director, Connectomic Neuromodulation Research, Massachusetts General Hospital"),
        ("academic_appointments", "Emmy Noether Group Leader", "Emmy Noether Group Leader, Neurology, Charité Berlin"),
    ]
    rows = [("Years", "Position")]
    for section_key, title, label in desired:
        row = row_by_title(con, section_key, title)
        if row:
            rows.append((range_years(row["start_date"], row["end_date"]), label))
    return rows[:7]


def award_rows(con: sqlite3.Connection) -> list[str]:
    selected = [
        ("funding", "Project C05 in 3rd funding period of CRC ELAINE", "CRC ELAINE Project C05, German Research Foundation (DFG), PI"),
        ("funding", "Schilling Professorship: Institute for Network Stimulation, Cologne", "Schilling Professorship: Institute for Network Stimulation, Schilling Foundation, PI"),
        ("honors", "International Brain Stimulation Early Career Award", "International Brain Stimulation Early Career Award"),
        ("honors", "One of the World's 'Highly Cited Researchers'", "Highly Cited Researcher, Clarivate; Falling Walls Global Call Winner"),
        ("honors", "Heinz-Maier-Leibnitz Prize", "Heinz-Maier-Leibnitz Prize, German Research Foundation"),
        ("funding", "Toward Connectomic Brain Stimulation", "DFG Emmy Noether Grant: Toward Connectomic Brain Stimulation, PI"),
    ]
    rows = []
    for section_key, title, label in selected:
        row = row_by_title(con, section_key, title)
        if row:
            if section_key == "honors":
                date_label = year(row["start_date"])
            else:
                date_label = range_years(row["start_date"], row["end_date"]) or year(row["start_date"])
            rows.append(f"{date_label} – {label}")
        elif title.startswith("One of the World's"):
            rows.append("2024 – Highly Cited Researcher, Clarivate; Falling Walls Global Call Winner")
    return rows[:6]


def publication_citation(row: sqlite3.Row) -> str:
    if clean(row["short_citation"]):
        return clean(row["short_citation"])
    authors = clean(row["authors"])
    if authors:
        parts = [part.strip() for part in authors.split(",")]
        if len(parts) > 3:
            authors = ", ".join(parts[:3]) + ", et al."
    citation_parts_out = [part for part in [authors, clean(row["title"])] if part]
    citation = ". ".join(citation_parts_out)
    if clean(row["venue"]):
        citation += f". {clean(row['venue'])}"
    if clean(row["year"]):
        citation += f". {clean(row['year'])}"
    if clean(row["doi"]):
        citation += f"; doi:{clean(row['doi'])}"
    return citation.rstrip(".") + "."


def selected_publications(con: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return con.execute(
        """
        SELECT *
        FROM publications
        WHERE include_ultrashort = 1
          AND COALESCE(suppress_display, 0) = 0
          AND category = 'peer_reviewed'
        ORDER BY COALESCE(ultrashort_selected_order, selected_order, 999), CAST(year AS INTEGER) DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def publication_limit(con: sqlite3.Connection, fallback: int = 10) -> int:
    row = con.execute("SELECT publication_limit FROM export_settings WHERE profile='ultrashort'").fetchone()
    return int(row["publication_limit"] if row else fallback)


def build(template: Path, output: Path, publication_limit: int) -> Path:
    with connect() as con:
        person = con.execute("SELECT * FROM person WHERE id=1").fetchone()
        edu = education_rows(con)
        positions = position_rows(con)
        awards = award_rows(con)
        publications = selected_publications(con, publication_limit)

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, output)
    doc = Document(output)
    doc.core_properties.author = person["display_name"] or person["full_name"]
    doc.core_properties.title = "Andreas Horn - Tabular CV"
    doc.core_properties.subject = "One-page tabular CV"

    set_simple_paragraph(doc.paragraphs[0], f"Prof. Dr. {person['display_name']}, {person['degrees']}", bold=True)
    add_text(
        doc.paragraphs[1],
        [
            (person["position_title"], True),
            ("\nInstitute for Network Stimulation & Department of Stereotaxy and Functional Neurosurgery", False),
            ("\nUniversity Hospital Cologne, Germany", False),
        ],
    )
    set_simple_paragraph(doc.paragraphs[2], "Education and Training", bold=True)
    set_simple_paragraph(doc.paragraphs[3], "Positions and Scientific Appointments", bold=True)
    set_simple_paragraph(doc.paragraphs[4], "Awards, Research Funding and Presentations", bold=True)
    set_simple_paragraph(doc.paragraphs[11], "Selected Publications", bold=True)

    education_column_count = len(doc.tables[0].columns)
    for row_idx, row in enumerate(edu):
        if education_column_count == 3:
            row_values = (row[0], row[1], row[2])
        else:
            row_values = row
        for col_idx, value in enumerate(row_values):
            set_cell(doc.tables[0].cell(row_idx, col_idx), value, bold=(row_idx == 0))
    for row_idx, row in enumerate(positions):
        for col_idx, value in enumerate(row):
            set_cell(doc.tables[1].cell(row_idx, col_idx), value, bold=(row_idx == 0))
    for paragraph, line in zip(doc.paragraphs[5:11], awards):
        add_text(paragraph, split_year_prefix(line))
    publication_paragraphs = doc.paragraphs[12 : 12 + publication_limit]
    for paragraph, publication in zip(publication_paragraphs, publications):
        citation = publication_citation(publication)
        add_text(paragraph, citation_parts(citation, ("Horn", clean(publication["venue"]))))
    for paragraph in publication_paragraphs[len(publications) :]:
        clear_paragraph(paragraph)

    doc.save(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--publication-limit", type=int)
    args = parser.parse_args()
    if args.publication_limit is None:
        with connect() as con:
            args.publication_limit = publication_limit(con)
    print(build(args.template, args.output, args.publication_limit))


if __name__ == "__main__":
    main()
