"""Shared filesystem paths for VitaMine."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path


PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
STATIC = PACKAGE / "static"
LOGO = PACKAGE / "logo"
SCRIPTS = PACKAGE / "scripts"
SCHEMA = PACKAGE / "schema.sql"
EXAMPLE_DB = DATA / "example.sqlite"
ACTIVE_DB_FILE = DATA / "active_db.txt"
DEFAULT_WORKSPACE_DB = DATA / "workspace.sqlite"
METRICS_CSV = DATA / "journal_metrics.csv"


def sanitize_database_name(name: str | None) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "").strip()).strip("._")
    if not text:
        text = "workspace"
    if not text.endswith(".sqlite"):
        text += ".sqlite"
    return text


def active_db_path() -> Path:
    override = os.environ.get("VITAMINE_DB")
    if override:
        return Path(override).expanduser().resolve()
    if ACTIVE_DB_FILE.exists():
        raw = ACTIVE_DB_FILE.read_text(encoding="utf-8").strip()
        if raw:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = ROOT / path
            return path.resolve()
    return EXAMPLE_DB


def set_active_db(path: Path) -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    resolved = path.expanduser().resolve()
    try:
        value = str(resolved.relative_to(ROOT))
    except ValueError:
        value = str(resolved)
    ACTIVE_DB_FILE.write_text(value + "\n", encoding="utf-8")
    return resolved


def create_blank_database(path: Path) -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    with sqlite3.connect(path) as con:
        con.executescript(SCHEMA.read_text(encoding="utf-8"))
        con.execute(
            """
            INSERT OR IGNORE INTO documents
              (slug, title, source_path, source_format, imported_at, notes)
            VALUES
              ('manual_cv_database', 'Manual CV database edits', ?, 'sqlite', datetime('now'), 'Created in VitaMine.')
            """,
            (str(path),),
        )
        con.execute(
            """
            INSERT OR IGNORE INTO person
              (id, full_name, display_name, raw_json)
            VALUES
              (1, '', '', '{}')
            """
        )
        con.execute(
            """
            INSERT OR IGNORE INTO export_settings
              (profile, publication_limit, authorship_filter)
            VALUES
              ('short', 10, 'first_last'),
              ('ultrashort', 10, 'first_last')
            """
        )
        con.commit()
    return path
