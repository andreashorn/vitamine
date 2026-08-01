"""Shared publication citation rendering backed by Zotero-compatible CSL styles."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from vitamine.paths import active_db_path


CITATION_STYLES = [
    {
        "id": "vitamine-long",
        "label": "VitaMine Default",
        "description": "The consistent VitaMine citation format used across CV exports.",
        "csl": None,
        "group": "VitaMine",
    },
    {
        "id": "apa",
        "label": "APA 7th edition",
        "description": "Author–date style from the American Psychological Association.",
        "csl": "apa",
        "group": "General & author–date",
    },
    {
        "id": "harvard",
        "label": "Harvard – Cite Them Right 12th edition",
        "description": "Widely used author–date style following Cite Them Right.",
        "csl": "harvard-cite-them-right",
        "group": "General & author–date",
    },
    {
        "id": "chicago-author-date",
        "label": "Chicago 16th edition – author–date",
        "description": "Chicago Manual of Style author–date bibliography format.",
        "csl": "chicago-author-date-16th-edition",
        "group": "General & author–date",
    },
    {
        "id": "mla",
        "label": "MLA 9th edition – without URLs",
        "description": "Modern Language Association bibliography style, omitting web URLs.",
        "csl": "modern-language-association-no-url",
        "group": "General & author–date",
    },
    {
        "id": "dgps",
        "label": "Deutsche Gesellschaft für Psychologie",
        "description": "German-language author–date style used by the DGPs.",
        "csl": "deutsche-gesellschaft-fur-psychologie",
        "group": "General & author–date",
    },
    {
        "id": "vancouver",
        "label": "Vancouver",
        "description": "Compact numeric biomedical bibliography style.",
        "csl": "elsevier-vancouver",
        "group": "Numeric & biomedical",
    },
    {
        "id": "nlm",
        "label": "Vancouver – NLM",
        "description": "Citation-sequence format following the US National Library of Medicine.",
        "csl": "vancouver-nlm",
        "group": "Numeric & biomedical",
    },
    {
        "id": "ama",
        "label": "AMA 11th edition",
        "description": "Numeric medical style from the AMA Manual of Style.",
        "csl": "american-medical-association",
        "group": "Numeric & biomedical",
    },
    {
        "id": "jama",
        "label": "JAMA",
        "description": "Bibliography style used by the Journal of the American Medical Association.",
        "csl": "jama",
        "group": "Numeric & biomedical",
    },
    {
        "id": "ieee",
        "label": "IEEE",
        "description": "Compact numeric engineering and computer-science style.",
        "csl": "ieee",
        "group": "Numeric & biomedical",
    },
    {
        "id": "nature",
        "label": "Nature",
        "description": "Compact scientific journal bibliography style.",
        "csl": "nature",
        "group": "Scientific journals",
    },
    {
        "id": "science",
        "label": "Science",
        "description": "Compact numeric bibliography style used by Science.",
        "csl": "science",
        "group": "Scientific journals",
    },
    {
        "id": "cell",
        "label": "Cell",
        "description": "Author–date bibliography style used by Cell.",
        "csl": "cell",
        "group": "Scientific journals",
    },
    {
        "id": "lancet",
        "label": "The Lancet",
        "description": "Numeric biomedical bibliography style used by The Lancet.",
        "csl": "the-lancet",
        "group": "Scientific journals",
    },
    {
        "id": "nejm",
        "label": "New England Journal of Medicine",
        "description": "Numeric biomedical style used by the New England Journal of Medicine.",
        "csl": "the-new-england-journal-of-medicine",
        "group": "Scientific journals",
    },
    {
        "id": "bmj",
        "label": "BMJ",
        "description": "Numeric biomedical bibliography style used by The BMJ.",
        "csl": "bmj",
        "group": "Scientific journals",
    },
    {
        "id": "plos",
        "label": "PLOS",
        "description": "Numeric bibliography style used by PLOS journals.",
        "csl": "plos",
        "group": "Scientific journals",
    },
    {
        "id": "elife",
        "label": "eLife",
        "description": "Author–date bibliography style used by eLife.",
        "csl": "elife",
        "group": "Scientific journals",
    },
    {
        "id": "frontiers",
        "label": "Frontiers journals",
        "description": "Numbered bibliography style shared across Frontiers journals.",
        "csl": "frontiers",
        "group": "Scientific journals",
    },
    {
        "id": "acs",
        "label": "American Chemical Society",
        "description": "ACS-style chemistry bibliography using the compatible Elsevier CSL variant.",
        "csl": "elsevier-american-chemical-society",
        "group": "Chemistry & publishers",
    },
    {
        "id": "rsc",
        "label": "Royal Society of Chemistry",
        "description": "RSC bibliography format with article titles.",
        "csl": "royal-society-of-chemistry-with-titles",
        "group": "Chemistry & publishers",
    },
    {
        "id": "springer-author-date",
        "label": "Springer – basic author–date",
        "description": "General Springer author–date bibliography format.",
        "csl": "springer-basic-author-date",
        "group": "Chemistry & publishers",
    },
    {
        "id": "elsevier-harvard",
        "label": "Elsevier – Harvard",
        "description": "General Elsevier author–date bibliography format.",
        "csl": "elsevier-harvard",
        "group": "Chemistry & publishers",
    },
]

STYLE_BY_ID = {item["id"]: item for item in CITATION_STYLES}
DEFAULT_CITATION_STYLE = "vitamine-long"


def validate_citation_style(style_id: str | None) -> str:
    value = str(style_id or DEFAULT_CITATION_STYLE).strip()
    return value if value in STYLE_BY_ID else DEFAULT_CITATION_STYLE


def configured_citation_style() -> str:
    try:
        with sqlite3.connect(active_db_path()) as con:
            row = con.execute(
                "SELECT value FROM app_settings WHERE key='export_citation_style'"
            ).fetchone()
    except sqlite3.Error:
        row = None
    return validate_citation_style(row[0] if row else None)


def _value(row: Mapping[str, Any], key: str) -> str:
    try:
        value = row[key]
    except (KeyError, IndexError):
        value = ""
    return str(value or "").strip()


def _author_names(value: str) -> list[dict[str, str]]:
    names = []
    for raw_name in (part.strip() for part in value.split(",")):
        if not raw_name:
            continue
        parts = raw_name.split()
        if len(parts) == 1:
            names.append({"literal": parts[0]})
        elif len(parts[-1].rstrip(".-")) <= 2 and parts[-1][0].isupper():
            names.append({"family": parts[0], "given": " ".join(parts[1:])})
        else:
            family_start = len(parts) - 1
            while family_start > 0 and parts[family_start - 1].casefold().strip(".") in {
                "da", "de", "del", "der", "di", "dos", "du", "la", "le", "van", "von",
            }:
                family_start -= 1
            names.append({"family": " ".join(parts[family_start:]), "given": " ".join(parts[:family_start])})
    return names


def publication_to_csl(row: Mapping[str, Any]) -> dict[str, Any]:
    year_match = re.search(r"\d{4}", _value(row, "year"))
    item: dict[str, Any] = {
        "id": _value(row, "id") or _value(row, "doi") or _value(row, "title"),
        "type": "article-journal",
        "title": _value(row, "title"),
        "container-title": _value(row, "venue"),
        "author": _author_names(_value(row, "authors")),
    }
    if year_match:
        item["issued"] = {"date-parts": [[int(year_match.group(0))]]}
    mappings = {
        "doi": "DOI",
        "volume": "volume",
        "issue": "issue",
        "pages": "page",
    }
    for source, target in mappings.items():
        value = _value(row, source)
        if value:
            item[target] = value
    url = _value(row, "url")
    if url and not item.get("DOI"):
        item["URL"] = url
    return item


def _plain_csl_entry(item: dict[str, Any], csl_style: str) -> str:
    from citeproc import Citation, CitationItem, CitationStylesBibliography, CitationStylesStyle, formatter
    from citeproc.source.json import CiteProcJSON

    source = CiteProcJSON([item])
    style = CitationStylesStyle(csl_style, validate=False)
    bibliography = CitationStylesBibliography(style, source, formatter.plain)
    bibliography.register(Citation([CitationItem(str(item["id"]))]))
    entries = bibliography.bibliography()
    if not entries:
        return ""
    text = re.sub(r"\s+", " ", str(entries[0])).strip()
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"([.;:])(?=[A-Z])", r"\1 ", text)
    text = re.sub(r"([.;:])(?=https?://)", r"\1 ", text)
    text = re.sub(r"(?<=\.)&", " &", text)
    text = re.sub(
        r"(https://doi\.org/[^\s]+)\s+doi:[^\s.]+\.?$",
        r"\1.",
        text,
        flags=re.IGNORECASE,
    )
    doi_url = re.search(r"https://doi\.org/([^\s)]+)", text, flags=re.IGNORECASE)
    if doi_url:
        doi_value = doi_url.group(1).rstrip(".,;")
        text = re.sub(
            rf"\s+doi:{re.escape(doi_value)}\.?",
            "",
            text,
            flags=re.IGNORECASE,
        )
    # The CV exporter owns list numbering; remove CSL's single-entry numeric label.
    return re.sub(r"^\s*(?:\[\s*1\s*\]|\(\s*1\s*\)|1[\.\)]?)\s*", "", text).strip()


def format_publication(row: Mapping[str, Any], style_id: str | None) -> str | None:
    style = STYLE_BY_ID[validate_citation_style(style_id)]
    csl_style = style.get("csl")
    if not csl_style:
        return None
    return _plain_csl_entry(publication_to_csl(row), str(csl_style))


def format_publications(rows: Iterable[Mapping[str, Any]], style_id: str | None) -> dict[int, str]:
    selected_style = validate_citation_style(style_id)
    if selected_style == DEFAULT_CITATION_STYLE:
        return {}
    output: dict[int, str] = {}
    for row in rows:
        try:
            publication_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        rendered = format_publication(row, selected_style)
        if rendered:
            output[publication_id] = rendered
    return output
