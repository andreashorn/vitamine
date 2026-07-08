"""Shared filesystem paths for VitaMine."""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import shutil
import json
from pathlib import Path


FROZEN = bool(getattr(sys, "frozen", False))


def frozen_resource_root() -> Path:
    contents = Path(sys.executable).resolve().parents[1]
    resources = contents / "Resources"
    if resources.exists():
        return resources
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


RESOURCE_ROOT = frozen_resource_root() if FROZEN else Path(__file__).resolve().parents[1]
ROOT = RESOURCE_ROOT
PACKAGE = RESOURCE_ROOT / "vitamine"
APP_SUPPORT = Path.home() / "Library" / "Application Support" / "VitaMine"
PREFERENCES = Path.home() / "Library" / "Preferences" / "de.netstim.vitamine.json"
DATA = (APP_SUPPORT / "data") if FROZEN else (ROOT / "data")
OUTPUT = (APP_SUPPORT / "output") if FROZEN else (ROOT / "output")
STATIC = PACKAGE / "static"
LOGO = PACKAGE / "logo"
SCRIPTS = PACKAGE / "scripts"
SCHEMA = PACKAGE / "schema.sql"
BIN = RESOURCE_ROOT / "bin"
LOCAL_TOOLCHAIN_BIN = ROOT / "vendor" / "export-tools" / "bin"
MODELS = RESOURCE_ROOT / "models"
LOCAL_MODELS = ROOT / "vendor" / "models"
BUNDLED_DATA = RESOURCE_ROOT / "data"
BUNDLED_EXAMPLE_DB = BUNDLED_DATA / "example.vitamine"
BUNDLED_LEGACY_EXAMPLE_DB = BUNDLED_DATA / "example.sqlite"
BUNDLED_METRICS_CSV = BUNDLED_DATA / "journal_metrics.csv"
EXAMPLE_DB = DATA / "example.vitamine"
ACTIVE_DB_FILE = DATA / "active_db.txt"
DEFAULT_WORKSPACE_DB = DATA / "workspace.vitamine"
METRICS_CSV = DATA / "journal_metrics.csv"
DATABASE_EXTENSIONS = {".vitamine", ".sqlite", ".db"}


def ensure_runtime_files() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if FROZEN:
        bundled_example = BUNDLED_EXAMPLE_DB if BUNDLED_EXAMPLE_DB.exists() else BUNDLED_LEGACY_EXAMPLE_DB
        if bundled_example.exists() and not EXAMPLE_DB.exists():
            shutil.copy2(bundled_example, EXAMPLE_DB)


def sanitize_database_name(name: str | None) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "").strip()).strip("._")
    if not text:
        text = "workspace"
    suffix = Path(text).suffix.lower()
    if suffix not in DATABASE_EXTENSIONS:
        text += ".vitamine"
    return text


def active_db_path() -> Path:
    ensure_runtime_files()
    override = os.environ.get("VITAMINE_DB")
    if override:
        return Path(override).expanduser().resolve()
    prefs = read_preferences()
    raw_pref = str(prefs.get("active_db") or "").strip()
    if raw_pref:
        return Path(raw_pref).expanduser().resolve()
    if ACTIVE_DB_FILE.exists():
        raw = ACTIVE_DB_FILE.read_text(encoding="utf-8").strip()
        if raw:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = ROOT / path
            resolved = path.resolve()
            write_preferences({**prefs, "active_db": str(resolved)})
            return resolved
    return EXAMPLE_DB


def validate_database(path: Path) -> None:
    if path.suffix.lower() not in DATABASE_EXTENSIONS:
        raise ValueError("Choose a .vitamine or .sqlite database file.")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(path)
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
            tables = {
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            integrity = con.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise ValueError("The selected file is not a readable SQLite database.") from exc
    if not integrity or integrity[0] != "ok":
        raise ValueError("The selected database did not pass SQLite integrity checks.")
    required = {"documents", "person", "cv_entries", "publications"}
    missing = sorted(required - tables)
    if missing:
        raise ValueError(f"The selected file is not a VitaMine database; missing: {', '.join(missing)}.")


def set_active_db(path: Path) -> Path:
    ensure_runtime_files()
    resolved = path.expanduser().resolve()
    prefs = read_preferences()
    prefs["active_db"] = str(resolved)
    write_preferences(prefs)
    return resolved


def read_preferences() -> dict:
    if not PREFERENCES.exists():
        return {}
    try:
        data = json.loads(PREFERENCES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_preferences(values: dict) -> None:
    PREFERENCES.parent.mkdir(parents=True, exist_ok=True)
    PREFERENCES.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def create_blank_database(path: Path) -> Path:
    ensure_runtime_files()
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


def output_ref(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(OUTPUT.resolve()))
    except ValueError:
        pass
    try:
        return str(resolved.relative_to(ROOT.resolve()))
    except ValueError:
        return str(resolved)


def tool_path(name: str) -> str | None:
    candidates = [BIN / name, LOCAL_TOOLCHAIN_BIN / name]
    if FROZEN:
        contents = Path(sys.executable).resolve().parents[1]
        candidates.append(contents / "Frameworks" / "bin" / name)
    for bundled in candidates:
        if bundled.exists():
            return str(bundled)
    return shutil.which(name)


def bundled_model_path(name: str = "vitamine-import.gguf") -> Path | None:
    candidates = [MODELS / name, LOCAL_MODELS / name]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None
