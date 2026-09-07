"""Migrate VitaMine gateway metadata from SQLite to PostgreSQL.

The target URL is read only from VITAMINE_DATABASE_URL so credentials do not
need to appear in shell history or the process list. The migration is
idempotent and reports counts only; it never prints credential hashes or user
data.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Any

from vitamine.cloud_app import POSTGRES_SCHEMA


TABLE_COLUMNS = {
    "invitations": [
        "id",
        "label",
        "code_hash",
        "max_uses",
        "use_count",
        "expires_at",
        "revoked_at",
        "created_at",
    ],
    "members": [
        "id",
        "invitation_id",
        "email",
        "password_hash",
        "display_name",
        "account_created_at",
        "created_at",
        "last_seen_at",
        "revoked_at",
    ],
    "device_credentials": [
        "id",
        "member_id",
        "token_hash",
        "created_at",
        "last_used_at",
        "revoked_at",
    ],
    "public_profiles": [
        "slug",
        "member_id",
        "snapshot_json",
        "portrait_blob",
        "portrait_mime_type",
        "created_at",
        "updated_at",
        "published_at",
    ],
    "workspace_sessions": [
        "id",
        "member_id",
        "database_id",
        "token_hash",
        "db_path",
        "output_path",
        "port",
        "pid",
        "original_filename",
        "created_at",
        "last_seen_at",
        "expires_at",
    ],
}

PRIMARY_KEYS = {
    "invitations": "id",
    "members": "id",
    "device_credentials": "id",
    "public_profiles": "slug",
    "workspace_sessions": "id",
}

MISSING_DEFAULTS: dict[str, dict[str, Any]] = {
    "members": {"display_name": ""},
}


def source_columns(source: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in source.execute(f"PRAGMA table_info({table})")}


def source_rows(source: sqlite3.Connection, table: str, columns: list[str]) -> list[dict[str, Any]]:
    available = source_columns(source, table)
    if not available:
        return []
    selected = [column for column in columns if column in available]
    rows = source.execute(f"SELECT {', '.join(selected)} FROM {table}").fetchall()
    defaults = MISSING_DEFAULTS.get(table, {})
    return [
        {
            column: row[column] if column in selected else defaults.get(column)
            for column in columns
        }
        for row in rows
    ]


def migrate(source_path: Path, database_url: str) -> dict[str, int]:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Install psycopg before running the migration.") from exc

    if not source_path.is_file():
        raise FileNotFoundError(f"Gateway SQLite database not found: {source_path}")

    counts: dict[str, int] = {}
    with sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        with psycopg.connect(database_url) as target:
            target.execute(POSTGRES_SCHEMA)
            target.execute("ALTER TABLE public_profiles ADD COLUMN IF NOT EXISTS portrait_blob BYTEA")
            target.execute("ALTER TABLE public_profiles ADD COLUMN IF NOT EXISTS portrait_mime_type TEXT")
            for table, columns in TABLE_COLUMNS.items():
                rows = source_rows(source, table, columns)
                if not rows:
                    counts[table] = 0
                    continue
                placeholders = ", ".join(["%s"] * len(columns))
                primary_key = PRIMARY_KEYS[table]
                updates = ", ".join(
                    f"{column}=EXCLUDED.{column}" for column in columns if column != primary_key
                )
                statement = f"""
                    INSERT INTO {table} ({", ".join(columns)})
                    VALUES ({placeholders})
                    ON CONFLICT ({primary_key}) DO UPDATE SET {updates}
                """
                before = target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for row in rows:
                    target.execute(statement, tuple(row[column] for column in columns))
                after = target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                counts[table] = int(after) - int(before)
            for table in ("invitations", "device_credentials"):
                target.execute(
                    f"""
                    SELECT setval(
                      pg_get_serial_sequence('{table}', 'id'),
                      COALESCE((SELECT MAX(id) FROM {table}), 1),
                      EXISTS(SELECT 1 FROM {table})
                    )
                    """
                )
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(os.environ.get("VITAMINE_CLOUD_DB", "/var/lib/vitamine-cloud/vitamine-cloud.sqlite")),
    )
    args = parser.parse_args()
    database_url = str(os.environ.get("VITAMINE_DATABASE_URL") or "").strip()
    if not database_url:
        parser.error("VITAMINE_DATABASE_URL must be set in the environment.")
    counts = migrate(args.source.expanduser(), database_url)
    print("Migrated gateway rows: " + ", ".join(f"{table}={count}" for table, count in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
