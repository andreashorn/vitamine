#!/usr/bin/env python3
"""Build a compact short CV from entries and publications flagged for short export."""

from __future__ import annotations

import html
import json
import re
import sqlite3
import subprocess
from pathlib import Path

from vitamine.scripts.export_utils import compile_typst_if_available
from vitamine.paths import OUTPUT, ROOT, active_db_path

DB = active_db_path()


SECTION_TITLES = {
    "education": "Education and Training",
    "postdoctoral_training": "Postdoctoral Training",
    "academic_appointments": "Positions and Appointments",
    "hospital_appointments": "Hospital Appointments",
    "honors": "Selected Honors",
    "funding": "Selected Funding",
    "mentoring": "Mentoring",
    "invited_presentations": "Selected Presentations",
}

SECTION_ORDER = [
    "education",
    "postdoctoral_training",
    "academic_appointments",
    "hospital_appointments",
    "funding",
    "honors",
    "mentoring",
    "invited_presentations",
]


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    ensure_export_columns(con)
    return con


def ensure_export_columns(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(publications)").fetchall()}
    columns = {
        "short_selected_order": "INTEGER",
        "ultrashort_selected_order": "INTEGER",
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
        VALUES ('short', 10, 'first_last')
        """
    )


def clean(value: str | None) -> str:
    return (value or "").strip()


def typ(value: str | None) -> str:
    return json.dumps(clean(value), ensure_ascii=False)


def text(value: str | None, *, bold: bool = False, size: str | None = None) -> str:
    args = []
    if bold:
        args.append('weight: "bold"')
    if size:
        args.append(f"size: {size}")
    args.append(typ(value))
    return f"#text({', '.join(args)})"


def period(row: sqlite3.Row) -> str:
    start = clean(row["start_date"])
    end = clean(row["end_date"])
    if start and end:
        return f"{start}-{end}"
    return start


def year_label(row: sqlite3.Row) -> str:
    start = clean(row["start_date"])
    end = clean(row["end_date"])
    if start and end and start != end:
        return f"{start}-{end}"
    return start or end


def normalize_text(value: str) -> str:
    return re.sub(r"\W+", " ", value.casefold()).strip()


def entry_detail(row: sqlite3.Row) -> str:
    title = clean(row["title"])
    organization = clean(row["organization"])
    role = clean(row["role"])
    amount = clean(row["amount"])
    description = clean(row["description"])
    if row["section_key"] == "honors":
        pieces = [title]
        if organization and normalize_text(organization) not in normalize_text(title):
            pieces.append(organization)
        return ", ".join(piece for piece in pieces if piece)
    if "|" in description:
        desc_parts = [clean(part) for part in description.split("|") if clean(part)]
        if desc_parts:
            title = title or desc_parts[0]
            if len(desc_parts) >= 2 and not role:
                role = desc_parts[1]
            if len(desc_parts) >= 3 and not organization:
                organization = desc_parts[2]
    parts = [title, organization, role, amount]
    if description and "|" not in description:
        parts.append(description)
    return "; ".join(part for part in parts if part)


def citation(row: sqlite3.Row) -> str:
    authors = clean(row["authors"])
    if authors:
        author_parts = [part.strip() for part in authors.split(",") if part.strip()]
        if len(author_parts) > 4:
            authors = ", ".join(author_parts[:3]) + ", et al."
    parts = [authors, clean(row["title"]), clean(row["venue"]), clean(row["year"])]
    out = ". ".join(part for part in parts if part)
    if clean(row["doi"]):
        out = f"{out}. doi:{clean(row['doi'])}"
    return out


def load_data() -> tuple[sqlite3.Row | None, list[sqlite3.Row], list[sqlite3.Row]]:
    with connect() as con:
        person = con.execute("SELECT * FROM person WHERE id=1").fetchone()
        settings = con.execute("SELECT publication_limit FROM export_settings WHERE profile='short'").fetchone()
        publication_limit = int(settings["publication_limit"] if settings else 10)
        entries = con.execute(
            """
            SELECT *
            FROM cv_entries
            WHERE include_short=1
            ORDER BY section_key, start_date, id
            """
        ).fetchall()
        pubs = con.execute(
            """
            SELECT *
            FROM publications
            WHERE include_short=1
              AND COALESCE(suppress_display, 0)=0
              AND category='peer_reviewed'
            ORDER BY COALESCE(short_selected_order, selected_order, 999), CAST(year AS INTEGER) DESC, id DESC
            LIMIT ?
            """
            ,
            (publication_limit,),
        ).fetchall()
    return person, entries, pubs


def grouped_sections(entries: list[sqlite3.Row]) -> list[tuple[str, list[sqlite3.Row]]]:
    by_section: dict[str, list[sqlite3.Row]] = {}
    seen: set[tuple[str, str]] = set()
    for row in entries:
        detail = entry_detail(row)
        key = (row["section_key"], normalize_text(f"{year_label(row)} {detail}"))
        if key in seen:
            continue
        seen.add(key)
        by_section.setdefault(row["section_key"], []).append(row)
    ordered = []
    for section_key in SECTION_ORDER:
        if section_key in by_section:
            rows = by_section.pop(section_key)
            if section_key == "honors":
                rows = sorted(rows, key=lambda row: (year_label(row), row["id"]), reverse=True)
            ordered.append((section_key, rows))
    ordered.extend((key, rows) for key, rows in sorted(by_section.items()))
    return ordered


def build_html() -> str:
    person, entries, pubs = load_data()
    name = clean(person["display_name"] if person else "") or "Andreas Horn"
    title = clean(person["position_title"] if person else "")
    body = [f"<h1>{html.escape(name)}</h1>"]
    if title:
        body.append(f"<p class=\"subtitle\">{html.escape(title)}</p>")
    for section_key, rows in grouped_sections(entries):
        body.append(f"<h2>{html.escape(SECTION_TITLES.get(section_key, section_key.replace('_', ' ').title()))}</h2>")
        body.append("<table>")
        for row in rows:
            body.append(f"<tr><td>{html.escape(year_label(row))}</td><td>{html.escape(entry_detail(row))}</td></tr>")
        body.append("</table>")
    if pubs:
        body.append("<h2>Selected Publications</h2><ol>")
        for row in pubs:
            body.append(f"<li>{html.escape(citation(row))}</li>")
        body.append("</ol>")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Short CV</title>
  <style>
    body {{ font-family: Arial, Helvetica, sans-serif; max-width: 900px; margin: 28px auto; color: #111; font-size: 12px; line-height: 1.32; }}
    h1 {{ font-size: 22px; margin: 0 0 4px; }}
    .subtitle {{ font-weight: 700; margin-bottom: 16px; }}
    h2 {{ font-size: 14px; margin: 16px 0 6px; border-bottom: 1px solid #999; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td {{ padding: 3px 6px; vertical-align: top; border-bottom: 1px solid #ddd; }}
    td:first-child {{ width: 150px; font-weight: 700; }}
  </style>
</head>
<body>
{''.join(body)}
</body>
</html>
"""


def build_typst() -> str:
    person, entries, pubs = load_data()
    name = clean(person["display_name"] if person else "") or "Andreas Horn"
    title = clean(person["position_title"] if person else "")
    lines = [
        '#set page(width: 8.5in, height: 11in, margin: (left: 0.65in, right: 0.65in, top: 0.65in, bottom: 0.58in))',
        '#set text(font: "Arial", size: 10pt)',
        "#set par(leading: 0.45em)",
        text(name, bold=True, size="16pt"),
    ]
    if title:
        lines.append(text(title, bold=True))
    for section_key, rows in grouped_sections(entries):
        lines.append(f"\n#line(length: 100%)\n{text(SECTION_TITLES.get(section_key, section_key.replace('_', ' ').title()), bold=True)}")
        cells = []
        for row in rows:
            cells.append(f"[{text(year_label(row), bold=True)}]")
            cells.append(f"[{text(entry_detail(row))}]")
        lines.append("#grid(columns: (1.45in, 5.55in), gutter: 0.2in, row-gutter: 0.045in,\n" + ",\n".join(cells) + "\n)")
    if pubs:
        lines.append(f"\n#line(length: 100%)\n{text('Selected Publications', bold=True)}")
        for index, row in enumerate(pubs, 1):
            lines.append("#grid(columns: (0.3in, 6.8in), gutter: 0.08in,\n"
                         f"  [{text(str(index)+'.')}],\n  [{text(citation(row), size='9pt')}]\n)")
    return "\n".join(lines) + "\n"


def build() -> dict[str, str]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    html_path = OUTPUT / "short_cv.html"
    typ_path = OUTPUT / "short_cv.typ"
    pdf_path = OUTPUT / "short_cv.pdf"
    html_path.write_text(build_html(), encoding="utf-8")
    typ_path.write_text(build_typst(), encoding="utf-8")
    pdf, warning = compile_typst_if_available(typ_path, pdf_path, ROOT)
    result = {
        "html": str(html_path.relative_to(ROOT)),
        "typst": str(typ_path.relative_to(ROOT)),
    }
    if pdf:
        result["pdf"] = str(pdf.relative_to(ROOT))
    if warning:
        result["warning"] = warning
    return result


if __name__ == "__main__":
    for name, path in build().items():
        print(f"{name}: {path}")
