"""Invite-only hosted VitaMine service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import re
import secrets
import shutil
import smtplib
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx
from cryptography.fernet import Fernet
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .cloud_crypto import decrypt_private_data, encrypt_private_data, is_encrypted_private_data
from .llm_usage import usage_costs
from .public_profiles import (
    PROFILE_BLOCK_KEYS,
    PUBLIC_PROFILE_SCHEMA_VERSION,
    build_public_profile_snapshot,
    normalize_profile_blocks,
)


ORCID_WRITE_SCOPE = "/activities/update"


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
    "imprint",
    "privacy",
    "static",
    "terms",
    "support",
    "www",
}
ADMIN_SESSION_COOKIE = "vitamine_admin_session"
ADMIN_SESSION_MAX_AGE = 60 * 60 * 8
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
ZOTERO_OAUTH_REQUEST_MAX_AGE = 10 * 60
ORCID_PATTERN = re.compile(r"^\d{4}-\d{4}-\d{4}-[\dX]{4}$", re.I)
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")
WORKER_PROCESSES: dict[int, subprocess.Popen] = {}
DATABASE_LOCKS: dict[str, threading.Lock] = {}
DATABASE_LOCKS_GUARD = threading.Lock()
CLEANUP_STOP = threading.Event()
JOB_STOP = threading.Event()
PROJECT = Path(__file__).resolve().parent
LOGO_PATH = PROJECT / "logo" / "vitamine_logo.png"
INVITE_ARROW_PATH = PROJECT / "static" / "assets" / "onboarding_arrow_blank.png"
CLOUD_STATIC = PROJECT / "cloud_static"
CLOUD_SCHEMA_VERSION = 15
OPENAI_CV_PROCESSING_CONSENT_VERSION = "2026-08-05"
EMAIL_VERIFICATION_MAX_AGE = 60 * 60 * 24
PASSWORD_RESET_MAX_AGE = 60 * 60
PASSKEY_CHALLENGE_MAX_AGE = 5 * 60
PASSKEY_RP_ID = "vitamine.cloud"
PASSKEY_ORIGIN = "https://vitamine.cloud"
PLUS_TRIAL_DAYS = 90
INITIAL_PREMIUM_CREDIT_MICROUSD = 3_000_000  # Legacy migrations only.
PAYPAL_BETA_TOPUP_MICROUSD = 5_000_000
OPENAI_ACCOUNT_SPEND_LIMIT_MICROUSD = 200_000
OPENAI_ACCOUNT_SPEND_WINDOW_HOURS = 24
ADMIN_EXPENSIVE_JOB_MICROUSD = 50_000
ADMIN_REPEATED_JOB_WINDOW_HOURS = 24
ADMIN_REPEATED_JOB_COUNT = 3
ADMIN_STUCK_JOB_MINUTES = 15
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
    plus_trial_ends_at TEXT,
    plus_paid_until TEXT,
    plus_dev_toggle_enabled INTEGER NOT NULL DEFAULT 0,
    plus_dev_override INTEGER,
    email_verified_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at TEXT,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS member_activity_events (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN ('login', 'cv_import', 'enrich_cv', 'cleanup_cv')),
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_member_activity_events_member_time
ON member_activity_events(member_id, occurred_at);

CREATE TABLE IF NOT EXISTS openai_processing_consents (
    member_id TEXT PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
    policy_version TEXT NOT NULL,
    granted_at TEXT,
    withdrawn_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS openai_processing_consent_events (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('granted', 'withdrawn')),
    policy_version TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_openai_processing_consent_events_member
ON openai_processing_consent_events(member_id, occurred_at);

CREATE TABLE IF NOT EXISTS email_verification_tokens (
    token_hash TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_hash TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS passkey_credentials (
    credential_id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    transports_json TEXT NOT NULL DEFAULT '[]',
    label TEXT NOT NULL DEFAULT 'Passkey',
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS passkey_challenges (
    challenge_hash TEXT PRIMARY KEY,
    member_id TEXT REFERENCES members(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK (purpose IN ('register', 'authenticate')),
    challenge TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
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
    kind TEXT NOT NULL CHECK (kind IN ('cv_import', 'enrich_cv', 'cleanup_cv')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    base_revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT,
    request_fingerprint TEXT,
    progress_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error_message TEXT,
    support_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    acknowledged_at TEXT,
    cancel_requested_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_background_jobs_member
ON background_jobs(member_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_background_jobs_database
ON background_jobs(database_id, status, created_at);

CREATE TABLE IF NOT EXISTS llm_usage_events (
    id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    database_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
    job_id TEXT REFERENCES background_jobs(id) ON DELETE SET NULL,
    operation TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    priced_model TEXT,
    pricing_version TEXT,
    wholesale_cost_microusd INTEGER,
    charged_cost_microusd INTEGER,
    markup_basis_points INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_member_created
ON llm_usage_events(member_id, created_at);

CREATE TABLE IF NOT EXISTS premium_account_transactions (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    amount_microusd INTEGER NOT NULL,
    kind TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_premium_transactions_member_created
ON premium_account_transactions(member_id, created_at);

CREATE TABLE IF NOT EXISTS paypal_beta_topups (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    claim_key_hash TEXT NOT NULL UNIQUE,
    amount_microusd INTEGER NOT NULL,
    currency TEXT NOT NULL,
    confirmation_mode TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paypal_beta_topups_member_created
ON paypal_beta_topups(member_id, created_at);

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

CREATE TABLE IF NOT EXISTS zotero_oauth_requests (
    request_token_hash TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    database_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
    request_secret_ciphertext TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_zotero_oauth_requests_expiry
ON zotero_oauth_requests(expires_at);

CREATE TABLE IF NOT EXISTS zotero_oauth_connections (
    member_id TEXT PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
    last_database_id TEXT REFERENCES account_databases(id) ON DELETE SET NULL,
    zotero_user_id TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    api_key_ciphertext TEXT NOT NULL,
    access_json TEXT NOT NULL DEFAULT '{}',
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
    plus_trial_ends_at TEXT,
    plus_paid_until TEXT,
    plus_dev_toggle_enabled INTEGER NOT NULL DEFAULT 0,
    plus_dev_override INTEGER,
    email_verified_at TEXT,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_login_at TEXT,
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS member_activity_events (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK (event_type IN ('login', 'cv_import', 'enrich_cv', 'cleanup_cv')),
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_member_activity_events_member_time
ON member_activity_events(member_id, occurred_at);

CREATE TABLE IF NOT EXISTS email_verification_tokens (
    token_hash TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token_hash TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS passkey_credentials (
    credential_id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    public_key BYTEA NOT NULL,
    sign_count BIGINT NOT NULL DEFAULT 0,
    transports_json TEXT NOT NULL DEFAULT '[]',
    label TEXT NOT NULL DEFAULT 'Passkey',
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS passkey_challenges (
    challenge_hash TEXT PRIMARY KEY,
    member_id TEXT REFERENCES members(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK (purpose IN ('register', 'authenticate')),
    challenge TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
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
    kind TEXT NOT NULL CHECK (kind IN ('cv_import', 'enrich_cv', 'cleanup_cv')),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
    base_revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT,
    request_fingerprint TEXT,
    progress_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error_message TEXT,
    support_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    acknowledged_at TEXT,
    cancel_requested_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_background_jobs_member
ON background_jobs(member_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_background_jobs_database
ON background_jobs(database_id, status, created_at);

CREATE TABLE IF NOT EXISTS llm_usage_events (
    id TEXT PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    database_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
    job_id TEXT REFERENCES background_jobs(id) ON DELETE SET NULL,
    operation TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens BIGINT,
    cached_input_tokens BIGINT,
    output_tokens BIGINT,
    reasoning_tokens BIGINT,
    priced_model TEXT,
    pricing_version TEXT,
    wholesale_cost_microusd BIGINT,
    charged_cost_microusd BIGINT,
    markup_basis_points INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_member_created
ON llm_usage_events(member_id, created_at);

CREATE TABLE IF NOT EXISTS premium_account_transactions (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    amount_microusd BIGINT NOT NULL,
    kind TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_premium_transactions_member_created
ON premium_account_transactions(member_id, created_at);

CREATE TABLE IF NOT EXISTS paypal_beta_topups (
    id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    claim_key_hash TEXT NOT NULL UNIQUE,
    amount_microusd BIGINT NOT NULL,
    currency TEXT NOT NULL,
    confirmation_mode TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paypal_beta_topups_member_created
ON paypal_beta_topups(member_id, created_at);

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

CREATE TABLE IF NOT EXISTS zotero_oauth_requests (
    request_token_hash TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
    database_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
    request_secret_ciphertext TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_zotero_oauth_requests_expiry
ON zotero_oauth_requests(expires_at);

CREATE TABLE IF NOT EXISTS zotero_oauth_connections (
    member_id TEXT PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
    last_database_id TEXT REFERENCES account_databases(id) ON DELETE SET NULL,
    zotero_user_id TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    api_key_ciphertext TEXT NOT NULL,
    access_json TEXT NOT NULL DEFAULT '{}',
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


class AdminLogin(BaseModel):
    username: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class PasskeyEmail(BaseModel):
    email: str | None = Field(default=None, min_length=3, max_length=254)


class PasskeyResponse(BaseModel):
    challenge_id: str = Field(min_length=20, max_length=200)
    credential: dict[str, Any]
    label: str = Field(default="Passkey", max_length=80)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class PasswordResetCompletion(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class PaypalBetaTopupClaim(BaseModel):
    acknowledged_paid: bool


class PlusDeveloperToggle(BaseModel):
    active: bool


class OpenAIProcessingConsentUpdate(BaseModel):
    accepted: bool


class AccountDeletion(BaseModel):
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    confirmation: str = Field(min_length=6, max_length=32)


class DatabaseRename(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class PublicProfileSnapshot(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=PUBLIC_PROFILE_SCHEMA_VERSION)
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


def new_support_id() -> str:
    return f"VM-{secrets.token_hex(16).upper()}"


def failure_category(error: Exception) -> str:
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, OSError):
        return "io_error"
    if isinstance(error, ValueError):
        return "invalid_internal_state"
    return "internal_error"


def log_support_event(event: str, support_id: str, **safe_fields: Any) -> None:
    # Values passed here must be structural allowlisted metadata. In particular,
    # never pass exception messages, request headers/bodies, filenames, or CV data.
    record = {
        "event": event,
        "support_id": support_id,
        "timestamp": utc_now(),
        **safe_fields,
    }
    LOGGER.error(
        json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    )


def request_endpoint_template(request: Request) -> str:
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return str(template) if template else "<unmatched>"


def cloud_db_path() -> Path:
    return Path(os.environ.get("VITAMINE_CLOUD_DB", DEFAULT_DB)).expanduser().resolve()


def cloud_database_url() -> str:
    return str(os.environ.get("VITAMINE_DATABASE_URL") or "").strip()


def workspace_root() -> Path:
    return Path(
        os.environ.get("VITAMINE_SESSION_ROOT", "/run/vitamine-cloud/sessions")
    ).expanduser().resolve()


def job_root() -> Path:
    return Path(
        os.environ.get("VITAMINE_JOB_ROOT", "/var/lib/vitamine-cloud/jobs")
    ).expanduser().resolve()


def job_work_root() -> Path:
    return Path(
        os.environ.get("VITAMINE_JOB_WORK_ROOT", "/run/vitamine-cloud/jobs")
    ).expanduser().resolve()


def secret_hash(value: str) -> str:
    pepper = os.environ.get("VITAMINE_CLOUD_PEPPER", "")
    return hashlib.sha256(f"{pepper}\0{value}".encode("utf-8")).hexdigest()


def database_encryption_context(member_id: str, database_id: str) -> str:
    return f"vitamine-account-database-v1:{member_id}:{database_id}"


def job_upload_encryption_context(member_id: str, job_id: str, stored_name: str) -> str:
    return f"vitamine-job-upload-v1:{member_id}:{job_id}:{stored_name}"


def encrypt_database_content(content: bytes, *, member_id: str, database_id: str) -> bytes:
    return encrypt_private_data(content, context=database_encryption_context(member_id, database_id))


def decrypt_database_content(
    content: bytes | memoryview,
    *,
    member_id: str,
    database_id: str,
    allow_plaintext: bool = False,
) -> bytes:
    return decrypt_private_data(
        content,
        context=database_encryption_context(member_id, database_id),
        allow_plaintext=allow_plaintext,
    )


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


def orcid_api_base_url() -> str:
    config = orcid_oauth_config()
    if config is None:
        raise HTTPException(status_code=503, detail="ORCID sign-in is not configured.")
    return "https://api.sandbox.orcid.org" if config["base_url"] == "https://sandbox.orcid.org" else "https://api.orcid.org"


def orcid_write_scope_granted(scope: str) -> bool:
    return ORCID_WRITE_SCOPE in {part.strip() for part in str(scope or "").split()}


def oauth_token_cipher() -> Fernet:
    pepper = str(os.environ.get("VITAMINE_CLOUD_PEPPER") or "")
    if not pepper:
        raise RuntimeError("VITAMINE_CLOUD_PEPPER is required for encrypted OAuth token storage.")
    key = hashlib.sha256(f"vitamine-oauth-token-v1\0{pepper}".encode("utf-8")).digest()
    import base64

    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_oauth_token(value: str) -> str:
    return oauth_token_cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_oauth_token(value: str) -> str:
    return oauth_token_cipher().decrypt(value.encode("ascii")).decode("utf-8")


def zotero_oauth_config() -> dict[str, str] | None:
    client_key = str(os.environ.get("ZOTERO_OAUTH_CLIENT_KEY") or "").strip()
    client_secret = str(os.environ.get("ZOTERO_OAUTH_CLIENT_SECRET") or "").strip()
    callback_url = str(os.environ.get("ZOTERO_OAUTH_CALLBACK_URL") or "").strip()
    if not client_key or not client_secret or not callback_url:
        return None
    if not callback_url.startswith("https://"):
        raise RuntimeError("Zotero OAuth requires an HTTPS callback URL.")
    return {
        "client_key": client_key,
        "client_secret": client_secret,
        "callback_url": callback_url,
        "request_url": "https://www.zotero.org/oauth/request",
        "authorize_url": "https://www.zotero.org/oauth/authorize",
        "access_url": "https://www.zotero.org/oauth/access",
    }


def oauth1_quote(value: Any) -> str:
    return quote(str(value), safe="~-._")


def oauth1_authorization_header(
    method: str,
    url: str,
    *,
    client_key: str,
    client_secret: str,
    token: str = "",
    token_secret: str = "",
    extra: dict[str, str] | None = None,
) -> str:
    parameters = {
        "oauth_consumer_key": client_key,
        "oauth_nonce": secrets.token_urlsafe(24),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_version": "1.0",
        **(extra or {}),
    }
    if token:
        parameters["oauth_token"] = token
    normalized = "&".join(
        f"{oauth1_quote(key)}={oauth1_quote(value)}"
        for key, value in sorted(parameters.items())
    )
    signature_base = "&".join(
        (method.upper(), oauth1_quote(url), oauth1_quote(normalized))
    )
    signing_key = f"{oauth1_quote(client_secret)}&{oauth1_quote(token_secret)}"
    signature = base64.b64encode(
        hmac.new(signing_key.encode("utf-8"), signature_base.encode("utf-8"), hashlib.sha1).digest()
    ).decode("ascii")
    header_parameters = {**parameters, "oauth_signature": signature}
    return "OAuth " + ", ".join(
        f'{oauth1_quote(key)}="{oauth1_quote(value)}"'
        for key, value in sorted(header_parameters.items())
    )


async def request_zotero_temporary_credentials() -> dict[str, str]:
    config = zotero_oauth_config()
    if config is None:
        raise HTTPException(status_code=503, detail="Zotero sign-in is not configured.")
    header = oauth1_authorization_header(
        "POST",
        config["request_url"],
        client_key=config["client_key"],
        client_secret=config["client_secret"],
        extra={"oauth_callback": config["callback_url"]},
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
            response = await client.post(config["request_url"], headers={"Authorization": header})
            response.raise_for_status()
        payload = {key: values[0] for key, values in parse_qs(response.text).items() if values}
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Zotero could not start authorization. Please try again.") from exc
    token = str(payload.get("oauth_token") or "")
    secret = str(payload.get("oauth_token_secret") or "")
    if not token or not secret or payload.get("oauth_callback_confirmed") != "true":
        raise HTTPException(status_code=502, detail="Zotero returned an invalid authorization response.")
    return {"token": token, "secret": secret}


def store_zotero_oauth_request(*, token: str, secret: str, member_id: str, database_id: str) -> None:
    now = datetime.now(timezone.utc)
    with connect() as con:
        con.execute("DELETE FROM zotero_oauth_requests WHERE expires_at<=?", (now.isoformat(),))
        con.execute(
            """
            INSERT INTO zotero_oauth_requests
              (request_token_hash, member_id, database_id, request_secret_ciphertext, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                secret_hash(f"zotero-oauth-request:{token}"), member_id, database_id,
                encrypt_oauth_token(secret), now.isoformat(),
                (now + timedelta(seconds=ZOTERO_OAUTH_REQUEST_MAX_AGE)).isoformat(),
            ),
        )


def consume_zotero_oauth_request(token: str, member_id: str) -> Any:
    if not token or len(token) > 500:
        raise HTTPException(status_code=400, detail="This Zotero authorization request is invalid.")
    token_hash = secret_hash(f"zotero-oauth-request:{token}")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT * FROM zotero_oauth_requests WHERE request_token_hash=? FOR UPDATE",
            (token_hash,),
        ).fetchone()
        if row is not None:
            con.execute("DELETE FROM zotero_oauth_requests WHERE request_token_hash=?", (token_hash,))
    if row is None or str(row["expires_at"]) <= utc_now():
        raise HTTPException(status_code=400, detail="This Zotero authorization request expired or was already used.")
    if not hmac.compare_digest(str(row["member_id"]), member_id):
        raise HTTPException(status_code=403, detail="This Zotero authorization belongs to another account.")
    return row


async def exchange_zotero_access_token(token: str, token_secret: str, verifier: str) -> dict[str, Any]:
    config = zotero_oauth_config()
    if config is None:
        raise HTTPException(status_code=503, detail="Zotero sign-in is not configured.")
    header = oauth1_authorization_header(
        "POST", config["access_url"], client_key=config["client_key"],
        client_secret=config["client_secret"], token=token, token_secret=token_secret,
        extra={"oauth_verifier": verifier},
    )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
            response = await client.post(config["access_url"], headers={"Authorization": header})
            response.raise_for_status()
        payload = {key: values[0] for key, values in parse_qs(response.text).items() if values}
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Zotero could not complete authorization. Please try again.") from exc
    api_key = str(payload.get("oauth_token") or "")
    user_id = str(payload.get("userID") or "")
    if not api_key or not user_id:
        raise HTTPException(status_code=502, detail="Zotero returned an incomplete authorization response.")
    return {"api_key": api_key, "user_id": user_id, "username": str(payload.get("username") or "")[:200]}


async def verify_zotero_api_key(api_key: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
            response = await client.get(
                "https://api.zotero.org/keys/current",
                headers={"Zotero-API-Key": api_key, "Zotero-API-Version": "3"},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Zotero could not verify the new connection.") from exc
    if not isinstance(payload, dict) or not (payload.get("access") or {}).get("user", {}).get("library"):
        raise HTTPException(status_code=403, detail="VitaMine needs read access to your Zotero library.")
    return payload


def _zotero_permission_rows(access_payload: dict[str, Any]) -> list[dict[str, Any]]:
    access = access_payload.get("access") if isinstance(access_payload.get("access"), dict) else {}
    rows: list[dict[str, Any]] = []
    user = access.get("user")
    if isinstance(user, dict):
        rows.append(user)
    groups = access.get("groups")
    if isinstance(groups, dict):
        rows.extend(value for value in groups.values() if isinstance(value, dict))
    return rows


def zotero_source_write_access(access: dict[str, Any], source: dict[str, Any]) -> bool:
    """Check the exact selected library, rather than merely any Zotero access."""
    permissions = access.get("access") if isinstance(access.get("access"), dict) else {}
    library_type = str(source.get("library_type") or "")
    library_id = str(source.get("library_id") or "")
    if library_type == "users":
        granted = permissions.get("user") if isinstance(permissions.get("user"), dict) else {}
    elif library_type == "groups":
        groups = permissions.get("groups") if isinstance(permissions.get("groups"), dict) else {}
        granted = groups.get(library_id) if isinstance(groups.get(library_id), dict) else groups.get("all")
        granted = granted if isinstance(granted, dict) else {}
    else:
        return False
    return bool(granted.get("library") and granted.get("write"))


def account_zotero_write_connection(member_id: str, source: dict[str, Any]) -> str:
    with connect() as con:
        row = con.execute(
            "SELECT api_key_ciphertext, access_json FROM zotero_oauth_connections WHERE member_id=?",
            (member_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=409, detail="Connect your Zotero account before updating the selected source.")
    access = parsed_json_object(row["access_json"])
    if not zotero_source_write_access(access, source):
        raise HTTPException(
            status_code=403,
            detail="Reconnect Zotero and grant write access to the selected library before updating it.",
        )
    return decrypt_oauth_token(str(row["api_key_ciphertext"]))


def zotero_headers(api_key: str, *, version: str = "") -> dict[str, str]:
    headers = {"Zotero-API-Key": api_key, "Zotero-API-Version": "3", "Accept": "application/json"}
    if version:
        headers["If-Unmodified-Since-Version"] = version
    return headers


def zotero_item_payload(publication: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    title = str(publication.get("title") or "").strip()
    doi = str(publication.get("doi") or "").strip()
    if not title or not doi:
        raise HTTPException(status_code=422, detail="A Zotero addition needs both a publication title and DOI.")
    item_type = str(publication.get("item_type") or "").strip()
    item_type = {
        "journal-article": "journalArticle",
        "book-chapter": "bookSection",
        "conference-paper": "conferencePaper",
    }.get(item_type, item_type or "journalArticle")
    payload: dict[str, Any] = {"itemType": item_type, "title": title, "DOI": doi}
    venue = str(publication.get("venue") or "").strip()
    if venue:
        payload["publicationTitle"] = venue
    year = str(publication.get("year") or "").strip()
    if year:
        payload["date"] = year
    creators = [name.strip() for name in str(publication.get("authors") or "").split(",") if name.strip()]
    if creators:
        payload["creators"] = [{"creatorType": "author", "name": name} for name in creators]
    if source.get("source_mode") == "collection":
        payload["collections"] = [str(source["collection_key"])]
    return payload


async def zotero_current_item(
    client: httpx.AsyncClient, prefix: str, remote_id: str, api_key: str
) -> dict[str, Any] | None:
    response = await client.get(f"{prefix}/items/{quote(remote_id, safe='')}", headers=zotero_headers(api_key))
    if response.status_code == 404:
        return None
    response.raise_for_status()
    raw = response.json()
    data = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    return data if isinstance(data, dict) else None


async def zotero_patch_source_membership(
    client: httpx.AsyncClient,
    *,
    prefix: str,
    api_key: str,
    source: dict[str, Any],
    remote_id: str,
    present: bool,
) -> None:
    item = await zotero_current_item(client, prefix, remote_id, api_key)
    if item is None:
        if present:
            raise HTTPException(status_code=409, detail="The selected Zotero item is no longer available.")
        return
    version = str(item.get("version") or "")
    if not version:
        raise HTTPException(status_code=502, detail="Zotero did not return an item version for this update.")
    if source["source_mode"] == "my_publications":
        patch = {"inPublications": present}
    else:
        collection_key = str(source["collection_key"])
        collections = [str(value) for value in item.get("collections") or []]
        patch = {"collections": ([*collections, collection_key] if present else [value for value in collections if value != collection_key])}
        patch["collections"] = list(dict.fromkeys(patch["collections"]))
    response = await client.patch(
        f"{prefix}/items/{quote(remote_id, safe='')}",
        headers={**zotero_headers(api_key, version=version), "Content-Type": "application/json"},
        json=patch,
    )
    response.raise_for_status()


async def zotero_delete_from_selected_library(
    client: httpx.AsyncClient, *, prefix: str, api_key: str, remote_id: str
) -> None:
    """Delete the item only after the user explicitly chose whole-library sync."""
    item = await zotero_current_item(client, prefix, remote_id, api_key)
    if item is None:
        return
    version = str(item.get("version") or "")
    if not version:
        raise HTTPException(status_code=502, detail="Zotero did not return an item version for this deletion.")
    response = await client.delete(
        f"{prefix}/items/{quote(remote_id, safe='')}",
        headers=zotero_headers(api_key, version=version),
    )
    if response.status_code != 404:
        response.raise_for_status()


async def zotero_library_version(client: httpx.AsyncClient, prefix: str, api_key: str) -> str:
    response = await client.get(f"{prefix}/items?format=json&limit=1", headers=zotero_headers(api_key))
    response.raise_for_status()
    version = str(response.headers.get("Last-Modified-Version") or "")
    if not version:
        raise HTTPException(status_code=502, detail="Zotero did not return a library version for this update.")
    return version


async def zotero_add_to_selected_source(
    client: httpx.AsyncClient, *, api_key: str, source: dict[str, Any], publication: dict[str, Any]
) -> str:
    prefix = f"https://api.zotero.org/{source['library_type']}/{source['library_id']}"
    remote_id = str(publication.get("zotero_key") or "").strip()
    if remote_id and source["source_mode"] != "library":
        existing = await zotero_current_item(client, prefix, remote_id, api_key)
        existing_doi = str((existing or {}).get("DOI") or "").strip().lower().rstrip(".")
        publication_doi = str(publication.get("doi") or "").strip().lower().rstrip(".")
        if existing is not None and existing_doi == publication_doi:
            await zotero_patch_source_membership(
                client, prefix=prefix, api_key=api_key, source=source, remote_id=remote_id, present=True
            )
            return remote_id
    version = await zotero_library_version(client, prefix, api_key)
    response = await client.post(
        f"{prefix}/items",
        headers={**zotero_headers(api_key, version=version), "Content-Type": "application/json"},
        json=[zotero_item_payload(publication, source)],
    )
    response.raise_for_status()
    result = response.json()
    remote_id = str((result.get("success") or {}).get("0") or "") if isinstance(result, dict) else ""
    if not remote_id:
        raise HTTPException(status_code=502, detail="Zotero did not confirm the new publication.")
    if source["source_mode"] == "my_publications":
        await zotero_patch_source_membership(
            client, prefix=prefix, api_key=api_key, source=source, remote_id=remote_id, present=True
        )
    return remote_id


def store_zotero_oauth_connection(*, member_id: str, database_id: str, token: dict[str, Any], access: dict[str, Any]) -> None:
    now = utc_now()
    with connect() as con:
        con.execute(
            """
            INSERT INTO zotero_oauth_connections
              (member_id, last_database_id, zotero_user_id, username, api_key_ciphertext,
               access_json, verified_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (member_id) DO UPDATE SET
              last_database_id=excluded.last_database_id, zotero_user_id=excluded.zotero_user_id,
              username=excluded.username, api_key_ciphertext=excluded.api_key_ciphertext,
              access_json=excluded.access_json, verified_at=excluded.verified_at,
              updated_at=excluded.updated_at
            """,
            (
                member_id, database_id, token["user_id"], token["username"],
                encrypt_oauth_token(str(token["api_key"])), json.dumps(access), now, now,
            ),
        )


def account_zotero_api_key(member_id: str) -> str:
    with connect() as con:
        row = con.execute(
            "SELECT api_key_ciphertext FROM zotero_oauth_connections WHERE member_id=?",
            (member_id,),
        ).fetchone()
    return decrypt_oauth_token(str(row["api_key_ciphertext"])) if row is not None else ""


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


def account_orcid_write_connection(member_id: str) -> tuple[str, str, str]:
    with connect() as con:
        row = con.execute(
            """
            SELECT orcid_id, access_token_ciphertext, scope
            FROM orcid_oauth_connections WHERE member_id=?
            """,
            (member_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=409, detail="Connect your ORCID account before updating its profile.")
    scope = str(row["scope"] or "")
    if not orcid_write_scope_granted(scope):
        raise HTTPException(
            status_code=403,
            detail="Reconnect ORCID and grant permission to update activities before changing your ORCID profile.",
        )
    return (
        str(row["orcid_id"]),
        decrypt_oauth_token(str(row["access_token_ciphertext"])),
        scope,
    )


def orcid_work_payload(publication: dict[str, Any]) -> dict[str, Any]:
    """Build the smallest portable ORCID work payload for a DOI-backed paper."""
    title = str(publication.get("title") or "").strip()
    doi = str(publication.get("doi") or "").strip()
    if not title or not doi:
        raise HTTPException(status_code=422, detail="An ORCID addition needs both a publication title and DOI.")
    payload: dict[str, Any] = {
        "title": {"title": {"value": title}},
        "type": str(publication.get("item_type") or "journal-article").strip() or "journal-article",
        "visibility": "PUBLIC",
        "external-ids": {
            "external-id": [{
                "external-id-type": "doi",
                "external-id-value": doi,
                "external-id-relationship": "self",
                "external-id-url": {"value": f"https://doi.org/{doi}"},
            }]
        },
    }
    venue = str(publication.get("venue") or "").strip()
    if venue:
        payload["journal-title"] = {"value": venue}
    year = str(publication.get("year") or "").strip()
    if year.isdigit() and len(year) == 4:
        payload["publication-date"] = {"year": {"value": year}}
    return payload


def orcid_put_code_from_location(location: str) -> str:
    match = re.search(r"/work/([^/?#]+)", str(location or ""))
    return match.group(1) if match else ""


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


def record_member_activity(con: GatewayConnection, member_id: str, event_type: str, *, occurred_at: str | None = None) -> None:
    """Store structural account activity only; never request or CV content."""
    if event_type not in {"login", "cv_import", "enrich_cv", "cleanup_cv"}:
        raise ValueError("Unsupported member activity event.")
    con.execute(
        "INSERT INTO member_activity_events(id, member_id, event_type, occurred_at) VALUES (?, ?, ?, ?)",
        (secrets.token_urlsafe(18), member_id, event_type, occurred_at or utc_now()),
    )


def admin_settings() -> tuple[str, str] | None:
    """Return the configured operator identity without ever exposing its secret."""
    password_hash = os.environ.get("VITAMINE_ADMIN_PASSWORD_HASH", "").strip()
    if not password_hash:
        return None
    username = os.environ.get("VITAMINE_ADMIN_USERNAME", "admin").strip() or "admin"
    return username, password_hash


def admin_session_signature(payload: str, password_hash: str) -> str:
    pepper = os.environ.get("VITAMINE_CLOUD_PEPPER", "")
    return hmac.new(
        pepper.encode("utf-8"),
        f"admin-session\\0{password_hash}\\0{payload}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def issue_admin_session(password_hash: str) -> str:
    expires_at = int(time.time()) + ADMIN_SESSION_MAX_AGE
    payload = f"{expires_at}.{secrets.token_urlsafe(24)}"
    return f"{payload}.{admin_session_signature(payload, password_hash)}"


def authenticated_admin(request: Request) -> None:
    settings = admin_settings()
    if settings is None:
        raise HTTPException(status_code=503, detail="The operator dashboard is not configured.")
    _, password_hash = settings
    token = str(request.cookies.get(ADMIN_SESSION_COOKIE) or "")
    expires_at, separator, remainder = token.partition(".")
    nonce, separator2, signature = remainder.partition(".") if separator else ("", "", "")
    payload = f"{expires_at}.{nonce}"
    if (
        not separator2
        or not expires_at.isdigit()
        or int(expires_at) < int(time.time())
        or not nonce
        or not hmac.compare_digest(signature, admin_session_signature(payload, password_hash))
    ):
        raise HTTPException(status_code=401, detail="Operator sign-in is required.")


def set_admin_session_cookie(response: Response, request: Request, token: str) -> None:
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    response.set_cookie(
        key=ADMIN_SESSION_COOKIE,
        value=token,
        max_age=ADMIN_SESSION_MAX_AGE,
        httponly=True,
        secure=request.url.scheme == "https" or forwarded_proto == "https",
        samesite="strict",
        path="/",
    )


def admin_member_reference(member_id: str) -> str:
    """Stable dashboard-only pseudonym; no account identifier leaves the API."""
    return f"Member {secret_hash(f'admin-member:{member_id}')[:12]}"


def admin_dashboard_payload() -> dict[str, Any]:
    """Return operational aggregates without emails, names, CV data, or raw IDs."""
    now = datetime.now(timezone.utc)
    cutoff_30 = (now - timedelta(days=30)).isoformat()
    cutoff_24 = (now - timedelta(hours=ADMIN_REPEATED_JOB_WINDOW_HOURS)).isoformat()
    with connect() as con:
        members = con.execute(
            """
            SELECT m.id, COALESCE(m.account_created_at, m.created_at) AS joined_at,
                   m.last_seen_at, m.last_login_at,
                   (SELECT COUNT(*) FROM member_activity_events e
                    WHERE e.member_id=m.id AND e.event_type='login') AS login_count,
                   (SELECT COUNT(*) FROM background_jobs j
                    WHERE j.member_id=m.id AND j.kind='enrich_cv') AS enrichment_count,
                   (SELECT COUNT(*) FROM background_jobs j
                    WHERE j.member_id=m.id AND j.kind='cv_import') AS import_count,
                   (SELECT COUNT(*) FROM background_jobs j
                    WHERE j.member_id=m.id AND j.kind='cleanup_cv') AS cleanup_count,
                   (SELECT COUNT(*) FROM llm_usage_events u
                    WHERE u.member_id=m.id) AS llm_call_count,
                   (SELECT COALESCE(SUM(u.input_tokens), 0) FROM llm_usage_events u
                    WHERE u.member_id=m.id) AS input_tokens,
                   (SELECT COALESCE(SUM(u.output_tokens), 0) FROM llm_usage_events u
                    WHERE u.member_id=m.id) AS output_tokens,
                   (SELECT COALESCE(SUM(u.wholesale_cost_microusd), 0) FROM llm_usage_events u
                    WHERE u.member_id=m.id) AS cost_microusd,
                   (SELECT COALESCE(SUM(u.wholesale_cost_microusd), 0) FROM llm_usage_events u
                    WHERE u.member_id=m.id AND u.created_at>=?) AS cost_24h_microusd,
                   (SELECT COUNT(*) FROM llm_usage_events u
                    WHERE u.member_id=m.id AND u.created_at>=? AND u.wholesale_cost_microusd IS NULL) AS unpriced_24h
            FROM members m
            WHERE m.email IS NOT NULL AND m.password_hash IS NOT NULL AND m.revoked_at IS NULL
            ORDER BY m.last_seen_at DESC
            """,
            (cutoff_24, cutoff_24),
        ).fetchall()
        usage_30 = con.execute(
            """
            SELECT COUNT(*) AS llm_calls, COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(wholesale_cost_microusd), 0) AS cost_microusd,
                   COUNT(*) FILTER (WHERE wholesale_cost_microusd IS NULL) AS unpriced_responses
            FROM llm_usage_events WHERE created_at>=?
            """,
            (cutoff_30,),
        ).fetchone()
        jobs_30 = con.execute(
            """
            SELECT kind, status, COUNT(*) AS count FROM background_jobs
            WHERE created_at>=? GROUP BY kind, status ORDER BY kind, status
            """,
            (cutoff_30,),
        ).fetchall()
        daily_logins = con.execute(
            """
            SELECT SUBSTR(occurred_at, 1, 10) AS day, COUNT(*) AS count
            FROM member_activity_events
            WHERE event_type='login' AND occurred_at>=?
            GROUP BY SUBSTR(occurred_at, 1, 10)
            """,
            (cutoff_30,),
        ).fetchall()
        daily_jobs = con.execute(
            """
            SELECT SUBSTR(created_at, 1, 10) AS day, kind, COUNT(*) AS count
            FROM background_jobs WHERE created_at>=?
            GROUP BY SUBSTR(created_at, 1, 10), kind
            """,
            (cutoff_30,),
        ).fetchall()
        daily_llm = con.execute(
            """
            SELECT SUBSTR(created_at, 1, 10) AS day, COUNT(*) AS count
            FROM llm_usage_events WHERE created_at>=?
            GROUP BY SUBSTR(created_at, 1, 10)
            """,
            (cutoff_30,),
        ).fetchall()
        job_usage = con.execute(
            """
            SELECT j.member_id, j.kind, j.status, j.created_at, j.started_at, j.heartbeat_at,
                   j.finished_at, COALESCE(SUM(u.wholesale_cost_microusd), 0) AS cost_microusd,
                   COUNT(u.id) AS llm_calls,
                   COUNT(*) FILTER (WHERE u.id IS NOT NULL AND u.wholesale_cost_microusd IS NULL) AS unpriced_responses
            FROM background_jobs j
            LEFT JOIN llm_usage_events u ON u.job_id=j.id
            WHERE j.created_at>=? OR j.status IN ('queued', 'running')
            GROUP BY j.id, j.member_id, j.kind, j.status, j.created_at, j.started_at,
                     j.heartbeat_at, j.finished_at
            """,
            (cutoff_30,),
        ).fetchall()
        failed_jobs = con.execute(
            """
            SELECT member_id, kind, COUNT(*) AS count, MAX(COALESCE(finished_at, updated_at, created_at)) AS latest_at
            FROM background_jobs
            WHERE status='failed' AND created_at>=?
            GROUP BY member_id, kind
            """,
            (cutoff_30,),
        ).fetchall()
        repeated_jobs = con.execute(
            """
            SELECT member_id, kind, COUNT(*) AS count, MAX(created_at) AS latest_at
            FROM background_jobs
            WHERE created_at>=?
            GROUP BY member_id, kind
            HAVING COUNT(*)>=?
            """,
            (cutoff_24, ADMIN_REPEATED_JOB_COUNT),
        ).fetchall()

    def days_since(value: Any) -> int | None:
        parsed = parsed_utc(value)
        return max(0, int((now - parsed).total_seconds() // 86_400)) if parsed else None

    spend_limit = openai_account_spend_limit_microusd()
    member_rows = [
        {
            "reference": admin_member_reference(str(row["id"])),
            "account_age_days": days_since(row["joined_at"]),
            "inactive_days": days_since(row["last_seen_at"]),
            "last_login_days": days_since(row["last_login_at"]),
            "logins_since_dashboard_enabled": int(row["login_count"] or 0),
            "enrichment_calls": int(row["enrichment_count"] or 0),
            "imports": int(row["import_count"] or 0),
            "cleanup_calls": int(row["cleanup_count"] or 0),
            "llm_calls": int(row["llm_call_count"] or 0),
            "input_tokens": int(row["input_tokens"] or 0),
            "output_tokens": int(row["output_tokens"] or 0),
            "cost_microusd": int(row["cost_microusd"] or 0),
            "cost_24h_microusd": int(row["cost_24h_microusd"] or 0),
            "unpriced_24h": int(row["unpriced_24h"] or 0),
            "managed_ai_paused": (
                int(row["cost_24h_microusd"] or 0) >= spend_limit
                or int(row["unpriced_24h"] or 0) > 0
            ),
        }
        for row in members
    ]
    alerts: list[dict[str, Any]] = []
    for row in job_usage:
        cost = int(row["cost_microusd"] or 0)
        unpriced = int(row["unpriced_responses"] or 0)
        heartbeat = parsed_utc(row["heartbeat_at"] or row["started_at"] or row["created_at"])
        age_minutes = max(0, int((now - heartbeat).total_seconds() // 60)) if heartbeat else None
        base = {
            "reference": admin_member_reference(str(row["member_id"])),
            "kind": str(row["kind"]),
            "cost_microusd": cost,
            "llm_calls": int(row["llm_calls"] or 0),
            "latest_at": str(row["finished_at"] or row["heartbeat_at"] or row["created_at"] or ""),
        }
        if cost >= ADMIN_EXPENSIVE_JOB_MICROUSD:
            alerts.append({**base, "signal": "expensive", "count": 1, "age_minutes": None})
        if unpriced:
            alerts.append({**base, "signal": "unpriced", "count": unpriced, "age_minutes": None})
        if str(row["status"]) in {"queued", "running"} and age_minutes is not None and age_minutes >= ADMIN_STUCK_JOB_MINUTES:
            alerts.append({**base, "signal": "stuck", "count": 1, "age_minutes": age_minutes})
    for row in failed_jobs:
        alerts.append({
            "reference": admin_member_reference(str(row["member_id"])), "kind": str(row["kind"]),
            "signal": "failed", "count": int(row["count"] or 0), "age_minutes": None,
            "cost_microusd": 0, "llm_calls": 0, "latest_at": str(row["latest_at"] or ""),
        })
    for row in repeated_jobs:
        alerts.append({
            "reference": admin_member_reference(str(row["member_id"])), "kind": str(row["kind"]),
            "signal": "repeated", "count": int(row["count"] or 0), "age_minutes": None,
            "cost_microusd": 0, "llm_calls": 0, "latest_at": str(row["latest_at"] or ""),
        })
    alert_order = {"stuck": 0, "unpriced": 1, "expensive": 2, "failed": 3, "repeated": 4}
    alerts.sort(key=lambda row: (alert_order.get(str(row["signal"]), 99), str(row["latest_at"])), reverse=False)
    daily: dict[str, dict[str, Any]] = {}
    for offset in range(29, -1, -1):
        day = (now - timedelta(days=offset)).date().isoformat()
        daily[day] = {"day": day, "logins": 0, "enrichments": 0, "imports": 0, "cleanup": 0, "llm_calls": 0}
    for row in daily_logins:
        if row["day"] in daily:
            daily[row["day"]]["logins"] = int(row["count"])
    for row in daily_jobs:
        if row["day"] in daily:
            target = {"enrich_cv": "enrichments", "cv_import": "imports", "cleanup_cv": "cleanup"}.get(str(row["kind"]))
            if target:
                daily[row["day"]][target] = int(row["count"])
    for row in daily_llm:
        if row["day"] in daily:
            daily[row["day"]]["llm_calls"] = int(row["count"])
    jobs = [dict(row) for row in jobs_30]
    return {
        "generated_at": now.isoformat(),
        "privacy": "Each row uses a dashboard-only pseudonym. No email, name, CV content, filename, raw account ID, prompt, or model output is returned.",
        "overview": {
            "members": len(member_rows),
            "active_7_days": sum(row["inactive_days"] is not None and row["inactive_days"] < 7 for row in member_rows),
            "active_30_days": sum(row["inactive_days"] is not None and row["inactive_days"] < 30 for row in member_rows),
            "llm_calls_30_days": int(usage_30["llm_calls"] or 0),
            "input_tokens_30_days": int(usage_30["input_tokens"] or 0),
            "output_tokens_30_days": int(usage_30["output_tokens"] or 0),
            "cost_microusd_30_days": int(usage_30["cost_microusd"] or 0),
            "unpriced_responses_30_days": int(usage_30["unpriced_responses"] or 0),
            "managed_ai_paused_accounts": sum(row["managed_ai_paused"] for row in member_rows),
        },
        "jobs_30_days": jobs,
        "daily_30_days": list(daily.values()),
        "job_alerts": alerts,
        "members": member_rows,
    }


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


def cloud_table_exists(con: GatewayConnection, table: str) -> bool:
    if con.backend == "postgres":
        row = con.execute("SELECT to_regclass(?) AS name", (table,)).fetchone()
        return bool(row and row["name"])
    return bool(
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
    )


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


def migration_004_background_job_idempotency(con: GatewayConnection) -> None:
    if con.backend == "postgres":
        con.execute("ALTER TABLE background_jobs ADD COLUMN IF NOT EXISTS idempotency_key TEXT")
        con.execute("ALTER TABLE background_jobs ADD COLUMN IF NOT EXISTS request_fingerprint TEXT")
    else:
        job_columns = sqlite_column_names(con, "background_jobs")
        if "idempotency_key" not in job_columns:
            con.execute("ALTER TABLE background_jobs ADD COLUMN idempotency_key TEXT")
        if "request_fingerprint" not in job_columns:
            con.execute("ALTER TABLE background_jobs ADD COLUMN request_fingerprint TEXT")
    con.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_background_jobs_idempotency
        ON background_jobs(member_id, database_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )


def migration_005_encrypt_private_cv_storage(con: GatewayConnection) -> None:
    # Normalized private projections duplicated almost the entire CV in clear
    # text and currently have no read consumers. Public profiles remain an
    # explicit, deliberately unencrypted projection.
    for table in ("hosted_cv_people", "hosted_cv_entries", "hosted_cv_publications"):
        if cloud_table_exists(con, table):
            con.execute(f"DELETE FROM {table}")
    if not cloud_table_exists(con, "account_databases"):
        return
    rows = con.execute(
        "SELECT id, member_id, sqlite_blob FROM account_databases"
    ).fetchall()
    for row in rows:
        content = bytes(row["sqlite_blob"])
        if is_encrypted_private_data(content):
            # Authenticate existing ciphertext before declaring the migration
            # complete; a missing/wrong key must stop startup safely.
            decrypt_database_content(
                content,
                member_id=str(row["member_id"]),
                database_id=str(row["id"]),
            )
            continue
        encrypted = encrypt_database_content(
            content,
            member_id=str(row["member_id"]),
            database_id=str(row["id"]),
        )
        con.execute("UPDATE account_databases SET sqlite_blob=? WHERE id=?", (encrypted, row["id"]))


def migration_006_background_job_support_ids(con: GatewayConnection) -> None:
    if con.backend == "postgres":
        con.execute("ALTER TABLE background_jobs ADD COLUMN IF NOT EXISTS support_id TEXT")
    elif "support_id" not in sqlite_column_names(con, "background_jobs"):
        con.execute("ALTER TABLE background_jobs ADD COLUMN support_id TEXT")
    con.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_background_jobs_support_id
        ON background_jobs(support_id)
        WHERE support_id IS NOT NULL
        """
    )


def migration_007_llm_usage_ledger(con: GatewayConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_usage_events (
            id TEXT PRIMARY KEY,
            event_key TEXT NOT NULL UNIQUE,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            database_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
            job_id TEXT REFERENCES background_jobs(id) ON DELETE SET NULL,
            operation TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER,
            cached_input_tokens INTEGER,
            output_tokens INTEGER,
            reasoning_tokens INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_llm_usage_member_created ON llm_usage_events(member_id, created_at)"
    )


def migration_008_premium_account_costs(con: GatewayConnection) -> None:
    definitions = {
        "priced_model": "TEXT",
        "pricing_version": "TEXT",
        "wholesale_cost_microusd": "BIGINT" if con.backend == "postgres" else "INTEGER",
        "charged_cost_microusd": "BIGINT" if con.backend == "postgres" else "INTEGER",
        "markup_basis_points": "INTEGER",
    }
    if con.backend == "postgres":
        for name, definition in definitions.items():
            con.execute(f"ALTER TABLE llm_usage_events ADD COLUMN IF NOT EXISTS {name} {definition}")
    else:
        columns = sqlite_column_names(con, "llm_usage_events")
        for name, definition in definitions.items():
            if name not in columns:
                con.execute(f"ALTER TABLE llm_usage_events ADD COLUMN {name} {definition}")
    for row in con.execute(
        """
        SELECT id, model, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens
        FROM llm_usage_events WHERE pricing_version IS NULL
        """
    ).fetchall():
        costs = usage_costs(dict(row))
        con.execute(
            """
            UPDATE llm_usage_events
            SET priced_model=?, pricing_version=?, wholesale_cost_microusd=?,
                charged_cost_microusd=?, markup_basis_points=?
            WHERE id=?
            """,
            (
                costs["priced_model"], costs["pricing_version"], costs["wholesale_cost_microusd"],
                costs["charged_cost_microusd"], costs["markup_basis_points"], row["id"],
            ),
        )
    amount_type = "BIGINT" if con.backend == "postgres" else "INTEGER"
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS premium_account_transactions (
            id TEXT PRIMARY KEY,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            amount_microusd {amount_type} NOT NULL,
            kind TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_premium_transactions_member_created "
        "ON premium_account_transactions(member_id, created_at)"
    )
    if cloud_table_exists(con, "members"):
        con.execute(
            """
            INSERT INTO premium_account_transactions(id, member_id, amount_microusd, kind, note, created_at)
            SELECT 'initial-credit:' || id, id, ?, 'promotional_credit', 'Early-access credit', ?
            FROM members
            WHERE 1=1
            ON CONFLICT(id) DO NOTHING
            """,
            (INITIAL_PREMIUM_CREDIT_MICROUSD, utc_now()),
        )


def migration_009_verified_email_and_passkeys(con: GatewayConnection) -> None:
    if cloud_table_exists(con, "members"):
        if con.backend == "postgres":
            con.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS email_verified_at TEXT")
            con.execute(
                "UPDATE members SET email_verified_at=COALESCE(account_created_at, created_at) "
                "WHERE email IS NOT NULL AND email_verified_at IS NULL"
            )
        else:
            member_columns = sqlite_column_names(con, "members")
            if "email_verified_at" not in member_columns:
                con.execute("ALTER TABLE members ADD COLUMN email_verified_at TEXT")
            if {"email", "account_created_at", "created_at"}.issubset(member_columns):
                con.execute(
                    "UPDATE members SET email_verified_at=COALESCE(account_created_at, created_at) "
                    "WHERE email IS NOT NULL AND email_verified_at IS NULL"
                )
    blob_type = "BYTEA" if con.backend == "postgres" else "BLOB"
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS email_verification_tokens (
            token_hash TEXT PRIMARY KEY,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token_hash TEXT PRIMARY KEY,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS passkey_credentials (
            credential_id TEXT PRIMARY KEY,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            public_key {blob_type} NOT NULL,
            sign_count BIGINT NOT NULL DEFAULT 0,
            transports_json TEXT NOT NULL DEFAULT '[]',
            label TEXT NOT NULL DEFAULT 'Passkey',
            created_at TEXT NOT NULL,
            last_used_at TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS passkey_challenges (
            challenge_hash TEXT PRIMARY KEY,
            member_id TEXT REFERENCES members(id) ON DELETE CASCADE,
            purpose TEXT NOT NULL CHECK (purpose IN ('register', 'authenticate')),
            challenge TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_passkeys_member ON passkey_credentials(member_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_passkey_challenges_expiry ON passkey_challenges(expires_at)")


def migration_010_zotero_oauth(con: GatewayConnection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS zotero_oauth_requests (
            request_token_hash TEXT PRIMARY KEY,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            database_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
            request_secret_ciphertext TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_zotero_oauth_requests_expiry ON zotero_oauth_requests(expires_at)")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS zotero_oauth_connections (
            member_id TEXT PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
            last_database_id TEXT REFERENCES account_databases(id) ON DELETE SET NULL,
            zotero_user_id TEXT NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            api_key_ciphertext TEXT NOT NULL,
            access_json TEXT NOT NULL DEFAULT '{}',
            verified_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def migration_011_paypal_beta_topups(con: GatewayConnection) -> None:
    amount_type = "BIGINT" if con.backend == "postgres" else "INTEGER"
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS paypal_beta_topups (
            id TEXT PRIMARY KEY,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            claim_key_hash TEXT NOT NULL UNIQUE,
            amount_microusd {amount_type} NOT NULL,
            currency TEXT NOT NULL,
            confirmation_mode TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_paypal_beta_topups_member_created "
        "ON paypal_beta_topups(member_id, created_at)"
    )


def migration_012_vitamine_plus(con: GatewayConnection) -> None:
    definitions = {
        "plus_trial_ends_at": "TEXT",
        "plus_paid_until": "TEXT",
        "plus_dev_toggle_enabled": "INTEGER NOT NULL DEFAULT 0",
        "plus_dev_override": "INTEGER",
    }
    if cloud_table_exists(con, "members"):
        if con.backend == "postgres":
            for name, definition in definitions.items():
                con.execute(f"ALTER TABLE members ADD COLUMN IF NOT EXISTS {name} {definition}")
        else:
            columns = sqlite_column_names(con, "members")
            for name, definition in definitions.items():
                if name not in columns:
                    con.execute(f"ALTER TABLE members ADD COLUMN {name} {definition}")
        trial_end = (datetime.now(timezone.utc) + timedelta(days=PLUS_TRIAL_DAYS)).isoformat()
        con.execute(
            "UPDATE members SET plus_trial_ends_at=? WHERE plus_trial_ends_at IS NULL",
            (trial_end,),
        )
    # The former 2x debit and promotional-credit balance are superseded by a
    # private at-cost usage ledger. Historical measurements are normalized too.
    con.execute(
        """
        UPDATE llm_usage_events
        SET charged_cost_microusd=wholesale_cost_microusd,
            markup_basis_points=10000
        WHERE wholesale_cost_microusd IS NOT NULL
        """
    )
    if cloud_table_exists(con, "members") and cloud_table_exists(con, "premium_account_transactions"):
        con.execute("DELETE FROM premium_account_transactions")


def migration_013_cleanup_cv_jobs(con: GatewayConnection) -> None:
    """Permit the additive cleanup job kind without changing any private CV data."""
    if con.backend == "postgres":
        rows = con.execute(
            """
            SELECT conname FROM pg_constraint
            WHERE conrelid='background_jobs'::regclass AND contype='c'
              AND pg_get_constraintdef(oid) LIKE '%%kind%%'
            """
        ).fetchall()
        for row in rows:
            name = str(row["conname"])
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                con.execute(f'ALTER TABLE background_jobs DROP CONSTRAINT "{name}"')
        con.execute(
            "ALTER TABLE background_jobs ADD CONSTRAINT background_jobs_kind_check "
            "CHECK (kind IN ('cv_import', 'enrich_cv', 'cleanup_cv'))"
        )
        return
    schema_row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='background_jobs'"
    ).fetchone()
    schema_sql = str(schema_row["sql"] or "") if schema_row else ""
    # Some early SQLite test/dev stores had no kind CHECK at all, and so
    # already accept the new additive value without a risky table rebuild.
    if "CHECK" not in schema_sql.upper() or "cleanup_cv" in schema_sql:
        return
    # SQLite cannot alter a CHECK constraint. Rebuild only the queue table and
    # preserve every existing job and index-defining column.
    con.execute("ALTER TABLE background_jobs RENAME TO background_jobs_before_cleanup")
    con.execute(
        """
        CREATE TABLE background_jobs (
            id TEXT PRIMARY KEY,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            database_id TEXT NOT NULL REFERENCES account_databases(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('cv_import', 'enrich_cv', 'cleanup_cv')),
            status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed')),
            base_revision INTEGER NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            idempotency_key TEXT,
            request_fingerprint TEXT,
            progress_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT,
            error_message TEXT,
            support_id TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            heartbeat_at TEXT,
            finished_at TEXT,
            updated_at TEXT NOT NULL,
            acknowledged_at TEXT
        )
        """
    )
    con.execute(
        """
        INSERT INTO background_jobs
          SELECT id, member_id, database_id, kind, status, base_revision, payload_json,
                 idempotency_key, request_fingerprint, progress_json, result_json,
                 error_message, support_id, created_at, started_at, heartbeat_at,
                 finished_at, updated_at, acknowledged_at
        FROM background_jobs_before_cleanup
        """
    )
    con.execute("DROP TABLE background_jobs_before_cleanup")
    con.execute("CREATE INDEX IF NOT EXISTS idx_background_jobs_member ON background_jobs(member_id, status, created_at)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_background_jobs_database ON background_jobs(database_id, status, created_at)")
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_background_jobs_idempotency "
        "ON background_jobs(member_id, database_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    con.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_background_jobs_support_id "
        "ON background_jobs(support_id) WHERE support_id IS NOT NULL"
    )


def migration_014_admin_usage_dashboard(con: GatewayConnection) -> None:
    """Add privacy-minimal account activity needed by the operator dashboard."""
    # A few very early development fixtures intentionally contain only the
    # table introduced by the migration under test. A real cloud store always
    # has members from migration 1, but keep that historical test path safe.
    if not cloud_table_exists(con, "members"):
        return
    if con.backend == "postgres":
        con.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS last_login_at TEXT")
    elif "last_login_at" not in sqlite_column_names(con, "members"):
        con.execute("ALTER TABLE members ADD COLUMN last_login_at TEXT")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS member_activity_events (
            id TEXT PRIMARY KEY,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL CHECK (event_type IN ('login', 'cv_import', 'enrich_cv', 'cleanup_cv')),
            occurred_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_member_activity_events_member_time "
        "ON member_activity_events(member_id, occurred_at)"
    )


def migration_015_openai_consent(con: GatewayConnection) -> None:
    """Record the current, versioned permission for managed OpenAI processing."""
    if not cloud_table_exists(con, "members"):
        return
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS openai_processing_consents (
            member_id TEXT PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
            policy_version TEXT NOT NULL,
            granted_at TEXT,
            withdrawn_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS openai_processing_consent_events (
            id TEXT PRIMARY KEY,
            member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
            action TEXT NOT NULL CHECK (action IN ('granted', 'withdrawn')),
            policy_version TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )
        """
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_openai_processing_consent_events_member "
        "ON openai_processing_consent_events(member_id, occurred_at)"
    )
    if con.backend == "postgres":
        con.execute("ALTER TABLE background_jobs ADD COLUMN IF NOT EXISTS cancel_requested_at TEXT")
    elif "cancel_requested_at" not in sqlite_column_names(con, "background_jobs"):
        con.execute("ALTER TABLE background_jobs ADD COLUMN cancel_requested_at TEXT")


CLOUD_MIGRATIONS = (
    (1, migration_001_initial_cloud_schema),
    (2, migration_002_account_workspaces),
    (3, migration_003_oauth_and_portraits),
    (4, migration_004_background_job_idempotency),
    (5, migration_005_encrypt_private_cv_storage),
    (6, migration_006_background_job_support_ids),
    (7, migration_007_llm_usage_ledger),
    (8, migration_008_premium_account_costs),
    (9, migration_009_verified_email_and_passkeys),
    (10, migration_010_zotero_oauth),
    (11, migration_011_paypal_beta_topups),
    (12, migration_012_vitamine_plus),
    (13, migration_013_cleanup_cv_jobs),
    (14, migration_014_admin_usage_dashboard),
    (15, migration_015_openai_consent),
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
    if not str(member["email_verified_at"] or "").strip():
        raise HTTPException(status_code=403, detail="Confirm your email address before using VitaMine.")
    return member


def openai_processing_consent(member_id: str) -> dict[str, Any]:
    with connect() as con:
        row = con.execute(
            "SELECT * FROM openai_processing_consents WHERE member_id=?",
            (member_id,),
        ).fetchone()
    granted_at = str(row["granted_at"] or "") if row else ""
    withdrawn_at = str(row["withdrawn_at"] or "") if row else ""
    policy_version = str(row["policy_version"] or "") if row else ""
    return {
        "accepted": bool(granted_at and not withdrawn_at and policy_version == OPENAI_CV_PROCESSING_CONSENT_VERSION),
        "policy_version": OPENAI_CV_PROCESSING_CONSENT_VERSION,
        "granted_at": granted_at or None,
        "withdrawn_at": withdrawn_at or None,
    }


def require_openai_processing_consent(member_id: str) -> dict[str, Any]:
    consent = openai_processing_consent(member_id)
    if not consent["accepted"]:
        raise HTTPException(
            status_code=403,
            detail=(
                "Before VitaMine sends CV or template content to OpenAI's managed AI service, "
                "review and accept the OpenAI processing notice in Settings."
            ),
        )
    return consent


def openai_account_spend_limit_microusd() -> int:
    raw = str(os.environ.get("VITAMINE_OPENAI_ACCOUNT_SPEND_LIMIT_MICROUSD") or "").strip()
    try:
        configured = int(raw) if raw else OPENAI_ACCOUNT_SPEND_LIMIT_MICROUSD
    except ValueError:
        configured = OPENAI_ACCOUNT_SPEND_LIMIT_MICROUSD
    # An accidental empty, negative, or implausibly high setting must not
    # silently remove the account-level safety guard.
    return min(max(configured, 1), 50_000_000)


def openai_account_spend_status(member_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Return the rolling managed-AI cost guard for one hosted account."""
    current = now or datetime.now(timezone.utc)
    cutoff = (current - timedelta(hours=OPENAI_ACCOUNT_SPEND_WINDOW_HOURS)).isoformat()
    with connect() as con:
        row = con.execute(
            """
            SELECT COALESCE(SUM(wholesale_cost_microusd), 0) AS spent_microusd,
                   SUM(CASE WHEN wholesale_cost_microusd IS NULL THEN 1 ELSE 0 END) AS unpriced_responses
            FROM llm_usage_events
            WHERE member_id=? AND created_at>=?
            """,
            (member_id, cutoff),
        ).fetchone()
    spent_microusd = int(row["spent_microusd"] or 0)
    unpriced_responses = int(row["unpriced_responses"] or 0)
    limit_microusd = openai_account_spend_limit_microusd()
    return {
        "limit_microusd": limit_microusd,
        "window_hours": OPENAI_ACCOUNT_SPEND_WINDOW_HOURS,
        "spent_microusd": spent_microusd,
        "unpriced_responses": unpriced_responses,
        "paused": spent_microusd >= limit_microusd or unpriced_responses > 0,
    }


def require_openai_account_spend_available(member_id: str) -> dict[str, Any]:
    status = openai_account_spend_status(member_id)
    if status["unpriced_responses"]:
        raise HTTPException(
            status_code=429,
            detail=(
                "Managed AI is paused for this account because a recent OpenAI response could not be priced. "
                "Please contact support."
            ),
        )
    if status["paused"]:
        limit = status["limit_microusd"] / 1_000_000
        raise HTTPException(
            status_code=429,
            detail=(
                f"Managed AI is paused for this account after reaching the ${limit:.2f} safety limit "
                f"in the last {status['window_hours']} hours. It resumes automatically as earlier usage leaves the rolling window."
            ),
        )
    return status


def parsed_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def vitamine_plus_status(member: Any, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    trial_end = parsed_utc(member["plus_trial_ends_at"] if "plus_trial_ends_at" in member.keys() else None)
    paid_until = parsed_utc(member["plus_paid_until"] if "plus_paid_until" in member.keys() else None)
    paid = bool(paid_until and paid_until > now)
    trial = bool(trial_end and trial_end > now)
    dev_enabled = bool(member["plus_dev_toggle_enabled"] if "plus_dev_toggle_enabled" in member.keys() else False)
    raw_override = member["plus_dev_override"] if "plus_dev_override" in member.keys() else None
    dev_override = bool(raw_override) if dev_enabled and raw_override is not None else None
    active = dev_override if dev_override is not None else paid or trial
    active_until = paid_until if paid else trial_end if trial else None
    return {
        "active": active,
        "plan": "developer" if dev_override is not None else "paid" if paid else "trial" if trial else "free",
        "label": "VitaMine+",
        "trial_ends_at": trial_end.isoformat() if trial_end else None,
        "paid_until": paid_until.isoformat() if paid_until else None,
        "active_until": active_until.isoformat() if active_until else None,
        "price_eur_per_year": 25,
        "developer_toggle": dev_enabled,
        "developer_override": dev_override,
    }


def require_vitamine_plus(member: Any) -> dict[str, Any]:
    status = vitamine_plus_status(member)
    if not status["active"]:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "vitamine_plus_required",
                "message": "This feature is part of VitaMine+.",
                "plus": status,
            },
        )
    return status


def member_plus_status(member_id: str) -> dict[str, Any]:
    with connect() as con:
        member = con.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
    return vitamine_plus_status(member) if member else {"active": False, "plan": "free", "label": "VitaMine+"}


def public_app_url() -> str:
    return os.environ.get("VITAMINE_PUBLIC_URL", PASSKEY_ORIGIN).rstrip("/")


def smtp_configuration() -> dict[str, Any]:
    return {
        "host": os.environ.get("VITAMINE_SMTP_HOST", "").strip(),
        "port": int(os.environ.get("VITAMINE_SMTP_PORT", "465")),
        "username": os.environ.get("VITAMINE_SMTP_USERNAME", "").strip(),
        "password": os.environ.get("VITAMINE_SMTP_PASSWORD", ""),
        "from_address": os.environ.get("VITAMINE_SMTP_FROM", "hello@vitamine.cloud").strip(),
        "use_tls": os.environ.get("VITAMINE_SMTP_USE_TLS", "true").lower() not in {"0", "false", "no"},
    }


def send_transactional_email(message: EmailMessage) -> None:
    config = smtp_configuration()
    if not config["host"] or not config["username"] or not config["password"]:
        raise RuntimeError("Outbound email is not configured.")
    message["From"] = f"VitaMine <{config['from_address']}>"
    context = ssl.create_default_context()
    if config["use_tls"] and config["port"] == 465:
        with smtplib.SMTP_SSL(config["host"], config["port"], context=context, timeout=20) as client:
            client.login(config["username"], config["password"])
            client.send_message(message)
    else:
        with smtplib.SMTP(config["host"], config["port"], timeout=20) as client:
            if config["use_tls"]:
                client.starttls(context=context)
            client.login(config["username"], config["password"])
            client.send_message(message)


def send_verification_email(email: str, token: str) -> None:
    link = f"{public_app_url()}/api/account/verify-email?token={quote(token)}"
    message = EmailMessage()
    message["Subject"] = "Confirm your VitaMine email address"
    message["To"] = email
    message.set_content(
        "Welcome to VitaMine. Confirm your email address using this link:\n\n"
        f"{link}\n\nThis link expires in 24 hours. If you did not create this account, ignore this message."
    )
    send_transactional_email(message)


def send_password_reset_email(email: str, token: str) -> None:
    link = f"{public_app_url()}/?password_reset={quote(token)}"
    message = EmailMessage()
    message["Subject"] = "Reset your VitaMine password"
    message["To"] = email
    message.set_content(
        "Use this link to choose a new VitaMine password:\n\n"
        f"{link}\n\nThis link expires in one hour. If you did not request a reset, ignore this message."
    )
    send_transactional_email(message)


def create_email_verification(con: GatewayConnection, member_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM email_verification_tokens WHERE member_id=? OR expires_at<?", (member_id, now.isoformat()))
    con.execute(
        "INSERT INTO email_verification_tokens(token_hash, member_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (secret_hash(token), member_id, (now + timedelta(seconds=EMAIL_VERIFICATION_MAX_AGE)).isoformat(), now.isoformat()),
    )
    return token


def create_password_reset(con: GatewayConnection, member_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM password_reset_tokens WHERE member_id=? OR expires_at<?", (member_id, now.isoformat()))
    con.execute(
        "INSERT INTO password_reset_tokens(token_hash, member_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (secret_hash(token), member_id, (now + timedelta(seconds=PASSWORD_RESET_MAX_AGE)).isoformat(), now.isoformat()),
    )
    return token


def passkey_settings() -> tuple[str, str]:
    origin = public_app_url()
    host = origin.split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    rp_id = os.environ.get("VITAMINE_PASSKEY_RP_ID", PASSKEY_RP_ID).strip() or host
    return rp_id, origin


def base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def unbase64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def store_passkey_challenge(
    con: GatewayConnection, member_id: str | None, purpose: str, challenge: bytes
) -> str:
    challenge_id = secrets.token_urlsafe(24)
    now = datetime.now(timezone.utc)
    con.execute("DELETE FROM passkey_challenges WHERE expires_at<?", (now.isoformat(),))
    con.execute(
        """
        INSERT INTO passkey_challenges
          (challenge_hash, member_id, purpose, challenge, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            secret_hash(challenge_id), member_id, purpose, base64url(challenge),
            (now + timedelta(seconds=PASSKEY_CHALLENGE_MAX_AGE)).isoformat(), now.isoformat(),
        ),
    )
    return challenge_id


def consume_passkey_challenge(con: GatewayConnection, challenge_id: str, purpose: str) -> Any:
    row = con.execute(
        "SELECT * FROM passkey_challenges WHERE challenge_hash=? AND purpose=? AND expires_at>? FOR UPDATE",
        (secret_hash(challenge_id), purpose, utc_now()),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="This passkey request expired. Please try again.")
    con.execute("DELETE FROM passkey_challenges WHERE challenge_hash=?", (row["challenge_hash"],))
    return row


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
        "VITAMINE_LLM_USAGE_PATH": str(session_dir / "llm-usage.jsonl"),
        "VITAMINE_PLUS_ACTIVE": "1" if member_plus_status(str(row["member_id"]))["active"] else "0",
        "VITAMINE_OPENAI_PROCESSING_CONSENT": "1"
        if openai_processing_consent(str(row["member_id"]))["accepted"] else "0",
    }
    env.pop("ZOTERO_API_KEY", None)
    zotero_api_key = account_zotero_api_key(str(row["member_id"]))
    if zotero_api_key:
        env["ZOTERO_API_KEY"] = zotero_api_key
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


def sync_hosted_projections(con: GatewayConnection, cv_id: str, _snapshot_path: Path) -> None:
    # Private projections were retired when application-level encryption was
    # introduced. Keeping them empty avoids a plaintext copy of the CV.
    con.execute("DELETE FROM hosted_cv_people WHERE cv_id=?", (cv_id,))
    con.execute("DELETE FROM hosted_cv_entries WHERE cv_id=?", (cv_id,))
    con.execute("DELETE FROM hosted_cv_publications WHERE cv_id=?", (cv_id,))


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
            encrypted_content = encrypt_database_content(
                content, member_id=member_id, database_id=database_id
            )
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
                        encrypted_content,
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
            encrypted_content = encrypt_database_content(
                content, member_id=member_id, database_id=database_id
            )
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
                        encrypted_content,
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
    db_path.write_bytes(
        decrypt_database_content(
            database["sqlite_blob"],
            member_id=str(database["member_id"]),
            database_id=str(database["id"]),
        )
    )
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
        "support_id": row["support_id"] or "",
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


def normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    key = value.strip()
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key must contain 8–200 letters, numbers, dots, colons, underscores, or hyphens.",
        )
    return key


def paypal_beta_topup_config() -> dict[str, Any]:
    payment_url = os.getenv("VITAMINE_PAYPAL_BETA_TOPUP_URL", "").strip()
    parsed = urlparse(payment_url) if payment_url else None
    enabled = bool(
        parsed
        and parsed.scheme == "https"
        and (parsed.hostname or "").lower() in {"paypal.me", "www.paypal.com"}
    )
    return {
        "enabled": enabled,
        "payment_url": payment_url if enabled else "",
        "amount_microusd": PAYPAL_BETA_TOPUP_MICROUSD,
        "currency": "USD",
        "confirmation_mode": "trusted_beta_self_attested",
    }


def premium_balance_microusd(con: GatewayConnection, member_id: str) -> int:
    credit_row = con.execute(
        "SELECT COALESCE(SUM(amount_microusd), 0) AS total "
        "FROM premium_account_transactions WHERE member_id=?",
        (member_id,),
    ).fetchone()
    usage_row = con.execute(
        "SELECT COALESCE(SUM(charged_cost_microusd), 0) AS total "
        "FROM llm_usage_events WHERE member_id=?",
        (member_id,),
    ).fetchone()
    return int(credit_row["total"] or 0) - int(usage_row["total"] or 0)


def background_job_fingerprint(kind: str, payload: dict[str, Any]) -> str:
    material = json.dumps(
        {"kind": kind, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def matching_idempotent_job(
    con: GatewayConnection,
    *,
    member_id: str,
    database_id: str,
    idempotency_key: str,
    kind: str,
    request_fingerprint: str,
) -> Any | None:
    row = con.execute(
        """
        SELECT * FROM background_jobs
        WHERE member_id=? AND database_id=? AND idempotency_key=?
        """,
        (member_id, database_id, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    if row["kind"] != kind or not hmac.compare_digest(
        str(row["request_fingerprint"] or ""), request_fingerprint
    ):
        raise HTTPException(
            status_code=409,
            detail="This Idempotency-Key was already used for a different background request.",
        )
    return row


def create_background_job(
    *,
    workspace: Any,
    kind: str,
    payload: dict[str, Any],
    job_id: str,
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    database_id = str(workspace["database_id"] or "")
    member_id = str(workspace["member_id"])
    if not database_id:
        raise HTTPException(status_code=409, detail="Save this CV to your account before starting a background job.")
    idempotency_key = normalize_idempotency_key(idempotency_key)
    request_fingerprint = background_job_fingerprint(kind, payload) if idempotency_key else None
    if idempotency_key and request_fingerprint:
        with connect() as con:
            replay = matching_idempotent_job(
                con,
                member_id=member_id,
                database_id=database_id,
                idempotency_key=idempotency_key,
                kind=kind,
                request_fingerprint=request_fingerprint,
            )
        if replay is not None:
            return background_job_payload(replay), False
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
            (database_id, member_id),
        ).fetchone()
        if database is None:
            raise HTTPException(status_code=404, detail="The saved VitaMine database no longer exists.")
        if idempotency_key and request_fingerprint:
            replay = matching_idempotent_job(
                con,
                member_id=member_id,
                database_id=database_id,
                idempotency_key=idempotency_key,
                kind=kind,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return background_job_payload(replay), False
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
        if background_job_uses_managed_openai(kind, payload):
            require_openai_account_spend_available(member_id)
        progress = {
            "phase": "queued",
            "message": "Waiting for the VitaMine worker",
            "percent": 0,
        }
        parameters = (
            job_id,
            member_id,
            database_id,
            kind,
            int(database["revision"]),
            json.dumps(payload, ensure_ascii=False),
            idempotency_key,
            request_fingerprint,
            json.dumps(progress),
            now,
            now,
        )
        if idempotency_key:
            cursor = con.execute(
                """
                INSERT INTO background_jobs
                  (id, member_id, database_id, kind, status, base_revision, payload_json,
                   idempotency_key, request_fingerprint, progress_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (member_id, database_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL DO NOTHING
                """,
                parameters,
            )
            if cursor.rowcount == 0:
                replay = matching_idempotent_job(
                    con,
                    member_id=member_id,
                    database_id=database_id,
                    idempotency_key=idempotency_key,
                    kind=kind,
                    request_fingerprint=str(request_fingerprint),
                )
                if replay is None:
                    raise RuntimeError("The idempotent background request could not be recovered.")
                return background_job_payload(replay), False
        else:
            con.execute(
                """
                INSERT INTO background_jobs
                  (id, member_id, database_id, kind, status, base_revision, payload_json,
                   idempotency_key, request_fingerprint, progress_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)
                """,
                parameters,
            )
        row = con.execute("SELECT * FROM background_jobs WHERE id=?", (job_id,)).fetchone()
        record_member_activity(con, member_id, kind, occurred_at=now)
    return background_job_payload(row), True


def claim_next_background_job() -> Any | None:
    with connect() as con:
        row = con.execute(
            """
            SELECT * FROM background_jobs
            WHERE status='queued' AND cancel_requested_at IS NULL
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
            WHERE id=? AND status='queued' AND cancel_requested_at IS NULL
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


def ingest_llm_usage_events(job: Any, path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return 0
    keys = set(job.keys())
    job_id = job["id"] if "id" in keys else None
    operation = str(job["kind"] if "kind" in keys else "unknown")[:100]
    inserted = 0
    with connect() as con:
        for line in lines:
            event = parsed_json_object(line)
            event_key = str(event.get("event_key") or "")[:100]
            provider = str(event.get("provider") or "")[:40]
            model = str(event.get("model") or "unknown")[:120]
            if not event_key or provider != "openai" or not re.fullmatch(r"[A-Za-z0-9._:/-]+", model):
                continue
            counts = []
            for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens"):
                value = event.get(key)
                counts.append(int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None)
            if event.get("usage_available") is False:
                costs = {
                    "priced_model": None,
                    "pricing_version": "usage-unavailable",
                    "wholesale_cost_microusd": None,
                    "charged_cost_microusd": None,
                    "markup_basis_points": None,
                }
            else:
                costs = usage_costs({**event, **dict(zip(
                    ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens"), counts
                ))})
            cursor = con.execute(
                """
                INSERT INTO llm_usage_events
                  (id, event_key, member_id, database_id, job_id, operation, provider, model,
                   input_tokens, cached_input_tokens, output_tokens, reasoning_tokens,
                   priced_model, pricing_version, wholesale_cost_microusd, charged_cost_microusd,
                   markup_basis_points, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_key) DO NOTHING
                """,
                (
                    secrets.token_urlsafe(18), event_key, job["member_id"], job["database_id"], job_id,
                    operation, provider, model, *counts, costs["priced_model"], costs["pricing_version"],
                    costs["wholesale_cost_microusd"], costs["charged_cost_microusd"],
                    costs["markup_basis_points"],
                    str(event.get("occurred_at") or utc_now())[:40],
                ),
            )
            inserted += max(0, cursor.rowcount)
    return inserted


def background_job_uses_managed_openai(kind: str, payload: dict[str, Any]) -> bool:
    if kind in {"cv_import", "cleanup_cv"}:
        return True
    if kind != "enrich_cv":
        return False
    return str(payload.get("scope") or "") != "citation_network"


def background_job_requires_openai_consent(job: Any) -> bool:
    return background_job_uses_managed_openai(str(job["kind"]), parsed_json_object(job["payload_json"]))


def execute_background_job(job: Any) -> None:
    directory = (job_root() / str(job["id"])).resolve()
    try:
        directory.relative_to(job_root())
    except ValueError as exc:
        raise RuntimeError("Unsafe background-job path.") from exc
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    work_directory = (job_work_root() / str(job["id"])).resolve()
    try:
        work_directory.relative_to(job_work_root())
    except ValueError as exc:
        raise RuntimeError("Unsafe background-job work path.") from exc
    shutil.rmtree(work_directory, ignore_errors=True)
    work_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    database_path = work_directory / "workspace.vitamine"
    result_path = work_directory / "result.json"
    progress_path = work_directory / "progress.json"
    usage_path = work_directory / "llm-usage.jsonl"
    log_path = work_directory / "worker.log"
    if background_job_requires_openai_consent(job):
        require_openai_processing_consent(str(job["member_id"]))
        require_openai_account_spend_available(str(job["member_id"]))
    with connect() as con:
        database = con.execute(
            """
            SELECT id, member_id, sqlite_blob, revision FROM account_databases
            WHERE id=? AND member_id=? AND deleted_at IS NULL
            """,
            (job["database_id"], job["member_id"]),
        ).fetchone()
    if database is None:
        raise RuntimeError("The saved VitaMine database no longer exists.")
    if int(database["revision"]) != int(job["base_revision"]):
        raise RuntimeError("The CV changed before its background job could start.")
    database_path.write_bytes(
        decrypt_database_content(
            database["sqlite_blob"],
            member_id=str(database["member_id"]),
            database_id=str(database["id"]),
        )
    )
    database_path.chmod(0o600)
    validate_workspace_database(database_path)
    payload = parsed_json_object(job["payload_json"])
    payload_path = work_directory / "payload.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    payload_path.chmod(0o600)
    if str(job["kind"]) == "cv_import":
        runtime_uploads = work_directory / "uploads"
        runtime_uploads.mkdir(mode=0o700)
        for item in payload.get("files") or []:
            if not isinstance(item, dict):
                continue
            stored_name = Path(str(item.get("stored_name") or "")).name
            encrypted_name = Path(str(item.get("encrypted_name") or f"{stored_name}.enc")).name
            if not stored_name:
                raise RuntimeError("A queued CV upload has an invalid filename.")
            encrypted_path = directory / "uploads" / encrypted_name
            if not encrypted_path.is_file():
                raise RuntimeError("A queued CV upload is unavailable.")
            content = decrypt_private_data(
                encrypted_path.read_bytes(),
                context=job_upload_encryption_context(str(job["member_id"]), str(job["id"]), stored_name),
            )
            destination = runtime_uploads / stored_name
            destination.write_bytes(content)
            destination.chmod(0o600)
    env = {
        **os.environ,
        "VITAMINE_DB": str(database_path),
        "VITAMINE_DATA": str(work_directory / "data"),
        "VITAMINE_OUTPUT": str(work_directory / "output"),
        "VITAMINE_PREFERENCES": str(work_directory / "preferences.json"),
        "VITAMINE_CLOUD_WORKER": "1",
        "VITAMINE_LLM_USAGE_PATH": str(usage_path),
        "VITAMINE_OPENAI_PROCESSING_CONSENT": "1",
    }
    env.pop("ZOTERO_API_KEY", None)
    zotero_api_key = account_zotero_api_key(str(job["member_id"]))
    if zotero_api_key:
        env["ZOTERO_API_KEY"] = zotero_api_key
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
            if background_job_requires_openai_consent(job):
                try:
                    require_openai_processing_consent(str(job["member_id"]))
                    ingest_llm_usage_events(job, usage_path)
                    require_openai_account_spend_available(str(job["member_id"]))
                except HTTPException:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    raise RuntimeError("Managed AI permission was withdrawn or this account reached its safety limit; the job was stopped.")
            if progress_path.exists():
                raw_progress = progress_path.read_text(encoding="utf-8", errors="replace")
                if raw_progress != last_progress:
                    progress = parsed_json_object(raw_progress)
                    if progress:
                        update_background_job_progress(str(job["id"]), progress)
                    last_progress = raw_progress
        returncode = int(process.returncode or 0)
    ingest_llm_usage_events(job, usage_path)
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
    shutil.rmtree(work_directory, ignore_errors=True)


def fail_background_job(job_id: str, error: Exception) -> None:
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
        shutil.rmtree(job_work_root() / job_id, ignore_errors=True)
        return
    support_id = new_support_id()
    category = failure_category(error)
    message = f"The background process failed. Support ID: {support_id}"
    now = utc_now()
    with connect() as con:
        job = con.execute(
            "SELECT kind FROM background_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        job_kind = (
            str(job["kind"])
            if job and job["kind"] in {"cv_import", "enrich_cv", "cleanup_cv"}
            else "unknown"
        )
        con.execute(
            """
            UPDATE background_jobs
            SET status='failed', error_message=?, support_id=?, heartbeat_at=?, finished_at=?,
                updated_at=?, progress_json=?
            WHERE id=?
            """,
            (
                message,
                support_id,
                now,
                now,
                now,
                json.dumps({"phase": "failed", "message": message, "percent": 100}),
                job_id,
            ),
        )
    log_support_event(
        "background_job_failed",
        support_id,
        category=category,
        job_id=job_id,
        job_kind=job_kind,
    )
    shutil.rmtree(job_root() / job_id, ignore_errors=True)
    shutil.rmtree(job_work_root() / job_id, ignore_errors=True)


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
    shutil.rmtree(job_work_root(), ignore_errors=True)
    job_work_root().mkdir(parents=True, exist_ok=True, mode=0o700)
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


def run_background_job_runner() -> None:
    """Run the durable background-job worker in its dedicated process.

    Recovery belongs here rather than in the HTTP gateway: restarting Uvicorn
    must never reset a job that this process is currently executing.
    """
    JOB_STOP.clear()
    initialize_database()
    job_root().mkdir(parents=True, exist_ok=True, mode=0o700)
    recover_background_jobs()
    background_job_loop()


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
    if workspace_request_uses_managed_openai(worker_path, request.method):
        require_openai_account_spend_available(str(row["member_id"]))
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
    if row["database_id"]:
        ingest_llm_usage_events(
            {
                "member_id": row["member_id"],
                "database_id": row["database_id"],
                "kind": f"workspace:{worker_path.lstrip('/') or 'root'}",
            },
            Path(row["db_path"]).parent / "llm-usage.jsonl",
        )
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


def workspace_request_uses_managed_openai(worker_path: str, method: str) -> bool:
    if method.upper() != "POST":
        return False
    path = "/" + worker_path.lstrip("/")
    if re.fullmatch(r"/api/entries/\d+/translate", path):
        return True
    if path in {"/api/export-templates", "/api/cv-import/upload", "/api/actions/enrich-cv", "/api/actions/cleanup-cv"}:
        return True
    return bool(
        re.fullmatch(r"/api/export-formats/[^/]+/prompt-plan", path)
        or re.fullmatch(r"/api/actions/export/[^/]+", path)
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


def zotero_oauth_result_redirect(result: str) -> RedirectResponse:
    return RedirectResponse(url=f"/?{urlencode({'zotero_oauth': result})}", status_code=303)


def restart_workspace_with_current_connections(workspace: Any) -> None:
    stop_workspace(workspace, remove_files=False)
    pid = start_workspace_worker(workspace)
    with connect() as con:
        con.execute(
            "UPDATE workspace_sessions SET pid=?, last_seen_at=? WHERE id=?",
            (pid, utc_now(), workspace["id"]),
        )


app = FastAPI(
    title="VitaMine Cloud",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.exception_handler(Exception)
async def unexpected_request_failure(request: Request, error: Exception) -> JSONResponse:
    support_id = new_support_id()
    log_support_event(
        "unexpected_request_failure",
        support_id,
        category=failure_category(error),
        endpoint=request_endpoint_template(request),
        method=(
            request.method
            if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
            else "OTHER"
        ),
    )
    return JSONResponse(
        {
            "detail": f"Something went wrong. Support ID: {support_id}",
            "support_id": support_id,
        },
        status_code=500,
    )


@app.on_event("startup")
def startup() -> None:
    initialize_database()
    cleanup_expired_workspaces()
    CLEANUP_STOP.clear()
    threading.Thread(
        target=workspace_cleanup_loop,
        name="vitamine-workspace-cleanup",
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
        "can_update_activities": bool(row is not None and orcid_write_scope_granted(str(row["scope"] or ""))),
    }


@app.post("/gateway/orcid/oauth/start")
def start_orcid_oauth(
    request: Request,
    authorization: str | None = Header(default=None),
    write_access: bool = False,
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
            "scope": f"/authenticate {ORCID_WRITE_SCOPE}" if write_access else "/authenticate",
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


async def workspace_profile_sync_call(row: Any, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as client:
            response = await client.post(f"http://127.0.0.1:{row['port']}{path}", json=payload)
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="The private VitaMine workspace is unavailable.") from exc
    return result if isinstance(result, dict) else {}


@app.post("/gateway/profile-sync/orcid/actions")
async def apply_orcid_profile_sync_actions(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    workspace = workspace_for_request(request, authorization)
    if active_background_job(str(workspace["database_id"])):
        raise HTTPException(status_code=409, detail="Wait for the current background process to finish before updating ORCID.")
    payload = await request.json()
    direction = str(payload.get("direction") or "")
    if direction != "add_remote":
        raise HTTPException(status_code=400, detail="Profile sync only adds current VitaMine publications to ORCID.")
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="Choose at least one publication.")
    prepared = await workspace_profile_sync_call(
        workspace,
        "/api/profile-sync/orcid/prepare",
        {"direction": direction, "ids": ids},
    )
    items = prepared.get("items") or []
    orcid_id, access_token, _scope = account_orcid_write_connection(str(workspace["member_id"]))
    api_base = orcid_api_base_url()
    completed_ids: list[int] = []
    remote_ids: dict[int, str] = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as client:
            for item in items:
                recommendation_id = int(item.get("id") or 0)
                publication = item.get("payload") or {}
                if not recommendation_id or not isinstance(publication, dict):
                    continue
                response = await client.post(
                    f"{api_base}/v3.0/{quote(orcid_id, safe='')}/work",
                    headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json", "Content-Type": "application/json"},
                    json=orcid_work_payload(publication),
                )
                response.raise_for_status()
                remote_id = orcid_put_code_from_location(response.headers.get("Location", ""))
                if remote_id:
                    remote_ids[recommendation_id] = remote_id
                completed_ids.append(recommendation_id)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="ORCID could not apply this profile update. Please try again.") from exc
    completed = await workspace_profile_sync_call(
        workspace,
        "/api/profile-sync/orcid/complete",
        {"direction": direction, "ids": completed_ids, "remote_ids": remote_ids},
    )
    persist_workspace_snapshot(workspace)
    return {"ok": True, "completed": int(completed.get("completed") or 0)}


@app.post("/gateway/profile-sync/zotero/actions")
async def apply_zotero_profile_sync_actions(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    workspace = workspace_for_request(request, authorization)
    if active_background_job(str(workspace["database_id"])):
        raise HTTPException(status_code=409, detail="Wait for the current background process to finish before updating Zotero.")
    payload = await request.json()
    direction = str(payload.get("direction") or "")
    if direction != "add_remote":
        raise HTTPException(status_code=400, detail="Profile sync only adds current VitaMine publications to Zotero.")
    ids = payload.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="Choose at least one publication.")
    prepared = await workspace_profile_sync_call(
        workspace,
        "/api/profile-sync/zotero/prepare",
        {"direction": direction, "ids": ids},
    )
    items = prepared.get("items") or []
    source = prepared.get("source") or {}
    required_source = {"library_type", "library_id", "source_mode"}
    if not isinstance(source, dict) or not required_source.issubset(source) or source.get("source_mode") not in {"my_publications", "collection", "library"}:
        raise HTTPException(status_code=409, detail="Choose a Zotero source before updating Zotero.")
    if source["source_mode"] == "collection" and not str(source.get("collection_key") or ""):
        raise HTTPException(status_code=409, detail="Choose a Zotero collection before updating Zotero.")
    api_key = account_zotero_write_connection(str(workspace["member_id"]), source)
    prefix = f"https://api.zotero.org/{source['library_type']}/{source['library_id']}"
    completed_ids: list[int] = []
    remote_ids: dict[int, str] = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as client:
            for item in items:
                recommendation_id = int(item.get("id") or 0)
                publication = item.get("payload") or {}
                if not recommendation_id or not isinstance(publication, dict):
                    continue
                remote_ids[recommendation_id] = await zotero_add_to_selected_source(
                    client, api_key=api_key, source=source, publication=publication
                )
                completed_ids.append(recommendation_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 412:
            raise HTTPException(status_code=409, detail="A Zotero item changed while VitaMine was updating it. Refresh and try again.") from exc
        raise HTTPException(status_code=502, detail="Zotero could not apply this selected-source update. Please try again.") from exc
    completed = await workspace_profile_sync_call(
        workspace,
        "/api/profile-sync/zotero/complete",
        {"direction": direction, "ids": completed_ids, "remote_ids": remote_ids},
    )
    persist_workspace_snapshot(workspace)
    return {"ok": True, "completed": int(completed.get("completed") or 0)}


@app.get("/gateway/zotero/oauth/status")
def zotero_oauth_status(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        row = con.execute(
            "SELECT zotero_user_id, username, access_json, verified_at FROM zotero_oauth_connections WHERE member_id=?",
            (member["id"],),
        ).fetchone()
    return {
        "ok": True, "configured": zotero_oauth_config() is not None,
        "connected": row is not None,
        "zotero_user_id": str(row["zotero_user_id"]) if row is not None else "",
        "username": str(row["username"] or "") if row is not None else "",
        "access": parsed_json_object(row["access_json"]) if row is not None else {},
        "verified_at": str(row["verified_at"]) if row is not None else None,
        "can_write": bool(
            row is not None
            and any(
                bool(permission.get("library") and permission.get("write"))
                for permission in _zotero_permission_rows(parsed_json_object(row["access_json"]))
            )
        ),
    }


@app.post("/gateway/zotero/oauth/start")
async def start_zotero_oauth(
    request: Request,
    authorization: str | None = Header(default=None),
    write_access: bool = True,
) -> dict[str, Any]:
    config = zotero_oauth_config()
    if config is None:
        raise HTTPException(status_code=503, detail="Zotero sign-in is not configured.")
    workspace = workspace_for_request(request, authorization)
    if not workspace["database_id"]:
        raise HTTPException(status_code=409, detail="Open a saved VitaMine CV before connecting Zotero.")
    if active_background_job(str(workspace["database_id"])):
        raise HTTPException(status_code=409, detail="Wait for the current background process to finish before connecting Zotero.")
    credentials = await request_zotero_temporary_credentials()
    store_zotero_oauth_request(
        token=credentials["token"], secret=credentials["secret"],
        member_id=str(workspace["member_id"]), database_id=str(workspace["database_id"]),
    )
    permissions = urlencode({
        "name": "VitaMine", "library_access": "1", "notes_access": "0",
        "write_access": "1" if write_access else "0",
        "all_groups": "write" if write_access else "read",
    })
    return {
        "ok": True,
        "authorization_url": f"{config['authorize_url']}?oauth_token={quote(credentials['token'])}&{permissions}",
    }


@app.get("/gateway/zotero/oauth/callback")
async def complete_zotero_oauth(
    request: Request,
    oauth_token: str = "",
    oauth_verifier: str = "",
    authorization: str | None = Header(default=None),
) -> RedirectResponse:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    oauth_request = consume_zotero_oauth_request(oauth_token, str(member["id"]))
    if not oauth_verifier or len(oauth_verifier) > 500:
        return zotero_oauth_result_redirect("cancelled")
    workspace = workspace_for_request(request, authorization)
    if not hmac.compare_digest(str(workspace["database_id"] or ""), str(oauth_request["database_id"])):
        return zotero_oauth_result_redirect("workspace-changed")
    try:
        token = await exchange_zotero_access_token(
            oauth_token, decrypt_oauth_token(str(oauth_request["request_secret_ciphertext"])), oauth_verifier
        )
        access = await verify_zotero_api_key(str(token["api_key"]))
        store_zotero_oauth_connection(
            member_id=str(member["id"]), database_id=str(workspace["database_id"]), token=token, access=access
        )
        restart_workspace_with_current_connections(workspace)
    except HTTPException:
        return zotero_oauth_result_redirect("link-error")
    return zotero_oauth_result_redirect("connected")


@app.delete("/gateway/zotero/oauth/connection")
async def disconnect_zotero_oauth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    workspace = workspace_for_request(request, authorization)
    if active_background_job(str(workspace["database_id"])):
        raise HTTPException(status_code=409, detail="Wait for the current background process to finish before disconnecting Zotero.")
    api_key = account_zotero_api_key(str(workspace["member_id"]))
    if not api_key:
        return {"ok": True, "disconnected": False}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as client:
            response = await client.delete(
                f"https://api.zotero.org/keys/{quote(api_key, safe='')}",
                headers={"Zotero-API-Key": api_key, "Zotero-API-Version": "3"},
            )
            if response.status_code not in {204, 404}:
                response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Zotero could not revoke this connection. Please try again.") from exc
    with connect() as con:
        con.execute("DELETE FROM zotero_oauth_connections WHERE member_id=?", (workspace["member_id"],))
    restart_workspace_with_current_connections(workspace)
    return {"ok": True, "disconnected": True}


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
        member = con.execute("SELECT * FROM members WHERE id=?", (row["member_id"],)).fetchone()
    return {
        "ok": True,
        "database_id": row["database_id"],
        "filename": row["original_filename"],
        "expires_at": row["expires_at"],
        "persistent": bool(row["database_id"]),
        "background_jobs": True,
        "plus": vitamine_plus_status(member),
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
            SELECT id, member_id, sqlite_blob FROM account_databases
            WHERE id=? AND member_id=? AND deleted_at IS NULL
            """,
            (row["database_id"], row["member_id"]),
        ).fetchone()
    if database is None:
        raise HTTPException(status_code=404, detail="The saved VitaMine database no longer exists.")
    content = decrypt_database_content(
        database["sqlite_blob"],
        member_id=str(database["member_id"]),
        database_id=str(database["id"]),
    )
    return database_download_response(content, filename)


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
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    workspace = workspace_for_request(request, authorization)
    with connect() as con:
        member = con.execute("SELECT * FROM members WHERE id=?", (workspace["member_id"],)).fetchone()
    require_vitamine_plus(member)
    require_openai_processing_consent(str(member["id"]))
    if not files:
        raise HTTPException(status_code=400, detail="Please choose at least one CV document.")
    job_id = secrets.token_urlsafe(18)
    directory = (job_root() / job_id).resolve()
    uploads = directory / "uploads"
    try:
        directory.relative_to(job_root())
        uploads.mkdir(parents=True, mode=0o700)
        payload_files: list[dict[str, Any]] = []
        total_bytes = 0
        for index, file in enumerate(files, start=1):
            original_name = Path(file.filename or f"uploaded-cv-{index}").name
            safe_name = cloud_cv_import_name(original_name)
            stored_name = f"{index}-{safe_name}"
            encrypted_name = f"{stored_name}.enc"
            destination = uploads / encrypted_name
            file_digest = hashlib.sha256()
            file_bytes = 0
            upload_content = bytearray()
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                file_digest.update(chunk)
                file_bytes += len(chunk)
                total_bytes += len(chunk)
                if total_bytes > MAX_JOB_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="The selected CV documents exceed the 50 MB upload limit.",
                    )
                upload_content.extend(chunk)
            encrypted_upload = encrypt_private_data(
                bytes(upload_content),
                context=job_upload_encryption_context(str(workspace["member_id"]), job_id, stored_name),
            )
            destination.write_bytes(encrypted_upload)
            destination.chmod(0o600)
            payload_files.append(
                {
                    "stored_name": stored_name,
                    "encrypted_name": encrypted_name,
                    "original_name": original_name,
                    "size_bytes": file_bytes,
                    "sha256": file_digest.hexdigest(),
                }
            )
        job, created = create_background_job(
            workspace=workspace,
            kind="cv_import",
            payload={"files": payload_files},
            job_id=job_id,
            idempotency_key=idempotency_key,
        )
        if not created:
            shutil.rmtree(directory, ignore_errors=True)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        for file in files:
            await file.close()
    return JSONResponse(
        {"ok": True, "background": True, "idempotent_replay": not created, "job": job},
        status_code=202 if created else 200,
    )


@app.post("/api/cloud/jobs/enrich-cv")
def queue_enrichment_job(
    request: Request,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    workspace = workspace_for_request(request, authorization)
    with connect() as con:
        member = con.execute("SELECT * FROM members WHERE id=?", (workspace["member_id"],)).fetchone()
    require_vitamine_plus(member)
    require_openai_processing_consent(str(member["id"]))
    job_id = secrets.token_urlsafe(18)
    job, created = create_background_job(
        workspace=workspace,
        kind="enrich_cv",
        payload={},
        job_id=job_id,
        idempotency_key=idempotency_key,
    )
    return JSONResponse(
        {"ok": True, "background": True, "idempotent_replay": not created, "job": job},
        status_code=202 if created else 200,
    )


@app.post("/api/cloud/jobs/citation-network")
def queue_citation_network_job(
    request: Request,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    workspace = workspace_for_request(request, authorization)
    with connect() as con:
        member = con.execute("SELECT * FROM members WHERE id=?", (workspace["member_id"],)).fetchone()
    require_vitamine_plus(member)
    job, created = create_background_job(
        workspace=workspace,
        kind="enrich_cv",
        payload={"scope": "citation_network"},
        job_id=secrets.token_urlsafe(18),
        idempotency_key=idempotency_key,
    )
    return JSONResponse(
        {"ok": True, "background": True, "idempotent_replay": not created, "job": job},
        status_code=202 if created else 200,
    )


@app.post("/api/cloud/jobs/cleanup-cv")
def queue_cleanup_job(
    request: Request,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    workspace = workspace_for_request(request, authorization)
    with connect() as con:
        member = con.execute("SELECT * FROM members WHERE id=?", (workspace["member_id"],)).fetchone()
    require_vitamine_plus(member)
    require_openai_processing_consent(str(member["id"]))
    job, created = create_background_job(
        workspace=workspace,
        kind="cleanup_cv",
        payload={},
        job_id=secrets.token_urlsafe(18),
        idempotency_key=idempotency_key,
    )
    return JSONResponse(
        {"ok": True, "background": True, "idempotent_replay": not created, "job": job},
        status_code=202 if created else 200,
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
        "openai_processing_consent": openai_processing_consent(str(member["id"])),
        "plus": vitamine_plus_status(member),
        "databases": [account_database_payload(row) for row in rows],
        "profile": {
            "slug": profile["slug"],
            "source_database_id": profile.get("source_database_id", ""),
            "updated_at": profile["updated_at"],
        } if profile else None,
    }


@app.put("/api/account/openai-processing-consent")
def update_openai_processing_consent(
    payload: OpenAIProcessingConsentUpdate,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    member_id = str(member["id"])
    now = utc_now()
    with connect() as con:
        existing = con.execute(
            "SELECT * FROM openai_processing_consents WHERE member_id=?",
            (member_id,),
        ).fetchone()
        currently_accepted = bool(
            existing
            and existing["granted_at"]
            and not existing["withdrawn_at"]
            and existing["policy_version"] == OPENAI_CV_PROCESSING_CONSENT_VERSION
        )
        if payload.accepted:
            con.execute(
                """
                INSERT INTO openai_processing_consents
                  (member_id, policy_version, granted_at, withdrawn_at, updated_at)
                VALUES (?, ?, ?, NULL, ?)
                ON CONFLICT(member_id) DO UPDATE SET
                  policy_version=excluded.policy_version, granted_at=excluded.granted_at,
                  withdrawn_at=NULL, updated_at=excluded.updated_at
                """,
                (member_id, OPENAI_CV_PROCESSING_CONSENT_VERSION, now, now),
            )
            if not currently_accepted:
                con.execute(
                    """
                    INSERT INTO openai_processing_consent_events
                      (id, member_id, action, policy_version, occurred_at)
                    VALUES (?, ?, 'granted', ?, ?)
                    """,
                    (secrets.token_urlsafe(18), member_id, OPENAI_CV_PROCESSING_CONSENT_VERSION, now),
                )
        elif currently_accepted:
            con.execute(
                """
                UPDATE openai_processing_consents
                SET withdrawn_at=?, updated_at=? WHERE member_id=?
                """,
                (now, now, member_id),
            )
            con.execute(
                """
                INSERT INTO openai_processing_consent_events
                  (id, member_id, action, policy_version, occurred_at)
                VALUES (?, ?, 'withdrawn', ?, ?)
                """,
                (secrets.token_urlsafe(18), member_id, OPENAI_CV_PROCESSING_CONSENT_VERSION, now),
            )
    return {"ok": True, "consent": openai_processing_consent(member_id)}


def remove_member_job_artifacts(job_ids: list[str]) -> None:
    for job_id in job_ids:
        safe_job_id = Path(job_id).name
        if safe_job_id != job_id:
            raise RuntimeError("Unsafe background-job identifier.")
        for root in (job_root(), job_work_root()):
            target = (root / safe_job_id).resolve()
            if target.parent != root.resolve():
                raise RuntimeError("Unsafe background-job path.")
            shutil.rmtree(target, ignore_errors=True)


@app.delete("/api/account")
def delete_account(
    payload: AccountDeletion,
    request: Request,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    member_id = str(member["id"])
    if payload.confirmation.strip().upper() != "DELETE":
        raise HTTPException(status_code=422, detail='Type DELETE to confirm permanent account deletion.')
    if not verify_password(payload.password, str(member["password_hash"] or "")):
        raise HTTPException(status_code=403, detail="Your password could not be confirmed.")
    with connect() as con:
        jobs = con.execute(
            "SELECT id, status FROM background_jobs WHERE member_id=?",
            (member_id,),
        ).fetchall()
        running = [str(row["id"]) for row in jobs if row["status"] == "running"]
        if running:
            raise HTTPException(
                status_code=409,
                detail="Wait for the running background process to finish before deleting your account.",
            )
        queued_job_ids = [str(row["id"]) for row in jobs if row["status"] == "queued"]
        if queued_job_ids:
            con.execute(
                "UPDATE background_jobs SET cancel_requested_at=? WHERE member_id=? AND status='queued'",
                (utc_now(), member_id),
            )
        workspaces = con.execute(
            "SELECT * FROM workspace_sessions WHERE member_id=?",
            (member_id,),
        ).fetchall()
        counts = {
            "cvs": int(con.execute("SELECT COUNT(*) AS count FROM account_databases WHERE member_id=?", (member_id,)).fetchone()["count"]),
            "public_profiles": int(con.execute("SELECT COUNT(*) AS count FROM public_profiles WHERE member_id=?", (member_id,)).fetchone()["count"]),
            "sessions": int(con.execute("SELECT COUNT(*) AS count FROM device_credentials WHERE member_id=?", (member_id,)).fetchone()["count"]),
            "queued_artifacts": len(queued_job_ids),
        }
        job_ids = [str(row["id"]) for row in jobs]
    for workspace in workspaces:
        stop_workspace(workspace)
    remove_member_job_artifacts(job_ids)
    with connect() as con:
        cursor = con.execute("DELETE FROM members WHERE id=?", (member_id,))
        if cursor.rowcount != 1:
            raise HTTPException(status_code=404, detail="This account no longer exists.")
    response = JSONResponse({"ok": True, "erased": counts})
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(WORKSPACE_COOKIE, path="/")
    return response


@app.get("/api/account/premium-account")
def premium_account_summary(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        credit_row = con.execute(
            "SELECT COALESCE(SUM(amount_microusd), 0) AS total FROM premium_account_transactions WHERE member_id=?",
            (member["id"],),
        ).fetchone()
        usage_row = con.execute(
            """
            SELECT COALESCE(SUM(charged_cost_microusd), 0) AS charged,
                   COALESCE(SUM(wholesale_cost_microusd), 0) AS wholesale,
                   SUM(CASE WHEN charged_cost_microusd IS NULL THEN 1 ELSE 0 END) AS unpriced
            FROM llm_usage_events WHERE member_id=?
            """,
            (member["id"],),
        ).fetchone()
        daily = [dict(row) for row in con.execute(
                """
                SELECT substr(created_at, 1, 10) AS day,
                       COALESCE(SUM(charged_cost_microusd), 0) AS charged_microusd,
                       COALESCE(SUM(wholesale_cost_microusd), 0) AS wholesale_microusd
                FROM llm_usage_events
                WHERE member_id=? AND created_at>=?
                GROUP BY substr(created_at, 1, 10)
                ORDER BY day
                """,
                (member["id"], (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()),
            ).fetchall()]
        recent = [dict(row) for row in con.execute(
                """
                SELECT operation, model, charged_cost_microusd, wholesale_cost_microusd, created_at
                FROM llm_usage_events WHERE member_id=?
                ORDER BY created_at DESC LIMIT 12
                """,
                (member["id"],),
            ).fetchall()]
    credited = int(credit_row["total"] or 0)
    charged = int(usage_row["charged"] or 0)
    return {
        "currency": "USD",
        "balance_microusd": credited - charged,
        "credited_microusd": credited,
        "charged_microusd": charged,
        "wholesale_cost_microusd": int(usage_row["wholesale"] or 0),
        "unpriced_responses": int(usage_row["unpriced"] or 0),
        "markup_factor": 1,
        "pricing_source": "https://developers.openai.com/api/docs/pricing",
        "daily": daily,
        "recent": recent,
        "enforcement_enabled": True,
        "spend_guard": openai_account_spend_status(str(member["id"])),
        "top_up": paypal_beta_topup_config(),
    }


@app.put("/api/account/plus-developer-toggle")
def set_plus_developer_toggle(
    payload: PlusDeveloperToggle,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    if not bool(member["plus_dev_toggle_enabled"]):
        raise HTTPException(status_code=404, detail="Developer entitlement control is not available.")
    with connect() as con:
        workspace = con.execute(
            "SELECT * FROM workspace_sessions WHERE member_id=?",
            (member["id"],),
        ).fetchone()
        if workspace is not None and workspace_has_active_job(workspace):
            raise HTTPException(status_code=409, detail="Wait for the active background job before switching plans.")
        con.execute(
            "UPDATE members SET plus_dev_override=? WHERE id=? AND plus_dev_toggle_enabled=1",
            (1 if payload.active else 0, member["id"]),
        )
        updated = con.execute("SELECT * FROM members WHERE id=?", (member["id"],)).fetchone()
    if workspace is not None:
        stop_workspace(workspace, remove_files=False)
        with connect() as con:
            con.execute("UPDATE workspace_sessions SET pid=NULL WHERE id=?", (workspace["id"],))
    return {
        "ok": True,
        "plus": vitamine_plus_status(updated),
        "workspace_worker_restarted": workspace is not None,
    }


@app.post("/api/account/premium-account/paypal-beta-topup")
def confirm_paypal_beta_topup(
    payload: PaypalBetaTopupClaim,
    request: Request,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    account_member(authorization, request.cookies.get(SESSION_COOKIE))
    raise HTTPException(status_code=410, detail="Balance top-ups have been retired in favor of VitaMine+.")


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
    content = decrypt_database_content(
        row["sqlite_blob"], member_id=str(row["member_id"]), database_id=str(row["id"])
    )
    return database_download_response(content, row["filename"])


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


@app.get("/assets/legal.css", response_class=FileResponse)
def legal_css() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "legal.css", media_type="text/css")


@app.get("/privacy", response_class=FileResponse)
def privacy_policy() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "privacy.html", media_type="text/html")


@app.get("/terms", response_class=FileResponse)
def terms_of_service() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "terms.html", media_type="text/html")


@app.get("/imprint", response_class=FileResponse)
def imprint() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "imprint.html", media_type="text/html")


@app.get("/assets/admin.css", response_class=FileResponse)
def admin_css() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "admin.css", media_type="text/css")


@app.get("/assets/admin.js", response_class=FileResponse)
def admin_javascript() -> FileResponse:
    return FileResponse(CLOUD_STATIC / "admin.js", media_type="text/javascript")


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
                SET email=?, password_hash=?, display_name=?, account_created_at=?, last_seen_at=?,
                    plus_trial_ends_at=?
                WHERE id=? AND email IS NULL AND password_hash IS NULL
                """,
                (
                    email, password_hash, display_name, now, now,
                    (datetime.now(timezone.utc) + timedelta(days=PLUS_TRIAL_DAYS)).isoformat(),
                    member["id"],
                ),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=409, detail="This invitation already belongs to an account.")
            verification_token = create_email_verification(con, member["id"])
    except Exception as exc:
        if not is_unique_violation(exc):
            raise
        raise HTTPException(status_code=409, detail="An account with that email address already exists.") from exc
    database_id = promote_current_workspace(member["id"])
    try:
        send_verification_email(email, verification_token)
    except Exception:
        LOGGER.exception("email_verification_delivery_failed member_id=%s", member["id"])
        raise HTTPException(
            status_code=503,
            detail="Your account was created, but the confirmation email could not be sent. Please use resend confirmation.",
        )
    return {
        "ok": True,
        "account": {"email": email, "display_name": display_name},
        "promoted_database_id": database_id,
        "email_verification_required": True,
    }


@app.get("/api/account/verify-email")
def verify_account_email(token: str) -> RedirectResponse:
    now = utc_now()
    with connect() as con:
        row = con.execute(
            """
            SELECT * FROM email_verification_tokens
            WHERE token_hash=? AND used_at IS NULL AND expires_at>?
            FOR UPDATE
            """,
            (secret_hash(token), now),
        ).fetchone()
        if row is None:
            return RedirectResponse("/?email_confirmation=invalid", status_code=303)
        con.execute("UPDATE members SET email_verified_at=? WHERE id=?", (now, row["member_id"]))
        con.execute("UPDATE email_verification_tokens SET used_at=? WHERE token_hash=?", (now, row["token_hash"]))
    return RedirectResponse("/?email_confirmation=verified", status_code=303)


@app.post("/api/account/resend-verification")
def resend_account_verification(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    member = authenticated_member(authorization, request.cookies.get(SESSION_COOKIE))
    if member["email_verified_at"]:
        return {"ok": True}
    with connect() as con:
        token = create_email_verification(con, member["id"])
    try:
        send_verification_email(str(member["email"]), token)
    except Exception:
        LOGGER.exception("email_verification_delivery_failed member_id=%s", member["id"])
        raise HTTPException(status_code=503, detail="The confirmation email could not be sent.")
    return {"ok": True}


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
        now = utc_now()
        con.execute("UPDATE members SET last_seen_at=?, last_login_at=? WHERE id=?", (now, now, member["id"]))
        record_member_activity(con, str(member["id"]), "login", occurred_at=now)
    response = JSONResponse(
        {
            "ok": bool(member["email_verified_at"]),
            "email_verification_required": not bool(member["email_verified_at"]),
            "email": member["email"],
        }
    )
    set_session_cookie(response, request, token)
    return response


@app.post("/api/account/password-reset/request")
def request_password_reset(payload: PasswordResetRequest) -> dict[str, bool]:
    email = normalize_email(payload.email)
    token: str | None = None
    with connect() as con:
        member = con.execute(
            "SELECT * FROM members WHERE email=? AND revoked_at IS NULL AND email_verified_at IS NOT NULL",
            (email,),
        ).fetchone()
        if member is not None:
            recent = con.execute(
                "SELECT created_at FROM password_reset_tokens WHERE member_id=? ORDER BY created_at DESC LIMIT 1",
                (member["id"],),
            ).fetchone()
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
            if recent is None or datetime.fromisoformat(recent["created_at"]) < cutoff:
                token = create_password_reset(con, member["id"])
    if token is not None:
        try:
            send_password_reset_email(email, token)
        except Exception:
            LOGGER.exception("password_reset_delivery_failed")
    # Always return the same response so this endpoint cannot enumerate accounts.
    return {"ok": True}


@app.post("/api/account/password-reset/complete")
def complete_password_reset(payload: PasswordResetCompletion) -> JSONResponse:
    now = utc_now()
    workspace = None
    with connect() as con:
        row = con.execute(
            """
            SELECT * FROM password_reset_tokens
            WHERE token_hash=? AND used_at IS NULL AND expires_at>?
            FOR UPDATE
            """,
            (secret_hash(payload.token), now),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=400, detail="This password-reset link is invalid or expired.")
        con.execute("UPDATE members SET password_hash=?, last_seen_at=? WHERE id=?", (hash_password(payload.password), now, row["member_id"]))
        con.execute("UPDATE password_reset_tokens SET used_at=? WHERE token_hash=?", (now, row["token_hash"]))
        con.execute("UPDATE device_credentials SET revoked_at=? WHERE member_id=? AND revoked_at IS NULL", (now, row["member_id"]))
        workspace = con.execute("SELECT * FROM workspace_sessions WHERE member_id=?", (row["member_id"],)).fetchone()
    if workspace:
        if workspace["database_id"] and Path(workspace["db_path"]).exists() and not workspace_has_active_job(workspace):
            persist_workspace_snapshot(workspace)
        stop_workspace(workspace)
        with connect() as con:
            con.execute("DELETE FROM workspace_sessions WHERE id=?", (workspace["id"],))
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(WORKSPACE_COOKIE, path="/")
    return response


@app.post("/api/account/passkeys/register/options")
def passkey_registration_options(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.structs import (
        AuthenticatorSelectionCriteria,
        PublicKeyCredentialDescriptor,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )

    member = authenticated_member(authorization, request.cookies.get(SESSION_COOKIE))
    if not str(member["email"] or "").strip() or not str(member["password_hash"] or "").strip():
        raise HTTPException(status_code=403, detail="Create your VitaMine account before adding a passkey.")
    with connect() as con:
        credentials = con.execute(
            "SELECT credential_id FROM passkey_credentials WHERE member_id=?", (member["id"],)
        ).fetchall()
        options = generate_registration_options(
            rp_id=passkey_settings()[0],
            rp_name="VitaMine",
            user_id=str(member["id"]).encode("utf-8"),
            user_name=str(member["email"]),
            user_display_name=str(member["display_name"] or member["email"]),
            exclude_credentials=[
                PublicKeyCredentialDescriptor(id=unbase64url(row["credential_id"])) for row in credentials
            ],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        challenge_id = store_passkey_challenge(con, member["id"], "register", options.challenge)
    return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}


@app.post("/api/account/passkeys/register/complete")
def complete_passkey_registration(
    payload: PasskeyResponse,
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    from webauthn import verify_registration_response

    member = authenticated_member(authorization, request.cookies.get(SESSION_COOKIE))
    if not str(member["email"] or "").strip() or not str(member["password_hash"] or "").strip():
        raise HTTPException(status_code=403, detail="Create your VitaMine account before adding a passkey.")
    with connect() as con:
        challenge = consume_passkey_challenge(con, payload.challenge_id, "register")
        if challenge["member_id"] != member["id"]:
            raise HTTPException(status_code=403, detail="This passkey request belongs to another account.")
        try:
            verified = verify_registration_response(
                credential=payload.credential,
                expected_challenge=unbase64url(challenge["challenge"]),
                expected_rp_id=passkey_settings()[0],
                expected_origin=passkey_settings()[1],
                require_user_verification=True,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Passkey registration could not be verified.") from exc
        credential_id = base64url(verified.credential_id)
        transports = payload.credential.get("response", {}).get("transports", [])
        try:
            con.execute(
                """
                INSERT INTO passkey_credentials
                  (credential_id, member_id, public_key, sign_count, transports_json, label, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    credential_id, member["id"], verified.credential_public_key,
                    int(verified.sign_count), json.dumps(transports), payload.label.strip() or "Passkey", utc_now(),
                ),
            )
        except Exception as exc:
            if not is_unique_violation(exc):
                raise
            raise HTTPException(status_code=409, detail="This passkey is already registered.") from exc
    return {"ok": True}


@app.post("/api/account/passkeys/login/options")
def passkey_login_options(payload: PasskeyEmail, request: Request) -> dict[str, Any]:
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement

    email = normalize_email(payload.email) if payload.email else None
    with connect() as con:
        member = None
        credentials = []
        if email:
            enforce_login_rate_limit(con, request, email)
            member = con.execute(
                "SELECT * FROM members WHERE email=? AND revoked_at IS NULL AND email_verified_at IS NOT NULL",
                (email,),
            ).fetchone()
            credentials = [] if member is None else con.execute(
                "SELECT * FROM passkey_credentials WHERE member_id=?", (member["id"],)
            ).fetchall()
            if not credentials:
                raise HTTPException(status_code=404, detail="No passkey is registered for this account.")
        options = generate_authentication_options(
            rp_id=passkey_settings()[0],
            allow_credentials=None if not email else [
                PublicKeyCredentialDescriptor(id=unbase64url(row["credential_id"])) for row in credentials
            ],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        challenge_id = store_passkey_challenge(
            con, member["id"] if member is not None else None, "authenticate", options.challenge
        )
    return {"challenge_id": challenge_id, "options": json.loads(options_to_json(options))}


@app.post("/api/account/passkeys/login/complete")
def complete_passkey_login(payload: PasskeyResponse, request: Request) -> JSONResponse:
    from webauthn import verify_authentication_response

    credential_id = str(payload.credential.get("id", ""))
    with connect() as con:
        challenge = consume_passkey_challenge(con, payload.challenge_id, "authenticate")
        credential = con.execute(
            """
            SELECT p.* FROM passkey_credentials p
            JOIN members m ON m.id=p.member_id
            WHERE p.credential_id=? AND m.revoked_at IS NULL AND m.email_verified_at IS NOT NULL
            FOR UPDATE
            """,
            (credential_id,),
        ).fetchone()
        if credential is None:
            raise HTTPException(status_code=401, detail="This passkey is not registered.")
        if challenge["member_id"] is not None and credential["member_id"] != challenge["member_id"]:
            raise HTTPException(status_code=401, detail="This passkey is not registered for that account.")
        try:
            verified = verify_authentication_response(
                credential=payload.credential,
                expected_challenge=unbase64url(challenge["challenge"]),
                expected_rp_id=passkey_settings()[0],
                expected_origin=passkey_settings()[1],
                credential_public_key=bytes(credential["public_key"]),
                credential_current_sign_count=int(credential["sign_count"]),
                require_user_verification=True,
            )
        except Exception as exc:
            raise HTTPException(status_code=401, detail="Passkey sign-in could not be verified.") from exc
        now = utc_now()
        con.execute(
            "UPDATE passkey_credentials SET sign_count=?, last_used_at=? WHERE credential_id=?",
            (int(verified.new_sign_count), now, credential_id),
        )
        token = issue_device_credential(con, credential["member_id"])
        con.execute("UPDATE members SET last_seen_at=?, last_login_at=? WHERE id=?", (now, now, credential["member_id"]))
        record_member_activity(con, str(credential["member_id"]), "login", occurred_at=now)
    response = JSONResponse({"ok": True})
    set_session_cookie(response, request, token)
    return response


@app.get("/api/account/passkeys")
def list_passkeys(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    member = account_member(authorization, request.cookies.get(SESSION_COOKIE))
    with connect() as con:
        rows = con.execute(
            "SELECT credential_id, label, created_at, last_used_at FROM passkey_credentials "
            "WHERE member_id=? ORDER BY created_at",
            (member["id"],),
        ).fetchall()
    return {"passkeys": [dict(row) for row in rows]}


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
        "email_verified": bool(member["email_verified_at"]),
        "email": member["email"] or "",
        "display_name": member["display_name"] or "",
        "database_count": int(database_count),
        "profile": dict(profile) if profile else None,
        "plus": vitamine_plus_status(member),
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
    content = decrypt_database_content(
        database["sqlite_blob"],
        member_id=str(database["member_id"]),
        database_id=str(database["id"]),
    )
    with materialized_database_blob(content) as database_path:
        return build_public_profile_snapshot(
            database_path,
            database_id=database_id,
            blocks=blocks,
        )


def current_public_profile_row(row: Any) -> Any:
    """Lazily rebuild snapshots created by older projection code."""
    current = public_snapshot(row, include_internal=True)
    try:
        version = int(current.get("schema_version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version >= PUBLIC_PROFILE_SCHEMA_VERSION:
        return row
    database_id = str(current.get("source_database_id") or "")
    if not database_id:
        return row
    try:
        with connect() as con:
            database = con.execute(
                """
                SELECT * FROM account_databases
                WHERE id=? AND member_id=? AND deleted_at IS NULL
                """,
                (database_id, str(row["member_id"])),
            ).fetchone()
        if database is None:
            return row
        content = decrypt_database_content(
            database["sqlite_blob"], member_id=str(database["member_id"]),
            database_id=str(database["id"]),
        )
        with materialized_database_blob(content) as database_path:
            rebuilt = build_public_profile_snapshot(
                database_path, database_id=database_id, blocks=current.get("blocks"),
            )
        preserve_public_profile_customizations(rebuilt, current)
        with connect() as con:
            write_public_profile(
                con, slug=str(row["slug"]), member_id=str(row["member_id"]), snapshot=rebuilt,
            )
            refreshed = con.execute(
                "SELECT * FROM public_profiles WHERE slug=?", (str(row["slug"]),)
            ).fetchone()
        return refreshed or row
    except Exception:
        LOGGER.warning("public_profile_projection_upgrade_failed")
        return row


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


@app.get("/admin", response_class=FileResponse)
def admin_dashboard_page() -> FileResponse:
    return FileResponse(
        CLOUD_STATIC / "admin.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/admin/login")
def login_admin(payload: AdminLogin, request: Request) -> JSONResponse:
    settings = admin_settings()
    if settings is None:
        raise HTTPException(status_code=503, detail="The operator dashboard is not configured.")
    username, password_hash = settings
    attempted_identity = f"admin:{payload.username.strip().casefold()[:160]}"
    with connect() as con:
        enforce_login_rate_limit(con, request, attempted_identity)
        valid = hmac.compare_digest(payload.username.strip(), username) and verify_password(payload.password, password_hash)
        record_login_attempt(con, request, attempted_identity, valid)
    if not valid:
        raise HTTPException(status_code=401, detail="Operator username or password is incorrect.")
    response = JSONResponse({"ok": True})
    set_admin_session_cookie(response, request, issue_admin_session(password_hash))
    return response


@app.post("/api/admin/logout")
def logout_admin(request: Request) -> JSONResponse:
    authenticated_admin(request)
    response = JSONResponse({"ok": True})
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    return response


@app.get("/api/admin/dashboard")
def admin_dashboard_data(request: Request) -> dict[str, Any]:
    authenticated_admin(request)
    return admin_dashboard_payload()


@app.get("/api/public/{slug}")
def public_profile_json(slug: str) -> JSONResponse:
    normalized_slug = normalize_slug(slug)
    with connect() as con:
        row = con.execute("SELECT * FROM public_profiles WHERE slug=?", (normalized_slug,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Public profile not found.")
    row = current_public_profile_row(row)
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
    plus_active: bool = True,
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
      <link rel="stylesheet" href="/assets/public-profile.css?v=20260801-citation-map-plus">
      <script src="/assets/public-profile.js?v=20260801-citation-map-plus" defer></script>
    </head>
    <body
      data-profile-slug="{slug}"
      data-profile-theme="{theme}"
      data-profile-embedded="{embedded_attribute}"
      data-profile-block="{block_attribute}"
      data-plus-active="{'true' if plus_active else 'false'}"
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
    row = current_public_profile_row(row)
    snapshot = public_snapshot(row)
    plus_active = member_plus_status(str(row["member_id"]))["active"]
    visible = {
        item["key"]: item["visible"]
        for item in normalize_profile_blocks(snapshot.get("blocks"))
    }
    if block and not visible.get(block, False):
        raise HTTPException(status_code=404, detail="That public-profile block is not published.")
    return render_public_profile(snapshot, embedded=True, theme=theme, block=block, plus_active=plus_active)


@app.get("/{slug}", response_class=HTMLResponse)
def public_profile_page(slug: str) -> str:
    normalized_slug = normalize_slug(slug)
    with connect() as con:
        row = con.execute("SELECT * FROM public_profiles WHERE slug=?", (normalized_slug,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Public profile not found.")
    row = current_public_profile_row(row)
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
