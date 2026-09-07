#!/usr/bin/env python3
"""Create privacy-safe DOCX template sources from retained, handmade Word files.

The source files are never modified. The generated files preserve their page
geometry, styles, tables, headers, footers, and representative repeatable
blocks while replacing or removing all CV-specific content and metadata.
"""

from __future__ import annotations

import argparse
import copy
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from docx import Document
from docx.oxml.ns import qn


REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
EXTENDED_PROPS_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


LONG_STRUCTURAL_PARAGRAPHS = {
    "Education:",
    "Postdoctoral Training:",
    "Faculty Academic Appointments:",
    "Appointments at Hospitals/Affiliated Institutions:",
    "Other Professional Positions:",
    "Committee Service:",
    "Local",
    "International",
    "Professional Societies:",
    "Grant Review Activities:",
    "Editorial Activities:",
    "Ad hoc Reviewer",
    "Other Editorial Roles",
    "Honors and Prizes:",
    "Report of Funded and Unfunded Projects",
    "Past",
    "Current",
    "Report of Local Teaching and Training",
    "Teaching of Students in Courses:",
    "Research Supervisory and Training Responsibilities:",
    "Other Formally Supervised Trainees",
    "Report of Regional, National and International Invited Teaching and Presentations",
    "No presentations below were sponsored by 3rd parties/outside entities.",
    "Report of Clinical Activities and Innovations",
    "Clinical Innovations:",
    "Report of Teaching and Education Innovations",
    "Report of Education of Patients and Service to the Community",
    "Activities",
    "Report of Scholarship",
    "Peer-Reviewed Scholarship in print or other media:",
    "Research Investigations",
    "Other peer-reviewed scholarship",
    "Books / Chapters",
    "Case reports",
    "Letters to the Editor",
    "Theses",
    "Patents",
    "Narrative Report",
}


def paragraph_text(paragraph) -> str:
    return " ".join(paragraph.text.split())


def set_paragraph_text(paragraph, value: str) -> None:
    run_properties = None
    if paragraph.runs and paragraph.runs[0]._r.rPr is not None:
        run_properties = copy.deepcopy(paragraph.runs[0]._r.rPr)
    for child in list(paragraph._p):
        if child.tag != qn("w:pPr"):
            paragraph._p.remove(child)
    run = paragraph.add_run(value)
    if run_properties is not None:
        run._r.insert(0, run_properties)


def delete_paragraph(paragraph) -> None:
    parent = paragraph._p.getparent()
    if parent is not None:
        parent.remove(paragraph._p)


def set_cell_text(cell, value: str) -> None:
    set_paragraph_text(cell.paragraphs[0], value)
    for paragraph in list(cell.paragraphs[1:]):
        delete_paragraph(paragraph)


def trim_table_to_rows(table, keep: int) -> None:
    for row in list(table.rows[keep:]):
        table._tbl.remove(row._tr)


def scrub_core_properties(doc: Document, title: str, subject: str) -> None:
    props = doc.core_properties
    props.author = "VitaMine"
    props.last_modified_by = "VitaMine"
    props.title = title
    props.subject = subject
    props.comments = ""
    props.keywords = "VitaMine academic CV template"


def sanitize_one_page(source: Path, output: Path) -> None:
    doc = Document(source)
    paragraphs = doc.paragraphs
    replacements = {
        0: "{{PERSON_NAME_AND_DEGREES}}",
        1: "{{POSITION_AND_AFFILIATION}}",
        2: "Education and Training",
        3: "Positions and Scientific Appointments",
        4: "Awards, Research Funding and Presentations",
        11: "Selected Publications",
    }
    for index, value in replacements.items():
        set_paragraph_text(paragraphs[index], value)
    for paragraph in paragraphs[5:11]:
        set_paragraph_text(paragraph, "{{AWARD_OR_FUNDING_ENTRY}}")
    for paragraph in paragraphs[12:22]:
        set_paragraph_text(paragraph, "{{PUBLICATION_CITATION}}")

    education_tokens = [
        ("Years", "Qualification", "Institution"),
        ("{{DATES}}", "{{QUALIFICATION}}", "{{INSTITUTION}}"),
    ]
    for row_index, row in enumerate(doc.tables[0].rows):
        tokens = education_tokens[min(row_index, 1)]
        for cell, value in zip(row.cells, tokens):
            set_cell_text(cell, value)

    position_tokens = [
        ("Years", "Position"),
        ("{{DATES}}", "{{POSITION}}"),
    ]
    for row_index, row in enumerate(doc.tables[1].rows):
        tokens = position_tokens[min(row_index, 1)]
        for cell, value in zip(row.cells, tokens):
            set_cell_text(cell, value)

    scrub_core_properties(doc, "Tabular One Page CV Template", "Privacy-safe editable Word template")
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    clean_docx_package(output)


def sanitize_long(source: Path, output: Path) -> None:
    doc = Document(source)
    paragraphs = list(doc.paragraphs)
    if paragraphs:
        set_paragraph_text(paragraphs[0], "The Faculty of Medicine of University Cologne")
    if len(paragraphs) > 1:
        set_paragraph_text(paragraphs[1], "Curriculum Vitae")
    previous_structural = ""
    kept_current_funding_slot = False
    for paragraph in paragraphs[2:]:
        text = paragraph_text(paragraph)
        if not text:
            continue
        if text in LONG_STRUCTURAL_PARAGRAPHS:
            previous_structural = text
            continue
        if previous_structural == "Current" and not kept_current_funding_slot:
            set_paragraph_text(paragraph, "{{CURRENT_FUNDING_ENTRY}}")
            kept_current_funding_slot = True
            previous_structural = ""
            continue
        if text.startswith(("165 Publications;", "Publications;")) or (
            "Publications;" in text and "ORCID:" in text
        ):
            set_paragraph_text(paragraph, "{{SCHOLARSHIP_METRICS}}")
            previous_structural = ""
            continue
        if previous_structural == "Narrative Report":
            set_paragraph_text(paragraph, "{{NARRATIVE_BODY}}")
            previous_structural = ""
            continue
        delete_paragraph(paragraph)

    metadata = doc.tables[0]
    metadata_values = {
        "Date Prepared:": "{{DATE_PREPARED}}",
        "Name:": "{{PERSON_NAME}}",
        "Office Address:": "{{OFFICE_ADDRESS}}",
        "Home Address:": "{{HOME_ADDRESS_OPTIONAL}}",
        "Work Phone:": "{{WORK_PHONE}}",
        "Work Email:": "{{WORK_EMAIL}}",
        "Place of Birth:": "{{PLACE_OF_BIRTH_OPTIONAL}}",
    }
    for row in metadata.rows:
        label = paragraph_text(row.cells[0].paragraphs[0]).strip()
        set_cell_text(row.cells[1], metadata_values.get(label, "{{VALUE}}"))

    for table in doc.tables[1:]:
        trim_table_to_rows(table, 1)
        column_count = len(table.columns)
        if column_count == 4:
            tokens = ("{{DATES}}", "{{ROLE_OR_DEGREE}}", "{{FIELD_OR_DETAILS}}", "{{INSTITUTION}}")
        elif column_count == 3:
            tokens = ("{{DATES}}", "{{ACTIVITY_OR_ROLE}}", "{{INSTITUTION_OR_DETAILS}}")
        elif column_count == 2:
            tokens = ("{{DATES_OR_TITLE}}", "{{DETAILS}}")
        else:
            tokens = ("{{ENTRY}}",)
        for cell, value in zip(table.rows[0].cells, tokens):
            set_cell_text(cell, value)

    scrub_core_properties(doc, "Formal Academic CV Template", "Privacy-safe long academic CV design source")
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    clean_docx_package(output)


def sanitize_biosketch(source: Path, output: Path) -> None:
    doc = Document(source)
    paragraphs = list(doc.paragraphs)
    replacements = {
        0: "BIOGRAPHICAL SKETCH — DRAFT TEMPLATE",
        2: "NAME: {{PERSON_NAME_AND_DEGREES}}",
        3: "eRA COMMONS USER NAME: {{ERA_COMMONS_USERNAME}}",
        4: "POSITION TITLE: {{POSITION_TITLE}}",
        5: "EDUCATION/TRAINING",
        7: "A. Personal Statement",
        8: "{{PERSONAL_STATEMENT}}",
        10: "Selected projects relevant to this application:",
        13: "Selected citations that highlight experience and qualifications:",
        14: "{{SELECTED_CITATION}}",
        18: "B. Positions, Scientific Appointments, and Honors",
        19: "Positions and Scientific Appointments",
        20: "{{POSITION_OR_APPOINTMENT}}",
        37: "Honors",
        38: "{{HONOR}}",
        56: "C. Contributions to Science",
        58: "1. {{CONTRIBUTION_TITLE_AND_NARRATIVE}}",
        60: "{{CONTRIBUTION_CITATION}}",
        61: "{{CONTRIBUTION_CITATION}}",
        62: "{{CONTRIBUTION_CITATION}}",
        63: "{{CONTRIBUTION_CITATION}}",
        86: "Complete list of published work: {{BIBLIOGRAPHY_URL}}",
    }
    for index, paragraph in enumerate(paragraphs):
        if index in replacements:
            set_paragraph_text(paragraph, replacements[index])
        elif paragraph_text(paragraph):
            delete_paragraph(paragraph)

    education = doc.tables[0]
    trim_table_to_rows(education, 2)
    headers = ("INSTITUTION AND LOCATION", "DEGREE", "Completion Date", "FIELD OF STUDY")
    values = ("{{INSTITUTION_AND_LOCATION}}", "{{DEGREE}}", "{{COMPLETION_DATE}}", "{{FIELD_OF_STUDY}}")
    for cell, value in zip(education.rows[0].cells, headers):
        set_cell_text(cell, value)
    for cell, value in zip(education.rows[1].cells, values):
        set_cell_text(cell, value)

    project = doc.tables[1]
    trim_table_to_rows(project, 1)
    set_cell_text(project.rows[0].cells[0], "{{PROJECT_DATES}}")
    set_cell_text(project.rows[0].cells[1], "{{PROJECT_ROLE_AND_RELEVANCE}}")

    scrub_core_properties(doc, "Scientific Biosketch Draft Template", "Privacy-safe legacy biosketch design source")
    output.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output)
    clean_docx_package(output)


def clean_docx_package(path: Path) -> None:
    """Remove hidden personal metadata, custom XML, and external relationships."""
    with tempfile.NamedTemporaryFile(prefix="vitamine-template-", suffix=".docx", delete=False) as handle:
        cleaned = Path(handle.name)
    try:
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(cleaned, "w", zipfile.ZIP_DEFLATED) as target:
            for info in source.infolist():
                name = info.filename
                if name.startswith("customXml/") or name == "docProps/custom.xml":
                    continue
                data = source.read(name)
                if name == "_rels/.rels":
                    data = clean_root_relationships(data)
                elif name.endswith(".rels"):
                    data = clean_external_relationships(data)
                elif name == "[Content_Types].xml":
                    data = clean_content_types(data)
                elif name == "docProps/app.xml":
                    data = clean_extended_properties(data)
                elif name.startswith("word/") and name.endswith(".xml"):
                    data = strip_revision_ids(data)
                canonical_info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIMESTAMP)
                canonical_info.compress_type = zipfile.ZIP_DEFLATED
                canonical_info.create_system = info.create_system
                canonical_info.external_attr = info.external_attr
                target.writestr(canonical_info, data)
        shutil.move(cleaned, path)
    finally:
        cleaned.unlink(missing_ok=True)


def clean_root_relationships(data: bytes) -> bytes:
    root = ET.fromstring(data)
    for relationship in list(root):
        target = relationship.attrib.get("Target", "")
        rel_type = relationship.attrib.get("Type", "")
        if target.startswith("customXml/") or target == "docProps/custom.xml" or "custom-properties" in rel_type:
            root.remove(relationship)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def clean_external_relationships(data: bytes) -> bytes:
    root = ET.fromstring(data)
    for relationship in list(root):
        target = relationship.attrib.get("Target", "")
        rel_type = relationship.attrib.get("Type", "")
        if (
            relationship.attrib.get("TargetMode") == "External"
            or "customXml" in target
            or "customXml" in rel_type
            or "custom-properties" in rel_type
        ):
            root.remove(relationship)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def clean_content_types(data: bytes) -> bytes:
    root = ET.fromstring(data)
    for child in list(root):
        part_name = child.attrib.get("PartName", "")
        if part_name.startswith("/customXml/") or part_name == "/docProps/custom.xml":
            root.remove(child)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def clean_extended_properties(data: bytes) -> bytes:
    root = ET.fromstring(data)
    for local_name in ("Company", "Manager", "HyperlinkBase"):
        node = root.find(f"{{{EXTENDED_PROPS_NS}}}{local_name}")
        if node is not None:
            node.text = ""
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def strip_revision_ids(data: bytes) -> bytes:
    # Preserve the source namespace prefixes byte-for-byte. Re-serializing with
    # ElementTree renames prefixes (for example ``w14`` to ``ns2``) without
    # updating the prefix names stored inside ``mc:Ignorable``. Word then treats
    # parts such as fontTable.xml as unreadable even though generic XML parsers
    # accept them. Revision IDs are simple attributes, so a targeted byte-level
    # removal is both sufficient and namespace-safe.
    return re.sub(rb"""\\s+w:rsid[A-Za-z0-9]*=(["'])[^"']*\\1""", b"", data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--one-page-source", type=Path)
    parser.add_argument("--one-page-output", type=Path)
    parser.add_argument("--long-source", type=Path)
    parser.add_argument("--long-output", type=Path)
    parser.add_argument("--biosketch-source", type=Path)
    parser.add_argument("--biosketch-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    jobs = [
        (args.one_page_source, args.one_page_output, sanitize_one_page),
        (args.long_source, args.long_output, sanitize_long),
        (args.biosketch_source, args.biosketch_output, sanitize_biosketch),
    ]
    ran = False
    for source, output, sanitizer in jobs:
        if source is None and output is None:
            continue
        if source is None or output is None:
            raise SystemExit("Each requested template needs both a source and an output path.")
        if not source.exists():
            raise SystemExit(f"Source not found: {source}")
        if source.resolve() == output.resolve():
            with tempfile.NamedTemporaryFile(prefix="vitamine-template-source-", suffix=".docx", delete=False) as handle:
                retained_source = Path(handle.name)
            shutil.copy2(source, retained_source)
            try:
                sanitizer(retained_source, output)
            finally:
                retained_source.unlink(missing_ok=True)
        else:
            sanitizer(source, output)
        print(f"template: {output}")
        ran = True
    if not ran:
        raise SystemExit("No template sources were requested.")


if __name__ == "__main__":
    main()
