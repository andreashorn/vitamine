"""Invite-only hosted VitaMine service."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote, urlencode

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .public_profiles import (
    PROFILE_BLOCK_KEYS,
    build_public_profile_snapshot,
    normalize_profile_blocks,
)


DEFAULT_DB = Path("cloud-data/vitamine-cloud.sqlite")
SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])?$")
RESERVED_SLUGS = {
    "admin",
    "api",
    "app",
    "assets",
    "embed",
    "health",
    "login",
    "logout",
    "static",
    "support",
    "www",
}
MAX_SNAPSHOT_BYTES = 512_000
TOKEN_PREFIX = "vtd_"
SESSION_COOKIE = "vitamine_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 180
WORKSPACE_COOKIE = "vitamine_workspace"
WORKSPACE_MAX_AGE = 60 * 60 * 24
JOB_RETENTION_DAYS = 30
MAX_DATABASE_BYTES = 200 * 1024 * 1024
MAX_JOB_UPLOAD_BYTES = 50 * 1024 * 1024
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 256
LOGIN_WINDOW_MINUTES = 15
LOGIN_MAX_FAILURES = 10
ORCID_OAUTH_STATE_MAX_AGE = 10 * 60
ORCID_PATTERN = re.compile(r"^\d{4}-\d{4}-\d{4}-[\dX]{4}$", re.I)
WORKER_PROCESSES: dict[int, subprocess.Popen] = {}
DATABASE_LOCKS: dict[str, threading.Lock] = {}
DATABASE_LOCKS_GUARD = threading.Lock()
CLEANUP_STOP = threading.Event()
JOB_STOP = threading.Event()
PROJECT = Path(__file__).resolve().parent
LOGO_PATH = PROJECT / "logo" / "vitamine_logo.png"
INVITE_ARROW_PATH = PROJECT / "static" / "assets" / "onboarding_arrow_blank.png"
CLOUD_STATIC = PROJECT / "cloud_static"
CLOUD_SCHEMA_VERSION = 3
LOGGER = logging.getLogger("vitamine.cloud")


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS invitations (
    id INTEGER PRIMARY KEY,
    label TEXT NOT NULL,
    code_hash TEXT NOT NULL UNIQUE,
    max_uses INTEGER NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
    use_count INTEGER NOT NULL DEFAULT 0 CHECK (use_count >= 0),
    expires_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS members (
    id TEXT PRIMARY KEY,
    invitation_id INTEGER NOT NULL REFERENCES invitations(id),
    email TEXT,
    password_hash TEXT,
    display_name TEXT NOT NULL DEFAULT '',
    account_created_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS device_credentials (
    id INTEGER PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS public_profiles (
    slug TEXT PRIMARY KEY,
    member_id TEXT NOT NULL UNIQUE REFERENCES members(id) ON DELETE CASCADE,
    snapshot_json TEXT NOT NULL,
    portrait_blob BLOB,
    portrait_mime_type TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_device_credentials_hash
ON device_credentials(token_hash);

CREATE TABLE IF NOT EXISTS workspace_sessions (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL UNIQUE REFERENCES members(id) ON DELETE CASCADE,
    database_id TEXT REFERENCES account_databases(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    db_path TEXT NOT NULL,
    output_path TEXT NOT NULL,
    port INTEGER NOT NULL,
    pid INTEGER,
    original_filename TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_databases (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    sqlite_blob BLOB NOT NULL,
    checksum TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_opened_at TEXT,
    deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_account_databases_member
ON account_databases(member_id, deleted_at, updated_at);

CREATE TABLE IF NOT EXISTS background_jobs (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    database_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('cv_import', 'enrich_cv')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    base_revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    progress_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    acknowledged_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_background_jobs_member
ON background_jobs(member_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_background_jobs_database
ON background_jobs(database_id, status, created_at);

CREATE TABLE IF NOT EXISTS hosted_cv_people (
    cv_id TEXT PRIMARY KEY REFERENCES account_databases(id) ON DELETE CASCADE,
    full_name TEXT,
    display_name TEXT,
    position_title TEXT,
    work_email TEXT,
    orcid_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hosted_cv_entries (
    cv_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL,
    section_key TEXT,
    start_date TEXT,
    end_date TEXT,
    title TEXT,
    organization TEXT,
    role TEXT,
    description TEXT,
    PRIMARY KEY (cv_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_hosted_cv_entries_section
ON hosted_cv_entries(cv_id, section_key);

CREATE TABLE IF NOT EXISTS hosted_cv_publications (
    cv_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
    source_id INTEGER NOT NULL,
    category TEXT,
    authors TEXT,
    title TEXT,
    venue TEXT,
    year TEXT,
    doi TEXT,
    pmid TEXT,
    url TEXT,
    cited_by_count INTEGER,
    suppress_display INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (cv_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_hosted_cv_publications_doi
ON hosted_cv_publications(cv_id, doi);

CREATE TABLE IF NOT EXISTS authentication_attempts (
    id INTEGER PRIMARY KEY,
    email_hash TEXT NOT NULL,
    ip_hash TEXT NOT NULL,
    succeeded INTEGER NOT NULL DEFAULT 0,
    attempted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_authentication_attempts_lookup
ON authentication_attempts(email_hash, ip_hash, attempted_at);

CREATE TABLE IF NOT EXISTS oauth_authorization_states (
    state_hash TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    database_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oauth_authorization_states_expiry
ON oauth_authorization_states(expires_at);

CREATE TABLE IF NOT EXISTS orcid_oauth_connections (
    member_id TEXT PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
    last_database_id TEXT REFERENCES account_databases(id) ON DELETE SET NULL,
    orcid_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    access_token_ciphertext TEXT NOT NULL,
    refresh_token_ciphertext TEXT,
    token_type TEXT NOT NULL DEFAULT 'bearer',
    scope TEXT NOT NULL,
    expires_at TEXT,
    verified_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS invitations (
    id BIGSERIAL PRIMARY KEY,
    label TEXT NOT NULL,
    code_hash TEXT NOT NULL UNIQUE,
    max_uses INTEGER NOT NULL DEFAULT 1 CHECK (max_uses >= 1),
    use_count INTEGER NOT NULL DEFAULT 0 CHECK (use_count >= 0),
    expires_at TEXT,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    id TEXT PRIMARY KEY,
    invitation_id BIGINT NOT NULL REFERENCES invitations(id),
    email TEXT,
    password_hash TEXT,
    display_name TEXT NOT NULL DEFAULT '',
    account_created_at TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_members_email_unique
ON members(email) WHERE email IS NOT NULL;

CREATE TABLE IF NOT EXISTS device_credentials (
    id BIGSERIAL PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    revoked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_device_credentials_hash
ON device_credentials(token_hash);

CREATE TABLE IF NOT EXISTS public_profiles (
    slug TEXT PRIMARY KEY,
    member_id TEXT NOT NULL UNIQUE REFERENCES members(id) ON DELETE CASCADE,
    snapshot_json TEXT NOT NULL,
    portrait_blob BYTEA,
    portrait_mime_type TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account_databases (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    filename TEXT NOT NULL,
    sqlite_blob BYTEA NOT NULL,
    checksum TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    size_bytes BIGINT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_opened_at TEXT,
    deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_account_databases_member
ON account_databases(member_id, deleted_at, updated_at);

CREATE TABLE IF NOT EXISTS background_jobs (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    database_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('cv_import', 'enrich_cv')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    base_revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    progress_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    acknowledged_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_background_jobs_member
ON background_jobs(member_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_background_jobs_database
ON background_jobs(database_id, status, created_at);

CREATE TABLE IF NOT EXISTS hosted_cv_people (
    cv_id TEXT PRIMARY KEY REFERENCES account_databases(id) ON DELETE CASCADE,
    full_name TEXT,
    display_name TEXT,
    position_title TEXT,
    work_email TEXT,
    orcid_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hosted_cv_entries (
    cv_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
    source_id BIGINT NOT NULL,
    section_key TEXT,
    start_date TEXT,
    end_date TEXT,
    title TEXT,
    organization TEXT,
    role TEXT,
    description TEXT,
    PRIMARY KEY (cv_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_hosted_cv_entries_section
ON hosted_cv_entries(cv_id, section_key);

CREATE TABLE IF NOT EXISTS hosted_cv_publications (
    cv_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
    source_id BIGINT NOT NULL,
    category TEXT,
    authors TEXT,
    title TEXT,
    venue TEXT,
    year TEXT,
    doi TEXT,
    pmid TEXT,
    url TEXT,
    cited_by_count INTEGER,
    suppress_display INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (cv_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_hosted_cv_publications_doi
ON hosted_cv_publications(cv_id, doi);

CREATE TABLE IF NOT EXISTS workspace_sessions (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL UNIQUE REFERENCES members(id) ON DELETE CASCADE,
    database_id TEXT REFERENCES account_databases(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    db_path TEXT NOT NULL,
    output_path TEXT NOT NULL,
    port INTEGER NOT NULL,
    pid INTEGER,
    original_filename TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS authentication_attempts (
    id BIGSERIAL PRIMARY KEY,
    email_hash TEXT NOT NULL,
    ip_hash TEXT NOT NULL,
    succeeded INTEGER NOT NULL DEFAULT 0,
    attempted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_authentication_attempts_lookup
ON authentication_attempts(email_hash, ip_hash, attempted_at);

CREATE TABLE IF NOT EXISTS oauth_authorization_states (
    state_hash TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    database_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oauth_authorization_states_expiry
ON oauth_authorization_states(expires_at);

CREATE TABLE IF NOT EXISTS orcid_oauth_connections (
    member_id TEXT PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
    last_database_id TEXT REFERENCES account_databases(id) ON DELETE SET NULL,
    orcid_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    access_token_ciphertext TEXT NOT NULL,
    refresh_token_ciphertext TEXT,
    token_type TEXT NOT NULL DEFAULT 'bearer',
    scope TEXT NOT NULL,
    expires_at TEXT,
    verified_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class InviteRedemption(BaseModel):
    code: str = Field(min_length=8, max_length=200)


class AccountRegistration(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    display_name: str = Field(default="", max_length=160)


class AccountLogin(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class DatabaseRename(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class PublicProfileSnapshot(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=2)
    source_database_id: str = Field(default="", max_length=200)
    display_name: str = Field(min_length=1, max_length=160)
    profile_title: str = Field(default="", max_length=200)
    headline: str = Field(default="", max_length=300)
    location: str = Field(default="", max_length=200)
    biography: str = Field(default="", max_length=5000)
    orcid: str = Field(default="", max_length=80)
    website: str = Field(default="", max_length=500)
    sections: list[dict[str, Any]] = Field(default_factory=list, max_length=30)
    publications: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    blocks: list[dict[str, Any]] = Field(default_factory=list, max_length=4)
    bio: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(default_factory=dict)
    collaborators: dict[str, Any] = Field(default_factory=dict)


class ProfilePublishRequest(BaseModel):
    slug: str = Field(min_length=3, max_length=40)
    database_id: str = Field(min_length=8, max_length=200)


class ProfileBlocksRequest(BaseModel):
    blocks: list[dict[str, Any]] = Field(min_length=1, max_length=4)


class ProfileHeaderRequest(BaseModel):
    profile_title: str = Field(min_length=1, max_length=200)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cloud_db_path() -> Path:
    return Path(os.environ.get("VITAMINE_CLOUD_DB", DEFAULT_DB)).expanduser().resolve()


def cloud_database_url() -> str:
    return str(os.environ.get("VITAMINE_DATABASE_URL") or "").strip()


def workspace_root() -> Path:
    return Path(
        os.environ.get("VITAMINE_SESSION_ROOT", "/var/lib/vitamine-cloud/sessions")
    ).expanduser().resolve()


def job_root() -> Path:
    return Path(
        os.environ.get("VITAMINE_JOB_ROOT", "/var/lib/vitamine-cloud/jobs")
    ).expanduser().resolve()


def secret_hash(value: str) -> str:
    pepper = os.environ.get("VITAMINE_CLOUD_PEPPER", "")
    return hashlib.sha256(f"{pepper}\0{value}".encode("utf-8")).hexdigest()


def orcid_oauth_config() -> dict[str, str] | None:
    client_id = str(os.environ.get("ORCID_OAUTH_CLIENT_ID") or "").strip()
    client_secret = str(os.environ.get("ORCID_OAUTH_CLIENT_SECRET") or "").strip()
    redirect_uri = str(os.environ.get("ORCID_OAUTH_REDIRECT_URI") or "").strip()
    base_url = str(os.environ.get("ORCID_OAUTH_BASE_URL") or "https://orcid.org").strip().rstrip("/")
    if not client_id or not client_secret or not redirect_uri:
        return None
    if base_url not in {"https://orcid.org", "https://sandbox.orcid.org"}:
        raise RuntimeError("ORCID_OAUTH_BASE_URL must use ORCID's production or sandbox host.")
    if not redirect_uri.startswith("https://"):
        raise RuntimeError("ORCID OAuth requires an HTTPS redirect URI.")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "base_url": base_url,
    }


def oauth_token_cipher() -> Fernet:
    pepper = str(os.environ.get("VITAMINE_CLOUD_PEPPER") or "")
    if not pepper:
        raise RuntimeError("VITAMINE_CLOUD_PEPPER is required for encrypted OAuth token storage.")
    key = hashlib.sha256(f"vitamine-oauth-token-v1\0{pepper}".encode("utf-8")).digest()
    import base64

    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_oauth_token(value: str) -> str:
    return oauth_token_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def create_oauth_authorization_state(member_id: str, database_id: str) -> str:
    state = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with connect() as con:
        con.execute("DELETE FROM oauth_authorization_states WHERE expires_at<=?", (now.isoformat(),))
        con.execute(
            """
            INSERT INTO oauth_authorization_states
              (state_hash, member_id, database_id, provider, created_at, expires_at)
            VALUES (?, ?, ?, 'orcid', ?, ?)
            """,
            (
                secret_hash(f"oauth-state:{state}"),
                member_id,
                database_id,
                now.isoformat(),
                (now + timedelta(seconds=ORCID_OAUTH_STATE_MAX_AGE)).isoformat(),
            ),
        )
    return state


def consume_oauth_authorization_state(state: str, member_id: str) -> Any:
    if not state or len(state) > 256:
        raise HTTPException(status_code=400, detail="This ORCID authorization request is invalid.")
    state_hash = secret_hash(f"oauth-state:{state}")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            """
            SELECT * FROM oauth_authorization_states
            WHERE state_hash=? AND provider='orcid'
            FOR UPDATE
            """,
            (state_hash,),
        ).fetchone()
        if row is not None:
            con.execute("DELETE FROM oauth_authorization_states WHERE state_hash=?", (state_hash,))
    if row is None or str(row["expires_at"]) <= utc_now():
        raise HTTPException(status_code=400, detail="This ORCID authorization request expired or was already used.")
    if not hmac.compare_digest(str(row["member_id"]), member_id):
        raise HTTPException(status_code=403, detail="This ORCID authorization belongs to another account.")
    return row


async def exchange_orcid_authorization_code(code: str) -> dict[str, Any]:
    config = orcid_oauth_config()
    if config is None:
        raise HTTPException(status_code=503, detail="ORCID sign-in is not configured.")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
            response = await client.post(
                f"{config['base_url']}/oauth/token",
                data={
                    "client_id": config["client_id"],
                    "client_secret": config["client_secret"],
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": config["redirect_uri"],
                },
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="ORCID could not complete the authorization. Please try again.",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="ORCID returned an invalid authorization response.")
    orcid_id = str(payload.get("orcid") or "").strip().upper()
    access_token = str(payload.get("access_token") or "").strip()
    if not ORCID_PATTERN.fullmatch(orcid_id) or not access_token:
        raise HTTPException(status_code=502, detail="ORCID returned an incomplete authorization response.")
    return {
        "orcid_id": orcid_id,
        "display_name": str(payload.get("name") or "").strip()[:200],
        "access_token": access_token,
        "refresh_token": str(payload.get("refresh_token") or "").strip(),
        "token_type": str(payload.get("token_type") or "bearer").strip()[:40],
        "scope": str(payload.get("scope") or "/authenticate").strip()[:200],
        "expires_in": payload.get("expires_in"),
    }


def store_orcid_oauth_connection(
    *,
    member_id: str,
    database_id: str,
    token: dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc)
    expires_at = None
    try:
        expires_in = max(0, min(int(token.get("expires_in")), 60 * 60 * 24 * 365 * 100))
        expires_at = (now + timedelta(seconds=expires_in)).isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    refresh_token = str(token.get("refresh_token") or "")
    with connect() as con:
        con.execute(
            """
            INSERT INTO orcid_oauth_connections
              (member_id, last_database_id, orcid_id, display_name,
               access_token_ciphertext, refresh_token_ciphertext, token_type, scope,
               expires_at, verified_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (member_id) DO UPDATE SET
              last_database_id=excluded.last_database_id,
              orcid_id=excluded.orcid_id,
              display_name=excluded.display_name,
              access_token_ciphertext=excluded.access_token_ciphertext,
              refresh_token_ciphertext=excluded.refresh_token_ciphertext,
              token_type=excluded.token_type,
              scope=excluded.scope,
              expires_at=excluded.expires_at,
              verified_at=excluded.verified_at,
              updated_at=excluded.updated_at
            """,
            (
                member_id,
                database_id,
                token["orcid_id"],
                token["display_name"],
                encrypt_oauth_token(str(token["access_token"])),
                encrypt_oauth_token(refresh_token) if refresh_token else None,
                token["token_type"],
                token["scope"],
                expires_at,
                now.isoformat(),
                now.isoformat(),
            ),
        )


def normalize_email(value: str) -> str:
    email = str(value or "").strip().casefold()
    if (
        len(email) > 254
        or email.count("@") != 1
        or email.startswith("@")
        or email.endswith("@")
        or "." not in email.rsplit("@", 1)[1]
        or any(character.isspace() for character in email)
    ):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return email


def is_unique_violation(exc: Exception) -> bool:
    return isinstance(exc, sqlite3.IntegrityError) or getattr(exc, "sqlstate", None) == "23505"


def hash_password(password: str) -> str:
    if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Use between {PASSWORD_MIN_LENGTH} and {PASSWORD_MAX_LENGTH} characters.",
        )
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, expected_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(expected_hex)),
        )
        return hmac.compare_digest(derived.hex(), expected_hex)
    except (ValueError, TypeError):
        return False


class GatewayConnection:
    def __init__(self, raw: Any, backend: str):
        self.raw = raw
        self.backend = backend

    def execute(self, sql: str, parameters: tuple[Any, ...] | list[Any] = ()):
        statement = sql
        if self.backend == "postgres":
            if statement.strip().upper() == "BEGIN IMMEDIATE":
                statement = "SELECT 1"
            statement = statement.replace("?", "%s")
        else:
            statement = re.sub(r"\s+FOR\s+UPDATE\s*$", "", statement, flags=re.I)
        return self.raw.execute(statement, parameters)

    def executescript(self, sql: str) -> None:
        if self.backend == "postgres":
            self.raw.execute(sql)
        else:
            self.raw.executescript(sql)


@contextmanager
def connect() -> Iterator[GatewayConnection]:
    database_url = cloud_database_url()
    if database_url:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL is configured but psycopg is not installed.") from exc
        raw = psycopg.connect(database_url, row_factory=dict_row)
        con = GatewayConnection(raw, "postgres")
    else:
        path = cloud_db_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(path, timeout=30)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        con = GatewayConnection(raw, "sqlite")
    try:
        yield con
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        raw.close()


def execute_sql_batch(con: GatewayConnection, sql: str) -> None:
    """Execute simple schema statements without SQLite executescript auto-commits."""
    for statement in sql.split(";"):
        statement = statement.strip()
        if statement:
            con.execute(statement)


def sqlite_column_names(con: GatewayConnection, table: str) -> set[str]:
    return {row["name"] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}


def migration_001_initial_cloud_schema(con: GatewayConnection) -> None:
    execute_sql_batch(con, POSTGRES_SCHEMA if con.backend == "postgres" else SCHEMA)


def migration_002_account_workspaces(con: GatewayConnection) -> None:
    # CREATE IF NOT EXISTS statements fill tables that were introduced during
    # the account-storage cutover; explicit columns upgrade the earlier tables.
    execute_sql_batch(con, POSTGRES_SCHEMA if con.backend == "postgres" else SCHEMA)
    if con.backend == "postgres":
        for name, definition in {
            "email": "TEXT",
            "password_hash": "TEXT",
            "display_name": "TEXT NOT NULL DEFAULT ''",
            "account_created_at": "TEXT",
        }.items():
            con.execute(f"ALTER TABLE members ADD COLUMN IF NOT EXISTS {name} {definition}")
        con.execute("ALTER TABLE workspace_sessions ADD COLUMN IF NOT EXISTS database_id TEXT")
    else:
        member_columns = sqlite_column_names(con, "members")
        for name, definition in {
            "email": "TEXT",
            "password_hash": "TEXT",
            "display_name": "TEXT NOT NULL DEFAULT ''",
            "account_created_at": "TEXT",
        }.items():
            if name not in member_columns:
                con.execute(f"ALTER TABLE members ADD COLUMN {name} {definition}")
        if "database_id" not in sqlite_column_names(con, "workspace_sessions"):
            con.execute("ALTER TABLE workspace_sessions ADD COLUMN database_id TEXT")
    con.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_members_email_unique
        ON members(email)
        WHERE email IS NOT NULL
        """
    )


def migration_003_oauth_and_portraits(con: GatewayConnection) -> None:
    # The latest idempotent definitions create the OAuth tables for v2 stores.
    execute_sql_batch(con, POSTGRES_SCHEMA if con.backend == "postgres" else SCHEMA)
    if con.backend == "postgres":
        con.execute("ALTER TABLE public_profiles ADD COLUMN IF NOT EXISTS portrait_blob BYTEA")
        con.execute("ALTER TABLE public_profiles ADD COLUMN IF NOT EXISTS portrait_mime_type TEXT")
    else:
        profile_columns = sqlite_column_names(con, "public_profiles")
        for name, definition in {
            "portrait_blob": "BLOB",
            "portrait_mime_type": "TEXT",
        }.items():
            if name not in profile_columns:
                con.execute(f"ALTER TABLE public_profiles ADD COLUMN {name} {definition}")


CLOUD_MIGRATIONS = (
    (1, migration_001_initial_cloud_schema),
    (2, migration_002_account_workspaces),
    (3, migration_003_oauth_and_portraits),
)


def run_cloud_migrations(con: GatewayConnection) -> list[int]:
    """Upgrade one cloud store transactionally and return applied versions."""
    con.execute("BEGIN IMMEDIATE")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS cloud_schema_metadata (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            version INTEGER NOT NULL CHECK (version >= 0),
            updated_at TEXT NOT NULL
        )
        """
    )
    row = con.execute(
        "SELECT version FROM cloud_schema_metadata WHERE singleton=1"
    ).fetchone()
    if row is None:
        con.execute(
            """
            INSERT INTO cloud_schema_metadata(singleton, version, updated_at)
            VALUES (1, 0, ?)
            """,
            (utc_now(),),
        )
        current_version = 0
    else:
        current_version = int(row["version"])
    if current_version > CLOUD_SCHEMA_VERSION:
        raise RuntimeError(
            "Cloud database schema is newer than this VitaMine version supports "
            f"({current_version} > {CLOUD_SCHEMA_VERSION})."
        )
    applied: list[int] = []
    for version, migration in CLOUD_MIGRATIONS:
        if version <= current_version:
            continue
        migration(con)
        con.execute(
            """
            UPDATE cloud_schema_metadata
            SET version=?, updated_at=?
            WHERE singleton=1
            """,
            (version, utc_now()),
        )
        applied.append(version)
        LOGGER.info("cloud_schema_migration_applied backend=%s version=%d", con.backend, version)
    return applied


def initialize_database() -> None:
    with connect() as con:
        run_cloud_migrations(con)


def normalize_invite_code(code: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", code).upper()


def normalize_slug(slug: str) -> str:
    value = slug.strip().lower()
    if value in RESERVED_SLUGS or not SLUG_PATTERN.fullmatch(value):
        raise HTTPException(
            status_code=422,
            detail="Use 3–40 lowercase letters, numbers, or hyphens; the first and last character must be alphanumeric.",
        )
    return value


def bearer_token(authorization: str | None) -> str:
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not value.startswith(TOKEN_PREFIX):
        raise HTTPException(status_code=401, detail="A valid VitaMine device credential is required.")
    return value


def authenticated_member(
    authorization: str | None,
    session_cookie: str | None = None,
) -> sqlite3.Row:
    token = bearer_token(authorization) if authorization else session_cookie
    if not token or not token.startswith(TOKEN_PREFIX):
        raise HTTPException(status_code=401, detail="This device credential is invalid or revoked.")
    with connect() as con:
        row = con.execute(
            """
            SELECT m.*
            FROM device_credentials d
            JOIN members m ON m.id = d.member_id
            WHERE d.token_hash = ?
              AND d.revoked_at IS NULL
              AND m.revoked_at IS NULL
            """,
            (secret_hash(token),),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="This device credential is invalid or revoked.")
        now = utc_now()
        con.execute(
            "UPDATE device_credentials SET last_used_at=? WHERE token_hash=?",
            (now, secret_hash(token)),
        )
        con.execute("UPDATE members SET last_seen_at=? WHERE id=?", (now, row["id"]))
        return row


def account_member(
    authorization: str | None,
    session_cookie: str | None = None,
) -> sqlite3.Row:
    member = authenticated_member(authorization, session_cookie)
    if not str(member["email"] or "").strip() or not str(member["password_hash"] or "").strip():
        raise HTTPException(status_code=403, detail="Create your VitaMine account before storing databases.")
    return member


def issue_device_credential(con: GatewayConnection, member_id: str) -> str:
    token = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    now = utc_now()
    con.execute(
        "INSERT INTO device_credentials (member_id, token_hash, created_at, last_used_at) VALUES (?, ?, ?, ?)",
        (member_id, secret_hash(token), now, now),
    )
    return token


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=request.url.scheme == "https" or forwarded_proto == "https",
        samesite="lax",
        path="/",
    )


def request_ip_hash(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    address = forwarded or (request.client.host if request.client else "unknown")
    return secret_hash(f"ip:{address}")


def enforce_login_rate_limit(con: GatewayConnection, request: Request, email: str) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=LOGIN_WINDOW_MINUTES)).isoformat()
    email_hash = secret_hash(f"email:{email}")
    ip_hash = request_ip_hash(request)
    failures = con.execute(
        """
        SELECT COUNT(*) AS count
        FROM authentication_attempts
        WHERE email_hash=? AND ip_hash=? AND succeeded=0 AND attempted_at>=?
        """,
        (email_hash, ip_hash, cutoff),
    ).fetchone()["count"]
    if int(failures) >= LOGIN_MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again in 15 minutes.")


def record_login_attempt(con: GatewayConnection, request: Request, email: str, succeeded: bool) -> None:
    con.execute(
        """
        INSERT INTO authentication_attempts(email_hash, ip_hash, succeeded, attempted_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            secret_hash(f"email:{email}"),
            request_ip_hash(request),
            1 if succeeded else 0,
            utc_now(),
        ),
    )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    con.execute("DELETE FROM authentication_attempts WHERE attempted_at<?", (cutoff,))


def safe_database_name(value: str, fallback: str = "My CV") -> str:
    name = re.sub(r"\s+", " ", Path(str(value or "")).stem).strip()
    return (name or fallback)[:160]


def account_database_payload(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "filename": row["filename"],
        "size_bytes": int(row["size_bytes"] or 0),
        "revision": int(row["revision"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_opened_at": row["last_opened_at"],
    }


def public_snapshot(
    row: Any,
    *,
    include_internal: bool = False,
    include_portrait: bool = False,
) -> dict[str, Any]:
    snapshot = parsed_json_object(row["snapshot_json"])
    try:
        portrait_blob = row["portrait_blob"]
        portrait_mime_type = row["portrait_mime_type"]
    except (KeyError, IndexError):
        portrait_blob = None
        portrait_mime_type = None
    snapshot.pop("_portrait_blob", None)
    snapshot.pop("_portrait_mime_type", None)
    if not include_internal:
        snapshot.pop("source_database_id", None)
        snapshot.pop("_profile_title_custom", None)
    if portrait_blob:
        version = quote(str(row["updated_at"] or ""), safe="")
        snapshot["portrait_url"] = f"/api/public/{row['slug']}/portrait?v={version}"
    else:
        snapshot.pop("portrait_url", None)
    if include_portrait:
        snapshot["_portrait_blob"] = bytes(portrait_blob) if portrait_blob else None
        snapshot["_portrait_mime_type"] = str(portrait_mime_type or "") or None
    snapshot["slug"] = row["slug"]
    snapshot["published_at"] = row["published_at"]
    snapshot["updated_at"] = row["updated_at"]
    return snapshot


def serialized_public_profile(snapshot: dict[str, Any]) -> str:
    serialized = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
        raise HTTPException(status_code=413, detail="The public profile snapshot is too large.")
    return serialized


def preserve_public_profile_customizations(
    rebuilt: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    if current.get("_profile_title_custom") is True:
        title = re.sub(r"\s+", " ", str(current.get("profile_title") or "")).strip()[:200]
        if title:
            rebuilt["profile_title"] = title
            rebuilt["_profile_title_custom"] = True
    return rebuilt


@contextmanager
def materialized_database_blob(blob: Any) -> Iterator[Path]:
    handle = tempfile.NamedTemporaryFile(
        prefix=".vitamine-profile-",
        suffix=".sqlite",
        delete=False,
    )
    path = Path(handle.name)
    try:
        handle.write(bytes(blob))
        handle.close()
        path.chmod(0o600)
        validate_workspace_database(path)
        yield path
    finally:
        try:
            handle.close()
        except Exception:
            pass
        path.unlink(missing_ok=True)


def write_public_profile(
    con: GatewayConnection,
    *,
    slug: str,
    member_id: str,
    snapshot: dict[str, Any],
    now: str | None = None,
) -> str:
    now = now or utc_now()
    stored_snapshot = dict(snapshot)
    replace_portrait = "_portrait_blob" in stored_snapshot
    portrait_blob = stored_snapshot.pop("_portrait_blob", None)
    portrait_mime_type = stored_snapshot.pop("_portrait_mime_type", None)
    stored_snapshot.pop("portrait_url", None)
    serialized = serialized_public_profile(stored_snapshot)
    con.execute(
        """
        INSERT INTO public_profiles
          (slug, member_id, snapshot_json, portrait_blob, portrait_mime_type,
           created_at, updated_at, published_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
          snapshot_json=excluded.snapshot_json,
          portrait_blob=CASE
            WHEN ? THEN excluded.portrait_blob
            ELSE public_profiles.portrait_blob
          END,
          portrait_mime_type=CASE
            WHEN ? THEN excluded.portrait_mime_type
            ELSE public_profiles.portrait_mime_type
          END,
          updated_at=excluded.updated_at
        """,
        (
            slug,
            member_id,
            serialized,
            portrait_blob,
            portrait_mime_type,
            now,
            now,
            now,
            replace_portrait,
            replace_portrait,
        ),
    )
    return now


def refresh_public_profile_snapshot(
    con: GatewayConnection,
    *,
    member_id: str,
    database_id: str,
    snapshot_path: Path,
) -> bool:
    profile = con.execute(
        "SELECT * FROM public_profiles WHERE member_id=?",
        (member_id,),
    ).fetchone()
    if profile is None:
        return False
    current = public_snapshot(profile, include_internal=True)
    if str(current.get("source_database_id") or "") != database_id:
        return False
    rebuilt = build_public_profile_snapshot(
        snapshot_path,
        database_id=database_id,
        blocks=current.get("blocks"),
    )
    preserve_public_profile_customizations(rebuilt, current)
    write_public_profile(
        con,
        slug=str(profile["slug"]),
        member_id=member_id,
        snapshot=rebuilt,
    )
    return True


def validate_workspace_database(path: Path) -> None:
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
        raise HTTPException(status_code=400, detail="The selected file is not a readable SQLite database.") from exc
    missing = {"documents", "person", "cv_entries", "publications"} - tables
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"The selected file is not a VitaMine database; missing: {', '.join(sorted(missing))}.",
        )
    if not integrity or integrity[0] != "ok":
        raise HTTPException(status_code=400, detail="The database did not pass its integrity check.")


def available_worker_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def process_is_running(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def workspace_worker_is_running(row: Any) -> bool:
    """Return whether the recorded PID is the expected VitaMine worker.

    Linux can reuse a stopped worker's PID before the gateway handles the next
    request.  Merely probing the PID can therefore make the gateway proxy to a
    closed port—or, worse, terminate an unrelated process during cleanup.
    """
    pid = int(row["pid"] or 0)
    if not process_is_running(pid):
        return False
    managed_process = WORKER_PROCESSES.get(pid)
    if managed_process is not None:
        return managed_process.poll() is None
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if not proc_cmdline.parent.parent.exists():
        # Non-Linux development environments do not expose /proc. Keep the
        # historical liveness behavior there; production is Linux.
        return True
    try:
        arguments = [
            item.decode("utf-8", "replace")
            for item in proc_cmdline.read_bytes().split(b"\0")
            if item
        ]
    except OSError:
        return False
    try:
        port_index = arguments.index("--port")
        recorded_port = arguments[port_index + 1]
    except (ValueError, IndexError):
        return False
    return "vitamine.app:app" in arguments and recorded_port == str(row["port"])


def stop_workspace(row: Any, *, remove_files: bool = True) -> None:
    pid = int(row["pid"] or 0)
    process = WORKER_PROCESSES.pop(pid, None)
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    elif workspace_worker_is_running(row):
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            pass
    if remove_files:
        directory = Path(row["db_path"]).parent
        root = workspace_root()
        try:
            directory.relative_to(root)
        except ValueError:
            return
        if directory.exists():
            shutil.rmtree(directory)


def start_workspace_worker(row: sqlite3.Row) -> int:
    session_dir = Path(row["db_path"]).parent
    log_path = session_dir / "worker.log"
    env = {
        **os.environ,
        "VITAMINE_DB": row["db_path"],
        "VITAMINE_DATA": str(session_dir / "data"),
        "VITAMINE_OUTPUT": row["output_path"],
        "VITAMINE_PREFERENCES": str(session_dir / "preferences.json"),
        "VITAMINE_CLOUD_WORKER": "1",
    }
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "vitamine.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(row["port"]),
                "--proxy-headers",
            ],
            cwd=PROJECT.parent,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    health_url = f"http://127.0.0.1:{row['port']}/health"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise HTTPException(status_code=500, detail="The private VitaMine workspace could not start.")
        try:
            with httpx.Client(timeout=0.5) as client:
                response = client.get(health_url)
                if response.status_code == 200:
                    WORKER_PROCESSES[process.pid] = process
                    return process.pid
        except httpx.HTTPError:
            time.sleep(0.2)
    process.terminate()
    raise HTTPException(status_code=504, detail="The private VitaMine workspace took too long to start.")


def create_blank_workspace_database(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.executescript((PROJECT / "schema.sql").read_text(encoding="utf-8"))
        con.execute(
            """
            INSERT INTO documents
              (slug, title, source_path, source_format, imported_at, notes)
            VALUES
              ('manual_cv_database', 'Manual CV database edits', ?, 'sqlite',
               datetime('now'), 'Created in VitaMine.')
            """,
            (str(path),),
        )
        con.execute(
            """
            INSERT INTO person (id, full_name, display_name, raw_json)
            VALUES (1, '', '', '{}')
            """
        )
        con.execute(
            """
            INSERT INTO export_settings (profile, publication_limit, authorship_filter)
            VALUES ('short', 10, 'first_last'), ('ultrashort', 10, 'first_last')
            """
        )
        con.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ('onboarding_enabled', '1', datetime('now'))
            """
        )


def database_lock(database_id: str) -> threading.Lock:
    with DATABASE_LOCKS_GUARD:
        return DATABASE_LOCKS.setdefault(database_id, threading.Lock())


@contextmanager
def consistent_sqlite_snapshot(source_path: Path) -> Iterator[Path]:
    source_path = source_path.resolve()
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="The open VitaMine database is unavailable.")
    handle = tempfile.NamedTemporaryFile(
        prefix=".vitamine-snapshot-",
        suffix=".sqlite",
        dir=source_path.parent,
        delete=False,
    )
    snapshot_path = Path(handle.name)
    handle.close()
    try:
        with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True, timeout=30) as source:
            with sqlite3.connect(snapshot_path, timeout=30) as destination:
                source.backup(destination)
        validate_workspace_database(snapshot_path)
        yield snapshot_path
    finally:
        snapshot_path.unlink(missing_ok=True)


def sync_hosted_projections(con: GatewayConnection, cv_id: str, snapshot_path: Path) -> None:
    now = utc_now()
    con.execute("DELETE FROM hosted_cv_people WHERE cv_id=?", (cv_id,))
    con.execute("DELETE FROM hosted_cv_entries WHERE cv_id=?", (cv_id,))
    con.execute("DELETE FROM hosted_cv_publications WHERE cv_id=?", (cv_id,))
    with sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        person = source.execute("SELECT * FROM person WHERE id=1").fetchone()
        if person:
            keys = set(person.keys())
            con.execute(
                """
                INSERT INTO hosted_cv_people
                  (cv_id, full_name, display_name, position_title, work_email, orcid_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cv_id,
                    person["full_name"] if "full_name" in keys else None,
                    person["display_name"] if "display_name" in keys else None,
                    person["position_title"] if "position_title" in keys else None,
                    person["work_email"] if "work_email" in keys else None,
                    person["orcid_id"] if "orcid_id" in keys else None,
                    now,
                ),
            )
        for entry in source.execute(
            """
            SELECT id, section_key, start_date, end_date, title, organization, role, description
            FROM cv_entries
            """
        ):
            con.execute(
                """
                INSERT INTO hosted_cv_entries
                  (cv_id, source_id, section_key, start_date, end_date, title,
                   organization, role, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cv_id,
                    entry["id"],
                    entry["section_key"],
                    entry["start_date"],
                    entry["end_date"],
                    entry["title"],
                    entry["organization"],
                    entry["role"],
                    entry["description"],
                ),
            )
        publication_columns = {
            row["name"] for row in source.execute("PRAGMA table_info(publications)").fetchall()
        }
        citation_count = (
            "openalex_cited_by_count" if "openalex_cited_by_count" in publication_columns else "NULL"
        )
        suppressed = "suppress_display" if "suppress_display" in publication_columns else "0"
        publication_query = f"""
            SELECT id, category, authors, title, venue, year, doi, pmid, url,
                   {citation_count} AS openalex_cited_by_count,
                   {suppressed} AS suppress_display
            FROM publications
        """
        for publication in source.execute(publication_query):
            con.execute(
                """
                INSERT INTO hosted_cv_publications
                  (cv_id, source_id, category, authors, title, venue, year, doi,
                   pmid, url, cited_by_count, suppress_display)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cv_id,
                    publication["id"],
                    publication["category"],
                    publication["authors"],
                    publication["title"],
                    publication["venue"],
                    publication["year"],
                    publication["doi"],
                    publication["pmid"],
                    publication["url"],
                    publication["openalex_cited_by_count"],
                    int(publication["suppress_display"] or 0),
                ),
            )


def create_account_database_from_path(
    *,
    member_id: str,
    source_path: Path,
    name: str,
    filename: str,
    database_id: str | None = None,
) -> str:
    database_id = database_id or secrets.token_urlsafe(18)
    with database_lock(database_id):
        with consistent_sqlite_snapshot(source_path) as snapshot_path:
            content = snapshot_path.read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            now = utc_now()
            with connect() as con:
                con.execute(
                    """
                    INSERT INTO account_databases
                      (id, member_id, name, filename, sqlite_blob, checksum, revision,
                       size_bytes, created_at, updated_at, last_opened_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                    """,
                    (
                        database_id,
                        member_id,
                        safe_database_name(name),
                        Path(filename).stem[:160] + ".vitamine",
                        content,
                        checksum,
                        len(content),
                        now,
                        now,
                        now,
                    ),
                )
                sync_hosted_projections(con, database_id, snapshot_path)
    return database_id


def persist_database_snapshot(
    *,
    member_id: str,
    database_id: str,
    source_path: Path,
    expected_revision: int | None = None,
) -> int:
    with database_lock(database_id):
        with consistent_sqlite_snapshot(source_path) as snapshot_path:
            content = snapshot_path.read_bytes()
            checksum = hashlib.sha256(content).hexdigest()
            now = utc_now()
            with connect() as con:
                database = con.execute(
                    """
                    SELECT revision, checksum FROM account_databases
                    WHERE id=? AND member_id=? AND deleted_at IS NULL
                    FOR UPDATE
                    """,
                    (database_id, member_id),
                ).fetchone()
                if database is None:
                    raise HTTPException(status_code=404, detail="The saved VitaMine database no longer exists.")
                if expected_revision is not None and int(database["revision"]) != expected_revision:
                    raise RuntimeError(
                        "The saved CV changed while its background job was running; "
                        "the job result was not allowed to overwrite newer work."
                    )
                if hmac.compare_digest(str(database["checksum"]), checksum):
                    return int(database["revision"])
                revision = int(database["revision"]) + 1
                con.execute(
                    """
                    UPDATE account_databases
                    SET sqlite_blob=?, checksum=?, revision=?, size_bytes=?, updated_at=?
                    WHERE id=? AND member_id=? AND deleted_at IS NULL
                    """,
                    (
                        content,
                        checksum,
                        revision,
                        len(content),
                        now,
                        database_id,
                        member_id,
                    ),
                )
                sync_hosted_projections(con, database_id, snapshot_path)
                try:
                    refresh_public_profile_snapshot(
                        con,
                        member_id=member_id,
                        database_id=database_id,
                        snapshot_path=snapshot_path,
                    )
                except Exception:
                    # A public projection must never prevent a private CV save.
                    pass
    return revision


def persist_workspace_snapshot(row: Any) -> int | None:
    database_id = str(row["database_id"] or "")
    if not database_id:
        return None
    return persist_database_snapshot(
        member_id=str(row["member_id"]),
        database_id=database_id,
        source_path=Path(row["db_path"]),
    )


def materialize_account_database(database: Any, workspace_id: str) -> tuple[Path, Path]:
    session_dir = (workspace_root() / workspace_id).resolve()
    root = workspace_root()
    try:
        session_dir.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("Unsafe workspace path.") from exc
    session_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    session_dir.mkdir(mode=0o700)
    db_path = session_dir / "workspace.vitamine"
    db_path.write_bytes(bytes(database["sqlite_blob"]))
    db_path.chmod(0o600)
    validate_workspace_database(db_path)
    output_path = session_dir / "output"
    output_path.mkdir(mode=0o700)
    return db_path, output_path


def parsed_json_object(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def background_job_payload(row: Any, *, include_result: bool = True) -> dict[str, Any]:
    result = parsed_json_object(row["result_json"]) if include_result and row["result_json"] else None
    return {
        "id": row["id"],
        "database_id": row["database_id"],
        "kind": row["kind"],
        "status": row["status"],
        "progress": parsed_json_object(row["progress_json"]),
        "result": result,
        "error": row["error_message"] or "",
        "created_at": row["created_at"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "updated_at": row["updated_at"],
        "acknowledged": bool(row["acknowledged_at"]),
    }


def active_background_job(database_id: str) -> Any | None:
    with connect() as con:
        return con.execute(
            """
            SELECT * FROM background_jobs
            WHERE database_id=? AND status IN ('queued', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (database_id,),
        ).fetchone()


def workspace_has_active_job(row: Any) -> bool:
    database_id = str(row["database_id"] or "")
    return bool(database_id and active_background_job(database_id))


def create_background_job(
    *,
    workspace: Any,
    kind: str,
    payload: dict[str, Any],
    job_id: str,
) -> dict[str, Any]:
    database_id = str(workspace["database_id"] or "")
    if not database_id:
        raise HTTPException(status_code=409, detail="Save this CV to your account before starting a background job.")
    if active_background_job(database_id):
        raise HTTPException(
            status_code=409,
            detail="This CV already has a background process running.",
        )
    persist_workspace_snapshot(workspace)
    now = utc_now()
    with connect() as con:
        database = con.execute(
            """
            SELECT revision FROM account_databases
            WHERE id=? AND member_id=? AND deleted_at IS NULL
            """,
            (database_id, workspace["member_id"]),
        ).fetchone()
        if database is None:
            raise HTTPException(status_code=404, detail="The saved VitaMine database no longer exists.")
        existing = con.execute(
            """
            SELECT id FROM background_jobs
            WHERE database_id=? AND status IN ('queued', 'running')
            LIMIT 1
            """,
            (database_id,),
        ).fetchone()
        if existing:
            raise HTTPException(
                status_code=409,
                detail="This CV already has a background process running.",
            )
        progress = {
            "phase": "queued",
            "message": "Waiting for the VitaMine worker",
            "percent": 0,
        }
        con.execute(
            """
            INSERT INTO background_jobs
              (id, member_id, database_id, kind, status, base_revision, payload_json,
               progress_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                workspace["member_id"],
                database_id,
                kind,
                int(database["revision"]),
                json.dumps(payload, ensure_ascii=False),
                json.dumps(progress),
                now,
                now,
            ),
        )
        row = con.execute("SELECT * FROM background_jobs WHERE id=?", (job_id,)).fetchone()
    return background_job_payload(row)


def claim_next_background_job() -> Any | None:
    with connect() as con:
        row = con.execute(
            """
            SELECT * FROM background_jobs
            WHERE status='queued'
            ORDER BY created_at
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        now = utc_now()
        cursor = con.execute(
            """
            UPDATE background_jobs
            SET status='running', started_at=COALESCE(started_at, ?),
                heartbeat_at=?, updated_at=?,
                progress_json=?
            WHERE id=? AND status='queued'
            """,
            (
                now,
                now,
                now,
                json.dumps(
                    {
                        "phase": "preparing",
                        "message": "Preparing a private working copy",
                        "percent": 2,
                    }
                ),
                row["id"],
            ),
        )
        if cursor.rowcount != 1:
            return None
        return con.execute("SELECT * FROM background_jobs WHERE id=?", (row["id"],)).fetchone()


def update_background_job_progress(job_id: str, progress: dict[str, Any]) -> None:
    now = utc_now()
    with connect() as con:
        con.execute(
            """
            UPDATE background_jobs
            SET progress_json=?, heartbeat_at=?, updated_at=?
            WHERE id=? AND status='running'
            """,
            (json.dumps(progress, ensure_ascii=False), now, now, job_id),
        )


def refresh_open_workspace_from_job(job: Any, source_path: Path) -> None:
    database_id = str(job["database_id"])
    with database_lock(database_id):
        with connect() as con:
            workspace = con.execute(
                """
                SELECT * FROM workspace_sessions
                WHERE database_id=? AND member_id=?
                """,
                (database_id, job["member_id"]),
            ).fetchone()
        if workspace is None:
            return
        stop_workspace(workspace, remove_files=False)
        destination = Path(workspace["db_path"])
        if not destination.parent.exists():
            with connect() as con:
                con.execute("DELETE FROM workspace_sessions WHERE id=?", (workspace["id"],))
            return
        replacement = destination.with_name(".background-job-result.vitamine")
        shutil.copy2(source_path, replacement)
        replacement.chmod(0o600)
        validate_workspace_database(replacement)
        replacement.replace(destination)
        with connect() as con:
            con.execute(
                "UPDATE workspace_sessions SET pid=NULL, last_seen_at=? WHERE id=?",
                (utc_now(), workspace["id"]),
            )


def compact_job_result(result: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(result, ensure_ascii=False)
    if len(encoded.encode("utf-8")) <= 512_000:
        return result
    compact = dict(result)
    for key in ("stdout", "doi_stdout", "results"):
        compact.pop(key, None)
    compact["result_truncated"] = True
    return compact


def execute_background_job(job: Any) -> None:
    directory = (job_root() / str(job["id"])).resolve()
    try:
        directory.relative_to(job_root())
    except ValueError as exc:
        raise RuntimeError("Unsafe background-job path.") from exc
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    database_path = directory / "workspace.vitamine"
    result_path = directory / "result.json"
    progress_path = directory / "progress.json"
    log_path = directory / "worker.log"
    with connect() as con:
        database = con.execute(
            """
            SELECT sqlite_blob, revision FROM account_databases
            WHERE id=? AND member_id=? AND deleted_at IS NULL
            """,
            (job["database_id"], job["member_id"]),
        ).fetchone()
    if database is None:
        raise RuntimeError("The saved VitaMine database no longer exists.")
    if int(database["revision"]) != int(job["base_revision"]):
        raise RuntimeError("The CV changed before its background job could start.")
    database_path.write_bytes(bytes(database["sqlite_blob"]))
    database_path.chmod(0o600)
    validate_workspace_database(database_path)
    payload_path = directory / "payload.json"
    if not payload_path.exists():
        payload_path.write_text(str(job["payload_json"] or "{}"), encoding="utf-8")
        payload_path.chmod(0o600)
    env = {
        **os.environ,
        "VITAMINE_DB": str(database_path),
        "VITAMINE_DATA": str(directory / "data"),
        "VITAMINE_OUTPUT": str(directory / "output"),
        "VITAMINE_PREFERENCES": str(directory / "preferences.json"),
        "VITAMINE_CLOUD_WORKER": "1",
    }
    command = [
        sys.executable,
        "-m",
        "vitamine.cloud_job_runner",
        "--kind",
        str(job["kind"]),
        "--database",
        str(database_path),
        "--payload",
        str(payload_path),
        "--result",
        str(result_path),
        "--progress",
        str(progress_path),
    ]
    last_progress = ""
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            command,
            cwd=PROJECT.parent,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
        while process.poll() is None:
            if JOB_STOP.wait(1):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise RuntimeError("VitaMine restarted while the job was running; it will be retried.")
            if progress_path.exists():
                raw_progress = progress_path.read_text(encoding="utf-8", errors="replace")
                if raw_progress != last_progress:
                    progress = parsed_json_object(raw_progress)
                    if progress:
                        update_background_job_progress(str(job["id"]), progress)
                    last_progress = raw_progress
        returncode = int(process.returncode or 0)
    result = parsed_json_object(result_path.read_text(encoding="utf-8", errors="replace")) if result_path.exists() else {}
    if returncode != 0 or not result.get("ok"):
        message = str(result.get("error") or "The background process failed.")[-4000:]
        raise RuntimeError(message)
    persist_database_snapshot(
        member_id=str(job["member_id"]),
        database_id=str(job["database_id"]),
        source_path=database_path,
        expected_revision=int(job["base_revision"]),
    )
    refresh_open_workspace_from_job(job, database_path)
    result = compact_job_result(result)
    now = utc_now()
    with connect() as con:
        con.execute(
            """
            UPDATE background_jobs
            SET status='succeeded', progress_json=?, result_json=?, error_message=NULL,
                heartbeat_at=?, finished_at=?, updated_at=?
            WHERE id=?
            """,
            (
                json.dumps(
                    {
                        "phase": "completed",
                        "message": "Finished and saved to your account",
                        "percent": 100,
                    }
                ),
                json.dumps(result, ensure_ascii=False),
                now,
                now,
                now,
                job["id"],
            ),
        )
    shutil.rmtree(directory, ignore_errors=True)


def fail_background_job(job_id: str, error: Exception) -> None:
    message = str(error).strip()[-4000:] or "The background process failed."
    if JOB_STOP.is_set():
        with connect() as con:
            con.execute(
                """
                UPDATE background_jobs
                SET status='queued', started_at=NULL, heartbeat_at=NULL, updated_at=?,
                    progress_json=?
                WHERE id=? AND status='running'
                """,
                (
                    utc_now(),
                    json.dumps(
                        {
                            "phase": "queued",
                            "message": "Waiting to resume after a service restart",
                            "percent": 0,
                        }
                    ),
                    job_id,
                ),
            )
        return
    now = utc_now()
    with connect() as con:
        con.execute(
            """
            UPDATE background_jobs
            SET status='failed', error_message=?, heartbeat_at=?, finished_at=?,
                updated_at=?, progress_json=?
            WHERE id=?
            """,
            (
                message,
                now,
                now,
                now,
                json.dumps({"phase": "failed", "message": message, "percent": 100}),
                job_id,
            ),
        )
    shutil.rmtree(job_root() / job_id, ignore_errors=True)


def background_job_loop() -> None:
    while not JOB_STOP.is_set():
        job = claim_next_background_job()
        if job is None:
            JOB_STOP.wait(1)
            continue
        try:
            execute_background_job(job)
        except Exception as exc:
            fail_background_job(str(job["id"]), exc)


def recover_background_jobs() -> None:
    now = utc_now()
    with connect() as con:
        con.execute(
            """
            UPDATE background_jobs
            SET status='queued', started_at=NULL, heartbeat_at=NULL, updated_at=?,
                progress_json=?
            WHERE status='running'
            """,
            (
                now,
                json.dumps(
                    {
                        "phase": "queued",
                        "message": "Waiting to resume after a service restart",
                        "percent": 0,
                    }
                ),
            ),
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=JOB_RETENTION_DAYS)).isoformat()
        con.execute(
            """
            DELETE FROM background_jobs
            WHERE status IN ('succeeded', 'failed') AND finished_at<?
            """,
            (cutoff,),
        )


def database_download_response(content: bytes | memoryview, filename: str) -> Response:
    safe_filename = Path(filename).name
    return Response(
        content=bytes(content),
        media_type="application/vnd.sqlite3",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe_filename)}",
        },
    )


def promote_current_workspace(member_id: str) -> str | None:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM workspace_sessions WHERE member_id=?",
            (member_id,),
        ).fetchone()
    if not row or row["database_id"] or not Path(row["db_path"]).exists():
        return str(row["database_id"]) if row and row["database_id"] else None
    name = safe_database_name(row["original_filename"])
    filename = f"{name}.vitamine"
    database_id = create_account_database_from_path(
        member_id=member_id,
        source_path=Path(row["db_path"]),
        name=name,
        filename=filename,
    )
    with connect() as con:
        con.execute(
            """
            UPDATE workspace_sessions
            SET database_id=?, original_filename=?
            WHERE id=? AND member_id=?
            """,
            (database_id, filename, row["id"], member_id),
        )
    return database_id


def register_workspace(
    *,
    member_id: str,
    database_id: str | None,
    workspace_id: str,
    db_path: Path,
    output_path: Path,
    original_filename: str,
) -> str:
    token = f"vtw_{secrets.token_urlsafe(32)}"
    port = available_worker_port()
    now = utc_now()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=WORKSPACE_MAX_AGE)).isoformat()
    with connect() as con:
        previous = con.execute(
            "SELECT * FROM workspace_sessions WHERE member_id=?",
            (member_id,),
        ).fetchone()
    if previous:
        if (
            previous["database_id"]
            and Path(previous["db_path"]).exists()
            and not workspace_has_active_job(previous)
        ):
            persist_workspace_snapshot(previous)
        stop_workspace(previous)
        with connect() as con:
            con.execute("DELETE FROM workspace_sessions WHERE id=?", (previous["id"],))
    with connect() as con:
        con.execute(
            """
            INSERT INTO workspace_sessions
              (id, member_id, database_id, token_hash, db_path, output_path, port, pid,
               original_filename, created_at, last_seen_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                member_id,
                database_id,
                secret_hash(token),
                str(db_path),
                str(output_path),
                port,
                original_filename,
                now,
                now,
                expires,
            ),
        )
        row = con.execute(
            "SELECT * FROM workspace_sessions WHERE id=?",
            (workspace_id,),
        ).fetchone()
        try:
            pid = start_workspace_worker(row)
        except Exception:
            con.execute("DELETE FROM workspace_sessions WHERE id=?", (workspace_id,))
            stop_workspace(row)
            raise
        con.execute("UPDATE workspace_sessions SET pid=? WHERE id=?", (pid, workspace_id))
        if database_id:
            con.execute(
                "UPDATE account_databases SET last_opened_at=? WHERE id=? AND member_id=?",
                (now, database_id, member_id),
            )
    return token


def workspace_cookie_response(request: Request, token: str, payload: dict[str, Any]) -> JSONResponse:
    response = JSONResponse(payload)
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    response.set_cookie(
        key=WORKSPACE_COOKIE,
        value=token,
        max_age=WORKSPACE_MAX_AGE,
        httponly=True,
        secure=request.url.scheme == "https" or forwarded_proto == "https",
        samesite="lax",
        path="/",
    )
    return response


def cleanup_expired_workspaces() -> int:
    initialize_database()
    with connect() as con:
        workspaces = con.execute("SELECT * FROM workspace_sessions").fetchall()
        expired = [
            row
            for row in workspaces
            if row["expires_at"] <= utc_now() or not Path(row["db_path"]).exists()
        ]
        for row in expired:
            if (
                row["database_id"]
                and Path(row["db_path"]).exists()
                and not workspace_has_active_job(row)
            ):
                persist_workspace_snapshot(row)
            stop_workspace(row)
            con.execute("DELETE FROM workspace_sessions WHERE id=?", (row["id"],))
    return len(expired)


def workspace_cleanup_loop() -> None:
    while not CLEANUP_STOP.wait(15 * 60):
        cleanup_expired_workspaces()


def workspace_for_request(request: Request, authorization: str | None = None) -> Any:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    token = request.cookies.get(WORKSPACE_COOKIE, "")
    if not token:
        raise HTTPException(status_code=401, detail="Open a VitaMine database to start a workspace.")
    with connect() as con:
        row = con.execute(
            """
            SELECT *
            FROM workspace_sessions
            WHERE member_id=? AND token_hash=? AND expires_at>?
            """,
            (member["id"], secret_hash(token), utc_now()),
        ).fetchone()
        if row is None or not Path(row["db_path"]).exists():
            raise HTTPException(status_code=401, detail="This private workspace has expired.")
        if row["database_id"]:
            owned_database = con.execute(
                """
                SELECT id FROM account_databases
                WHERE id=? AND member_id=? AND deleted_at IS NULL
                """,
                (row["database_id"], member["id"]),
            ).fetchone()
            if owned_database is None:
                raise HTTPException(status_code=401, detail="This saved database is no longer available.")
        if not workspace_worker_is_running(row):
            database_id = str(row["database_id"] or "")
            if database_id:
                with database_lock(database_id):
                    row = con.execute(
                        "SELECT * FROM workspace_sessions WHERE id=?",
                        (row["id"],),
                    ).fetchone()
                    if row is None or not Path(row["db_path"]).exists():
                        raise HTTPException(status_code=401, detail="This private workspace has expired.")
                    if not workspace_worker_is_running(row):
                        pid = start_workspace_worker(row)
                        con.execute("UPDATE workspace_sessions SET pid=? WHERE id=?", (pid, row["id"]))
                        row = con.execute(
                            "SELECT * FROM workspace_sessions WHERE id=?",
                            (row["id"],),
                        ).fetchone()
            else:
                pid = start_workspace_worker(row)
                con.execute("UPDATE workspace_sessions SET pid=? WHERE id=?", (pid, row["id"]))
                row = con.execute("SELECT * FROM workspace_sessions WHERE id=?", (row["id"],)).fetchone()
        now = utc_now()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=WORKSPACE_MAX_AGE)).isoformat()
        con.execute(
            "UPDATE workspace_sessions SET last_seen_at=?, expires_at=? WHERE id=?",
            (now, expires, row["id"]),
        )
        if row["database_id"]:
            con.execute(
                "UPDATE account_databases SET last_opened_at=? WHERE id=? AND member_id=?",
                (now, row["database_id"], member["id"]),
            )
        return row


async def proxy_to_workspace(request: Request, worker_path: str) -> Response:
    row = workspace_for_request(request, request.headers.get("authorization"))
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and row["database_id"]:
        running_job = active_background_job(str(row["database_id"]))
        if running_job:
            raise HTTPException(
                status_code=409,
                detail="This CV is being updated by a background process. It will unlock automatically when the process finishes.",
            )
    url = f"http://127.0.0.1:{row['port']}/{worker_path.lstrip('/')}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    excluded_request_headers = {"host", "content-length", "connection"}
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in excluded_request_headers
    }
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
            upstream = await client.request(request.method, url, headers=headers, content=body)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="The private VitaMine workspace is unavailable.") from exc
    mapping_completed = False
    if (
        request.method == "GET"
        and worker_path.lstrip("/") == "api/person/institution-mapping-status"
        and upstream.status_code < 400
    ):
        try:
            mapping_completed = bool(upstream.json().get("mapped"))
        except (TypeError, ValueError):
            mapping_completed = False
    if (
        (request.method in {"POST", "PUT", "PATCH", "DELETE"} or mapping_completed)
        and upstream.status_code < 400
        and row["database_id"]
    ):
        persist_workspace_snapshot(row)
    excluded_response_headers = {
        "content-encoding",
        "content-length",
        "connection",
        "transfer-encoding",
    }
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in excluded_response_headers
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


async def apply_authenticated_orcid_to_workspace(row: Any, orcid_id: str) -> None:
    if not row["database_id"]:
        raise HTTPException(status_code=409, detail="Open a saved VitaMine CV before connecting ORCID.")
    if active_background_job(str(row["database_id"])):
        raise HTTPException(
            status_code=409,
            detail="Wait for the current background process to finish before linking ORCID.",
        )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
            response = await client.post(
                f"http://127.0.0.1:{row['port']}/api/orcid/link",
                json={"orcid_id": orcid_id},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="ORCID was authenticated, but VitaMine could not link it to this CV.",
        ) from exc
    persist_workspace_snapshot(row)


def orcid_oauth_result_redirect(result: str) -> RedirectResponse:
    return RedirectResponse(url=f"/?{urlencode({'orcid_oauth': result})}", status_code=303)


app = FastAPI(
    title="VitaMine Cloud",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.on_event("startup")
def startup() -> None:
    initialize_database()
    recover_background_jobs()
    cleanup_expired_workspaces()
    CLEANUP_STOP.clear()
    JOB_STOP.clear()
    threading.Thread(
        target=workspace_cleanup_loop,
        name="vitamine-workspace-cleanup",
        daemon=True,
    ).start()
    if os.environ.get("VITAMINE_DISABLE_JOB_RUNNER") != "1":
        job_root().mkdir(parents=True, exist_ok=True, mode=0o700)
        threading.Thread(
            target=background_job_loop,
            name="vitamine-background-jobs",
            daemon=True,
        ).start()


@app.on_event("shutdown")
def shutdown() -> None:
    CLEANUP_STOP.set()
    JOB_STOP.set()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin:
            expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
            forwarded_proto = request.headers.get("x-forwarded-proto")
            if forwarded_proto:
                expected = f"{forwarded_proto}://{request.headers.get('host', '')}"
            if origin.rstrip("/") != expected.rstrip("/"):
                return JSONResponse({"detail": "Cross-site request rejected."}, status_code=403)
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
        "frame-ancestors *; base-uri 'none'; form-action 'self'"
    )
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.get("/health")
def health() -> dict[str, Any]:
    with connect() as con:
        con.execute("SELECT 1").fetchone()
    return {"ok": True, "app": "vitamine-cloud", "stores_private_databases": True}


@app.get("/gateway/orcid/oauth/status")
def orcid_oauth_status(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    configured = orcid_oauth_config() is not None
    with connect() as con:
        row = con.execute(
            """
            SELECT orcid_id, display_name, scope, verified_at, expires_at
            FROM orcid_oauth_connections
            WHERE member_id=?
            """,
            (member["id"],),
        ).fetchone()
    return {
        "ok": True,
        "configured": configured,
        "connected": row is not None,
        "orcid_id": str(row["orcid_id"]) if row is not None else "",
        "display_name": str(row["display_name"] or "") if row is not None else "",
        "scope": str(row["scope"] or "") if row is not None else "",
        "verified_at": str(row["verified_at"]) if row is not None else None,
        "expires_at": str(row["expires_at"]) if row is not None and row["expires_at"] else None,
    }


@app.post("/gateway/orcid/oauth/start")
def start_orcid_oauth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    config = orcid_oauth_config()
    if config is None:
        raise HTTPException(status_code=503, detail="ORCID sign-in is not configured.")
    row = workspace_for_request(request, authorization)
    if not row["database_id"]:
        raise HTTPException(status_code=409, detail="Open a saved VitaMine CV before connecting ORCID.")
    if active_background_job(str(row["database_id"])):
        raise HTTPException(
            status_code=409,
            detail="Wait for the current background process to finish before connecting ORCID.",
        )
    state = create_oauth_authorization_state(str(row["member_id"]), str(row["database_id"]))
    query = urlencode(
        {
            "client_id": config["client_id"],
            "response_type": "code",
            "scope": "/authenticate",
            "redirect_uri": config["redirect_uri"],
            "state": state,
        }
    )
    return {
        "ok": True,
        "authorization_url": f"{config['base_url']}/oauth/authorize?{query}",
    }


@app.get("/gateway/orcid/oauth/callback")
async def complete_orcid_oauth(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    authorization: str | None = Header(default=None),
) -> RedirectResponse:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    authorization_state = consume_oauth_authorization_state(state, str(member["id"]))
    if error:
        return orcid_oauth_result_redirect("cancelled")
    if not code or len(code) > 1000:
        return orcid_oauth_result_redirect("error")
    workspace = workspace_for_request(request, authorization)
    if not hmac.compare_digest(
        str(workspace["database_id"] or ""),
        str(authorization_state["database_id"]),
    ):
        return orcid_oauth_result_redirect("workspace-changed")
    try:
        token = await exchange_orcid_authorization_code(code)
        store_orcid_oauth_connection(
            member_id=str(member["id"]),
            database_id=str(workspace["database_id"]),
            token=token,
        )
        await apply_authenticated_orcid_to_workspace(workspace, str(token["orcid_id"]))
    except HTTPException:
        return orcid_oauth_result_redirect("link-error")
    return orcid_oauth_result_redirect("connected")


@app.post("/gateway/orcid/oauth/link-current")
async def link_authenticated_orcid_to_current_cv(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    workspace = workspace_for_request(request, authorization)
    with connect() as con:
        connection = con.execute(
            "SELECT orcid_id FROM orcid_oauth_connections WHERE member_id=?",
            (workspace["member_id"],),
        ).fetchone()
    if connection is None:
        raise HTTPException(status_code=409, detail="Connect your ORCID account first.")
    await apply_authenticated_orcid_to_workspace(workspace, str(connection["orcid_id"]))
    return {"ok": True, "orcid_id": str(connection["orcid_id"])}


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> Response:
    if request.cookies.get(WORKSPACE_COOKIE):
        try:
            response = await proxy_to_workspace(request, "")
            response.headers["Cache-Control"] = "no-store"
            response.headers["Vary"] = "Cookie"
            return response
        except HTTPException as exc:
            if exc.status_code not in {401, 403}:
                raise
    return FileResponse(
        CLOUD_STATIC / "account.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store", "Vary": "Cookie"},
    )


@app.get("/gateway/workspace/enter")
def enter_workspace(
    request: Request,
    authorization: str | None = Header(default=None),
) -> RedirectResponse:
    workspace_for_request(request, authorization)
    return RedirectResponse(
        url="/",
        status_code=303,
        headers={"Cache-Control": "no-store", "Vary": "Cookie"},
    )


@app.post("/gateway/workspace/open")
async def open_workspace(
    request: Request,
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    original_filename = Path(file.filename or "workspace.vitamine").name
    if Path(original_filename).suffix.lower() not in {".vitamine", ".sqlite", ".db"}:
        raise HTTPException(status_code=422, detail="Choose a .vitamine, .sqlite, or .db file.")

    with connect() as con:
        database_count = con.execute(
            "SELECT COUNT(*) AS count FROM account_databases WHERE member_id=? AND deleted_at IS NULL",
            (member["id"],),
        ).fetchone()["count"]
    if int(database_count) >= 20:
        raise HTTPException(status_code=409, detail="This early-access account already has 20 databases.")
    database_id = secrets.token_urlsafe(18)
    workspace_id = secrets.token_urlsafe(18)
    session_dir = (workspace_root() / workspace_id).resolve()
    session_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    session_dir.mkdir(mode=0o700)
    db_path = session_dir / "workspace.vitamine"
    output_path = session_dir / "output"
    output_path.mkdir(mode=0o700)
    total = 0
    try:
        with db_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_DATABASE_BYTES:
                    raise HTTPException(status_code=413, detail="The VitaMine database exceeds the 200 MB session limit.")
                destination.write(chunk)
        validate_workspace_database(db_path)
        name = safe_database_name(original_filename)
        stored_filename = f"{name}.vitamine"
        create_account_database_from_path(
            member_id=member["id"],
            source_path=db_path,
            name=name,
            filename=stored_filename,
            database_id=database_id,
        )
        token = register_workspace(
            member_id=member["id"],
            database_id=database_id,
            workspace_id=workspace_id,
            db_path=db_path,
            output_path=output_path,
            original_filename=stored_filename,
        )
    except Exception:
        shutil.rmtree(session_dir, ignore_errors=True)
        with connect() as con:
            con.execute("DELETE FROM account_databases WHERE id=? AND member_id=?", (database_id, member["id"]))
        raise
    finally:
        await file.close()

    return workspace_cookie_response(
        request,
        token,
        {"ok": True, "database_id": database_id, "filename": stored_filename, "persistent": True},
    )


@app.post("/gateway/workspace/new")
def new_workspace(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        database_count = con.execute(
            "SELECT COUNT(*) AS count FROM account_databases WHERE member_id=? AND deleted_at IS NULL",
            (member["id"],),
        ).fetchone()["count"]
    if int(database_count) >= 20:
        raise HTTPException(status_code=409, detail="This early-access account already has 20 databases.")
    database_id = secrets.token_urlsafe(18)
    workspace_id = secrets.token_urlsafe(18)
    session_dir = (workspace_root() / workspace_id).resolve()
    session_dir.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    session_dir.mkdir(mode=0o700)
    db_path = session_dir / "workspace.vitamine"
    output_path = session_dir / "output"
    output_path.mkdir(mode=0o700)
    try:
        create_blank_workspace_database(db_path)
        create_account_database_from_path(
            member_id=member["id"],
            source_path=db_path,
            name="My CV",
            filename="My CV.vitamine",
            database_id=database_id,
        )
        token = register_workspace(
            member_id=member["id"],
            database_id=database_id,
            workspace_id=workspace_id,
            db_path=db_path,
            output_path=output_path,
            original_filename="My CV.vitamine",
        )
    except Exception:
        shutil.rmtree(session_dir, ignore_errors=True)
        with connect() as con:
            con.execute("DELETE FROM account_databases WHERE id=? AND member_id=?", (database_id, member["id"]))
        raise
    return workspace_cookie_response(
        request,
        token,
        {"ok": True, "database_id": database_id, "filename": "My CV.vitamine", "persistent": True},
    )


@app.post("/gateway/databases/{database_id}/open")
def open_account_database(
    database_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        database = con.execute(
            """
            SELECT * FROM account_databases
            WHERE id=? AND member_id=? AND deleted_at IS NULL
            """,
            (database_id, member["id"]),
        ).fetchone()
    if database is None:
        raise HTTPException(status_code=404, detail="That saved database could not be found.")
    workspace_id = secrets.token_urlsafe(18)
    try:
        db_path, output_path = materialize_account_database(database, workspace_id)
        token = register_workspace(
            member_id=member["id"],
            database_id=database["id"],
            workspace_id=workspace_id,
            db_path=db_path,
            output_path=output_path,
            original_filename=database["filename"],
        )
    except Exception:
        shutil.rmtree(workspace_root() / workspace_id, ignore_errors=True)
        raise
    return workspace_cookie_response(
        request,
        token,
        {
            "ok": True,
            "database_id": database["id"],
            "filename": database["filename"],
            "persistent": True,
        },
    )


@app.get("/gateway/workspace/status")
def workspace_status(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    row = workspace_for_request(request, authorization)
    with connect() as con:
        member = con.execute(
            "SELECT email, display_name FROM members WHERE id=?",
            (row["member_id"],),
        ).fetchone()
    return {
        "ok": True,
        "database_id": row["database_id"],
        "filename": row["original_filename"],
        "expires_at": row["expires_at"],
        "persistent": bool(row["database_id"]),
        "background_jobs": True,
        "account": {
            "email": member["email"] if member else "",
            "display_name": member["display_name"] if member else "",
        },
    }


@app.get("/gateway/workspace/download")
def download_workspace(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    row = workspace_for_request(request, authorization)
    if workspace_has_active_job(row):
        raise HTTPException(
            status_code=409,
            detail="Wait for the background process to finish before downloading this CV.",
        )
    persist_workspace_snapshot(row)
    filename = Path(row["original_filename"]).stem + ".vitamine"
    with connect() as con:
        database = con.execute(
            """
            SELECT sqlite_blob FROM account_databases
            WHERE id=? AND member_id=? AND deleted_at IS NULL
            """,
            (row["database_id"], row["member_id"]),
        ).fetchone()
    if database is None:
        raise HTTPException(status_code=404, detail="The saved VitaMine database no longer exists.")
    return database_download_response(database["sqlite_blob"], filename)


@app.delete("/gateway/workspace")
def close_workspace(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    row = workspace_for_request(request, authorization)
    if not workspace_has_active_job(row):
        persist_workspace_snapshot(row)
    stop_workspace(row)
    with connect() as con:
        con.execute("DELETE FROM workspace_sessions WHERE id=?", (row["id"],))
    response = JSONResponse({"ok": True, "database_preserved": bool(row["database_id"])})
    response.delete_cookie(WORKSPACE_COOKIE, path="/")
    return response


def cloud_cv_import_name(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".docx", ".pdf", ".txt", ".md"}:
        raise HTTPException(status_code=400, detail="Please upload a DOCX, PDF, TXT, or Markdown CV.")
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(filename).stem).strip("._-") or "uploaded-cv"
    return f"{stem}{suffix}"


@app.post("/api/cloud/jobs/cv-import")
async def queue_cv_import_job(
    request: Request,
    files: list[UploadFile] = File(...),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    workspace = workspace_for_request(request, authorization)
    if not files:
        raise HTTPException(status_code=400, detail="Please choose at least one CV document.")
    job_id = secrets.token_urlsafe(18)
    directory = (job_root() / job_id).resolve()
    uploads = directory / "uploads"
    try:
        directory.relative_to(job_root())
        uploads.mkdir(parents=True, mode=0o700)
        payload_files: list[dict[str, str]] = []
        total_bytes = 0
        for index, file in enumerate(files, start=1):
            original_name = Path(file.filename or f"uploaded-cv-{index}").name
            safe_name = cloud_cv_import_name(original_name)
            stored_name = f"{index}-{safe_name}"
            destination = uploads / stored_name
            with destination.open("wb") as handle:
                while True:
                    chunk = file.file.read(1024 * 1024)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > MAX_JOB_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail="The selected CV documents exceed the 50 MB upload limit.",
                        )
                    handle.write(chunk)
            destination.chmod(0o600)
            payload_files.append(
                {"stored_name": stored_name, "original_name": original_name}
            )
        job = create_background_job(
            workspace=workspace,
            kind="cv_import",
            payload={"files": payload_files},
            job_id=job_id,
        )
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        for file in files:
            await file.close()
    return JSONResponse(
        {"ok": True, "background": True, "job": job},
        status_code=202,
    )


@app.post("/api/cloud/jobs/enrich-cv")
def queue_enrichment_job(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    workspace = workspace_for_request(request, authorization)
    job_id = secrets.token_urlsafe(18)
    job = create_background_job(
        workspace=workspace,
        kind="enrich_cv",
        payload={},
        job_id=job_id,
    )
    return JSONResponse(
        {"ok": True, "background": True, "job": job},
        status_code=202,
    )


@app.get("/api/cloud/jobs")
def list_background_jobs(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        rows = con.execute(
            """
            SELECT * FROM background_jobs
            WHERE member_id=?
              AND (status IN ('queued', 'running') OR acknowledged_at IS NULL)
            ORDER BY created_at DESC
            LIMIT 30
            """,
            (member["id"],),
        ).fetchall()
    return {"jobs": [background_job_payload(row) for row in rows]}


@app.get("/api/cloud/jobs/{job_id}")
def get_background_job(
    job_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        row = con.execute(
            "SELECT * FROM background_jobs WHERE id=? AND member_id=?",
            (job_id, member["id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Background job not found.")
    return {"job": background_job_payload(row)}


@app.post("/api/cloud/jobs/{job_id}/acknowledge")
def acknowledge_background_job(
    job_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        cursor = con.execute(
            """
            UPDATE background_jobs
            SET acknowledged_at=?, updated_at=?
            WHERE id=? AND member_id=? AND status IN ('succeeded', 'failed')
            """,
            (utc_now(), utc_now(), job_id, member["id"]),
        )
    if cursor.rowcount != 1:
        raise HTTPException(status_code=404, detail="Completed background job not found.")
    return {"ok": True}


@app.get("/api/account/databases")
def list_account_databases(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        rows = con.execute(
            """
            SELECT * FROM account_databases
            WHERE member_id=? AND deleted_at IS NULL
            ORDER BY COALESCE(last_opened_at, updated_at) DESC, created_at DESC
            """,
            (member["id"],),
        ).fetchall()
        profile_row = con.execute(
            "SELECT * FROM public_profiles WHERE member_id=?",
            (member["id"],),
        ).fetchone()
    profile = public_snapshot(profile_row, include_internal=True) if profile_row else None
    return {
        "account": {
            "email": member["email"],
            "display_name": member["display_name"],
        },
        "databases": [account_database_payload(row) for row in rows],
        "profile": {
            "slug": profile["slug"],
            "source_database_id": profile.get("source_database_id", ""),
            "updated_at": profile["updated_at"],
        } if profile else None,
    }


@app.patch("/api/account/databases/{database_id}")
def rename_account_database(
    database_id: str,
    payload: DatabaseRename,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    name = safe_database_name(payload.name)
    filename = f"{name}.vitamine"
    now = utc_now()
    with connect() as con:
        cursor = con.execute(
            """
            UPDATE account_databases
            SET name=?, filename=?, updated_at=?
            WHERE id=? AND member_id=? AND deleted_at IS NULL
            """,
            (name, filename, now, database_id, member["id"]),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="That saved database could not be found.")
        con.execute(
            """
            UPDATE workspace_sessions
            SET original_filename=?
            WHERE database_id=? AND member_id=?
            """,
            (filename, database_id, member["id"]),
        )
    return {"ok": True, "id": database_id, "name": name, "filename": filename, "updated_at": now}


@app.get("/api/account/databases/{database_id}/download")
def download_account_database(
    database_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> Response:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    if active_background_job(database_id):
        raise HTTPException(
            status_code=409,
            detail="Wait for this CV's background process to finish before downloading it.",
        )
    with connect() as con:
        workspace = con.execute(
            "SELECT * FROM workspace_sessions WHERE database_id=? AND member_id=?",
            (database_id, member["id"]),
        ).fetchone()
    if workspace and Path(workspace["db_path"]).exists():
        persist_workspace_snapshot(workspace)
    with connect() as con:
        row = con.execute(
            """
            SELECT * FROM account_databases
            WHERE id=? AND member_id=? AND deleted_at IS NULL
            """,
            (database_id, member["id"]),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="That saved database could not be found.")
    return database_download_response(row["sqlite_blob"], row["filename"])


@app.delete("/api/account/databases/{database_id}")
def delete_account_database(
    database_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    if active_background_job(database_id):
        raise HTTPException(
            status_code=409,
            detail="Wait for this CV's background process to finish before deleting it.",
        )
    with connect() as con:
        database = con.execute(
            """
            SELECT * FROM account_databases
            WHERE id=? AND member_id=? AND deleted_at IS NULL
            """,
            (database_id, member["id"]),
        ).fetchone()
        if database is None:
            raise HTTPException(status_code=404, detail="That saved database could not be found.")
        profile_row = con.execute(
            "SELECT * FROM public_profiles WHERE member_id=?",
            (member["id"],),
        ).fetchone()
        if profile_row:
            profile = public_snapshot(profile_row, include_internal=True)
            if str(profile.get("source_database_id") or "") == database_id:
                raise HTTPException(
                    status_code=409,
                    detail="This CV supplies your public profile. Unpublish the profile before deleting the CV.",
                )
        workspace = con.execute(
            "SELECT * FROM workspace_sessions WHERE database_id=? AND member_id=?",
            (database_id, member["id"]),
        ).fetchone()
        if workspace:
            stop_workspace(workspace)
            con.execute("DELETE FROM workspace_sessions WHERE id=?", (workspace["id"],))
        con.execute(
            "DELETE FROM account_databases WHERE id=? AND member_id=?",
            (database_id, member["id"]),
        )
    response = JSONResponse({"ok": True})
    if workspace:
        response.delete_cookie(WORKSPACE_COOKIE, path="/")
    return response


@app.get("/assets/vitamine-logo.png", response_class=FileResponse)
def vitamine_logo() -> FileResponse:
    return FileResponse(LOGO_PATH, media_type="image/png")


@app.get("/assets/invite-arrow.png", response_class=FileResponse)
def invite_arrow() -> FileResponse:
    return FileResponse(INVITE_ARROW_PATH, media_type="image/png")


@app.get("/assets/account.css", response_class=FileResponse)
def account_css() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "account.css", media_type="text/css")


@app.get("/assets/account.js", response_class=FileResponse)
def account_javascript() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "account.js", media_type="text/javascript")


@app.get("/assets/public-profile.css", response_class=FileResponse)
def public_profile_css() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "public-profile.css", media_type="text/css")


@app.get("/assets/public-profile.js", response_class=FileResponse)
def public_profile_javascript() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "public-profile.js", media_type="text/javascript")


@app.get("/assets/workspace.css", response_class=FileResponse)
def workspace_css() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "workspace.css", media_type="text/css")


@app.get("/assets/workspace.js", response_class=FileResponse)
def workspace_javascript() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "workspace.js", media_type="text/javascript")


@app.get("/assets/sqlite/index.mjs", response_class=FileResponse)
def sqlite_module() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "vendor" / "sqlite" / "index.mjs", media_type="text/javascript")


@app.get("/assets/sqlite/sqlite3.wasm", response_class=FileResponse)
def sqlite_wasm() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "vendor" / "sqlite" / "sqlite3.wasm", media_type="application/wasm")


@app.get("/workspace", response_class=FileResponse)
def workspace(request: Request, authorization: str | None = Header(default=None)) -> FileResponse:
    account_member(authorization, request.cookies.get(SESSION_COOKIE))
    return FileResponse(CLOUD_STATIC / "workspace.html", media_type="text/html")


@app.post("/api/invitations/redeem")
async def redeem_invitation(request: Request) -> JSONResponse:
    content_type = request.headers.get("content-type", "").lower()
    code: Any = None
    if "multipart/form-data" in content_type:
        form = await request.form()
        code = form.get("code")
    else:
        raw_body = await request.body()
        if len(raw_body) > 4096:
            raise HTTPException(status_code=413, detail="The invitation submission is too large.")
        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            payload = None
        if isinstance(payload, dict):
            code = payload.get("code")
        elif isinstance(payload, str):
            code = payload
        if code is None and "application/x-www-form-urlencoded" in content_type:
            from urllib.parse import parse_qs

            values = parse_qs(raw_body.decode("utf-8", errors="replace"))
            code = (values.get("code") or [None])[0]
    if not isinstance(code, str):
        raise HTTPException(status_code=422, detail="Enter the invitation code shown in your invitation.")
    normalized = normalize_invite_code(code)
    if len(normalized) < 8:
        raise HTTPException(status_code=422, detail="That invitation code is not valid.")
    code_hash = secret_hash(normalized)
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        invitation = con.execute(
            """
            SELECT *
            FROM invitations
            WHERE code_hash=?
              AND revoked_at IS NULL
              AND use_count < max_uses
              AND (expires_at IS NULL OR expires_at > ?)
            FOR UPDATE
            """,
            (code_hash, utc_now()),
        ).fetchone()
        if invitation is None:
            raise HTTPException(status_code=403, detail="This invitation is invalid, expired, or fully used.")
        member_id = secrets.token_urlsafe(18)
        now = utc_now()
        con.execute(
            "INSERT INTO members (id, invitation_id, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (member_id, invitation["id"], now, now),
        )
        token = issue_device_credential(con, member_id)
        con.execute(
            "UPDATE invitations SET use_count=use_count+1 WHERE id=?",
            (invitation["id"],),
        )
    response = JSONResponse({"ok": True, "member_id": member_id})
    set_session_cookie(response, request, token)
    return response


@app.post("/api/account/register")
def register_account(
    payload: AccountRegistration,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = authenticated_member(authorization, request.cookies.get(SESSION_COOKIE))
    if member["email"] or member["password_hash"]:
        raise HTTPException(status_code=409, detail="This invitation already belongs to an account.")
    email = normalize_email(payload.email)
    display_name = re.sub(r"\s+", " ", payload.display_name).strip()[:160]
    password_hash = hash_password(payload.password)
    now = utc_now()
    try:
        with connect() as con:
            cursor = con.execute(
                """
                UPDATE members
                SET email=?, password_hash=?, display_name=?, account_created_at=?, last_seen_at=?
                WHERE id=? AND email IS NULL AND password_hash IS NULL
                """,
                (email, password_hash, display_name, now, now, member["id"]),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=409, detail="This invitation already belongs to an account.")
    except Exception as exc:
        if not is_unique_violation(exc):
            raise
        raise HTTPException(status_code=409, detail="An account with that email address already exists.") from exc
    database_id = promote_current_workspace(member["id"])
    return {
        "ok": True,
        "account": {"email": email, "display_name": display_name},
        "promoted_database_id": database_id,
    }


@app.post("/api/account/login")
def login_account(payload: AccountLogin, request: Request) -> JSONResponse:
    email = normalize_email(payload.email)
    with connect() as con:
        enforce_login_rate_limit(con, request, email)
        member = con.execute(
            """
            SELECT * FROM members
            WHERE email=? AND revoked_at IS NULL AND password_hash IS NOT NULL
            """,
            (email,),
        ).fetchone()
        valid = bool(member and verify_password(payload.password, str(member["password_hash"] or "")))
    with connect() as con:
        record_login_attempt(con, request, email, valid)
    if not valid:
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    with connect() as con:
        token = issue_device_credential(con, member["id"])
        con.execute("UPDATE members SET last_seen_at=? WHERE id=?", (utc_now(), member["id"]))
    response = JSONResponse({"ok": True})
    set_session_cookie(response, request, token)
    return response


@app.post("/api/account/logout")
def logout_account(
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    token = bearer_token(authorization) if authorization else request.cookies.get(SESSION_COOKIE, "")
    member = authenticated_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        workspace = con.execute(
            "SELECT * FROM workspace_sessions WHERE member_id=?",
            (member["id"],),
        ).fetchone()
    if workspace:
        if (
            workspace["database_id"]
            and Path(workspace["db_path"]).exists()
            and not workspace_has_active_job(workspace)
        ):
            persist_workspace_snapshot(workspace)
        stop_workspace(workspace)
    with connect() as con:
        if workspace:
            con.execute("DELETE FROM workspace_sessions WHERE id=?", (workspace["id"],))
        con.execute(
            "UPDATE device_credentials SET revoked_at=? WHERE token_hash=? AND member_id=?",
            (utc_now(), secret_hash(token), member["id"]),
        )
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(WORKSPACE_COOKIE, path="/")
    return response


@app.get("/api/session")
def session(request: Request, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    member = authenticated_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        profile = con.execute(
            "SELECT slug, updated_at FROM public_profiles WHERE member_id=?",
            (member["id"],),
        ).fetchone()
        database_count = con.execute(
            "SELECT COUNT(*) AS count FROM account_databases WHERE member_id=? AND deleted_at IS NULL",
            (member["id"],),
        ).fetchone()["count"]
    return {
        "member_id": member["id"],
        "account": bool(member["email"] and member["password_hash"]),
        "email": member["email"] or "",
        "display_name": member["display_name"] or "",
        "database_count": int(database_count),
        "profile": dict(profile) if profile else None,
    }


@app.put("/api/profiles/{slug}")
def publish_profile(
    slug: str,
    snapshot: PublicProfileSnapshot,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    normalized_slug = normalize_slug(slug)
    now = utc_now()
    with connect() as con:
        existing_for_slug = con.execute(
            "SELECT member_id FROM public_profiles WHERE slug=?",
            (normalized_slug,),
        ).fetchone()
        if existing_for_slug and not hmac.compare_digest(existing_for_slug["member_id"], member["id"]):
            raise HTTPException(status_code=409, detail="That public profile address is already reserved.")
        existing_for_member = con.execute(
            "SELECT slug FROM public_profiles WHERE member_id=?",
            (member["id"],),
        ).fetchone()
        if existing_for_member and existing_for_member["slug"] != normalized_slug:
            raise HTTPException(
                status_code=409,
                detail=f"This invitation already publishes at /{existing_for_member['slug']}.",
            )
        write_public_profile(
            con,
            slug=normalized_slug,
            member_id=str(member["id"]),
            snapshot=snapshot.model_dump(),
            now=now,
        )
    return {"ok": True, "slug": normalized_slug, "url": f"/{normalized_slug}", "updated_at": now}


def owned_profile_database(member_id: str, database_id: str) -> Any:
    if active_background_job(database_id):
        raise HTTPException(
            status_code=409,
            detail="Wait for this CV's background process to finish before updating its public profile.",
        )
    with connect() as con:
        workspace = con.execute(
            "SELECT * FROM workspace_sessions WHERE database_id=? AND member_id=?",
            (database_id, member_id),
        ).fetchone()
    if workspace and Path(workspace["db_path"]).exists():
        persist_workspace_snapshot(workspace)
    with connect() as con:
        database = con.execute(
            """
            SELECT * FROM account_databases
            WHERE id=? AND member_id=? AND deleted_at IS NULL
            """,
            (database_id, member_id),
        ).fetchone()
    if database is None:
        raise HTTPException(status_code=404, detail="That saved CV could not be found.")
    return database


def built_profile_for_database(
    member_id: str,
    database_id: str,
    *,
    blocks: Any = None,
) -> dict[str, Any]:
    database = owned_profile_database(member_id, database_id)
    with materialized_database_blob(database["sqlite_blob"]) as database_path:
        return build_public_profile_snapshot(
            database_path,
            database_id=database_id,
            blocks=blocks,
        )


@app.post("/api/profile/publish")
def publish_cv_profile(
    payload: ProfilePublishRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    normalized_slug = normalize_slug(payload.slug)
    with connect() as con:
        occupied = con.execute(
            "SELECT member_id FROM public_profiles WHERE slug=?",
            (normalized_slug,),
        ).fetchone()
        existing = con.execute(
            "SELECT * FROM public_profiles WHERE member_id=?",
            (member["id"],),
        ).fetchone()
    if occupied and not hmac.compare_digest(str(occupied["member_id"]), str(member["id"])):
        raise HTTPException(status_code=409, detail="That public profile address is already reserved.")
    if existing and str(existing["slug"]) != normalized_slug:
        raise HTTPException(
            status_code=409,
            detail=f"Your profile already lives at /{existing['slug']}.",
        )
    previous = public_snapshot(existing, include_internal=True) if existing else {}
    snapshot = built_profile_for_database(
        str(member["id"]),
        payload.database_id,
        blocks=previous.get("blocks"),
    )
    preserve_public_profile_customizations(snapshot, previous)
    now = utc_now()
    with connect() as con:
        write_public_profile(
            con,
            slug=normalized_slug,
            member_id=str(member["id"]),
            snapshot=snapshot,
            now=now,
        )
    return {
        "ok": True,
        "slug": normalized_slug,
        "url": f"/{normalized_slug}",
        "updated_at": now,
    }


@app.get("/api/profile/manage")
def manage_public_profile(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        row = con.execute(
            "SELECT * FROM public_profiles WHERE member_id=?",
            (member["id"],),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Publish a profile first.")
    return {"ok": True, "profile": public_snapshot(row, include_internal=True)}


@app.put("/api/profile/blocks")
def update_public_profile_blocks(
    payload: ProfileBlocksRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    blocks = normalize_profile_blocks(payload.blocks)
    if {item["key"] for item in blocks} != set(PROFILE_BLOCK_KEYS):
        raise HTTPException(status_code=422, detail="Choose each public-profile block once.")
    with connect() as con:
        row = con.execute(
            "SELECT * FROM public_profiles WHERE member_id=?",
            (member["id"],),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Publish a profile first.")
        snapshot = public_snapshot(row, include_internal=True, include_portrait=True)
        snapshot.pop("slug", None)
        snapshot.pop("published_at", None)
        snapshot.pop("updated_at", None)
        snapshot["blocks"] = blocks
        updated_at = write_public_profile(
            con,
            slug=str(row["slug"]),
            member_id=str(member["id"]),
            snapshot=snapshot,
        )
    return {"ok": True, "blocks": blocks, "updated_at": updated_at}


@app.put("/api/profile/header")
def update_public_profile_header(
    payload: ProfileHeaderRequest,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    profile_title = re.sub(r"\s+", " ", payload.profile_title).strip()[:200]
    if not profile_title:
        raise HTTPException(status_code=422, detail="Enter a public profile name.")
    with connect() as con:
        row = con.execute(
            "SELECT * FROM public_profiles WHERE member_id=?",
            (member["id"],),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Publish a profile first.")
        snapshot = public_snapshot(row, include_internal=True, include_portrait=True)
        snapshot.pop("slug", None)
        snapshot.pop("published_at", None)
        snapshot.pop("updated_at", None)
        snapshot["profile_title"] = profile_title
        snapshot["_profile_title_custom"] = True
        updated_at = write_public_profile(
            con,
            slug=str(row["slug"]),
            member_id=str(member["id"]),
            snapshot=snapshot,
        )
    return {
        "ok": True,
        "profile_title": profile_title,
        "updated_at": updated_at,
    }


@app.post("/api/profile/refresh")
def refresh_public_profile(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        row = con.execute(
            "SELECT * FROM public_profiles WHERE member_id=?",
            (member["id"],),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Publish a profile first.")
    current = public_snapshot(row, include_internal=True)
    database_id = str(current.get("source_database_id") or "")
    if not database_id:
        raise HTTPException(
            status_code=409,
            detail="This legacy profile is not connected to a saved CV. Publish it again from My CVs.",
        )
    snapshot = built_profile_for_database(
        str(member["id"]),
        database_id,
        blocks=current.get("blocks"),
    )
    preserve_public_profile_customizations(snapshot, current)
    with connect() as con:
        updated_at = write_public_profile(
            con,
            slug=str(row["slug"]),
            member_id=str(member["id"]),
            snapshot=snapshot,
        )
    return {"ok": True, "slug": row["slug"], "updated_at": updated_at}


@app.delete("/api/profiles/{slug}")
def unpublish_profile(
    slug: str,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    normalized_slug = normalize_slug(slug)
    with connect() as con:
        cursor = con.execute(
            "DELETE FROM public_profiles WHERE slug=? AND member_id=?",
            (normalized_slug, member["id"]),
        )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="No owned public profile was found at that address.")
    return {"ok": True}


@app.get("/api/public/{slug}")
def public_profile_json(slug: str) -> JSONResponse:
    normalized_slug = normalize_slug(slug)
    with connect() as con:
        row = con.execute("SELECT * FROM public_profiles WHERE slug=?", (normalized_slug,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Public profile not found.")
    return JSONResponse(
        public_snapshot(row),
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache",
        },
    )


@app.get("/api/public/{slug}/portrait")
def public_profile_portrait(slug: str) -> Response:
    normalized_slug = normalize_slug(slug)
    with connect() as con:
        row = con.execute(
            """
            SELECT portrait_blob, portrait_mime_type
            FROM public_profiles
            WHERE slug=?
            """,
            (normalized_slug,),
        ).fetchone()
    if row is None or not row["portrait_blob"]:
        raise HTTPException(status_code=404, detail="This public profile has no portrait.")
    return Response(
        content=bytes(row["portrait_blob"]),
        media_type=str(row["portrait_mime_type"] or "application/octet-stream"),
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


def render_public_profile(
    snapshot: dict[str, Any],
    *,
    embedded: bool = False,
    theme: str = "native",
    block: str = "",
) -> str:
    name = html.escape(
        str(
            snapshot.get("profile_title")
            or snapshot.get("display_name")
            or "Academic profile"
        )
    )
    headline = html.escape(str(snapshot.get("headline") or ""))
    biography = html.escape(str(snapshot.get("biography") or ""))
    description = html.escape(re.sub(r"\s+", " ", str(snapshot.get("biography") or headline))[:180], quote=True)
    slug = html.escape(str(snapshot["slug"]), quote=True)
    block_attribute = html.escape(block, quote=True)
    embedded_attribute = "true" if embedded else "false"
    initial_bio = (
        f'<section class="profile-block bio-block"><h2>About</h2>'
        f'<p class="profile-biography">{biography}</p></section>'
        if biography and (not block or block == "bio")
        else ""
    )
    return f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{name} — Academic profile</title>
      <meta name="description" content="{description}">
      <link rel="stylesheet" href="/assets/public-profile.css?v=20260731-portrait-full-width">
      <script src="/assets/public-profile.js?v=20260731-portrait-flow" defer></script>
    </head>
    <body
      data-profile-slug="{slug}"
      data-profile-theme="{theme}"
      data-profile-embedded="{embedded_attribute}"
      data-profile-block="{block_attribute}"
    >
      <aside id="ownerToolbar" class="owner-toolbar" hidden>
        <span>You’re viewing your public profile</span>
        <div>
          <a class="owner-manager-link" href="/">My CVs</a>
          <button id="editProfileLayout" type="button">Edit profile</button>
          <button id="refreshProfile" type="button">Refresh from CV</button>
          <button id="embedFullProfile" type="button">Embed profile</button>
        </div>
        <p id="ownerMessage" role="status"></p>
      </aside>

      <main class="public-profile">
        <section id="profileHero" class="profile-hero">
          <p class="profile-eyebrow">Public academic profile</p>
          <h1>{name}</h1>
          <p class="profile-headline">{headline}</p>
        </section>
        <div id="profileBlocks" class="profile-blocks">{initial_bio}</div>
        <footer class="profile-footer">
          <span>Made with VitaMine</span>
          <time id="profileUpdated"></time>
        </footer>
      </main>

      <dialog id="layoutDialog" class="profile-dialog">
        <form id="layoutForm" method="dialog">
          <div class="dialog-heading">
            <div>
              <p class="profile-eyebrow">Public profile</p>
              <h2>Edit profile</h2>
            </div>
            <button id="closeLayoutDialog" class="dialog-close" type="button" aria-label="Close">×</button>
          </div>
          <label class="profile-title-field">
            <span>Profile name</span>
            <input id="profileTitleInput" maxlength="200" required>
            <small>This changes the public heading only. Your CV record stays unchanged.</small>
          </label>
          <p>Move whole sections and decide which ones are public.</p>
          <div id="layoutBlockList" class="layout-block-list"></div>
          <div class="dialog-actions">
            <button id="unpublishProfile" class="danger-button" type="button">Unpublish</button>
            <button class="primary-action" type="submit">Save changes</button>
          </div>
        </form>
      </dialog>

      <dialog id="embedDialog" class="profile-dialog embed-dialog">
        <form method="dialog">
          <div class="dialog-heading">
            <div>
              <p class="profile-eyebrow">Embed</p>
              <h2 id="embedDialogTitle">Embed profile</h2>
            </div>
            <button id="closeEmbedDialog" class="dialog-close" type="button" aria-label="Close">×</button>
          </div>
          <fieldset class="theme-picker">
            <legend>Appearance</legend>
            <label><input type="radio" name="embed_theme" value="native" checked> Native VitaMine</label>
            <label><input type="radio" name="embed_theme" value="simple"> Simple</label>
            <label><input type="radio" name="embed_theme" value="dark"> Dark mode</label>
          </fieldset>
          <label class="embed-code-label">Embed code
            <textarea id="embedCode" rows="5" readonly></textarea>
          </label>
          <div class="dialog-actions">
            <a id="embedPreview" class="secondary-action" href="#" target="_blank" rel="noreferrer">Preview</a>
            <button id="copyEmbedCode" class="primary-action" type="button">Copy code</button>
          </div>
          <p id="embedMessage" role="status"></p>
        </form>
      </dialog>
    </body></html>
    """


@app.get("/embed/{slug}", response_class=HTMLResponse)
def embedded_profile(slug: str, block: str = "", theme: str = "native") -> str:
    normalized_slug = normalize_slug(slug)
    block = str(block or "").strip().lower()
    theme = str(theme or "native").strip().lower()
    if block and block not in PROFILE_BLOCK_KEYS:
        raise HTTPException(status_code=404, detail="Unknown public-profile block.")
    if theme not in {"native", "simple", "dark"}:
        raise HTTPException(status_code=422, detail="Choose native, simple, or dark.")
    with connect() as con:
        row = con.execute("SELECT * FROM public_profiles WHERE slug=?", (normalized_slug,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Public profile not found.")
    snapshot = public_snapshot(row)
    visible = {
        item["key"]: item["visible"]
        for item in normalize_profile_blocks(snapshot.get("blocks"))
    }
    if block and not visible.get(block, False):
        raise HTTPException(status_code=404, detail="That public-profile block is not published.")
    return render_public_profile(snapshot, embedded=True, theme=theme, block=block)


@app.get("/{slug}", response_class=HTMLResponse)
def public_profile_page(slug: str) -> str:
    normalized_slug = normalize_slug(slug)
    with connect() as con:
        row = con.execute("SELECT * FROM public_profiles WHERE slug=?", (normalized_slug,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Public profile not found.")
    return render_public_profile(public_snapshot(row))


WORKER_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


@app.api_route("/api/{worker_path:path}", methods=WORKER_METHODS)
async def proxy_worker_api(request: Request, worker_path: str) -> Response:
    return await proxy_to_workspace(request, f"api/{worker_path}")


@app.api_route("/static/{worker_path:path}", methods=["GET"])
async def proxy_worker_static(request: Request, worker_path: str) -> Response:
    return await proxy_to_workspace(request, f"static/{worker_path}")


@app.api_route("/logo/{worker_path:path}", methods=["GET"])
async def proxy_worker_logo(request: Request, worker_path: str) -> Response:
    return await proxy_to_workspace(request, f"logo/{worker_path}")


@app.api_route("/output/{worker_path:path}", methods=["GET"])
async def proxy_worker_output(request: Request, worker_path: str) -> Response:
    return await proxy_to_workspace(request, f"output/{worker_path}")
