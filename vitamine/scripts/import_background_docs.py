#!/usr/bin/env python3
"""Import background CV documents into the local CV SQLite database."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from vitamine.i18n import GERMAN_FIELD_PAIRS, draft_translate_to_german
from vitamine.paths import DATA, ROOT, SCHEMA, active_db_path, tool_path

EXTRACTS = ROOT / "extracts"
DB = active_db_path()


def background_doc(filename: str) -> Path:
    for directory in (ROOT / "background_docs",):
        candidate = directory / filename
        if candidate.exists():
            return candidate
    return ROOT / "background_docs" / filename

SOURCES = [
    {
        "slug": "harvard_cv_2026_03_12",
        "title": "Harvard-format curriculum vitae",
        "path": background_doc("Horn_08_05_25 CV.docx"),
        "format": "docx",
    },
    {
        "slug": "nih_biosketch_2025_12_08",
        "title": "NIH biosketch",
        "path": background_doc("Biosketch_Horn_08_12_2025.docx"),
        "format": "docx",
    },
]


SECTION_ALIASES = {
    "education": "education",
    "education/training": "education_training",
    "postdoctoral training": "postdoctoral_training",
    "faculty academic appointments": "academic_appointments",
    "appointments at hospitals/affiliated institutions": "hospital_appointments",
    "other professional positions": "professional_positions",
    "committee service": "committee_service",
    "professional societies": "professional_societies",
    "grant review activities": "grant_review",
    "editorial activities": "editorial_activities",
    "honors and prizes": "honors",
    "honors": "honors",
    "report of funded and unfunded projects": "funding",
    "report of local teaching and training": "local_teaching_training",
    "teaching of students in courses": "teaching",
    "research supervisory and training responsibilities": "mentoring",
    "report of regional, national and international invited teaching and presentations": "invited_presentations",
    "report of clinical activities and innovations": "clinical_activities",
    "report of teaching and education innovations": "education_innovations",
    "report of education of patients and service to the community": "community_service",
    "report of scholarship": "scholarship",
    "peer-reviewed scholarship in print or other media": "peer_reviewed_publications",
    "other peer-reviewed scholarship": "other_peer_reviewed_scholarship",
    "books / chapters": "books_chapters",
    "case reports": "case_reports",
    "letters to the editor": "letters_to_editor",
    "theses": "theses",
    "patents": "patents",
    "narrative report": "narrative_report",
    "a. personal statement": "biosketch_personal_statement",
    "b. positions, scientific appointments, and honors": "biosketch_positions_honors",
    "positions and scientific appointments": "biosketch_positions",
    "c. contributions to science": "biosketch_contributions",
}

PUBLICATION_SECTIONS = {
    "peer_reviewed_publications": "peer_reviewed",
    "other_peer_reviewed_scholarship": "other_peer_reviewed",
    "books_chapters": "books_chapters",
    "case_reports": "case_reports",
    "letters_to_editor": "letters_to_editor",
    "theses": "theses",
    "patents": "patents",
}

ZOTERO_ITEM_CATEGORIES = {
    "journalArticle": "peer_reviewed",
    "book": "books_chapters",
    "bookSection": "books_chapters",
    "conferencePaper": "conference_presentations",
    "presentation": "conference_presentations",
    "patent": "patents",
    "thesis": "theses",
    "letter": "letters_to_editor",
    "preprint": "preprints",
    "poster": "posters",
    "report": "reports",
}


def run_pandoc(path: Path) -> str:
    pandoc = tool_path("pandoc")
    if pandoc is None:
        raise RuntimeError("Pandoc is required to import DOCX background documents but was not found.")
    return subprocess.check_output(
        [pandoc, "-t", "markdown", "--wrap=none", str(path)],
        cwd=ROOT,
        text=True,
    )


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        env[key.strip()] = value
    return env


def clean_markup(text: str) -> str:
    text = text.replace("\\\n", "\n")
    text = re.sub(r"\{\.underline\}", "", text)
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"\*(.*?)\*", r"\1", text)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    text = re.sub(r"\[(.*?)\]", r"\1", text)
    text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
    text = text.replace("\\>", ">").replace("\\$", "$")
    text = text.replace("\\", "")
    text = text.replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


def heading_title(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("|") or set(stripped) <= {"-", "=", " ", ":"}:
        return None
    candidate = clean_markup(stripped)
    candidate = candidate.strip("> ").rstrip("\\").rstrip(":").strip()
    if not candidate:
        return None
    lower = candidate.lower()
    if lower in SECTION_ALIASES:
        return candidate
    if re.match(r"^[A-D]\. ", candidate):
        return candidate
    return None


def section_key(title: str) -> str:
    return SECTION_ALIASES.get(title.lower(), re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_"))


def split_sections(markdown: str) -> list[dict]:
    sections: list[dict] = []
    current_title = "Document Header"
    current_key = "document_header"
    current_lines: list[str] = []
    ordinal = 0

    for line in markdown.splitlines():
        title = heading_title(line)
        if title is not None:
            if current_lines:
                sections.append(
                    {
                        "ordinal": ordinal,
                        "title": current_title,
                        "section_key": current_key,
                        "raw_markdown": "\n".join(current_lines).strip(),
                    }
                )
                ordinal += 1
            current_title = title
            current_key = section_key(title)
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append(
            {
                "ordinal": ordinal,
                "title": current_title,
                "section_key": current_key,
                "raw_markdown": "\n".join(current_lines).strip(),
            }
        )
    return sections


def table_cells(line: str) -> list[str]:
    if not line.strip().startswith("|"):
        return []
    cells = [clean_markup(c) for c in line.strip().strip("|").split("|")]
    if not cells or all(not c for c in cells):
        return []
    if all(set(c) <= {"-", "=", ":", " "} for c in cells):
        return []
    return cells


def parse_date_range(text: str) -> tuple[str | None, str | None]:
    value = clean_markup(text)
    m = re.match(r"^(\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}/\d{1,2}/\d{2}|\d{4}|\d{4}-\d{4}|\d{4}-Present|\d{4}-)\s*(?:-|–|—)\s*(.*)$", value)
    if m and re.match(r"^\d{4}-\d{4}$", m.group(1)):
        start, end = m.group(1).split("-")
        return start, end
    if m:
        return m.group(1), m.group(2) or None
    m = re.match(r"^(\d{4})(?:\s+|$)", value)
    if m:
        return m.group(1), None
    return None, None


def split_date_prefix(text: str) -> tuple[str | None, str | None, str]:
    value = clean_markup(text)
    date = r"(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4})"
    m = re.match(rf"^({date})(?:\s*(?:-|–|—)\s*({date}|Present)?)?\s+(.*)$", value)
    if m:
        return m.group(1), m.group(2), m.group(3).strip()
    m = re.match(r"^(\d{4})-(\d{4}|Present)\s+(.*)$", value)
    if m:
        return m.group(1), m.group(2), m.group(3).strip()
    return None, None, value


def split_date_prefix_preserve_spacing(line: str) -> tuple[str | None, str | None, str]:
    value = line.strip()
    date = r"(?:\d{1,2}/\d{1,2}/\d{2,4}|\d{4})"
    m = re.match(rf"^({date})(?:\s*(?:-|–|—)\s*({date}|Present)?)?\s+(.*)$", value)
    if m:
        return m.group(1), m.group(2), m.group(3).strip()
    m = re.match(r"^(\d{4})-(\d{4}|Present)\s+(.*)$", value)
    if m:
        return m.group(1), m.group(2), m.group(3).strip()
    return None, None, value


def normalize_citation(raw: str) -> dict:
    text = clean_markup(raw)
    ordinal = None
    m = re.match(r"^(\d+)\.\s*(.*)$", text)
    if m:
        ordinal = int(m.group(1))
        text = m.group(2)
    doi = None
    m = re.search(r"\bdoi:?\s*([^\s;]+)", text, flags=re.I)
    if m:
        doi = m.group(1).rstrip(").")
    pmid = None
    m = re.search(r"\bPMID:?\s*(\d+)", text, flags=re.I)
    if m:
        pmid = m.group(1)
    year = None
    years = re.findall(r"\b(19\d{2}|20\d{2})\b", text)
    if years:
        year = years[-1]
    authors = title = venue = None
    parts = [p.strip() for p in text.split(". ") if p.strip()]
    if len(parts) >= 2:
        authors = parts[0]
        title = parts[1]
    if len(parts) >= 3:
        venue = parts[2].strip(".")
    return {
        "ordinal": ordinal,
        "authors": authors,
        "title": title,
        "venue": venue,
        "year": year,
        "doi": doi,
        "pmid": pmid,
        "raw_citation": text,
    }


def extract_publications(section: dict) -> list[dict]:
    category = PUBLICATION_SECTIONS.get(section["section_key"])
    if not category:
        return []
    pubs: list[dict] = []
    for line in section["raw_markdown"].splitlines():
        cells = table_cells(line)
        candidates = cells if cells else [line.strip()]
        for candidate in candidates:
            text = clean_markup(candidate)
            if re.match(r"^\d+\.\s+", text):
                pub = normalize_citation(text)
                pub["category"] = category
                pubs.append(pub)
    return pubs


def extract_entries(section: dict) -> list[dict]:
    key = section["section_key"]
    if key in PUBLICATION_SECTIONS or key.startswith("biosketch") or key in {"document_header"}:
        return []
    entries: list[dict] = []
    for line in section["raw_markdown"].splitlines():
        cells = table_cells(line)
        if cells:
            first = cells[0]
            start, end = parse_date_range(first)
            if start or len([c for c in cells if c]) > 1:
                entries.append(
                    {
                        "section_key": key,
                        "start_date": start,
                        "end_date": end,
                        "title": cells[1] if len(cells) > 1 else None,
                        "organization": cells[3] if len(cells) > 3 else (cells[2] if len(cells) > 2 else None),
                        "description": " | ".join(c for c in cells[1:] if c),
                        "raw_text": " | ".join(cells),
                        "confidence": "medium",
                    }
                )
            continue

        text = clean_markup(line)
        if not text:
            continue
        start, end, rest_with_spacing = split_date_prefix_preserve_spacing(line)
        if start:
            cells = [clean_markup(part) for part in re.split(r"\s{2,}", rest_with_spacing) if part.strip()]
            if not cells:
                _, _, rest = split_date_prefix(text)
                cells = [rest]
            title = cells[0] if cells else None
            organization = cells[-1] if len(cells) > 1 else None
            description = " | ".join(cells)
            entries.append(
                {
                    "section_key": key,
                    "start_date": start,
                    "end_date": end,
                    "title": title,
                    "organization": organization,
                    "description": description,
                    "raw_text": text,
                    "confidence": "medium" if len(cells) > 1 else "low",
                }
            )
    return entries


def extract_person(markdown_by_slug: dict[str, str]) -> dict:
    cv = markdown_by_slug.get("harvard_cv_2026_03_12", "")
    bio = markdown_by_slug.get("nih_biosketch_2025_12_08", "")
    fields: dict[str, str | None] = {
        "full_name": "Andreas Georg Horn",
        "display_name": "Andreas Horn",
        "degrees": "MD, PhD",
    }
    patterns = {
        "position_title": r"POSITION TITLE:\s*(.+)",
        "era_commons": r"eRA COMMONS USER NAME:\s*(.+)",
        "office_address": r"\*\*Office Address:\*\*\s+\*\*(.*?)\*\*",
        "home_address": r"\*\*Home Address:\*\*\s+\*\*(.*?)\*\*",
        "work_phone": r"\*\*Work Phone:\*\*\s+\*\*(.*?)\*\*",
        "work_email": r"\*\*Work Email:\*\*\s+\*\*(.*?)\*\*",
        "place_of_birth": r"\*\*Place of Birth:\*\*\s+\*\*(.*?)\*\*",
    }
    for key, pattern in patterns.items():
        source = bio if key in {"position_title", "era_commons"} else cv
        m = re.search(pattern, source)
        fields[key] = clean_markup(m.group(1)) if m else None
    name_match = re.search(r"NAME:\s*(.+)", bio)
    if name_match:
        name = clean_markup(name_match.group(1))
        name_without_degrees = name.replace(", MD, PhD", "")
        if "," in name_without_degrees:
            last, first = [part.strip() for part in name_without_degrees.split(",", 1)]
            name_without_degrees = f"{first} {last}"
        fields["full_name"] = name_without_degrees
    return fields


def extract_biosketch_contributions(section: dict) -> list[dict]:
    if section["section_key"] != "biosketch_contributions":
        return []
    blocks = re.split(r"\n(?=\*\*\d+\.)", section["raw_markdown"])
    contributions: list[dict] = []
    for block in blocks:
        text = block.strip()
        m = re.match(r"\*\*(\d+)\.\s*(.*?)\*\*\.?\s*(.*)", text, flags=re.S)
        if not m:
            continue
        ordinal = int(m.group(1))
        title = clean_markup(m.group(2))
        rest = m.group(3).strip()
        citations = []
        narrative_lines = []
        for line in rest.splitlines():
            clean = clean_markup(line)
            if re.match(r"^[a-d]\.\s+", clean):
                citations.append(clean)
            elif clean:
                narrative_lines.append(clean)
        contributions.append(
            {
                "ordinal": ordinal,
                "title": title,
                "narrative": " ".join(narrative_lines),
                "citations_json": json.dumps(citations, ensure_ascii=False),
            }
        )
    return contributions


def creator_name(creator: dict) -> str:
    if creator.get("name"):
        return creator["name"]
    first = creator.get("firstName", "").strip()
    last = creator.get("lastName", "").strip()
    return f"{first} {last}".strip()


def creator_is_andreas_horn(creator: dict) -> bool:
    name = creator_name(creator).lower()
    last = creator.get("lastName", "").lower()
    first = creator.get("firstName", "").lower()
    return last == "horn" and (not first or first.startswith("a") or "andreas" in first) or "andreas horn" in name


def zotero_request(url: str, api_key: str) -> tuple[list[dict], dict[str, str]]:
    request = urllib.request.Request(
        url,
        headers={
            "Zotero-API-Key": api_key,
            "Zotero-API-Version": "3",
            "User-Agent": "vitamine/0.1",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                headers = {k: v for k, v in response.headers.items()}
                return json.loads(response.read().decode("utf-8")), headers
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(float(exc.headers.get("Retry-After", "2")))
                continue
            raise
    raise RuntimeError("Zotero request failed after retries")


def fetch_zotero_items(env: dict[str, str]) -> list[dict]:
    api_key = env.get("ZOTERO_API_KEY") or os.environ.get("ZOTERO_API_KEY")
    library_id = env.get("ZOTERO_LIBRARY_ID") or os.environ.get("ZOTERO_LIBRARY_ID")
    library_type = (env.get("ZOTERO_LIBRARY_TYPE") or os.environ.get("ZOTERO_LIBRARY_TYPE") or "users").strip("/")
    if library_type in {"user", "users"}:
        library_type = "users"
    elif library_type in {"group", "groups"}:
        library_type = "groups"
    if not api_key or not library_id:
        return []

    base = f"https://api.zotero.org/{library_type}/{library_id}/items/top"
    items: list[dict] = []
    start = 0
    limit = 100
    while True:
        params = urllib.parse.urlencode(
            {
                "format": "json",
                "limit": limit,
                "start": start,
                "sort": "date",
                "direction": "asc",
            }
        )
        batch, headers = zotero_request(f"{base}?{params}", api_key)
        items.extend(batch)
        total = int(headers.get("Total-Results", len(items)))
        start += limit
        if start >= total or not batch:
            break
    return items


def zotero_year(data: dict) -> str | None:
    for key in ("date", "dateEnacted", "issueDate"):
        value = data.get(key)
        if value:
            m = re.search(r"\b(19\d{2}|20\d{2})\b", str(value))
            if m:
                return m.group(1)
    return None


def zotero_pmid(data: dict) -> str | None:
    extra = data.get("extra") or ""
    m = re.search(r"\bPMID:?\s*(\d+)", extra, flags=re.I)
    return m.group(1) if m else None


def zotero_raw_citation(data: dict) -> str:
    creators = ", ".join(creator_name(c) for c in data.get("creators", []) if creator_name(c))
    title = data.get("title") or ""
    venue = data.get("publicationTitle") or data.get("bookTitle") or data.get("conferenceName") or data.get("publisher") or ""
    year = zotero_year(data) or ""
    doi = data.get("DOI") or ""
    parts = [part for part in [creators, title, venue, year] if part]
    citation = ". ".join(parts)
    if doi:
        citation = f"{citation}. doi:{doi}" if citation else f"doi:{doi}"
    return citation


def zotero_dedupe_key(data: dict) -> tuple[str, str]:
    doi = (data.get("DOI") or "").strip().lower()
    if doi:
        return ("doi", doi)
    title = clean_markup(data.get("title") or "").lower()
    year = zotero_year(data) or ""
    return ("title_year", f"{title}|{year}")


def import_zotero_publications(con: sqlite3.Connection, imported_at: str, env: dict[str, str]) -> int:
    items = fetch_zotero_items(env)
    if not items:
        con.execute(
            """
            INSERT INTO import_warnings (warning_type, message)
            VALUES ('zotero_skipped', 'Zotero credentials were not available or returned no items; document-parsed publications were retained.')
            """
        )
        return 0

    cur = con.execute(
        """
        INSERT INTO documents (slug, title, source_path, source_format, imported_at, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "zotero_library",
            "Zotero publication library",
            "ZOTERO_LIBRARY_ID from .env",
            "zotero-api",
            imported_at,
            "Top-level Zotero items imported through the Zotero Web API. API key is read from .env and not stored.",
        ),
    )
    document_id = cur.lastrowid
    count = 0
    imported_items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        data = item.get("data", {})
        item_type = data.get("itemType")
        if item_type in {"attachment", "note", "annotation"}:
            continue
        creators = data.get("creators", [])
        if creators and not any(creator_is_andreas_horn(c) for c in creators):
            continue
        authors = ", ".join(creator_name(c) for c in creators if creator_name(c))
        title = data.get("title")
        if not title:
            continue
        dedupe_key = zotero_dedupe_key(data)
        if dedupe_key in seen:
            con.execute(
                """
                INSERT INTO import_warnings (document_id, warning_type, message, raw_text)
                VALUES (?, 'zotero_duplicate_skipped', ?, ?)
                """,
                (
                    document_id,
                    f"Skipped duplicate Zotero publication item {data.get('key') or item.get('key')}.",
                    zotero_raw_citation(data),
                ),
            )
            continue
        seen.add(dedupe_key)
        con.execute(
            """
            INSERT INTO publications (
              document_id, source, zotero_key, item_type, category, authors, title, venue,
              year, doi, pmid, url, abstract, extra, raw_citation, confidence
            ) VALUES (?, 'zotero', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'high')
            """,
            (
                document_id,
                data.get("key") or item.get("key"),
                item_type,
                ZOTERO_ITEM_CATEGORIES.get(item_type, item_type or "other"),
                authors,
                title,
                data.get("publicationTitle") or data.get("bookTitle") or data.get("conferenceName") or data.get("publisher"),
                zotero_year(data),
                data.get("DOI"),
                zotero_pmid(data),
                data.get("url"),
                data.get("abstractNote"),
                data.get("extra"),
                zotero_raw_citation(data),
            ),
        )
        imported_items.append(item)
        count += 1
    cache_path = DATA / "zotero_imported_items.json"
    cache_path.write_text(json.dumps(imported_items, ensure_ascii=False, indent=2), encoding="utf-8")
    return count


def prefill_german_entry_fields(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(cv_entries)").fetchall()}
    for _english, german in GERMAN_FIELD_PAIRS:
        if german not in existing:
            con.execute(f"ALTER TABLE cv_entries ADD COLUMN {german} TEXT")
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM cv_entries").fetchall()
    for row in rows:
        assignments = []
        values = []
        for english_field, german_field in GERMAN_FIELD_PAIRS:
            source = row[english_field] if english_field in row.keys() else None
            current = row[german_field] if german_field in row.keys() else None
            if source and not current:
                assignments.append(f"{german_field} = ?")
                values.append(draft_translate_to_german(source))
        if assignments:
            values.append(row["id"])
            con.execute(f"UPDATE cv_entries SET {', '.join(assignments)} WHERE id = ?", values)


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    EXTRACTS.mkdir(parents=True, exist_ok=True)

    markdown_by_slug: dict[str, str] = {}
    imported_at = dt.datetime.now(dt.timezone.utc).isoformat()
    for source in SOURCES:
        markdown = run_pandoc(source["path"])
        markdown_by_slug[source["slug"]] = markdown
        (EXTRACTS / f"{source['slug']}.md").write_text(markdown, encoding="utf-8")

    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    env = load_env(ROOT / ".env")

    person = extract_person(markdown_by_slug)
    con.execute(
        """
        INSERT INTO person (
          id, full_name, display_name, degrees, position_title, office_address,
          home_address, work_phone, work_email, place_of_birth, era_commons, raw_json
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            person.get("full_name"),
            person.get("display_name"),
            person.get("degrees"),
            person.get("position_title"),
            person.get("office_address"),
            person.get("home_address"),
            person.get("work_phone"),
            person.get("work_email"),
            person.get("place_of_birth"),
            person.get("era_commons"),
            json.dumps(person, ensure_ascii=False, indent=2),
        ),
    )

    for source in SOURCES:
        cur = con.execute(
            """
            INSERT INTO documents (slug, title, source_path, source_format, imported_at, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                source["slug"],
                source["title"],
                str(source["path"].relative_to(ROOT)),
                source["format"],
                imported_at,
                "Imported from DOCX with pandoc; PDFs retained as reference in background_docs.",
            ),
        )
        document_id = cur.lastrowid
        sections = split_sections(markdown_by_slug[source["slug"]])
        for section in sections:
            con.execute(
                """
                INSERT INTO sections (document_id, section_key, title, ordinal, raw_markdown)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    section["section_key"],
                    section["title"],
                    section["ordinal"],
                    section["raw_markdown"],
                ),
            )
            for entry in extract_entries(section):
                con.execute(
                    """
                    INSERT INTO cv_entries (
                      document_id, section_key, start_date, end_date, title,
                      organization, description, raw_text, confidence,
                      include_short, include_biosketch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        entry["section_key"],
                        entry.get("start_date"),
                        entry.get("end_date"),
                        entry.get("title"),
                        entry.get("organization"),
                        entry.get("description"),
                        entry["raw_text"],
                        entry["confidence"],
                        1 if entry["section_key"] in {"education", "honors", "academic_appointments"} else 0,
                        1 if entry["section_key"].startswith("biosketch") or entry["section_key"] in {"honors"} else 0,
                    ),
                )
            if not env.get("ZOTERO_API_KEY"):
                for pub in extract_publications(section):
                    con.execute(
                        """
                        INSERT INTO publications (
                          document_id, category, ordinal, authors, title, venue,
                          year, doi, pmid, raw_citation, confidence
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            document_id,
                            pub["category"],
                            pub.get("ordinal"),
                            pub.get("authors"),
                            pub.get("title"),
                            pub.get("venue"),
                            pub.get("year"),
                            pub.get("doi"),
                            pub.get("pmid"),
                            pub["raw_citation"],
                            "medium" if pub.get("title") else "low",
                        ),
                    )
            for contribution in extract_biosketch_contributions(section):
                con.execute(
                    """
                    INSERT INTO biosketch_contributions (
                      document_id, ordinal, title, narrative, citations_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        contribution["ordinal"],
                        contribution["title"],
                        contribution["narrative"],
                        contribution["citations_json"],
                    ),
                )

    zotero_count = import_zotero_publications(con, imported_at, env)
    if zotero_count:
        con.execute(
            """
            INSERT INTO import_warnings (warning_type, message)
            VALUES ('zotero_primary', ?)
            """,
            (f"Imported {zotero_count} publication records from Zotero; document-parsed publications were skipped.",),
        )

    prefill_german_entry_fields(con)

    con.execute(
        """
        INSERT INTO import_warnings (warning_type, message)
        VALUES ('review_needed', 'First-pass import keeps raw source text for every section; low-confidence cv_entries should be reviewed before generating final CV variants.')
        """
    )
    con.commit()
    con.close()

    print(f"Wrote {DB.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
