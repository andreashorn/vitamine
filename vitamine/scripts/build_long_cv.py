#!/usr/bin/env python3
"""Build the long CV from the SQLite database."""

from __future__ import annotations

import copy
import datetime as dt
import argparse
import html
import json
import re
import sqlite3
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Inches, Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph

from vitamine.scripts.export_utils import (
    configure_researcher_name,
    markdown_to_html_body,
    researcher_name_pattern,
    sanitize_docx_compatibility_markup,
)
from vitamine.paths import OUTPUT, ROOT, active_db_path, output_ref
from vitamine.citation_styles import configured_citation_style, format_publication

DB = active_db_path()
FORMAL_ACADEMIC_TEMPLATE = ROOT / "vitamine" / "templates" / "formal-academic" / "template.docx"

LANG = "en"


SECTION_ORDER = [
    ("education", "Education"),
    ("postdoctoral_training", "Postdoctoral Training"),
    ("academic_appointments", "Faculty Academic Appointments"),
    ("hospital_appointments", "Appointments at Hospitals/Affiliated Institutions"),
    ("professional_positions", "Other Professional Positions"),
    ("committee_service", "Committee Service"),
    ("professional_societies", "Professional Societies"),
    ("grant_review", "Grant Review Activities"),
    ("editorial_activities", "Editorial Activities"),
    ("honors", "Honors and Prizes"),
    ("funding", "Research Funding"),
    ("teaching", "Teaching of Students in Courses"),
    ("mentoring", "Research Supervisory and Training Responsibilities"),
    ("invited_presentations", "Report of Regional, National and International Invited Teaching and Presentations"),
    ("clinical_activities", "Report of Clinical Activities and Innovations"),
    ("education_innovations", "Report of Teaching and Education Innovations"),
    ("community_service", "Report of Education of Patients and Service to the Community"),
]

SECTION_LABELS_DE = {
    "education": "Ausbildung",
    "postdoctoral_training": "Postdoktorale Ausbildung",
    "academic_appointments": "Akademische Berufungen",
    "hospital_appointments": "Positionen an Kliniken und affiliierten Institutionen",
    "professional_positions": "Weitere berufliche Positionen",
    "committee_service": "Gremienarbeit",
    "professional_societies": "Fachgesellschaften",
    "grant_review": "Gutachtertätigkeiten für Forschungsförderung",
    "editorial_activities": "Editoriale Tätigkeiten",
    "honors": "Auszeichnungen und Preise",
    "funding": "Forschungsförderung",
    "teaching": "Lehre in Kursen",
    "mentoring": "Betreuung und Ausbildung",
    "invited_presentations": "Bericht über regionale, nationale und internationale eingeladene Lehre und Vorträge",
    "clinical_activities": "Bericht über klinische Tätigkeiten und Innovationen",
    "education_innovations": "Bericht über Lehr- und Ausbildungsinnovationen",
    "community_service": "Bericht über Patientenaufklärung und gesellschaftliches Engagement",
}

TEXT = {
    "en": {
        "faculty": "The Faculty of Medicine of University Cologne",
        "cv": "Curriculum Vitae",
        "date_prepared": "Date Prepared:",
        "name": "Name:",
        "office_address": "Office Address:",
        "home_address": "Home Address:",
        "work_phone": "Work Phone:",
        "work_email": "Work Email:",
        "place_of_birth": "Place of Birth:",
        "scholarship": "Report of Scholarship",
        "peer_reviewed": "Peer-Reviewed Scholarship in print or other media:",
        "other_scholarship": "Other Scholarship",
        "patents": "Patents",
        "books_chapters": "Books / Book Chapters",
        "preprints": "Preprints",
        "manuscripts_in_preparation": "Manuscripts in Preparation",
        "poster_presentations": "Poster Presentations",
        "no_sponsor": "No presentations below were sponsored by 3rd parties/outside entities.",
        "achievements": "Achievements",
        "ad_hoc_reviewer": "Ad hoc Reviewer",
        "other_editorial_roles": "Other Editorial Roles",
        "other_trainees": "Other Formally Supervised Trainees",
        "narrative_report": "Narrative Report",
        "dates": "Dates",
        "role_title": "Role / Title",
        "field_details": "Field / Details",
        "institution": "Institution / Organization",
        "funding_source": "Funding Source",
        "role": "Role",
        "amount": "Amount",
        "details": "Details",
    },
    "de": {
        "faculty": "Medizinische Fakultät der Universität zu Köln",
        "cv": "Lebenslauf",
        "date_prepared": "Erstellt am:",
        "name": "Name:",
        "office_address": "Dienstadresse:",
        "home_address": "Privatadresse:",
        "work_phone": "Telefon dienstlich:",
        "work_email": "E-Mail dienstlich:",
        "place_of_birth": "Geburtsort:",
        "scholarship": "Publikationsbericht",
        "peer_reviewed": "Begutachtete wissenschaftliche Publikationen:",
        "other_scholarship": "Weitere wissenschaftliche Beiträge",
        "patents": "Patente",
        "books_chapters": "Bücher / Buchkapitel",
        "preprints": "Preprints",
        "manuscripts_in_preparation": "Manuskripte in Vorbereitung",
        "poster_presentations": "Posterpräsentationen",
        "no_sponsor": "Die unten aufgeführten Vorträge wurden nicht durch Dritte/externe Einrichtungen gesponsert.",
        "achievements": "Erfolge",
        "ad_hoc_reviewer": "Ad-hoc-Gutachter",
        "other_editorial_roles": "Weitere editoriale Tätigkeiten",
        "other_trainees": "Weitere formal betreute Personen",
        "narrative_report": "Narrativer Bericht",
        "dates": "Zeitraum",
        "role_title": "Rolle / Titel",
        "field_details": "Fach / Details",
        "institution": "Institution / Organisation",
        "funding_source": "Fördermittelgeber",
        "role": "Rolle",
        "amount": "Betrag",
        "details": "Details",
    },
}

THREE_COLUMN_SECTIONS = {
    "committee_service",
    "professional_societies",
    "grant_review",
    "teaching",
}

PUBLICATION_CATEGORY_ORDER = [
    ("peer_reviewed", "peer_reviewed"),
    ("patents", "patents"),
    ("books_chapters", "books_chapters"),
    ("preprints", "preprints"),
    ("manuscripts_in_preparation", "manuscripts_in_preparation"),
    ("poster_presentations", "poster_presentations"),
]

DEFAULT_LONG_PUBLICATION_CATEGORIES = {"peer_reviewed", "patents"}

PUBLICATION_CATEGORY_ALIASES = {
    "patent": "patents",
    "preprint": "preprints",
    "book_chapter": "books_chapters",
    "book_chapters": "books_chapters",
    "poster": "poster_presentations",
    "poster_presentation": "poster_presentations",
    "posters": "poster_presentations",
    "manuscript": "manuscripts_in_preparation",
    "manuscripts": "manuscripts_in_preparation",
    "manuscripts_under_review": "manuscripts_in_preparation",
}


def clean(value: str | None) -> str:
    return value or ""


def clean_cell(value: str | None) -> str:
    text = clean(value).strip()
    if text.startswith(">"):
        text = text[1:].strip()
    text = text.replace("^st^", "st").replace("^nd^", "nd").replace("^rd^", "rd").replace("^th^", "th")
    return text


def tr(key: str) -> str:
    return TEXT.get(LANG, TEXT["en"])[key]


def get_setting(con: sqlite3.Connection, key: str) -> str:
    row = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return str(row["value"] or "") if row else ""


def selected_publication_categories(con: sqlite3.Connection) -> list[str]:
    selected = [item for item in get_setting(con, "long_cv_publication_categories").split(",") if item]
    allowed = [category for category, _label in PUBLICATION_CATEGORY_ORDER]
    selected = [category for category in selected if category in allowed]
    if not selected:
        selected = [category for category in allowed if category in DEFAULT_LONG_PUBLICATION_CATEGORIES]
    return selected


def publication_category_groups(con: sqlite3.Connection) -> list[tuple[str, list[sqlite3.Row]]]:
    selected = set(selected_publication_categories(con))
    rows = con.execute(
        """
        SELECT * FROM publications
        WHERE COALESCE(suppress_display, 0) = 0
        ORDER BY
          CASE WHEN year IS NULL OR year = '' THEN 1 ELSE 0 END,
          CAST(year AS INTEGER),
          lower(title)
        """
    ).fetchall()
    prompt_plan_raw = get_setting(con, "export_prompt_plan:vitamine.formal-academic")
    try:
        prompt_plan = json.loads(prompt_plan_raw) if prompt_plan_raw else {}
    except json.JSONDecodeError:
        prompt_plan = {}
    planned_ids = [
        int(value)
        for value in prompt_plan.get("selected_publication_ids", [])
        if str(value).isdigit()
    ]
    if planned_ids:
        order = {publication_id: index for index, publication_id in enumerate(planned_ids)}
        rows = [row for row in rows if int(row["id"]) in order]
        rows.sort(key=lambda row: order[int(row["id"])])
    groups: list[tuple[str, list[sqlite3.Row]]] = []
    for category, label_key in PUBLICATION_CATEGORY_ORDER:
        if category not in selected:
            continue
        category_rows = [
            row for row in rows
            if PUBLICATION_CATEGORY_ALIASES.get(str(row["category"] or ""), str(row["category"] or "")) == category
        ]
        if category_rows:
            groups.append((label_key, category_rows))
    return groups


def localized_section_title(section_key: str, english_title: str) -> str:
    if LANG == "de":
        return SECTION_LABELS_DE.get(section_key, english_title)
    return english_title


def row_value(row: sqlite3.Row, field: str) -> str:
    if LANG == "de":
        german = f"{field}_de"
        if german in row.keys() and clean_cell(row[german]):
            return clean_cell(row[german])
    return clean_cell(row[field] if field in row.keys() else "")


def period(start: str | None, end: str | None) -> str:
    if start and end:
        return f"{start}-{end}"
    if start:
        return f"{start}-"
    return ""


def sort_date(value: str | None) -> tuple[int, int, int]:
    if not value:
        return (9999, 12, 31)
    value = value.strip().rstrip(",")
    parts = value.split("/")
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        month, year = [int(part) for part in parts]
        return (year, month, 1)
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        month, day, year = [int(part) for part in parts]
        if year < 100:
            year += 2000 if year < 40 else 1900
        return (year, month, day)
    if value[:4].isdigit():
        return (int(value[:4]), 1, 1)
    return (9999, 12, 31)


def md_escape(value: str | None) -> str:
    text = clean(value)
    return text.replace("|", "\\|")


def html_escape(value: str | None) -> str:
    return html.escape(clean(value))


def typ_string(value: str | None) -> str:
    text = clean(value)
    text = text.replace("<br>", "\n")
    text = text.replace("\\|", "|")
    return json.dumps(text, ensure_ascii=False)


def typ_text(value: str | None, *, bold: bool = False, size: str | None = None) -> str:
    args = []
    if bold:
        args.append('weight: "bold"')
    if size:
        args.append(f"size: {size}")
    args.append(typ_string(value))
    return f"#text({', '.join(args)})"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          value TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
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
    return con


def person_block(con: sqlite3.Connection) -> list[str]:
    person = con.execute("SELECT * FROM person WHERE id=1").fetchone()
    configure_researcher_name(person)
    today = dt.datetime.now().strftime("%d.%m.%Y") if LANG == "de" else dt.datetime.now().strftime("%B %-d, %Y")
    if not person:
        return [f"# {tr('cv')}"]
    rows = [
        (tr("date_prepared").rstrip(":"), today),
        (tr("name").rstrip(":"), person["display_name"] or person["full_name"]),
        (tr("office_address").rstrip(":"), person["office_address"]),
        (tr("home_address").rstrip(":"), person["home_address"]),
        (tr("work_phone").rstrip(":"), person["work_phone"]),
        (tr("work_email").rstrip(":"), person["work_email"]),
        (tr("place_of_birth").rstrip(":"), person["place_of_birth"]),
    ]
    out = [f"# {tr('cv')}", ""]
    out.append("|  |  |")
    out.append("| --- | --- |")
    for label, value in rows:
        if value:
            out.append(f"| **{label}:** | {md_escape(value)} |")
    return out


def entry_rows(con: sqlite3.Connection, section_key: str) -> list[sqlite3.Row]:
    rows = con.execute(
        """
        SELECT * FROM cv_entries
        WHERE section_key = ?
          AND include_long = 1
          AND (section_key != 'funding' OR COALESCE(grant_status, 'funded') IN ('funded', 'past'))
        ORDER BY id
        """,
        (section_key,),
    ).fetchall()
    if section_key == "editorial_activities":
        return sorted(rows, key=lambda row: (0 if clean_cell(row["subcategory"]) == "Ad hoc Reviewer" else 1, row["id"]))
    return rows


def detail_columns(row: sqlite3.Row) -> tuple[str, str, str]:
    description = row_value(row, "description")
    parts = [clean_cell(part) for part in description.split("|")]
    parts = [part for part in parts if part]
    title = row_value(row, "title") or (parts[0] if parts else "")
    middle = ""
    organization = row_value(row, "organization") or row_value(row, "location") or ""
    if len(parts) >= 3:
        title = title or parts[0]
        middle = parts[1]
        organization = organization or parts[2]
    elif len(parts) == 2:
        middle = parts[1]
    elif row_value(row, "role"):
        middle = row_value(row, "role")
    details = row_value(row, "amount") or middle
    return title, details, organization


def trainee_achievement_map(con: sqlite3.Connection) -> dict[int, list[str]]:
    rows = con.execute(
        """
        SELECT t.cv_entry_id, a.title, a.organization, a.amount
        FROM trainee_achievements a
        JOIN trainees t ON t.id = a.trainee_id
        WHERE t.cv_entry_id IS NOT NULL
        ORDER BY a.year, a.id
        """
    ).fetchall()
    achievements: dict[int, list[str]] = {}
    for row in rows:
        title = row_value(row, "title")
        parts = [title] if title else []
        organization = row_value(row, "organization")
        amount = row_value(row, "amount")
        if organization and organization.casefold() not in title.casefold():
            parts.append(organization)
        if amount and amount.casefold() not in title.casefold():
            parts.append(amount)
        value = ", ".join(parts)
        existing = achievements.setdefault(row["cv_entry_id"], [])
        if value and value not in existing:
            existing.append(value)
    return achievements


def entries_table(
    rows: list[sqlite3.Row],
    achievements_by_entry: dict[int, list[str]] | None = None,
    include_amount: bool = False,
) -> list[str]:
    if include_amount:
        out = [
            f"| {tr('dates')} | {tr('role_title')} | {tr('funding_source')} | {tr('role')} | {tr('amount')} | {tr('details')} |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    else:
        out = [
            f"| {tr('dates')} | {tr('role_title')} | {tr('field_details')} | {tr('institution')} |",
            "| --- | --- | --- | --- |",
        ]
    for row in rows:
        title, details, organization = detail_columns(row)
        achievements = (achievements_by_entry or {}).get(row["id"], [])
        if achievements:
            suffix = f"{tr('achievements')}: " + "; ".join(achievements)
            details = f"{details}<br>{suffix}" if details else suffix
        if include_amount:
            out.append(
                f"| {md_escape(period(row['start_date'], row['end_date']))} | {md_escape(title)} | {md_escape(organization)} | {md_escape(row['role'])} | {md_escape(row['amount'])} | {md_escape(row['description'])} |"
            )
        else:
            out.append(
                f"| {md_escape(period(row['start_date'], row['end_date']))} | {md_escape(title)} | {md_escape(details)} | {md_escape(organization)} |"
            )
    return out


def publication_citation(row: sqlite3.Row, index: int) -> str:
    authors = row["authors"] or ""
    title = row["title"] or ""
    venue = row["venue"] or ""
    year = row["year"] or ""
    doi = row["doi"] or ""
    pmid = row["pmid"] or ""
    parts = []
    if authors:
        parts.append(authors)
    if title:
        parts.append(title)
    if venue:
        parts.append(f"*{venue}*")
    if year:
        parts.append(year)
    citation = ". ".join(parts).strip()
    extras = []
    if doi:
        extras.append(f"doi:{doi}")
    if pmid:
        extras.append(f"PMID:{pmid}")
    if extras:
        citation = f"{citation}. {'; '.join(extras)}"
    return f"{index}. {citation}"


def doi_url(row: sqlite3.Row) -> str:
    doi = clean_cell(row["doi"])
    if doi:
        return doi if doi.startswith("http") else f"https://doi.org/{doi}"
    return clean_cell(row["url"])


def markdown_publication_citation(row: sqlite3.Row) -> str:
    authors = clean_cell(row["authors"])
    title = clean_cell(row["title"])
    venue = clean_cell(row["venue"])
    year = clean_cell(row["year"])
    link = doi_url(row)
    authors = citation_cell(authors)
    title = citation_cell(title)
    venue = citation_cell(venue)
    year = citation_cell(year)
    authors = researcher_name_pattern().sub(r"**\g<0>**", authors)
    parts = []
    if authors:
        parts.append(authors)
    if title:
        parts.append(title)
    if venue:
        parts.append(f"<u><em>{html_escape(venue.title() if venue.isupper() else venue)}</em></u>")
    if year:
        parts.append(year)
    impact = impact_factor_label(row)
    if impact:
        parts.append(impact)
    citation = ". ".join(parts).strip()
    if link:
        citation = f"{citation}. [{link}]({link})"
    return citation


def publications_block(con: sqlite3.Connection) -> list[str]:
    groups = publication_category_groups(con)
    if not groups:
        return []
    out = [f"## {tr('scholarship')}", ""]
    for label_key, rows in groups:
        out.extend([f"### {tr(label_key)}", ""])
        for index, row in enumerate(rows, 1):
            out.append(f"{index}. {markdown_publication_citation(row)}")
        out.append("")
    return out


def narrative_report_block(con: sqlite3.Connection) -> list[str]:
    row = con.execute("SELECT title, body, title_de, body_de FROM narrative_reports WHERE id=1").fetchone()
    if not row:
        return []
    title = row_value(row, "title") if not (LANG == "de" and not clean_cell(row["title_de"])) else tr("narrative_report")
    body = row_value(row, "body")
    if not clean_cell(body):
        return []
    return ["", f"## {title}", "", clean_cell(body)]


def build_markdown() -> str:
    con = connect()
    lines = person_block(con)
    lines.append("")
    achievements_by_entry = trainee_achievement_map(con)
    for section_key, title in SECTION_ORDER:
        rows = entry_rows(con, section_key)
        if not rows:
            continue
        lines.extend([f"## {localized_section_title(section_key, title)}", ""])
        lines.extend(
            entries_table(
                rows,
                achievements_by_entry if section_key == "mentoring" else None,
                include_amount=section_key == "funding",
            )
        )
        lines.append("")
    lines.extend(publications_block(con))
    lines.extend(narrative_report_block(con))
    con.close()
    return "\n".join(lines).rstrip() + "\n"


def typ_period(start: str | None, end: str | None) -> str:
    value = period(start, end)
    return value.replace("-", "-\n", 1) if "-" in value and len(value) > 9 else value


def row_period(row: sqlite3.Row) -> str:
    value = ""
    if row["raw_text"]:
        if "|" in row["raw_text"]:
            raw_parts = [part.strip() for part in row["raw_text"].split("|")]
            first = next((part for part in raw_parts if part), "")
        else:
            match = re.match(r"^(\d{1,2}/\d{1,2}/\d{2,4}-\d{1,2}/\d{1,2}/\d{2,4},?|\d{1,2}/\d{1,2}/\d{2,4}-?|\d{4}-\d{4}|\d{4}-?)\b", row["raw_text"].strip())
            first = match.group(1) if match else ""
        if looks_like_period(first):
            value = first
    if not value:
        value = period(row["start_date"], row["end_date"])
    return value.replace("-", "-\n", 1) if "-" in value and len(value) > 9 else value


def looks_like_period(value: str | None) -> bool:
    text = clean_cell(value)
    if not text:
        return False
    return bool(text[:4].isdigit() or "/" in text)


def typ_section(title: str) -> str:
    return f"\n#v(0.15in)\n{typ_text(title + ':', bold=True)}\n#v(0.055in)\n"


def typ_subheading(title: str) -> str:
    return f"#v(0.075in)\n{typ_text(title, bold=True)}\n#v(0.04in)\n"


def typ_grid(rows: list[list[str]], *, columns: str = "(1.05in, 1.42in, 2.05in, 1.7in)", row_gutter: str = "0.052in") -> str:
    cells = []
    for row in rows:
        for cell in row:
            cells.append(f"[{typ_text(cell)}]")
    return (
        f"#grid(columns: {columns}, gutter: 0.13in, row-gutter: {row_gutter},\n"
        + ",\n".join(f"  {cell}" for cell in cells)
        + "\n)\n"
    )


def typ_rich_text(value: str | None, *, bold_names: bool = False, underline: bool = False, italic: bool = False) -> str:
    text = clean_cell(value)
    if not text:
        return typ_text("")
    if bold_names:
        pieces = []
        pos = 0
        for match in researcher_name_pattern().finditer(text):
            if match.start() > pos:
                before = text[pos : match.start()]
                stripped = before.rstrip()
                if stripped:
                    pieces.append(typ_text(stripped))
                if before and before[-1].isspace():
                    pieces.append("#h(0.28em)")
            pieces.append(typ_text(match.group(0), bold=True))
            pos = match.end()
        if pos < len(text):
            after = text[pos:]
            if after and after[0].isspace():
                pieces.append("#h(0.28em)")
                after = after.lstrip()
            if after:
                pieces.append(typ_text(after))
        return "".join(pieces)
    body = typ_text(text)
    if italic:
        body = f"#emph[{body}]"
    if underline:
        body = f"#underline[{body}]"
    return body


def citation_cell(value: str | None) -> str:
    text = clean_cell(value).strip()
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r",(?=\S)", ", ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.rstrip(" .")


def sentence_part(value: str | None) -> str:
    text = citation_cell(value)
    return text if not text or text.endswith((".", "?", "!")) else f"{text}."


def impact_factor_label(row: sqlite3.Row) -> str:
    value = row["impact_factor"] if "impact_factor" in row.keys() else None
    if value in (None, ""):
        return ""
    try:
        formatted = f"{float(value):g}"
    except (TypeError, ValueError):
        formatted = clean_cell(str(value))
    year = citation_cell(row["impact_factor_year"] if "impact_factor_year" in row.keys() else "")
    return f"IF {formatted} ({year})" if year else f"IF {formatted}"


def typ_link_text(url: str, display: str | None = None) -> str:
    return f'#link({typ_string(url)})[#underline[#text(fill: blue, {typ_string(display or url)})]]'


def normalize_url(url: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    return f"https://{url}"


def typ_text_with_links(value: str | None) -> str:
    text = clean_cell(value)
    if not text:
        return typ_text("")
    pieces = []
    pos = 0
    pattern = re.compile(r"(?<!@)\b(?:https?://[^\s)]+|www\.[^\s)]+)")
    for match in pattern.finditer(text):
        if match.start() > pos:
            pieces.append(typ_text(text[pos : match.start()]))
        display = match.group(0).rstrip(".,;")
        trailing = match.group(0)[len(display) :]
        pieces.append(typ_link_text(normalize_url(display), display))
        if trailing:
            pieces.append(typ_text(trailing))
        pos = match.end()
    if pos < len(text):
        pieces.append(typ_text(text[pos:]))
    return "".join(pieces)


def typ_publication_citation(row: sqlite3.Row) -> str:
    parts = []
    if row["authors"]:
        parts.append(typ_rich_text(sentence_part(row["authors"]), bold_names=True))
    if row["title"]:
        parts.append(typ_text(sentence_part(row["title"])))
    if row["venue"]:
        venue = citation_cell(row["venue"])
        venue = venue.title() if venue.isupper() else venue
        parts.append(typ_rich_text(sentence_part(venue), italic=True, underline=True))
    if row["year"]:
        parts.append(typ_text(sentence_part(row["year"])))
    impact = impact_factor_label(row)
    if impact:
        parts.append(typ_text(sentence_part(impact)))
    doi = citation_cell(row["doi"])
    if doi:
        parts.append(f'{typ_text("doi:")}{typ_link_text(doi_url(row), doi)}')
    else:
        link = doi_url(row)
        if link:
            parts.append(typ_link_text(link))
    return "#h(0.28em)".join(parts)


def typ_invited_presentations(rows: list[sqlite3.Row]) -> str:
    cells = []
    for row in rows:
        extra_parts = []
        title = row_value(row, "title")
        for text in [row_value(row, "organization"), row_value(row, "location"), row_value(row, "description")]:
            if text and text != title and text not in extra_parts:
                extra_parts.append(text)
        details = "\n".join(
            part
            for part in [
                title,
                "\n".join(extra_parts),
            ]
            if part
        )
        cells.append(f"[{typ_text(row_period(row))}]")
        cells.append(f"[{typ_text(details)}]")
    return (
        f"#block(below: 0.12in)[{typ_text(tr('no_sponsor'))}]\n"
        "#grid(columns: (0.56in, 6.04in), gutter: 0.22in, row-gutter: 0.07in,\n"
        + ",\n".join(f"  {cell}" for cell in cells)
        + "\n)\n"
    )


def typ_entry_rows(
    rows: list[sqlite3.Row],
    achievements_by_entry: dict[int, list[str]] | None = None,
    *,
    columns: int = 4,
) -> list[list[str]]:
    out = []
    for row in rows:
        title, details, organization = detail_columns(row)
        is_continuation = not clean_cell(row["start_date"]) and not clean_cell(row["end_date"])
        if row["section_key"] == "grant_review" and is_continuation and organization:
            period_title = row_period(row) or clean_cell(title)
            if out and period_title.replace("\n", "") in out[-1][0].replace("\n", ""):
                out[-1][-1] = "\n".join(part for part in [out[-1][-1], organization] if part)
                continue
        if is_continuation and (looks_like_period(title) or looks_like_period(row_period(row))) and organization:
            if out:
                out[-1][1] = "\n".join(part for part in [out[-1][1], row_period(row) or title] if part)
                out[-1][-1] = "\n".join(part for part in [out[-1][-1], organization] if part)
                continue
        achievements = (achievements_by_entry or {}).get(row["id"], [])
        if achievements:
            suffix = "Achievements: " + "; ".join(achievements)
            details = f"{details}\n{suffix}" if details else suffix
        if columns == 3:
            out.append([row_period(row), title, organization or details])
        else:
            out.append([row_period(row), title, details, organization])
    return out


def typ_funding_rows(rows: list[sqlite3.Row]) -> list[list[str]]:
    out = []
    for row in rows:
        out.append(
            [
                row_period(row),
                row_value(row, "title"),
                row_value(row, "organization"),
                "\n".join(part for part in [row_value(row, "role"), row_value(row, "amount")] if part),
            ]
        )
    return out


def typ_funding_blocks(rows: list[sqlite3.Row]) -> str:
    cells = []
    for row in rows:
        role_amount = row_value(row, "role")
        amount = row_value(row, "amount")
        if role_amount and amount:
            role_amount = f"{role_amount} ({amount})"
        elif amount:
            role_amount = amount
        details = "\n".join(
            part
            for part in [
                row_value(row, "title"),
                row_value(row, "organization"),
                role_amount,
                row_value(row, "description"),
            ]
            if part
        )
        cells.append(f"[{typ_text(row_period(row))}]")
        cells.append(f"[{typ_text(details)}]")
    return (
        "#grid(columns: (1.0in, 5.62in), gutter: 0.13in, row-gutter: 0.095in,\n"
        + ",\n".join(f"  {cell}" for cell in cells)
        + "\n)\n"
    )


def typ_editorial_blocks(rows: list[sqlite3.Row]) -> list[str]:
    lines = []
    reviewer_rows = [row for row in rows if clean_cell(row["subcategory"]) == "Ad hoc Reviewer"]
    role_rows = [row for row in rows if clean_cell(row["subcategory"]) != "Ad hoc Reviewer"]
    for row in reviewer_rows:
        lines.append(typ_subheading(tr("ad_hoc_reviewer")))
        reviewer_text = row_value(row, "description") or row_value(row, "raw_text")
        lines.append(f"#block(below: 0.08in)[{typ_text(reviewer_text)}]\n")
    if role_rows:
        lines.append(typ_subheading(tr("other_editorial_roles")))
        lines.append(typ_grid(typ_entry_rows(role_rows, columns=3), columns="(1.0in, 3.1in, 2.5in)", row_gutter="0.075in"))
    return lines


def typ_mentoring_blocks(rows: list[sqlite3.Row], achievements_by_entry: dict[int, list[str]]) -> list[str]:
    lines = []
    general_rows = [row for row in rows if clean_cell(row["title"]).startswith("Supervision of PhD students")]
    trainee_rows = [row for row in rows if row not in general_rows]
    if general_rows:
        lines.append(typ_grid(typ_entry_rows(general_rows, columns=3), columns="(1.0in, 3.1in, 2.5in)", row_gutter="0.075in"))
    if trainee_rows:
        lines.append(typ_subheading(tr("other_trainees")))
        cells = []
        for row in trainee_rows:
            details = row_value(row, "title")
            achievements = achievements_by_entry.get(row["id"], [])
            if achievements:
                details = f"{details}\n{tr('achievements')}: " + "; ".join(achievements)
            cells.append(f"[{typ_text(row_period(row))}]")
            cells.append(f"[{typ_text(details)}]")
        lines.append(
            "#grid(columns: (1.0in, 5.62in), gutter: 0.13in, row-gutter: 0.085in,\n"
            + ",\n".join(f"  {cell}" for cell in cells)
            + "\n)\n"
        )
    return lines


def grouped_rows(rows: list[sqlite3.Row]) -> list[tuple[str | None, list[sqlite3.Row]]]:
    groups: list[tuple[str | None, list[sqlite3.Row]]] = []
    for row in rows:
        subcategory = row_value(row, "subcategory") or clean_cell(row["subcategory"]) or None
        if not groups or groups[-1][0] != subcategory:
            groups.append((subcategory, []))
        groups[-1][1].append(row)
    return groups


def build_typst() -> str:
    con = connect()
    person = con.execute("SELECT * FROM person WHERE id=1").fetchone()
    configure_researcher_name(person)
    today = dt.datetime.now().strftime("%d.%m.%Y") if LANG == "de" else dt.datetime.now().strftime("%B %-d, %Y")
    achievements_by_entry = trainee_achievement_map(con)

    lines = [
        '#set page(width: 8.5in, height: 11in, margin: (left: 0.73in, right: 0.62in, top: 0.72in, bottom: 0.55in))',
        f'#set text(font: "Helvetica", size: 10.5pt, lang: "{LANG}")',
        "#set par(leading: 0.49em)",
        f"#align(center)[{typ_text(tr('cv'), bold=True)}]",
        "#v(0.32in)",
    ]
    if person:
        metadata = [
            (tr("date_prepared"), today),
            (tr("name"), person["display_name"] or person["full_name"]),
            (tr("office_address"), person["office_address"]),
            (tr("home_address"), person["home_address"]),
            (tr("work_phone"), person["work_phone"]),
            (tr("work_email"), person["work_email"]),
            (tr("place_of_birth"), person["place_of_birth"]),
        ]
        cells = []
        for label, value in metadata:
            if value:
                cells.append(f"[{typ_text(label, bold=True)}]")
                cells.append(f"[{typ_text(value, bold=True)}]")
        lines.append("#grid(columns: (1.38in, 4.9in), row-gutter: 0.11in,\n" + ",\n".join(f"  {cell}" for cell in cells) + "\n)")

    for section_key, title in SECTION_ORDER:
        rows = entry_rows(con, section_key)
        if not rows:
            continue
        lines.append(typ_section(localized_section_title(section_key, title)))
        if section_key == "funding":
            lines.append(typ_funding_blocks(rows))
        elif section_key == "editorial_activities":
            lines.extend(typ_editorial_blocks(rows))
        elif section_key == "mentoring":
            lines.extend(typ_mentoring_blocks(rows, achievements_by_entry))
        elif section_key == "invited_presentations":
            lines.append(typ_invited_presentations(rows))
        elif section_key in THREE_COLUMN_SECTIONS:
            for subcategory, grouped in grouped_rows(rows):
                if subcategory:
                    lines.append(typ_subheading(subcategory))
                lines.append(typ_grid(typ_entry_rows(grouped, columns=3), columns="(1.0in, 2.55in, 3.02in)", row_gutter="0.075in"))
        else:
            lines.append(typ_grid(typ_entry_rows(rows, achievements_by_entry if section_key == "mentoring" else None)))

    publication_groups = publication_category_groups(con)
    if publication_groups:
        lines.append(typ_section(tr("scholarship")))
        for label_key, rows in publication_groups:
            lines.append(typ_subheading(tr(label_key)))
            for index, row in enumerate(rows, 1):
                lines.append(
                    "#grid(columns: (0.34in, 6.28in), gutter: 0.08in, row-gutter: 0pt,\n"
                    f"  [{typ_text(str(index) + '.')}],\n"
                    f"  [{typ_publication_citation(row)}]\n"
                    ")"
                )
    report = con.execute("SELECT title, body, title_de, body_de FROM narrative_reports WHERE id=1").fetchone()
    if report and row_value(report, "body"):
        report_title = row_value(report, "title") if not (LANG == "de" and not clean_cell(report["title_de"])) else tr("narrative_report")
        lines.append(typ_section(report_title or tr("narrative_report")))
        lines.append(f"#block[#set par(justify: true)\n{typ_text_with_links(row_value(report, 'body'))}]")
    con.close()
    return "\n".join(lines) + "\n"


def markdown_to_html(markdown: str) -> tuple[str, str | None]:
    body, warning = markdown_to_html_body(markdown, ROOT)
    warning_html = f'<p class="warning">{html.escape(warning)}</p>' if warning else ""
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Long CV Preview</title>
  <style>
    @page {{ size: letter; margin: 0.65in; }}
    body {{ font-family: Arial, Helvetica, sans-serif; margin: 28px auto; max-width: 980px; line-height: 1.28; color: #111; font-size: 11px; }}
    .cv-kicker {{ font-weight: 700; text-align: center; margin-bottom: 8px; }}
    h1 {{ font-size: 18px; text-align: center; margin: 6px 0 14px; }}
    h2 {{ font-size: 13px; margin: 20px 0 6px; font-weight: 700; }}
    h3 {{ font-size: 11px; margin: 14px 0 6px; font-weight: 700; }}
    table {{ border-collapse: collapse; width: 100%; margin: 4px 0 12px; font-size: 10px; page-break-inside: auto; }}
    tr {{ page-break-inside: avoid; page-break-after: auto; }}
    th, td {{ border: 1px solid #9b9b9b; padding: 4px 5px; vertical-align: top; }}
    th {{ background: #f1f1f1; text-align: left; font-weight: 700; }}
    td:first-child, th:first-child {{ width: 112px; }}
    p {{ margin: 0 0 6px; }}
    .warning {{ border: 1px solid #d8b24c; background: #fff8db; padding: 8px; color: #5d4700; }}
    @media print {{
      body {{ margin: 0; max-width: none; }}
      a {{ color: #111; text-decoration: none; }}
    }}
  </style>
</head>
<body>
{warning_html}
{body}
</body>
</html>
"""
    return html_doc, warning


def output_stem() -> str:
    return "long_cv_de" if LANG == "de" else "long_cv"


def set_run_font(run, *, size: float = 11, bold: bool | None = None, italic: bool | None = None, underline: bool | None = None) -> None:
    run.font.name = "Arial"
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if underline is not None:
        run.underline = underline


def set_paragraph_spacing(paragraph, *, before: float = 0, after: float = 0, line_spacing: float = 1.0) -> None:
    paragraph.paragraph_format.space_before = Pt(before)
    paragraph.paragraph_format.space_after = Pt(after)
    paragraph.paragraph_format.line_spacing = line_spacing


def add_paragraph_border(paragraph, *, top: bool = False, bottom: bool = False) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    p_bdr = p_pr.find(qn("w:pBdr"))
    if p_bdr is None:
        p_bdr = OxmlElement("w:pBdr")
        p_pr.append(p_bdr)
    for edge, enabled in (("top", top), ("bottom", bottom)):
        if not enabled:
            continue
        node = p_bdr.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            p_bdr.append(node)
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), "8")
        node.set(qn("w:space"), "4")
        node.set(qn("w:color"), "000000")


def add_text_paragraph(doc: Document, text_value: str = "", *, bold: bool = False, size: float = 11, before: float = 0, after: float = 0, justify: bool = False):
    paragraph = doc.add_paragraph()
    set_paragraph_spacing(paragraph, before=before, after=after)
    if justify:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if text_value:
        run = paragraph.add_run(text_value)
        set_run_font(run, size=size, bold=bold)
    return paragraph


def add_section_heading(doc: Document, marker: str, title: str) -> None:
    paragraph = doc.add_paragraph()
    set_paragraph_spacing(paragraph, before=8, after=2)
    paragraph.paragraph_format.tab_stops.add_tab_stop(Inches(0.35))
    run = paragraph.add_run(f"{marker}\t{title}")
    set_run_font(run, size=11, bold=True)


def add_subheading(doc: Document, title: str) -> None:
    paragraph = add_text_paragraph(doc, title, bold=True, before=3, after=1)
    for run in paragraph.runs:
        set_run_font(run, size=11, bold=True)


def add_tabbed_paragraph(doc: Document, left: str, right: str, *, left_width: float = 1.28, size: float = 11) -> None:
    paragraph = doc.add_paragraph()
    set_paragraph_spacing(paragraph)
    paragraph.paragraph_format.tab_stops.add_tab_stop(Inches(left_width))
    run = paragraph.add_run(clean_cell(left))
    set_run_font(run, size=size)
    run = paragraph.add_run("\t" + clean_cell(right))
    set_run_font(run, size=size)


def add_metadata_table(doc: Document, rows: list[tuple[str, str]]) -> None:
    table = doc.add_table(rows=0, cols=2)
    table.autofit = False
    for label, value in rows:
        if not clean_cell(value):
            continue
        cells = table.add_row().cells
        cells[0].text = f"{label}:"
        cells[1].text = clean_cell(value)
        cells[0].width = Inches(1.55)
        cells[1].width = Inches(5.75)
        for cell in cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for paragraph in cell.paragraphs:
                set_paragraph_spacing(paragraph)
                for run in paragraph.runs:
                    set_run_font(run, size=11, bold=(cell is cells[0]))
    table.style = "Table Grid"


def format_entry_text(row: sqlite3.Row, *, include_amount: bool = False) -> str:
    title, details, organization = detail_columns(row)
    parts = [title]
    if include_amount:
        parts = [row_value(row, "title"), row_value(row, "organization"), row_value(row, "role"), row_value(row, "amount"), row_value(row, "description")]
    else:
        parts.extend([details, organization])
    return "; ".join(clean_cell(part) for part in parts if clean_cell(part))


def add_entry_rows_docx(doc: Document, rows: list[sqlite3.Row], achievements_by_entry: dict[int, list[str]] | None = None, *, include_amount: bool = False) -> None:
    for row in rows:
        details = format_entry_text(row, include_amount=include_amount)
        achievements = (achievements_by_entry or {}).get(row["id"], [])
        if achievements:
            details = f"{details}; {tr('achievements')}: " + "; ".join(achievements)
        add_tabbed_paragraph(doc, row_period(row).replace("\n", "-"), details)


def add_publication_docx(doc: Document, index: int, row: sqlite3.Row) -> None:
    paragraph = doc.add_paragraph()
    set_paragraph_spacing(paragraph)
    paragraph.paragraph_format.tab_stops.add_tab_stop(Inches(0.32))
    run = paragraph.add_run(f"{index}.\t")
    set_run_font(run, size=10.5)
    alternate = format_publication(row, configured_citation_style())
    if alternate:
        set_run_font(paragraph.add_run(alternate), size=10.5)
        return
    authors = citation_cell(row["authors"])
    title = citation_cell(row["title"])
    venue = citation_cell(row["venue"])
    year = citation_cell(row["year"])
    doi = citation_cell(row["doi"])
    impact = impact_factor_label(row)
    pieces = [(sentence_part(authors), False, False), (sentence_part(title), False, False), (sentence_part(venue.title() if venue.isupper() else venue), True, True), (sentence_part(year), False, False), (sentence_part(impact), False, False)]
    first = True
    for text_value, italic, underline in pieces:
        if not text_value:
            continue
        if not first:
            sep = paragraph.add_run(" ")
            set_run_font(sep, size=10.5)
        first = False
        pos = 0
        for match in researcher_name_pattern().finditer(text_value):
            if match.start() > pos:
                run = paragraph.add_run(text_value[pos:match.start()])
                set_run_font(run, size=10.5, italic=italic, underline=underline)
            run = paragraph.add_run(match.group(0))
            set_run_font(run, size=10.5, bold=True, italic=italic, underline=underline)
            pos = match.end()
        if pos < len(text_value):
            run = paragraph.add_run(text_value[pos:])
            set_run_font(run, size=10.5, italic=italic, underline=underline)
    if doi:
        run = paragraph.add_run(f" doi:{doi}")
        set_run_font(run, size=10.5)


def add_wrapped_body_paragraphs(doc: Document, value: str) -> None:
    for block in re.split(r"\n\s*\n", clean_cell(value)):
        text_value = " ".join(line.strip() for line in block.splitlines() if line.strip())
        if text_value:
            add_text_paragraph(doc, text_value, size=11, after=2, justify=True)


def _normalized_paragraph_text(paragraph: Paragraph) -> str:
    return " ".join(paragraph.text.split()).strip()


def _find_paragraph(doc: Document, text: str) -> Paragraph | None:
    expected = " ".join(text.split()).strip()
    return next(
        (paragraph for paragraph in doc.paragraphs if _normalized_paragraph_text(paragraph) == expected),
        None,
    )


def _remove_paragraph(paragraph: Paragraph | None) -> None:
    if paragraph is not None and paragraph._p.getparent() is not None:
        paragraph._p.getparent().remove(paragraph._p)


def _remove_table(table: Table | None) -> None:
    if table is not None and table._tbl.getparent() is not None:
        table._tbl.getparent().remove(table._tbl)


def _prototype_run_properties(paragraph: Paragraph):
    for run in paragraph.runs:
        if run._r.rPr is not None:
            return copy.deepcopy(run._r.rPr)
    return None


def _clear_paragraph(paragraph: Paragraph) -> None:
    for child in list(paragraph._p):
        if child.tag != qn("w:pPr"):
            paragraph._p.remove(child)


def _styled_run(
    paragraph: Paragraph,
    text_value: str,
    run_properties,
    *,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
):
    run = paragraph.add_run(text_value)
    if run_properties is not None:
        run._r.insert(0, copy.deepcopy(run_properties))
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic
    if underline is not None:
        run.underline = underline
    return run


def _set_plain_paragraph(paragraph: Paragraph, text_value: str) -> None:
    run_properties = _prototype_run_properties(paragraph)
    _clear_paragraph(paragraph)
    _styled_run(paragraph, text_value, run_properties)


def _replace_repeating_paragraph(paragraph: Paragraph, values: list[str]) -> list[Paragraph]:
    if not values:
        _remove_paragraph(paragraph)
        return []
    prototype = copy.deepcopy(paragraph._p)
    result = []
    cursor = paragraph
    _set_plain_paragraph(paragraph, values[0])
    result.append(paragraph)
    for value in values[1:]:
        element = copy.deepcopy(prototype)
        cursor._p.addnext(element)
        cursor = Paragraph(element, paragraph._parent)
        _set_plain_paragraph(cursor, value)
        result.append(cursor)
    return result


def _set_cell_paragraphs(cell, values: str | list[str]) -> None:
    items = values if isinstance(values, list) else [values]
    if not items:
        items = [""]
    paragraphs = cell.paragraphs
    prototype = copy.deepcopy(paragraphs[0]._p)
    for paragraph in paragraphs:
        if paragraph._p.getparent() is not None:
            paragraph._p.getparent().remove(paragraph._p)
    for value in items:
        element = copy.deepcopy(prototype)
        cell._tc.append(element)
        _set_plain_paragraph(Paragraph(element, cell), clean_cell(value))


def _populate_table(table: Table, records: list[list[str | list[str]]]) -> None:
    prototype = copy.deepcopy(table.rows[0]._tr)
    for row in list(table.rows):
        table._tbl.remove(row._tr)
    for record in records:
        row_element = copy.deepcopy(prototype)
        table._tbl.append(row_element)
        row = table.rows[-1]
        for index, cell in enumerate(row.cells):
            value: str | list[str] = record[index] if index < len(record) else ""
            _set_cell_paragraphs(cell, value)


def _paragraph_after(anchor: Paragraph, prototype_element) -> Paragraph:
    element = copy.deepcopy(prototype_element)
    anchor._p.addnext(element)
    return Paragraph(element, anchor._parent)


def _date_year(value: str | None) -> int | None:
    matches = re.findall(r"\b(?:19|20)\d{2}\b", clean_cell(value))
    return int(matches[0]) if matches else None


def _formal_period(row: sqlite3.Row) -> str:
    start = row_value(row, "start_date").rstrip(",")
    end = row_value(row, "end_date").rstrip(",")
    if start and end:
        return f"{start}-{end}"
    if start:
        raw_text = row_value(row, "raw_text").lstrip()
        raw_date = raw_text.split("|", 1)[0].strip()
        is_open_ended = raw_date.startswith(f"{start}-") or raw_text.startswith(f"{start}-")
        return f"{start}-" if is_open_ended else start
    return end


def _description_parts(row: sqlite3.Row) -> list[str]:
    return [clean_cell(part) for part in row_value(row, "description").split("|") if clean_cell(part)]


def _formal_four_columns(row: sqlite3.Row) -> list[str]:
    parts = _description_parts(row)
    title = row_value(row, "title") or (parts[0] if parts else "")
    organization = row_value(row, "organization")
    if not organization and len(parts) >= 3:
        organization = parts[-1]
    detail = ""
    if len(parts) >= 3:
        detail = parts[1]
    elif row_value(row, "role") and row_value(row, "role") != organization:
        detail = row_value(row, "role")
    elif row_value(row, "location") and row_value(row, "location") != organization:
        detail = row_value(row, "location")
    elif len(parts) == 2 and parts[1] != organization:
        detail = parts[1]
    elif len(parts) == 1 and parts[0] not in {title, organization}:
        detail = parts[0]
    return [_formal_period(row), title, detail, organization]


def _formal_three_columns(row: sqlite3.Row, *, role_in_right: bool = False) -> list[str]:
    parts = _description_parts(row)
    title = row_value(row, "title") or (parts[0] if parts else "")
    organization = row_value(row, "organization")
    role = row_value(row, "role")
    if not organization and len(parts) >= 2:
        organization = parts[-1]
    middle = title
    if role and not role_in_right and role not in middle:
        middle = f"{middle}\n{role}" if middle else role
    right = organization
    if role and role_in_right and role not in right:
        right = f"{right}\n{role}" if right else role
    extra = row_value(row, "location")
    if extra and extra not in right:
        right = f"{right}\n{extra}" if right else extra
    return [_formal_period(row), middle, right]


def _formal_two_columns(row: sqlite3.Row, *, date_left: bool = True) -> list[str | list[str]]:
    left = _formal_period(row) if date_left else row_value(row, "title")
    title = row_value(row, "title")
    details = []
    if date_left and title:
        details.append(title)
    description = row_value(row, "description")
    if description and description != title:
        details.extend(clean_cell(part) for part in description.split("|") if clean_cell(part) and clean_cell(part) != title)
    organization = row_value(row, "organization")
    if organization and organization not in details:
        details.append(organization)
    role = row_value(row, "role")
    if role and role not in details:
        details.append(role)
    if not details and row_value(row, "raw_text"):
        details.append(row_value(row, "raw_text"))
    return [left, details]


def _funding_record(row: sqlite3.Row) -> list[str | list[str]]:
    role_amount = " ".join(
        part for part in (row_value(row, "role"), f"({row_value(row, 'amount')})" if row_value(row, "amount") else "") if part
    )
    details = [
        "\n".join(part for part in (row_value(row, "title"), row_value(row, "organization")) if part),
    ]
    if role_amount:
        details.append(role_amount)
    if row_value(row, "description"):
        details.append(row_value(row, "description"))
    return [_formal_period(row), details]


def _formal_trainee_records(con: sqlite3.Connection) -> list[list[str | list[str]]]:
    rows = con.execute(
        """
        SELECT
          t.id AS trainee_id,
          t.cv_entry_id,
          t.name,
          t.name_de,
          t.degree,
          t.degree_de,
          t.career_stage,
          t.career_stage_de,
          t.institution,
          t.institution_de,
          COALESCE(t.start_date, c.start_date) AS start_date,
          COALESCE(t.end_date, c.end_date) AS end_date,
          t.mentoring_role,
          t.mentoring_role_de
        FROM trainees t
        LEFT JOIN cv_entries c ON c.id = t.cv_entry_id
        WHERE c.id IS NULL OR c.include_long = 1
        ORDER BY t.id
        """
    ).fetchall()
    achievement_rows = con.execute(
        """
        SELECT trainee_id, title, organization, amount
        FROM trainee_achievements
        ORDER BY year, id
        """
    ).fetchall()
    achievements: dict[int, list[str]] = {}
    for row in achievement_rows:
        title = clean_cell(row["title"])
        organization = clean_cell(row["organization"])
        amount = clean_cell(row["amount"])
        parts = [title] if title else []
        if organization and organization.casefold() not in title.casefold():
            parts.append(organization)
        if amount and amount.casefold() not in title.casefold():
            parts.append(amount)
        value = ", ".join(parts)
        values = achievements.setdefault(row["trainee_id"], [])
        if value and value not in values:
            values.append(value)

    records = []
    for row in rows:
        name = clean_cell(row["name_de"] if LANG == "de" and clean_cell(row["name_de"]) else row["name"])
        degree = clean_cell(row["degree_de"] if LANG == "de" and clean_cell(row["degree_de"]) else row["degree"])
        institution = clean_cell(
            row["institution_de"] if LANG == "de" and clean_cell(row["institution_de"]) else row["institution"]
        )
        career_stage = clean_cell(
            row["career_stage_de"] if LANG == "de" and clean_cell(row["career_stage_de"]) else row["career_stage"]
        )
        mentoring_role = clean_cell(
            row["mentoring_role_de"]
            if LANG == "de" and clean_cell(row["mentoring_role_de"])
            else row["mentoring_role"]
        )
        start = clean_cell(row["start_date"])
        end = clean_cell(row["end_date"])
        dates = f"{start}-{end}" if start and end else start or end
        identity = " / ".join(part for part in (name, degree, institution) if part)
        labels = (
            ("Karrierestufe", "Betreuungsrolle", "Erfolge")
            if LANG == "de"
            else ("Career Stage", "Mentoring Role", "Accomplishments")
        )
        detail_parts = []
        if career_stage:
            detail_parts.append(f"{labels[0]}: {career_stage}")
        if mentoring_role:
            detail_parts.append(f"{labels[1]}: {mentoring_role}")
        trainee_achievements = achievements.get(row["trainee_id"], [])
        if trainee_achievements:
            detail_parts.append(f"{labels[2]}: " + "; ".join(trainee_achievements))
        cell_paragraphs = [identity]
        if detail_parts:
            cell_paragraphs.append("; ".join(detail_parts))
        records.append([dates, cell_paragraphs])
    return records


def _compact_current_funding(row: sqlite3.Row) -> str:
    detail = ". ".join(
        part.rstrip(".")
        for part in (row_value(row, "title"), row_value(row, "role"), row_value(row, "amount"))
        if part
    )
    return f"{_formal_period(row)}\t{detail}"


def _format_initials(given_names: list[str]) -> str:
    initials = []
    for name in given_names:
        for part in re.split(r"[-–]", name):
            letter = next((character for character in part if character.isalpha()), "")
            if letter:
                initials.append(f"{letter.upper()}.")
    return " ".join(initials)


def _formal_authors(value: str | None) -> str:
    people = [clean_cell(person) for person in clean_cell(value).split(",") if clean_cell(person)]
    formatted = []
    for person in people:
        words = person.split()
        if len(words) < 2 or re.fullmatch(r"[A-Z][A-Za-zÀ-ž'’-]+,?\s+[A-Z](?:\\.[A-Z]?\\.?)*", person):
            formatted.append(person)
            continue
        suffix = ""
        if words[-1].rstrip(".").lower() in {"jr", "sr", "ii", "iii", "iv"}:
            suffix = f", {words.pop()}"
        surname = words.pop()
        initials = _format_initials(words)
        formatted.append(f"{surname}, {initials}{suffix}".strip())
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]}, & {formatted[1]}"
    return ", ".join(formatted[:-1]) + f", & {formatted[-1]}" if formatted else ""


def _add_text_with_own_name_bold(paragraph: Paragraph, value: str, run_properties) -> None:
    position = 0
    pattern = researcher_name_pattern()
    for match in pattern.finditer(value):
        if match.start() > position:
            _styled_run(paragraph, value[position:match.start()], run_properties, bold=False)
        _styled_run(paragraph, match.group(0), run_properties, bold=True)
        position = match.end()
    if position < len(value):
        _styled_run(paragraph, value[position:], run_properties, bold=False)


def _add_hyperlink(paragraph: Paragraph, url: str, display: str, run_properties) -> None:
    relationship_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    run = OxmlElement("w:r")
    if run_properties is not None:
        properties = copy.deepcopy(run_properties)
    else:
        properties = OxmlElement("w:rPr")
    for tag in ("w:b", "w:bCs", "w:color", "w:u"):
        for node in list(properties.findall(qn(tag))):
            properties.remove(node)
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    properties.append(color)
    properties.append(underline)
    run.append(properties)
    text = OxmlElement("w:t")
    text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text.text = display
    run.append(text)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _add_publication_citation(paragraph: Paragraph, row: sqlite3.Row) -> None:
    run_properties = _prototype_run_properties(paragraph)
    _clear_paragraph(paragraph)
    alternate = format_publication(row, configured_citation_style())
    if alternate:
        _add_text_with_own_name_bold(paragraph, alternate, run_properties)
        return
    authors = _formal_authors(row["authors"])
    year = citation_cell(row["year"])
    title = sentence_part(row["title"])
    venue = sentence_part(row["venue"].title() if clean_cell(row["venue"]).isupper() else row["venue"])
    if authors:
        _add_text_with_own_name_bold(paragraph, authors, run_properties)
    if year:
        _styled_run(paragraph, f" ({year}). ", run_properties, bold=False)
    elif authors:
        _styled_run(paragraph, ". ", run_properties, bold=False)
    if title:
        _styled_run(paragraph, f"{title} ", run_properties, bold=False)
    if venue:
        _styled_run(paragraph, venue, run_properties, bold=False, italic=True, underline=True)
    link = doi_url(row)
    if link:
        _styled_run(paragraph, " ", run_properties, bold=False)
        normalized = normalize_url(link)
        _add_hyperlink(paragraph, normalized, normalized, run_properties)


def _populate_publication_table(table: Table, rows: list[sqlite3.Row]) -> None:
    cell = table.rows[0].cells[0]
    prototype = copy.deepcopy(cell.paragraphs[0]._p)
    for paragraph in list(cell.paragraphs):
        cell._tc.remove(paragraph._p)
    if not rows:
        cell._tc.append(copy.deepcopy(prototype))
        return
    for row in rows:
        element = copy.deepcopy(prototype)
        cell._tc.append(element)
        _add_publication_citation(Paragraph(element, cell), row)


def _insert_publication_paragraphs(
    heading: Paragraph,
    prototype,
    rows: list[sqlite3.Row],
) -> None:
    cursor = heading
    for row in rows:
        cursor = _paragraph_after(cursor, prototype)
        _add_publication_citation(cursor, row)


def _is_own_publication(row: sqlite3.Row, person: sqlite3.Row | None) -> bool:
    if person is None:
        return True
    display_name = clean_cell(person["display_name"] or person["full_name"])
    surname = display_name.split()[-1].casefold() if display_name else ""
    authors = clean_cell(row["authors"]).casefold()
    return not surname or bool(re.search(rf"\b{re.escape(surname)}\b", authors))


def _set_metrics_paragraph(paragraph: Paragraph, con: sqlite3.Connection, person: sqlite3.Row | None) -> None:
    rows = con.execute(
        """
        SELECT authors, COALESCE(openalex_cited_by_count, 0) AS citations
        FROM publications
        WHERE COALESCE(suppress_display, 0) = 0
        """
    ).fetchall()
    counts = sorted(
        (int(row["citations"] or 0) for row in rows if _is_own_publication(row, person)),
        reverse=True,
    )
    total = sum(counts)
    h_index = max((index for index, count in enumerate(counts, 1) if count >= index), default=0)
    i10_index = sum(1 for count in counts if count >= 10)
    run_properties = _prototype_run_properties(paragraph)
    _clear_paragraph(paragraph)
    metrics = f"{len(counts)} Publications; {total:,} citations; h-index: {h_index}; i10-index: {i10_index}"
    _styled_run(paragraph, metrics, run_properties)
    orcid = clean_cell(person["orcid_id"] if person and "orcid_id" in person.keys() else "")
    if orcid:
        _styled_run(paragraph, "\nORCID: ", run_properties)
        _add_hyperlink(paragraph, f"https://orcid.org/{orcid}", orcid, run_properties)


def _set_linked_body_paragraph(paragraph: Paragraph, value: str) -> None:
    run_properties = _prototype_run_properties(paragraph)
    _clear_paragraph(paragraph)
    position = 0
    pattern = re.compile(r"(?<!@)\b(?:https?://[^\s)]+|www\.[^\s)]+)")
    for match in pattern.finditer(value):
        if match.start() > position:
            _styled_run(paragraph, value[position:match.start()], run_properties)
        display = match.group(0).rstrip(".,;")
        trailing = match.group(0)[len(display):]
        _add_hyperlink(paragraph, normalize_url(display), display, run_properties)
        if trailing:
            _styled_run(paragraph, trailing, run_properties)
        position = match.end()
    if position < len(value):
        _styled_run(paragraph, value[position:], run_properties)


def _remove_heading_and_table(heading: Paragraph | None, table: Table | None) -> None:
    _remove_paragraph(heading)
    _remove_table(table)


def _remove_empty_paragraphs_before(paragraph: Paragraph | None) -> None:
    if paragraph is None:
        return
    cursor = paragraph._p.getprevious()
    while cursor is not None and cursor.tag == qn("w:p"):
        text_value = "".join(node.text or "" for node in cursor.iter(qn("w:t"))).strip()
        if text_value:
            break
        previous = cursor.getprevious()
        cursor.getparent().remove(cursor)
        cursor = previous


def _strip_word_editing_ids(doc: Document) -> None:
    """Remove optional editing-session IDs that become duplicated by cloning.

    Word requires ``w14:paraId`` values to be unique. Prototype rows and
    publication paragraphs intentionally clone OOXML, so retaining those IDs
    would create hundreds of duplicates. They are editing metadata rather than
    layout or content and are safe to omit; Word recreates them when necessary.
    """
    attributes = (qn("w14:paraId"), qn("w14:textId"))
    for part in doc.part.package.parts:
        root = getattr(part, "element", None)
        if root is None:
            root = getattr(part, "_element", None)
        if root is None:
            continue
        for element in root.iter():
            for attribute in attributes:
                element.attrib.pop(attribute, None)


def build_docx(path: Path, lang: str = "en") -> Path:
    global LANG
    LANG = "de" if lang == "de" else "en"
    if not FORMAL_ACADEMIC_TEMPLATE.exists():
        raise FileNotFoundError(f"Formal Academic CV template is missing: {FORMAL_ACADEMIC_TEMPLATE}")
    con = connect()
    person = con.execute("SELECT * FROM person WHERE id=1").fetchone()
    configure_researcher_name(person)
    now = dt.datetime.now()
    if LANG == "de":
        today = now.strftime("%d.%m.%Y")
    else:
        day = now.day
        suffix = "th" if 10 <= day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        today = f"{day}{suffix} {now.strftime('%B %Y')}"

    doc = Document(FORMAL_ACADEMIC_TEMPLATE)
    tables = list(doc.tables)
    if len(tables) != 25:
        con.close()
        raise RuntimeError(f"Formal Academic CV template has {len(tables)} tables; expected 25.")

    if LANG == "de":
        _set_plain_paragraph(doc.paragraphs[1], tr("cv"))
    _remove_paragraph(doc.paragraphs[0])

    metadata = [
        (tr("date_prepared"), today),
        (tr("name"), person["display_name"] or person["full_name"] if person else ""),
        (tr("office_address"), person["office_address"] if person else ""),
        (tr("home_address"), person["home_address"] if person else ""),
        (tr("work_phone"), person["work_phone"] if person else ""),
        (tr("work_email"), person["work_email"] if person else ""),
        (tr("place_of_birth"), person["place_of_birth"] if person else ""),
    ]
    for index, (label, value) in enumerate(metadata):
        if index >= len(tables[0].rows):
            break
        _set_cell_paragraphs(tables[0].rows[index].cells[0], label)
        _set_cell_paragraphs(tables[0].rows[index].cells[1], value)

    rows_by_section = {key: entry_rows(con, key) for key, _title in SECTION_ORDER}
    four_column_mapping = [
        ("education", 1, "Education:"),
        ("postdoctoral_training", 2, "Postdoctoral Training:"),
        ("academic_appointments", 3, "Faculty Academic Appointments:"),
        ("hospital_appointments", 4, "Appointments at Hospitals/Affiliated Institutions:"),
        ("professional_positions", 5, "Other Professional Positions:"),
    ]
    for section_key, table_index, heading_text in four_column_mapping:
        rows = rows_by_section[section_key]
        if rows:
            records = [_formal_four_columns(row) for row in rows]
            if section_key == "hospital_appointments":
                merged_records = []
                for record in records:
                    if not any(record[1:]) and merged_records:
                        merged_records[-1][0] = f"{merged_records[-1][0]}\n{record[0]}"
                    else:
                        merged_records.append(record)
                records = merged_records
            _populate_table(tables[table_index], records)
        else:
            _remove_heading_and_table(_find_paragraph(doc, heading_text), tables[table_index])

    committee_rows = rows_by_section["committee_service"]
    local_committee = [row for row in committee_rows if row_value(row, "subcategory").lower() == "local"]
    international_committee = [row for row in committee_rows if row not in local_committee]
    if local_committee:
        _populate_table(tables[6], [_formal_three_columns(row) for row in local_committee])
    else:
        _remove_heading_and_table(_find_paragraph(doc, "Local"), tables[6])
    if international_committee:
        _populate_table(tables[7], [_formal_three_columns(row) for row in international_committee])
    else:
        _remove_heading_and_table(_find_paragraph(doc, "International"), tables[7])
    if not committee_rows:
        _remove_paragraph(_find_paragraph(doc, "Committee Service:"))

    simple_three_column = [
        ("professional_societies", 8, "Professional Societies:"),
        ("grant_review", 9, "Grant Review Activities:"),
    ]
    for section_key, table_index, heading_text in simple_three_column:
        rows = rows_by_section[section_key]
        if rows:
            _populate_table(
                tables[table_index],
                [
                    _formal_three_columns(row, role_in_right=section_key == "grant_review")
                    for row in rows
                ],
            )
        else:
            _remove_heading_and_table(_find_paragraph(doc, heading_text), tables[table_index])

    editorial_rows = rows_by_section["editorial_activities"]
    ad_hoc_rows = [
        row for row in editorial_rows
        if row_value(row, "subcategory").lower() == "ad hoc reviewer"
        or row_value(row, "title").lower() == "ad hoc reviewer"
    ]
    other_editorial = [row for row in editorial_rows if row not in ad_hoc_rows]
    if ad_hoc_rows:
        journals = []
        for row in ad_hoc_rows:
            value = row_value(row, "description") or row_value(row, "raw_text")
            journals.extend(line.strip() for line in value.splitlines() if line.strip())
        _populate_table(tables[10], [[journals]])
        for paragraph in tables[10].cell(0, 0).paragraphs:
            if re.search(r"(?:https?://|www\.)", paragraph.text):
                _set_linked_body_paragraph(paragraph, paragraph.text)
    else:
        _remove_heading_and_table(_find_paragraph(doc, "Ad hoc Reviewer"), tables[10])
    if other_editorial:
        _populate_table(tables[11], [_formal_three_columns(row) for row in other_editorial])
        other_editorial_heading = _find_paragraph(doc, "Other Editorial Roles")
        if other_editorial_heading:
            other_editorial_heading.paragraph_format.space_before = Pt(6)
    else:
        _remove_heading_and_table(_find_paragraph(doc, "Other Editorial Roles"), tables[11])
    if not editorial_rows:
        _remove_paragraph(_find_paragraph(doc, "Editorial Activities:"))

    honors = rows_by_section["honors"]
    if honors:
        _populate_table(tables[12], [_formal_four_columns(row) for row in honors])
    else:
        _remove_heading_and_table(_find_paragraph(doc, "Honors and Prizes:"), tables[12])

    funding = rows_by_section["funding"]
    current_funding = []
    past_funding = []
    for row in funding:
        start_year = _date_year(row_value(row, "start_date"))
        end_year = _date_year(row_value(row, "end_date"))
        if start_year is not None and start_year >= now.year - 1 and (end_year is None or end_year >= now.year):
            current_funding.append(row)
        else:
            past_funding.append(row)
    if past_funding:
        _populate_table(tables[13], [_funding_record(row) for row in past_funding])
    else:
        _remove_heading_and_table(_find_paragraph(doc, "Past"), tables[13])
    current_slot = _find_paragraph(doc, "{{CURRENT_FUNDING_ENTRY}}")
    if current_slot and current_funding:
        _replace_repeating_paragraph(current_slot, [_compact_current_funding(row) for row in current_funding])
    else:
        _remove_paragraph(current_slot)
        _remove_paragraph(_find_paragraph(doc, "Current"))
    if not funding:
        _remove_paragraph(_find_paragraph(doc, "Report of Funded and Unfunded Projects"))

    teaching = rows_by_section["teaching"]
    if teaching:
        teaching_records = []
        for row in teaching:
            record = _formal_three_columns(row)
            if not row_value(row, "title") and teaching_records:
                if record[0]:
                    teaching_records[-1][0] = f"{teaching_records[-1][0]}\n{record[0]}"
                if record[2]:
                    teaching_records[-1][2] = f"{teaching_records[-1][2]}\n{record[2]}"
            else:
                teaching_records.append(record)
        _populate_table(tables[14], teaching_records)
    else:
        _remove_heading_and_table(_find_paragraph(doc, "Teaching of Students in Courses:"), tables[14])

    mentoring = rows_by_section["mentoring"]
    general_mentoring = [
        row for row in mentoring
        if row_value(row, "title").lower().startswith("supervision of phd students")
    ]
    trainee_records = _formal_trainee_records(con)
    if general_mentoring:
        _populate_table(tables[15], [_formal_three_columns(row) for row in general_mentoring])
    else:
        _remove_heading_and_table(
            _find_paragraph(doc, "Research Supervisory and Training Responsibilities:"),
            tables[15],
        )
    if trainee_records:
        _populate_table(tables[16], trainee_records)
    else:
        _remove_heading_and_table(_find_paragraph(doc, "Other Formally Supervised Trainees"), tables[16])
    if not teaching and not mentoring:
        _remove_paragraph(_find_paragraph(doc, "Report of Local Teaching and Training"))

    invited = rows_by_section["invited_presentations"]
    if invited:
        _populate_table(tables[17], [_formal_two_columns(row) for row in invited])
    else:
        _remove_heading_and_table(
            _find_paragraph(doc, "Report of Regional, National and International Invited Teaching and Presentations"),
            tables[17],
        )
        _remove_paragraph(_find_paragraph(doc, tr("no_sponsor")))

    clinical = rows_by_section["clinical_activities"]
    if clinical:
        _populate_table(tables[18], [_formal_two_columns(row, date_left=False) for row in clinical])
    else:
        _remove_heading_and_table(_find_paragraph(doc, "Clinical Innovations:"), tables[18])
        _remove_paragraph(_find_paragraph(doc, "Report of Clinical Activities and Innovations"))

    innovations = rows_by_section["education_innovations"]
    if innovations:
        _populate_table(tables[19], [_formal_two_columns(row, date_left=False) for row in innovations])
    else:
        _remove_table(tables[19])
        _remove_paragraph(_find_paragraph(doc, "Report of Teaching and Education Innovations"))

    community = rows_by_section["community_service"]
    if community:
        _populate_table(tables[20], [_formal_two_columns(row) for row in community])
    else:
        _remove_heading_and_table(_find_paragraph(doc, "Activities"), tables[20])
        _remove_paragraph(_find_paragraph(doc, "Report of Education of Patients and Service to the Community"))

    publication_groups = {
        label_key: [row for row in rows if _is_own_publication(row, person)]
        for label_key, rows in publication_category_groups(con)
    }
    publication_groups = {label_key: rows for label_key, rows in publication_groups.items() if rows}
    publication_prototype = copy.deepcopy(tables[21].rows[0].cells[0].paragraphs[0]._p)
    peer_reviewed = publication_groups.get("peer_reviewed", [])
    if peer_reviewed:
        _populate_publication_table(tables[21], peer_reviewed)
    else:
        _remove_table(tables[21])
        _remove_paragraph(_find_paragraph(doc, "Research Investigations"))

    body_publication_headings = {
        "books_chapters": "Books / Chapters",
        "patents": "Patents",
    }
    for label_key, heading_text in body_publication_headings.items():
        heading = _find_paragraph(doc, heading_text)
        rows = publication_groups.get(label_key, [])
        if heading and rows:
            _insert_publication_paragraphs(heading, publication_prototype, rows)
        else:
            _remove_paragraph(heading)

    for unused_heading in (
        "Other peer-reviewed scholarship",
        "Case reports",
        "Letters to the Editor",
        "Theses",
    ):
        _remove_paragraph(_find_paragraph(doc, unused_heading))
    for table in tables[22:25]:
        _remove_table(table)

    metrics = _find_paragraph(doc, "{{SCHOLARSHIP_METRICS}}")
    if publication_groups and metrics:
        _set_metrics_paragraph(metrics, con, person)
    else:
        _remove_paragraph(metrics)
        _remove_paragraph(_find_paragraph(doc, "Peer-Reviewed Scholarship in print or other media:"))
        _remove_paragraph(_find_paragraph(doc, "Report of Scholarship"))

    report = con.execute("SELECT title, body, title_de, body_de FROM narrative_reports WHERE id=1").fetchone()
    narrative_heading = _find_paragraph(doc, "Narrative Report")
    _remove_empty_paragraphs_before(narrative_heading)
    narrative_body = _find_paragraph(doc, "{{NARRATIVE_BODY}}")
    if report and clean_cell(row_value(report, "body")) and narrative_body:
        _set_plain_paragraph(narrative_heading, row_value(report, "title") or tr("narrative_report"))
        _set_linked_body_paragraph(narrative_body, row_value(report, "body"))
        narrative_body.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    else:
        _remove_paragraph(narrative_heading)
        _remove_paragraph(narrative_body)

    con.close()
    if person:
        doc.core_properties.author = person["display_name"] or person["full_name"] or ""
    doc.core_properties.title = tr("cv")
    _strip_word_editing_ids(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    sanitize_docx_compatibility_markup(path)
    return path


def build(lang: str = "en") -> dict[str, str]:
    global LANG
    LANG = "de" if lang == "de" else "en"
    OUTPUT.mkdir(parents=True, exist_ok=True)
    stem = output_stem()
    docx_path = OUTPUT / f"{stem}.docx"
    docx = build_docx(docx_path, LANG)
    return {"docx": f"output/{output_ref(docx)}"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", choices=["en", "de"], default="en")
    args = parser.parse_args()
    for name, path in build(args.lang).items():
        print(f"{name}: {path}")
