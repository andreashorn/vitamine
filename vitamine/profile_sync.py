"""Provider-neutral reconciliation of CV records with connected profiles.

The portable CV stores only reconciliation state and public record metadata.
Credentials and provider write calls deliberately stay in the hosted gateway.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any


ORCID = "orcid"
ZOTERO = "zotero"
ADD_REMOTE = "add_remote"
REMOVE_REMOTE = "remove_remote"


def normalize_doi(value: Any) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    value = re.sub(r"^doi:\s*", "", value)
    return value.rstrip(".")


def profile_sync_provider(service: str) -> str:
    return ZOTERO if str(service).startswith(f"{ZOTERO}:") else str(service)


def zotero_service(source: dict[str, Any]) -> str | None:
    """Return a stable namespace for one explicitly selected Zotero source."""
    library_type = str(source.get("library_type") or "").strip().lower()
    library_id = str(source.get("library_id") or "").strip()
    mode = str(source.get("source_mode") or "").strip()
    collection_key = str(source.get("collection_key") or "").strip()
    if library_type not in {"users", "groups"} or not library_id:
        return None
    if mode == "collection" and collection_key:
        return f"{ZOTERO}:{library_type}:{library_id}:collection:{collection_key}"
    if mode == "my_publications" and library_type == "users":
        return f"{ZOTERO}:{library_type}:{library_id}:my_publications"
    if mode == "library":
        return f"{ZOTERO}:{library_type}:{library_id}:library"
    return None


def publication_key(payload: dict[str, Any]) -> str:
    doi = normalize_doi(payload.get("doi"))
    if doi:
        return f"doi:{doi}"
    remote_id = str(
        payload.get("orcid_put_code") or payload.get("zotero_key") or payload.get("remote_id") or ""
    ).strip()
    if remote_id:
        return f"remote:{remote_id}"
    title = re.sub(r"\W+", " ", str(payload.get("title") or "").casefold()).strip()
    return f"title:{title}|{str(payload.get('year') or '').strip()}"


def ensure_profile_sync_tables(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_sync_remote_records (
          id INTEGER PRIMARY KEY,
          service TEXT NOT NULL,
          entity_type TEXT NOT NULL,
          remote_id TEXT NOT NULL,
          normalized_key TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          UNIQUE(service, entity_type, remote_id)
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_profile_sync_remote_key
        ON profile_sync_remote_records(service, entity_type, normalized_key)
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_sync_service_state (
          service TEXT PRIMARY KEY,
          last_observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_sync_recommendations (
          id INTEGER PRIMARY KEY,
          service TEXT NOT NULL,
          entity_type TEXT NOT NULL,
          direction TEXT NOT NULL CHECK(direction IN ('add_remote', 'remove_remote')),
          entity_key TEXT NOT NULL,
          publication_id INTEGER REFERENCES publications(id) ON DELETE SET NULL,
          source_inbox_id INTEGER REFERENCES import_inbox_items(id) ON DELETE SET NULL,
          payload_json TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'skipped', 'completed', 'resolved')),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          completed_at TEXT,
          UNIQUE(service, entity_type, direction, entity_key)
        )
        """
    )
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_profile_sync_recommendations_pending
        ON profile_sync_recommendations(service, status, direction, created_at)
        """
    )


def observe_remote_publications(
    con: sqlite3.Connection,
    service: str,
    publications: list[dict[str, Any]],
) -> None:
    """Remember public metadata observed during an enrichment source scan."""
    ensure_profile_sync_tables(con)
    for payload in publications:
        provider = profile_sync_provider(service)
        remote_id = str(
            (payload.get("orcid_put_code") if provider == ORCID else payload.get("zotero_key"))
            or payload.get("remote_id") or ""
        ).strip()
        if not remote_id:
            continue
        con.execute(
            """
            INSERT INTO profile_sync_remote_records
              (service, entity_type, remote_id, normalized_key, payload_json, observed_at)
            VALUES (?, 'publication', ?, ?, ?, datetime('now'))
            ON CONFLICT(service, entity_type, remote_id) DO UPDATE SET
              normalized_key=excluded.normalized_key,
              payload_json=excluded.payload_json,
              observed_at=excluded.observed_at
            """,
            (service, remote_id, publication_key(payload), json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
    con.execute(
        """
        INSERT INTO profile_sync_service_state(service, last_observed_at)
        VALUES (?, datetime('now'))
        ON CONFLICT(service) DO UPDATE SET last_observed_at=excluded.last_observed_at
        """,
        (service,),
    )


def _upsert_recommendation(
    con: sqlite3.Connection,
    *,
    service: str,
    direction: str,
    entity_key: str,
    payload: dict[str, Any],
    publication_id: int | None = None,
    source_inbox_id: int | None = None,
) -> None:
    con.execute(
        """
        INSERT INTO profile_sync_recommendations
          (service, entity_type, direction, entity_key, publication_id, source_inbox_id, payload_json)
        VALUES (?, 'publication', ?, ?, ?, ?, ?)
        ON CONFLICT(service, entity_type, direction, entity_key) DO UPDATE SET
          publication_id=COALESCE(excluded.publication_id, profile_sync_recommendations.publication_id),
          source_inbox_id=COALESCE(excluded.source_inbox_id, profile_sync_recommendations.source_inbox_id),
          payload_json=excluded.payload_json,
          updated_at=datetime('now')
        """,
        (service, direction, entity_key, publication_id, source_inbox_id, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
    )


def refresh_recommendations(con: sqlite3.Connection, service: str = ORCID) -> dict[str, int]:
    """Derive actionable mismatches from inbox decisions and observed profile data."""
    ensure_profile_sync_tables(con)
    removed_candidates = 0
    added_candidates = 0
    provider = profile_sync_provider(service)
    if provider not in {ORCID, ZOTERO}:
        return {"remove_remote": 0, "add_remote": 0}

    rejected = con.execute(
        """
        SELECT id, payload_json FROM import_inbox_items
        WHERE target_type='publication' AND status='rejected' AND lower(source)=?
        """,
        (provider,),
    ).fetchall()
    for row in rejected:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            continue
        if provider == ZOTERO and str(payload.get("profile_sync_service") or "") != service:
            continue
        remote_id = str(
            (payload.get("orcid_put_code") if provider == ORCID else payload.get("zotero_key")) or ""
        ).strip()
        if not remote_id:
            continue
        payload["remote_id"] = remote_id
        _upsert_recommendation(
            con,
            service=service,
            direction=REMOVE_REMOTE,
            entity_key=f"remote:{remote_id}",
            payload=payload,
            source_inbox_id=int(row["id"]),
        )
        removed_candidates += 1

    # Restoring an Inbox item means that its prior rejection is no longer a
    # reason to suggest a remote deletion. Skipped/completed choices remain
    # historical user decisions and are intentionally left untouched.
    con.execute(
        """
        UPDATE profile_sync_recommendations
        SET status='resolved', updated_at=datetime('now')
        WHERE service=? AND direction=? AND status='pending'
          AND source_inbox_id IS NOT NULL
          AND source_inbox_id NOT IN (
            SELECT id FROM import_inbox_items
            WHERE target_type='publication' AND status='rejected' AND lower(source)=?
          )
        """,
        (service, REMOVE_REMOTE, provider),
    )

    observed = con.execute(
        "SELECT 1 FROM profile_sync_service_state WHERE service=?",
        (service,),
    ).fetchone()
    if not observed:
        return {"remove_remote": removed_candidates, "add_remote": 0}
    remote_keys = {
        str(row["normalized_key"])
        for row in con.execute(
            "SELECT normalized_key FROM profile_sync_remote_records WHERE service=? AND entity_type='publication'",
            (service,),
        ).fetchall()
    }
    publication_sql = """
        SELECT * FROM publications
        WHERE trim(COALESCE(doi, '')) != ''
    """
    if provider == ORCID:
        publication_sql += " AND trim(COALESCE(orcid_put_code, '')) = '' AND lower(COALESCE(source, '')) != 'orcid'"
    for publication in con.execute(publication_sql).fetchall():
        payload = dict(publication)
        key = publication_key(payload)
        if key in remote_keys:
            continue
        _upsert_recommendation(
            con,
            service=service,
            direction=ADD_REMOTE,
            entity_key=key,
            payload=payload,
            publication_id=int(publication["id"]),
        )
        added_candidates += 1
    # If a new remote scan now sees a formerly suggested publication, resolve
    # that suggestion. This also covers an item added outside VitaMine.
    if remote_keys:
        placeholders = ",".join("?" for _ in remote_keys)
        con.execute(
            f"""
            UPDATE profile_sync_recommendations
            SET status='resolved', updated_at=datetime('now')
            WHERE service=? AND direction=? AND status='pending' AND entity_key IN ({placeholders})
            """,
            (service, ADD_REMOTE, *sorted(remote_keys)),
        )
    return {"remove_remote": removed_candidates, "add_remote": added_candidates}


def pending_recommendations(con: sqlite3.Connection, service: str = ORCID) -> dict[str, Any]:
    refresh_recommendations(con, service)
    rows = con.execute(
        """
        SELECT * FROM profile_sync_recommendations
        WHERE service=? AND status='pending'
        ORDER BY CASE direction WHEN 'remove_remote' THEN 0 ELSE 1 END, id
        """,
        (service,),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
        except json.JSONDecodeError:
            item["payload"] = {}
        items.append(item)
    counts = {REMOVE_REMOTE: 0, ADD_REMOTE: 0}
    for item in items:
        counts[str(item["direction"])] += 1
    return {
        "service": service,
        "provider": profile_sync_provider(service),
        "items": items,
        "counts": counts,
        "total": len(items),
    }


def prepare_recommendation_action(
    con: sqlite3.Connection, service: str, direction: str, ids: list[int]
) -> list[dict[str, Any]]:
    if direction not in {ADD_REMOTE, REMOVE_REMOTE}:
        return []
    valid_ids = sorted({int(value) for value in ids if int(value) > 0})
    if not valid_ids:
        return []
    placeholders = ",".join("?" for _ in valid_ids)
    rows = con.execute(
        f"""
        SELECT * FROM profile_sync_recommendations
        WHERE service=? AND direction=? AND status='pending' AND id IN ({placeholders})
        ORDER BY id
        """,
        (service, direction, *valid_ids),
    ).fetchall()
    prepared: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        prepared.append({"id": int(row["id"]), "payload": payload, "publication_id": row["publication_id"]})
    return prepared


def complete_recommendations(
    con: sqlite3.Connection,
    service: str,
    direction: str,
    ids: list[int],
    remote_ids: dict[int, str] | None = None,
) -> int:
    valid_ids = sorted({int(value) for value in ids if int(value) > 0})
    if not valid_ids:
        return 0
    remote_ids = remote_ids or {}
    placeholders = ",".join("?" for _ in valid_ids)
    rows = con.execute(
        f"SELECT id, publication_id FROM profile_sync_recommendations WHERE service=? AND direction=? AND id IN ({placeholders})",
        (service, direction, *valid_ids),
    ).fetchall()
    for row in rows:
        remote_id = str(remote_ids.get(int(row["id"])) or "").strip()
        if direction == ADD_REMOTE and remote_id and row["publication_id"]:
            column = "zotero_key" if profile_sync_provider(service) == ZOTERO else "orcid_put_code"
            con.execute(f"UPDATE publications SET {column}=? WHERE id=?", (remote_id, row["publication_id"]))
    cursor = con.execute(
        f"""
        UPDATE profile_sync_recommendations
        SET status='completed', completed_at=datetime('now'), updated_at=datetime('now')
        WHERE service=? AND direction=? AND status='pending' AND id IN ({placeholders})
        """,
        (service, direction, *valid_ids),
    )
    return int(cursor.rowcount)


def skip_recommendations(con: sqlite3.Connection, service: str, ids: list[int]) -> int:
    valid_ids = sorted({int(value) for value in ids if int(value) > 0})
    if not valid_ids:
        return 0
    placeholders = ",".join("?" for _ in valid_ids)
    cursor = con.execute(
        f"""
        UPDATE profile_sync_recommendations
        SET status='skipped', updated_at=datetime('now')
        WHERE service=? AND status='pending' AND id IN ({placeholders})
        """,
        (service, *valid_ids),
    )
    return int(cursor.rowcount)
