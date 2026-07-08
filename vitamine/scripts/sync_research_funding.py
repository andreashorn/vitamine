#!/usr/bin/env python3
"""Normalize research funding rows and move amounts into structured fields."""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

from vitamine.i18n import draft_translate_to_german
from vitamine.paths import active_db_path

DB = active_db_path()


LONG_CV_FUNDING = {
    "Toward Modeling Brain Networks in Dystonia": {
        "organization": "Berlin Institute of Health",
        "amount": "$50,000",
        "role": "PI",
        "description": "This grant was used for protected research time during residency.",
    },
    "Thiemann Fellowship at German Neurology Association": {
        "organization": "Prof. Klaus Thiemann Foundation",
        "amount": "$55,000",
        "role": "PI",
        "description": "This grant was used to fund postdoctoral time at Harvard Medical School.",
    },
    "Toward Connectomic Deep Brain Stimulation": {
        "organization": "Harvard Radcliffe Institute Academic Ventures Grant",
        "amount": "$18,000",
        "role": "Co-PI",
        "description": "Event organization for an expert conference on connectomic brain stimulation.",
    },
    "Toward Connectomic Brain Stimulation": {
        "organization": "German Research Council 410169619",
        "amount": "$1,688,000",
        "role": "PI",
        "description": "Establish a connectomic deep brain stimulation framework to optimize surgical and neurological treatment.",
    },
    "Subproject S01 in Clinical Research Center 295 Retune": {
        "organization": "German Research Council SFB Retune",
        "amount": "$324,000",
        "role": "PI",
        "description": "Supports development of an open source pipeline for deep brain stimulation modeling.",
    },
    "Subproject B01 in Clinical Research Center 295 Retune": {
        "organization": "German Research Council SFB Retune",
        "amount": "$596,986",
        "role": "PI",
        "description": "Create personalized connectomic deep brain stimulation targets using diffusion-imaging based tractography.",
    },
    "Patient-specific dynamical modeling and optimization of Deep Brain Stimulation": {
        "organization": "JPND call on Novel imaging and brain stimulation methods and technologies related to Neurodegenerative Diseases",
        "amount": "$1,335,696",
        "role": "PI & Coordinator",
        "description": "Multi-center grant to create a dynamic model of deep brain stimulation validated by imaging and electrophysiological data.",
    },
    "FFOR Seed Grant Toward Personalized Circuit Therapy in OCD": {
        "organization": "FFOR - The Foundation for OCD Research",
        "amount": "$660,000",
        "role": "PI",
        "description": "Develop a machine-learning based model for network-blending and integration between connectomics and transcriptomics in OCD.",
    },
    "Sudardky Scholar Award": {
        "organization": "Brigham & Women's Hospital",
        "amount": "$25k",
        "role": "PI",
        "description": "Build a database for DBS patients.",
    },
    "2R01 MH113929": {
        "organization": "National Institutes of Health",
        "amount": "$4 MIO",
        "role": "Co-I (PI: Fox; 10% effort)",
        "description": "Identify the causal neuroanatomical substrate of depression symptoms.",
    },
    "1R01 13478451": {
        "organization": "NIH",
        "amount": "$2.5 MIO",
        "role": "PI (50% effort)",
        "description": "Determine networks associated with optimal deep brain stimulation for OCD.",
    },
    "1R01 NS127892-01": {
        "organization": "NIH",
        "amount": "$4.3 MIO",
        "role": "Co-I (PI: Fox; 5% effort)",
        "description": "Determine networks associated with the occurrence of epilepsy.",
    },
    "UM1NS132358": {
        "organization": "NIH",
        "amount": "$23 MIO; subaward to Horn $1.3 MIO",
        "role": "Co-I (PI: Yendiki)",
        "description": "Apply high resolution connectomes to deep brain stimulation imaging.",
    },
    "Raynor Cerebellum Project": {
        "organization": "Raynor Cerebellum Project",
        "amount": "€270k",
        "role": "PI",
        "description": "Mapping the Cerebellar Dysfunctome.",
        "title": "Raynor Cerebellum Project: Mapping the Cerebellar Dysfunctome",
    },
}


ERC_FUNDING = [
    {
        "match": "Schilling",
        "title": "Schilling Professorship: Institute for Network Stimulation, Cologne",
        "organization": "Schilling Foundation",
        "amount": "€2,999,320",
        "start_date": "05/2025",
        "end_date": "04/2033",
        "role": "PI",
        "description": "Base funding for the Institute for Network Stimulation at the University Hospital Cologne. Source: ERC_funding.pdf.",
    },
    {
        "match": "BG-Refine",
        "title": "BG-Refine: Personalization of Basal Ganglia Circuitry for DBS and Stroke",
        "organization": "I01 Project within CRC 1451, German Research Foundation (DFG)",
        "amount": "€164,798",
        "start_date": "09/2025",
        "end_date": "08/2028",
        "role": "PI",
        "description": "Develop methods to refine tract atlas deformations using individualized diffusion MRI scans. Source: ERC_funding.pdf.",
    },
    {
        "match": "Project C05 in 3rd funding period of CRC ELAINE",
        "title": "Project C05 in 3rd funding period of CRC ELAINE",
        "organization": "German Research Foundation (DFG)",
        "amount": "€348,700",
        "start_date": "01/2026",
        "end_date": "12/2029",
        "role": "PI (Subproject C05)",
        "description": "Continue development of the interface between OSS-DBS and Lead-DBS. Source: ERC_funding.pdf.",
        "include_long": 1,
    },
    {
        "match": "Causal Circuits for Brain Therapeutics",
        "title": "Causal Circuits for Brain Therapeutics (CCBT)",
        "organization": "German Research Foundation (DFG)",
        "amount": "€1,250,000",
        "start_date": "2026",
        "end_date": "2030",
        "role": "PI",
        "description": "Submitted grant application. Source: ERC_funding.pdf.",
        "include_long": 0,
        "include_extended": 0,
        "include_short": 0,
        "subcategory": "grant_application",
    },
    {
        "match": "Imaging Guided Deep Brain Stimulation",
        "title": "Imaging Guided Deep Brain Stimulation: Methods & Integrations",
        "organization": "Boston Scientific",
        "amount": "€527,800",
        "start_date": "01/2026",
        "end_date": "12/2028",
        "role": "PI",
        "description": "Submitted project comparing two software tools. Source: ERC_funding.pdf.",
        "include_long": 0,
        "include_extended": 0,
        "include_short": 0,
        "subcategory": "grant_application",
    },
]


def ensure_columns(con: sqlite3.Connection) -> None:
    existing = {row[1] for row in con.execute("PRAGMA table_info(cv_entries)").fetchall()}
    if "source_note" not in existing:
        con.execute("ALTER TABLE cv_entries ADD COLUMN source_note TEXT")


def normalize_title(title: str | None) -> str:
    return re.sub(r"\s+", " ", title or "").strip()


def find_entry(con: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return con.execute(
        """
        SELECT *
        FROM cv_entries
        WHERE section_key = 'funding'
          AND title LIKE ?
        ORDER BY id
        LIMIT 1
        """,
        (f"%{token}%",),
    ).fetchone()


def update_entry(con: sqlite3.Connection, entry_id: int, data: dict, source_note: str) -> None:
    description = data.get("description")
    con.execute(
        """
        UPDATE cv_entries
        SET title = ?,
            title_de = ?,
            start_date = COALESCE(?, start_date),
            end_date = COALESCE(?, end_date),
            organization = ?,
            organization_de = ?,
            amount = ?,
            amount_de = ?,
            role = ?,
            role_de = ?,
            description = ?,
            description_de = ?,
            raw_text = ?,
            raw_text_de = ?,
            source_note = ?,
            subcategory = COALESCE(?, subcategory),
            subcategory_de = COALESCE(?, subcategory_de),
            include_long = COALESCE(?, include_long),
            include_extended = COALESCE(?, include_extended),
            include_short = COALESCE(?, include_short)
        WHERE id = ?
        """,
        (
            data.get("title"),
            draft_translate_to_german(data.get("title")),
            data.get("start_date"),
            data.get("end_date"),
            data.get("organization"),
            draft_translate_to_german(data.get("organization")),
            data.get("amount"),
            draft_translate_to_german(data.get("amount")),
            data.get("role"),
            draft_translate_to_german(data.get("role")),
            description,
            draft_translate_to_german(description),
            " | ".join(part for part in [data.get("title"), data.get("organization"), data.get("role"), data.get("amount"), description] if part),
            draft_translate_to_german(" | ".join(part for part in [data.get("title"), data.get("organization"), data.get("role"), data.get("amount"), description] if part)),
            source_note,
            data.get("subcategory"),
            draft_translate_to_german(data.get("subcategory")),
            data.get("include_long"),
            data.get("include_extended"),
            data.get("include_short"),
            entry_id,
        ),
    )


def insert_entry(con: sqlite3.Connection, data: dict, source_note: str) -> int:
    document_id = con.execute("SELECT id FROM documents WHERE slug='manual_cv_database'").fetchone()
    if document_id:
        document_id = document_id[0]
    else:
        con.execute(
            """
            INSERT INTO documents (slug, title, source_path, source_format, imported_at, notes)
            VALUES ('manual_cv_database', 'Manual CV database edits', 'data/example.sqlite', 'sqlite', datetime('now'), 'Research funding normalized from source documents.')
            """
        )
        document_id = con.execute("SELECT id FROM documents WHERE slug='manual_cv_database'").fetchone()[0]
    raw_text = " | ".join(part for part in [data.get("title"), data.get("organization"), data.get("role"), data.get("amount"), data.get("description")] if part)
    cur = con.execute(
        """
        INSERT INTO cv_entries (
          document_id, section_key, start_date, end_date, title, title_de,
          organization, organization_de, role, role_de, amount, amount_de,
          description, description_de, raw_text, raw_text_de, confidence,
          include_extended, include_long, include_short, include_biosketch,
          language, source_note, subcategory, subcategory_de
        ) VALUES (?, 'funding', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'source', ?, ?, ?, 0, 'en', ?, ?, ?)
        """,
        (
            document_id,
            data.get("start_date"),
            data.get("end_date"),
            data.get("title"),
            draft_translate_to_german(data.get("title")),
            data.get("organization"),
            draft_translate_to_german(data.get("organization")),
            data.get("role"),
            draft_translate_to_german(data.get("role")),
            data.get("amount"),
            draft_translate_to_german(data.get("amount")),
            data.get("description"),
            draft_translate_to_german(data.get("description")),
            raw_text,
            draft_translate_to_german(raw_text),
            data.get("include_extended", 1),
            data.get("include_long", 1),
            data.get("include_short", 1),
            source_note,
            data.get("subcategory"),
            draft_translate_to_german(data.get("subcategory")),
        ),
    )
    return int(cur.lastrowid)


def sync_long_cv_amounts(con: sqlite3.Connection) -> int:
    updated = 0
    for token, data in LONG_CV_FUNDING.items():
        row = find_entry(con, token)
        if not row:
            continue
        merged = dict(data)
        merged["title"] = data.get("title") or row["title"]
        update_entry(con, row["id"], merged, "Amount/role/details parsed from long CV source.")
        updated += 1
    return updated


def sync_erc_funding(con: sqlite3.Connection) -> tuple[int, int]:
    updated = 0
    inserted = 0
    for data in ERC_FUNDING:
        row = find_entry(con, data["match"])
        if row:
            merged = dict(data)
            merged["title"] = data["title"]
            update_entry(con, row["id"], merged, "Amount/details parsed from ERC_funding.pdf.")
            updated += 1
        else:
            insert_entry(con, data, "Amount/details parsed from ERC_funding.pdf.")
            inserted += 1
    return updated, inserted


def main() -> None:
    with sqlite3.connect(DB) as con:
        con.row_factory = sqlite3.Row
        ensure_columns(con)
        long_cv_updated = sync_long_cv_amounts(con)
        erc_updated, erc_inserted = sync_erc_funding(con)
        con.commit()
    print(
        f"Updated {long_cv_updated} long-CV funding rows; updated {erc_updated} ERC rows; inserted {erc_inserted} ERC rows."
    )


if __name__ == "__main__":
    main()
