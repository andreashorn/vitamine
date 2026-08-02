"""Import Word CVs as private, reusable VitaMine export templates.

The uploaded document remains a Word-native package.  We replace recognized
person and section content with tokens while retaining its page geometry,
styles, tables, headers, footers, and images.  Export then pours the current
content from one of VitaMine's deterministic builders back into those slots.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import sqlite3
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.ns import qn
from docx.oxml.table import CT_Row, CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Row
from docx.text.paragraph import Paragraph
from lxml import etree


MAX_TEMPLATE_BYTES = 20 * 1024 * 1024
MAX_TEMPLATE_UNCOMPRESSED_BYTES = 120 * 1024 * 1024
MAX_TEMPLATE_PARTS = 2_000

CONTENT_PROFILES = {"long", "short", "one_page", "biosketch"}
PERSON_FIELDS = (
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
)

SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "education": (
        "education", "education and training", "education/training", "academic education",
        "ausbildung", "studium und ausbildung", "akademische ausbildung", "qualifications",
    ),
    "postdoctoral_training": (
        "postdoctoral training", "postdoctoral education", "postdoctoral fellowships",
        "postdoktorale ausbildung", "postdoc training",
    ),
    "academic_appointments": (
        "academic appointments", "faculty academic appointments", "scientific appointments",
        "positions and scientific appointments", "academic positions", "akademische berufungen",
        "akademische positionen",
    ),
    "hospital_appointments": (
        "hospital appointments", "appointments at hospitals/affiliated institutions",
        "clinical appointments", "klinische positionen",
    ),
    "professional_positions": (
        "professional positions", "other professional positions", "employment", "experience",
        "work experience", "career history", "beruflicher werdegang", "berufserfahrung",
    ),
    "committee_service": (
        "committee service", "committee membership", "committees", "gremienarbeit",
    ),
    "professional_societies": (
        "professional societies", "professional memberships", "memberships", "mitgliedschaften",
        "fachgesellschaften",
    ),
    "grant_review": (
        "grant review activities", "grant review", "review panels", "gutachtertätigkeiten",
    ),
    "editorial_activities": (
        "editorial activities", "editorial service", "journal service", "editoriale tätigkeiten",
    ),
    "honors": (
        "honors", "honours", "honors and prizes", "honors and awards", "awards",
        "awards and honors", "distinctions", "auszeichnungen", "auszeichnungen und preise",
    ),
    "funding": (
        "research funding", "funding", "grants", "grant support", "funded projects",
        "report of funded and unfunded projects", "forschungsförderung", "drittmittel",
    ),
    "teaching": (
        "teaching", "teaching activities", "teaching of students in courses", "lehre",
    ),
    "mentoring": (
        "mentoring", "supervision", "research supervisory and training responsibilities",
        "trainees", "nachwuchsförderung", "betreuung und ausbildung",
    ),
    "invited_presentations": (
        "invited presentations", "invited lectures", "invited talks", "presentations",
        "eingeladene vorträge", "vorträge",
    ),
    "clinical_activities": (
        "clinical activities", "clinical activities and innovations", "klinische tätigkeiten",
    ),
    "education_innovations": (
        "teaching and education innovations", "education innovations", "lehrinnovationen",
    ),
    "community_service": (
        "community service", "service to the community", "gesellschaftliches engagement",
    ),
    "publications": (
        "publications", "selected publications", "peer-reviewed publications",
        "peer-reviewed scholarship in print or other media", "report of scholarship",
        "bibliography", "scientific output", "works", "publikationen", "ausgewählte publikationen",
        "veröffentlichungen",
    ),
    "personal_statement": (
        "personal statement", "summary statement", "research profile", "profile", "profil",
    ),
    "contributions": (
        "contributions to science", "contributions", "contributions to research",
        "wissenschaftliche beiträge",
    ),
    "narrative_report": (
        "narrative report", "research narrative", "narrativer bericht",
    ),
}

STATIC_LABELS = {
    "dates", "date", "years", "year", "degree", "field of study", "institution",
    "institution and location", "position", "title", "organization", "details", "role",
    "completion date", "current", "past", "local", "national", "international",
    "ad hoc reviewer", "other editorial roles", "activities", "research investigations",
    "other scholarship", "patents", "books / book chapters", "books / chapters", "preprints",
    "manuscripts in preparation", "poster presentations", "achievements",
    "other formally supervised trainees", "selected projects relevant to this application",
    "selected citations that highlight experience and qualifications",
}

LLM_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["content_profile", "confidence", "heading_mappings"],
    "properties": {
        "content_profile": {"type": "string", "enum": ["long", "short", "one_page", "biosketch"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "heading_mappings": {
            "type": "array",
            "maxItems": 80,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source_text", "section_key"],
                "properties": {
                    "source_text": {"type": "string"},
                    "section_key": {"type": "string", "enum": sorted(SECTION_ALIASES)},
                },
            },
        },
    },
}


@dataclass
class Unit:
    index: int
    kind: str
    element: Any
    parent: Any
    paragraphs: list[Paragraph]
    cell_paragraphs: list[list[Paragraph]]

    @property
    def values(self) -> list[str]:
        if self.kind == "paragraph":
            return [clean_text(self.paragraphs[0].text)]
        return [clean_text("\n".join(paragraph.text for paragraph in paragraphs)) for paragraphs in self.cell_paragraphs]

    @property
    def text(self) -> str:
        return clean_text(" | ".join(value for value in self.values if value))


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalized_heading(value: str) -> str:
    text = clean_text(value).casefold()
    text = re.sub(r"^[a-z0-9ivx]+[.)]\s+", "", text)
    text = text.rstrip(":.- ")
    return re.sub(r"[^\wäöüß/&+ -]+", "", text).strip()


def unique_cells(row: _Row) -> list[Any]:
    cells: list[Any] = []
    seen: set[int] = set()
    for cell in row.cells:
        marker = id(cell._tc)
        if marker not in seen:
            seen.add(marker)
            cells.append(cell)
    return cells


def document_units(document: DocumentObject) -> list[Unit]:
    units: list[Unit] = []
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            paragraph = Paragraph(child, document)
            units.append(Unit(len(units), "paragraph", child, document, [paragraph], []))
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            for row in table.rows:
                cells = unique_cells(row)
                cell_paragraphs = [list(cell.paragraphs) for cell in cells]
                paragraphs = [paragraph for group in cell_paragraphs for paragraph in group]
                units.append(Unit(len(units), "row", row._tr, table, paragraphs, cell_paragraphs))
    return units


def paragraphs_in_container(container: Any) -> Iterable[Paragraph]:
    for paragraph in getattr(container, "paragraphs", []):
        yield paragraph
    for table in getattr(container, "tables", []):
        for row in table.rows:
            for cell in unique_cells(row):
                yield from paragraphs_in_container(cell)


def all_document_paragraphs(document: DocumentObject) -> Iterable[Paragraph]:
    yield from paragraphs_in_container(document)
    seen_parts: set[str] = set()
    for section in document.sections:
        for container in (section.header, section.first_page_header, section.even_page_header,
                          section.footer, section.first_page_footer, section.even_page_footer):
            part_name = str(container.part.partname)
            if part_name in seen_parts:
                continue
            seen_parts.add(part_name)
            yield from paragraphs_in_container(container)


def paragraph_is_bold(paragraph: Paragraph) -> bool:
    runs = [run for run in paragraph.runs if clean_text(run.text)]
    return bool(runs) and all(bool(run.bold) for run in runs)


def probable_heading(unit: Unit) -> bool:
    text = unit.text
    if not text or len(text) > 180:
        return False
    normalized = normalized_heading(text)
    if normalized in STATIC_LABELS:
        return True
    if any((paragraph.style and str(paragraph.style.name).casefold().startswith("heading")) for paragraph in unit.paragraphs):
        return True
    if unit.paragraphs and all(paragraph_is_bold(paragraph) for paragraph in unit.paragraphs if clean_text(paragraph.text)):
        return True
    letters = [character for character in text if character.isalpha()]
    return bool(letters) and len(text) <= 100 and sum(character.isupper() for character in letters) / len(letters) > 0.82


def alias_section_key(value: str) -> str | None:
    normalized = normalized_heading(value)
    if not normalized:
        return None
    for section_key, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            if normalized == alias or normalized.startswith(f"{alias} ") or normalized.endswith(f" {alias}"):
                return section_key
    return None


def mapped_section_key(value: str, mappings: dict[str, str]) -> str | None:
    normalized = normalized_heading(value)
    return mappings.get(normalized) or alias_section_key(value)


def page_count_from_docx(data: bytes) -> int | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            raw = archive.read("docProps/app.xml")
        root = etree.fromstring(raw)
        pages = root.xpath("//*[local-name()='Pages']/text()")
        return int(pages[0]) if pages and int(pages[0]) > 0 else None
    except (KeyError, ValueError, zipfile.BadZipFile, etree.XMLSyntaxError):
        return None


def validate_docx_bytes(data: bytes) -> None:
    if not data:
        raise ValueError("The Word template is empty.")
    if len(data) > MAX_TEMPLATE_BYTES:
        raise ValueError("The Word template is larger than 20 MB.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_TEMPLATE_PARTS:
                raise ValueError("The Word template contains too many package parts.")
            if sum(info.file_size for info in infos) > MAX_TEMPLATE_UNCOMPRESSED_BYTES:
                raise ValueError("The expanded Word template is too large.")
            if any(info.flag_bits & 0x1 for info in infos):
                raise ValueError("Password-protected Word templates are not supported.")
            names = {info.filename for info in infos}
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                raise ValueError("This file is not a readable DOCX document.")
            if any("vbaproject" in name.casefold() for name in names):
                raise ValueError("Macro-enabled Word documents are not supported.")
    except zipfile.BadZipFile as exc:
        raise ValueError("This file is not a readable DOCX document.") from exc


def deterministic_profile(text: str, *, page_count: int | None, heading_count: int) -> tuple[str, str]:
    normalized = normalized_heading(text)
    words = len(re.findall(r"\b\w+\b", text))
    biosketch_markers = sum(
        marker in normalized
        for marker in ("biographical sketch", "biosketch", "personal statement", "contributions to science", "era commons")
    )
    if biosketch_markers >= 2 or "contributions to science" in normalized:
        return "biosketch", "biosketch structure"
    if (page_count == 1 and words <= 1_600 and heading_count <= 10) or (words <= 750 and heading_count <= 8):
        return "one_page", "one-page or very compact structure"
    if (page_count is not None and page_count >= 6) or words >= 2_400 or heading_count >= 12:
        return "long", "comprehensive multi-section structure"
    return "short", "compact academic CV structure"


def llm_prompt(units: list[Unit], page_count: int | None, deterministic: str) -> str:
    candidates = []
    for unit in units:
        text = unit.text
        if text and len(text) <= 220 and (probable_heading(unit) or alias_section_key(text)):
            candidates.append(text)
        if len(candidates) >= 120:
            break
    excerpt = "\n".join(unit.text for unit in units if unit.text)[:14_000]
    allowed = ", ".join(sorted(SECTION_ALIASES))
    return f"""Analyze the structure of this academic CV Word document for a reusable export template.

Classify its content shape as exactly one of long, short, one_page, or biosketch.
Map only headings that appear verbatim in the candidate list to the closest allowed section key.
Do not treat a person's name, institution, degree, date, or CV title as a section heading.
Allowed section keys: {allowed}

Document metadata:
- recorded pages: {page_count if page_count is not None else 'unknown'}
- deterministic first pass: {deterministic}
- body units: {len(units)}

Candidate headings:
{json.dumps(candidates, ensure_ascii=False)}

Document excerpt:
{excerpt}
"""


def section_slots(units: list[Unit], mappings: dict[str, str]) -> tuple[list[dict[str, Any]], list[str]]:
    slots: list[dict[str, Any]] = []
    mapped_sections: list[str] = []
    current: str | None = None
    for unit in units:
        section_key = mapped_section_key(unit.text, mappings)
        if section_key:
            current = section_key
            if section_key not in mapped_sections:
                mapped_sections.append(section_key)
            continue
        if not current or not unit.text or not content_unit(unit):
            continue
        slots.append(
            {
                "unit_index": unit.index,
                "section_key": current,
                "kind": unit.kind,
                "columns": max(1, len(unit.values)),
            }
        )
    return slots, mapped_sections


def content_unit(unit: Unit) -> bool:
    text = unit.text
    normalized = normalized_heading(text)
    if not text or normalized in STATIC_LABELS:
        return False
    normalized_values = [normalized_heading(value) for value in unit.values if clean_text(value)]
    if normalized_values and all(value in STATIC_LABELS for value in normalized_values):
        return False
    if mapped_section_key(text, {}):
        return False
    return True


def set_paragraph_text(paragraph: Paragraph, value: str) -> None:
    run_properties = None
    for run in paragraph.runs:
        if run._r.rPr is not None:
            run_properties = copy.deepcopy(run._r.rPr)
            break
    for child in list(paragraph._p):
        if child.tag != qn("w:pPr"):
            paragraph._p.remove(child)
    run = paragraph.add_run(value)
    if run_properties is not None:
        run._r.insert(0, run_properties)


def replace_paragraph_text(paragraph: Paragraph, old: str, new: str) -> bool:
    text = paragraph.text
    if not old or old.casefold() not in text.casefold():
        return False
    match = re.search(re.escape(old), text, flags=re.IGNORECASE)
    if not match:
        return False
    set_paragraph_text(paragraph, text[:match.start()] + new + text[match.end():])
    return True


def set_unit_values(unit: Unit, values: list[str]) -> None:
    values = [clean_text(value) for value in values]
    if unit.kind == "paragraph":
        set_paragraph_text(unit.paragraphs[0], " · ".join(value for value in values if value))
        return
    count = len(unit.cell_paragraphs)
    if count <= 1:
        adapted = [" · ".join(value for value in values if value)]
    elif len(values) <= count:
        adapted = values + [""] * (count - len(values))
    else:
        adapted = values[: count - 1] + [" · ".join(value for value in values[count - 1:] if value)]
    for paragraphs, value in zip(unit.cell_paragraphs, adapted):
        if paragraphs:
            set_paragraph_text(paragraphs[0], value)
            for paragraph in paragraphs[1:]:
                set_paragraph_text(paragraph, "")


def person_values(con: sqlite3.Connection) -> dict[str, str]:
    row = con.execute("SELECT * FROM person WHERE id=1").fetchone()
    if row is None:
        return {}
    keys = set(row.keys())
    return {field: clean_text(row[field]) for field in PERSON_FIELDS if field in keys and clean_text(row[field])}


def skeletonize_person(document: DocumentObject, units: list[Unit], values: dict[str, str], mappings: dict[str, str]) -> list[str]:
    first_heading = next((unit.index for unit in units if mapped_section_key(unit.text, mappings)), len(units))
    body_paragraph_ids = {
        id(paragraph._p)
        for unit in units[:first_heading]
        for paragraph in unit.paragraphs
    }
    fields: list[str] = []
    candidates = sorted(values.items(), key=lambda item: len(item[1]), reverse=True)
    for paragraph in all_document_paragraphs(document):
        is_header_or_footer = str(paragraph.part.partname).startswith(("/word/header", "/word/footer"))
        if not is_header_or_footer and id(paragraph._p) not in body_paragraph_ids:
            continue
        for field, value in candidates:
            if len(value) < 4 or field in fields and f"{{{{VITAMINE_PERSON_{field.upper()}}}}}" in paragraph.text:
                continue
            token = f"{{{{VITAMINE_PERSON_{field.upper()}}}}}"
            if replace_paragraph_text(paragraph, value, token) and field not in fields:
                fields.append(field)
    return fields


def skeletonize_slots(units: list[Unit], slots: list[dict[str, Any]]) -> None:
    by_index = {unit.index: unit for unit in units}
    counters: dict[str, int] = defaultdict(int)
    for slot in slots:
        unit = by_index.get(int(slot["unit_index"]))
        if unit is None:
            continue
        section_key = str(slot["section_key"])
        counters[section_key] += 1
        token = f"{{{{VITAMINE_{section_key.upper()}_{counters[section_key]}}}}}"
        set_unit_values(unit, [token] * max(1, int(slot.get("columns") or 1)))


def scrub_core_properties(document: DocumentObject, name: str) -> None:
    props = document.core_properties
    props.author = "VitaMine"
    props.last_modified_by = "VitaMine"
    props.title = name
    props.subject = "Private reusable Word CV layout"
    props.comments = ""
    props.keywords = "VitaMine Word CV template"


def clean_word_xml(data: bytes) -> bytes:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data
    namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    for node in root.xpath("//w:del|//w:commentRangeStart|//w:commentRangeEnd|//w:commentReference", namespaces=namespaces):
        parent = node.getparent()
        if parent is not None:
            parent.remove(node)
    for node in root.xpath("//w:ins", namespaces=namespaces):
        parent = node.getparent()
        if parent is None:
            continue
        position = parent.index(node)
        for child in list(node):
            parent.insert(position, child)
            position += 1
        parent.remove(node)
    for element in root.iter():
        for attribute in list(element.attrib):
            if etree.QName(attribute).localname.startswith("rsid"):
                del element.attrib[attribute]
    return etree.tostring(root, encoding="utf-8", xml_declaration=True, standalone=True)


def clean_relationships(data: bytes) -> bytes:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data
    for relationship in list(root):
        target = relationship.attrib.get("Target", "").casefold()
        rel_type = relationship.attrib.get("Type", "").casefold()
        if (
            relationship.attrib.get("TargetMode") == "External"
            or "customxml" in target
            or "customxml" in rel_type
            or "custom-properties" in rel_type
            or "comments" in target
            or "comments" in rel_type
        ):
            root.remove(relationship)
    return etree.tostring(root, encoding="utf-8", xml_declaration=True)


def clean_content_types(data: bytes) -> bytes:
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError:
        return data
    for node in list(root):
        part = node.attrib.get("PartName", "").casefold()
        if "customxml" in part or "custom.xml" in part or "comments" in part:
            root.remove(node)
    return etree.tostring(root, encoding="utf-8", xml_declaration=True)


def clean_docx_package(data: bytes) -> bytes:
    source_buffer = io.BytesIO(data)
    target_buffer = io.BytesIO()
    with zipfile.ZipFile(source_buffer, "r") as source, zipfile.ZipFile(target_buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            name = info.filename
            lowered = name.casefold()
            if lowered.startswith("customxml/") or lowered == "docprops/custom.xml" or "comments" in lowered:
                continue
            content = source.read(name)
            if name.endswith(".rels"):
                content = clean_relationships(content)
            elif name == "[Content_Types].xml":
                content = clean_content_types(content)
            elif name.startswith("word/") and name.endswith(".xml"):
                content = clean_word_xml(content)
            clean_info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            clean_info.compress_type = zipfile.ZIP_DEFLATED
            clean_info.create_system = info.create_system
            clean_info.external_attr = info.external_attr
            target.writestr(clean_info, content)
    return target_buffer.getvalue()


def analyze_and_skeletonize(
    data: bytes,
    name: str,
    con: sqlite3.Connection,
    *,
    llm_json: Callable[[str, dict[str, Any], dict[str, str]], tuple[dict[str, Any] | None, str | None]] | None = None,
    settings: dict[str, str] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    validate_docx_bytes(data)
    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        raise ValueError("Word could not read this DOCX document.") from exc
    units = document_units(document)
    text = "\n".join(unit.text for unit in units if unit.text)
    page_count = page_count_from_docx(data)
    headings = [unit for unit in units if probable_heading(unit)]
    profile, reason = deterministic_profile(text, page_count=page_count, heading_count=len(headings))
    mappings = {
        normalized_heading(unit.text): section_key
        for unit in units
        if (section_key := alias_section_key(unit.text))
    }
    method = "deterministic"
    llm_warning = ""
    configured = settings or {}
    if llm_json is not None and str(configured.get("provider") or "none") != "none" and text:
        try:
            result, warning = llm_json(llm_prompt(units, page_count, profile), LLM_ANALYSIS_SCHEMA, configured)
        except Exception as exc:
            result, warning = None, str(exc)
        llm_warning = clean_text(warning or "")[:400]
        if isinstance(result, dict):
            proposed_profile = str(result.get("content_profile") or "")
            confidence = str(result.get("confidence") or "")
            if proposed_profile in CONTENT_PROFILES and confidence in {"high", "medium"}:
                profile = proposed_profile
                reason = f"LLM classification ({confidence} confidence)"
            exact_texts = {unit.text: normalized_heading(unit.text) for unit in units if unit.text}
            for item in result.get("heading_mappings") or []:
                if not isinstance(item, dict):
                    continue
                source_text = clean_text(item.get("source_text") or "")
                section_key = str(item.get("section_key") or "")
                if source_text in exact_texts and section_key in SECTION_ALIASES:
                    mappings[exact_texts[source_text]] = section_key
            method = "llm+deterministic"
    slots, mapped_sections = section_slots(units, mappings)
    if not slots:
        raise ValueError(
            "VitaMine could not identify reusable content sections in this Word CV. "
            "Use a DOCX with visible academic section headings and at least one entry."
        )
    person_fields = skeletonize_person(document, units, person_values(con), mappings)
    skeletonize_slots(units, slots)
    scrub_core_properties(document, name)
    buffer = io.BytesIO()
    document.save(buffer)
    skeleton = clean_docx_package(buffer.getvalue())
    validate_docx_bytes(skeleton)
    analysis = {
        "schema_version": 1,
        "content_profile": profile,
        "classification_reason": reason,
        "analysis_method": method,
        "analysis_model": str(configured.get("api_model") or configured.get("ollama_model") or "") if method.startswith("llm") else "",
        "page_count": page_count,
        "body_units": len(units),
        "table_rows": sum(unit.kind == "row" for unit in units),
        "mapped_sections": mapped_sections,
        "heading_mappings": mappings,
        "slots": slots,
        "person_fields": person_fields,
        "llm_warning": llm_warning,
    }
    return skeleton, analysis


def canonical_content(document: DocumentObject) -> dict[str, list[list[str]]]:
    content: dict[str, list[list[str]]] = defaultdict(list)
    current: str | None = None
    for unit in document_units(document):
        section_key = alias_section_key(unit.text)
        if section_key:
            current = section_key
            continue
        if current and unit.text and content_unit(unit):
            content[current].append(unit.values)
    return content


def clone_unit_after(unit: Unit) -> Unit:
    element = copy.deepcopy(unit.element)
    unit.element.addnext(element)
    if unit.kind == "paragraph":
        paragraph = Paragraph(element, unit.parent)
        return Unit(-1, "paragraph", element, unit.parent, [paragraph], [])
    row = _Row(element, unit.parent)
    cells = unique_cells(row)
    groups = [list(cell.paragraphs) for cell in cells]
    return Unit(-1, "row", element, unit.parent, [paragraph for group in groups for paragraph in group], groups)


def remove_unit(unit: Unit) -> None:
    parent = unit.element.getparent()
    if parent is not None:
        parent.remove(unit.element)


def render_template(
    skeleton: bytes,
    blueprint: dict[str, Any],
    canonical_docx: Path,
    output: Path,
    con: sqlite3.Connection,
) -> dict[str, Any]:
    validate_docx_bytes(skeleton)
    try:
        document = Document(io.BytesIO(skeleton))
        canonical = Document(canonical_docx)
    except Exception as exc:
        raise ValueError("The Word template could not be rendered.") from exc
    units = document_units(document)
    by_index = {unit.index: unit for unit in units}
    items = canonical_content(canonical)
    slots_by_section: dict[str, list[Unit]] = defaultdict(list)
    for slot in blueprint.get("slots") or []:
        try:
            unit = by_index[int(slot["unit_index"])]
        except (KeyError, TypeError, ValueError):
            continue
        section_key = str(slot.get("section_key") or "")
        if section_key:
            slots_by_section[section_key].append(unit)
    rendered_sections: list[str] = []
    rendered_items = 0
    for section_key, target_units in slots_by_section.items():
        source_items = items.get(section_key, [])
        if not target_units:
            continue
        while len(target_units) < len(source_items):
            target_units.append(clone_unit_after(target_units[-1]))
        for index, unit in enumerate(target_units):
            if index < len(source_items):
                set_unit_values(unit, source_items[index])
                rendered_items += 1
            else:
                remove_unit(unit)
        if source_items:
            rendered_sections.append(section_key)
    values = person_values(con)
    for paragraph in all_document_paragraphs(document):
        for field in blueprint.get("person_fields") or []:
            token = f"{{{{VITAMINE_PERSON_{str(field).upper()}}}}}"
            if token in paragraph.text:
                replace_paragraph_text(paragraph, token, values.get(str(field), ""))
    name = values.get("display_name") or values.get("full_name") or "VitaMine CV"
    document.core_properties.author = name
    document.core_properties.last_modified_by = "VitaMine"
    document.core_properties.title = str(blueprint.get("template_name") or output.stem)
    output.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    document.save(buffer)
    output.write_bytes(clean_docx_package(buffer.getvalue()))
    return {
        "rendered_sections": rendered_sections,
        "rendered_items": rendered_items,
        "unmapped_sections": sorted(set(items) - set(rendered_sections)),
    }


def template_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
