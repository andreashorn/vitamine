#!/usr/bin/env python3
"""Sync publication metadata from the configured Zotero library."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import urllib.parse

from maintain_publications import maintain
from import_background_docs import (
    DB,
    ZOTERO_ITEM_CATEGORIES,
    creator_name,
    zotero_dedupe_key,
    zotero_pmid,
    zotero_raw_citation,
    zotero_request,
    zotero_year,
)


def setting(con: sqlite3.Connection, key: str) -> str:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT PRIMARY KEY,
          value TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    row = con.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return str(row[0] or "") if row else ""


def current_user_id(api_key: str) -> str | None:
    current, _ = zotero_request("https://api.zotero.org/keys/current", api_key)
    return str(current.get("userID")) if isinstance(current, dict) and current.get("userID") else None


def accessible_libraries(api_key: str) -> list[dict[str, str]]:
    current, _ = zotero_request("https://api.zotero.org/keys/current", api_key)
    user_id = str(current.get("userID") or "") if isinstance(current, dict) else ""
    access = current.get("access") if isinstance(current.get("access"), dict) else {}
    libraries: list[dict[str, str]] = []
    user_access = access.get("user") if isinstance(access.get("user"), dict) else {}
    if user_id and user_access.get("library"):
        libraries.append(
            {
                "type": "users",
                "id": user_id,
                "name": str(current.get("displayName") or current.get("username") or "Personal library"),
            }
        )
    groups = access.get("groups") if isinstance(access.get("groups"), dict) else {}
    group_names: dict[str, str] = {}
    if groups and user_id:
        params = urllib.parse.urlencode({"format": "json", "limit": 100})
        try:
            group_rows, _ = zotero_request(f"https://api.zotero.org/users/{user_id}/groups?{params}", api_key)
            group_names = {
                str((row.get("data") or {}).get("id")): str((row.get("data") or {}).get("name") or "")
                for row in group_rows
                if (row.get("data") or {}).get("id")
            }
        except Exception:
            group_names = {}
    for group_id, permissions in groups.items():
        if isinstance(permissions, dict) and not permissions.get("library"):
            continue
        libraries.append({"type": "groups", "id": str(group_id), "name": group_names.get(str(group_id)) or f"Group {group_id}"})
    return libraries


def choose_library(api_key: str, library_type: str, library_id: str, group_name: str) -> tuple[str, str, str]:
    libraries = accessible_libraries(api_key)
    for library in libraries:
        if library_id and library["type"] == library_type and library["id"] == library_id:
            return library["type"], library["id"], library["name"]
    if group_name:
        for library in libraries:
            if library["type"] == "groups" and library["name"].casefold() == group_name.casefold():
                return library["type"], library["id"], library["name"]
    preferred = [library for library in libraries if library["type"] == library_type]
    if len(preferred) == 1:
        return preferred[0]["type"], preferred[0]["id"], preferred[0]["name"]
    if len(libraries) == 1:
        return libraries[0]["type"], libraries[0]["id"], libraries[0]["name"]
    personal = [library for library in libraries if library["type"] == "users"]
    if personal:
        return personal[0]["type"], personal[0]["id"], personal[0]["name"]
    return library_type, library_id, group_name


def discover_group_id(env: dict[str, str], group_name: str) -> str | None:
    api_key = env.get("ZOTERO_API_KEY")
    if not api_key:
        return None
    user_id = current_user_id(api_key)
    if not user_id:
        return None
    params = urllib.parse.urlencode({"format": "json", "limit": 100})
    groups, _ = zotero_request(f"https://api.zotero.org/users/{user_id}/groups?{params}", api_key)
    for group in groups:
        data = group.get("data", {})
        if data.get("name") == group_name:
            return str(data.get("id"))
    return None


def zotero_env(con: sqlite3.Connection) -> dict[str, str]:
    env: dict[str, str] = {}
    api_key = setting(con, "zotero_api_key") or os.environ.get("ZOTERO_API_KEY") or ""
    library_type = setting(con, "zotero_library_type") or os.environ.get("ZOTERO_LIBRARY_TYPE") or "users"
    library_id = setting(con, "zotero_library_id") or os.environ.get("ZOTERO_LIBRARY_ID") or ""
    group_name = setting(con, "zotero_group_name") or os.environ.get("ZOTERO_GROUP_NAME") or ""
    source_mode = setting(con, "zotero_source_mode") or os.environ.get("ZOTERO_SOURCE_MODE") or "my_publications"
    collection_key = setting(con, "zotero_collection_key") or os.environ.get("ZOTERO_COLLECTION_KEY") or ""
    collection_name = setting(con, "zotero_collection_name") or os.environ.get("ZOTERO_COLLECTION_NAME") or ""
    if not api_key:
        raise RuntimeError("Add a Zotero API key in Connections before syncing Zotero.")
    env["ZOTERO_API_KEY"] = api_key
    if group_name and not library_id:
        group_id = discover_group_id(env, group_name)
        if group_id:
            library_type = "groups"
            library_id = group_id
    if not library_id:
        library_type, library_id, group_name = choose_library(api_key, library_type, library_id, group_name)
    if not library_id and library_type == "users":
        library_id = current_user_id(api_key) or ""
    if not library_id:
        raise RuntimeError("Add a Zotero library ID, or leave type as users so VitaMine can use the API key owner.")
    env["ZOTERO_LIBRARY_TYPE"] = library_type.strip("/")
    env["ZOTERO_LIBRARY_ID"] = library_id
    if group_name:
        env["ZOTERO_GROUP_NAME"] = group_name
    env["ZOTERO_SOURCE_MODE"] = source_mode
    if collection_key:
        env["ZOTERO_COLLECTION_KEY"] = collection_key
    if collection_name:
        env["ZOTERO_COLLECTION_NAME"] = collection_name
    return env


def sync_source_label(env: dict[str, str]) -> str:
    if env.get("ZOTERO_SOURCE_MODE") == "my_publications":
        return "Zotero My Publications"
    if env.get("ZOTERO_SOURCE_MODE") == "collection":
        return f"Zotero collection: {env.get('ZOTERO_COLLECTION_NAME') or env.get('ZOTERO_COLLECTION_KEY')}"
    if env.get("ZOTERO_GROUP_NAME"):
        return f"Zotero group: {env['ZOTERO_GROUP_NAME']}"
    return f"Zotero {env.get('ZOTERO_LIBRARY_TYPE', 'users')}/{env.get('ZOTERO_LIBRARY_ID', '')}"


def zotero_items_base(env: dict[str, str]) -> str:
    library_type = env.get("ZOTERO_LIBRARY_TYPE", "users").strip("/")
    library_id = env["ZOTERO_LIBRARY_ID"]
    mode = env.get("ZOTERO_SOURCE_MODE") or "my_publications"
    if mode == "my_publications":
        return f"https://api.zotero.org/{library_type}/{library_id}/publications/items"
    if mode == "collection":
        collection_key = env.get("ZOTERO_COLLECTION_KEY")
        if not collection_key:
            raise RuntimeError("Choose a Zotero collection before syncing.")
        return f"https://api.zotero.org/{library_type}/{library_id}/collections/{collection_key}/items/top"
    return f"https://api.zotero.org/{library_type}/{library_id}/items/top"


def fetch_configured_zotero_items(env: dict[str, str]) -> list[dict]:
    items: list[dict] = []
    start = 0
    limit = 100
    base = zotero_items_base(env)
    api_key = env["ZOTERO_API_KEY"]
    while True:
        params = urllib.parse.urlencode(
            {
                "format": "json",
                "limit": limit,
                "start": start,
                "sort": "date",
                "direction": "asc",
            }
        )
        batch, headers = zotero_request(f"{base}?{params}", api_key)
        items.extend(batch)
        total = int(headers.get("Total-Results", len(items)))
        start += limit
        if start >= total or not batch:
            break
    return items


def normalize_doi(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"^https?://(dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text.rstrip(".")


def normalize_title(value: str | None) -> str:
    text = (value or "").casefold()
    text = text.replace("‐", "-").replace("‑", "-").replace("–", "-")
    return re.sub(r"\W+", " ", text).strip()


def find_existing_publication(
    con: sqlite3.Connection,
    *,
    key: str,
    doi: str | None,
    pmid: str | None,
    title: str | None,
    year: str | None,
) -> sqlite3.Row | None:
    if key:
        row = con.execute("SELECT * FROM publications WHERE zotero_key = ?", (key,)).fetchone()
        if row:
            return row
    doi_key = normalize_doi(doi)
    if doi_key:
        row = con.execute(
            "SELECT * FROM publications WHERE lower(COALESCE(doi, '')) = ? ORDER BY zotero_key IS NOT NULL, id LIMIT 1",
            (doi_key,),
        ).fetchone()
        if row:
            return row
    if pmid:
        row = con.execute("SELECT * FROM publications WHERE pmid = ? ORDER BY zotero_key IS NOT NULL, id LIMIT 1", (pmid,)).fetchone()
        if row:
            return row
    title_key = normalize_title(title)
    if title_key and year:
        for row in con.execute("SELECT * FROM publications WHERE year = ? ORDER BY zotero_key IS NOT NULL, id", (year,)).fetchall():
            if normalize_title(row["title"]) == title_key:
                return row
    return None


def ensure_zotero_document(con: sqlite3.Connection, env: dict[str, str]) -> int:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    source_label = sync_source_label(env)
    con.execute(
        """
        INSERT INTO documents (slug, title, source_path, source_format, imported_at, notes)
        VALUES ('zotero_library', 'Zotero library', ?, 'zotero-api', ?, 'Synced from Zotero Web API; credentials are stored in this VitaMine database.')
        ON CONFLICT(slug) DO UPDATE SET
          title=excluded.title,
          source_path=excluded.source_path,
          imported_at=excluded.imported_at,
          notes=excluded.notes
        """,
        (source_label, now),
    )
    return int(con.execute("SELECT id FROM documents WHERE slug='zotero_library'").fetchone()[0])



def sync_zotero(con: sqlite3.Connection | None = None) -> dict[str, int]:
    own_connection = con is None
    if con is None:
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
    env = zotero_env(con)
    items = fetch_configured_zotero_items(env)
    document_id = ensure_zotero_document(con, env)
    seen: set[tuple[str, str]] = set()
    imported_items: list[dict] = []
    upserted = 0
    skipped_duplicates = 0

    for item in items:
        data = item.get("data", {})
        item_type = data.get("itemType")
        if item_type in {"attachment", "note", "annotation"}:
            continue
        creators = data.get("creators", [])
        title = data.get("title")
        if not title:
            continue
        dedupe_key = zotero_dedupe_key(data)
        if dedupe_key in seen:
            skipped_duplicates += 1
            continue
        seen.add(dedupe_key)
        authors = ", ".join(creator_name(c) for c in creators if creator_name(c))
        key = data.get("key") or item.get("key")
        params = (
            document_id,
            key,
            item_type,
            ZOTERO_ITEM_CATEGORIES.get(item_type, item_type or "other"),
            authors,
            title,
            data.get("publicationTitle") or data.get("bookTitle") or data.get("conferenceName") or data.get("publisher"),
            zotero_year(data),
            data.get("DOI"),
            zotero_pmid(data),
            data.get("url"),
            data.get("abstractNote"),
            data.get("extra"),
            zotero_raw_citation(data),
        )
        existing = find_existing_publication(
            con,
            key=key,
            doi=data.get("DOI"),
            pmid=zotero_pmid(data),
            title=title,
            year=zotero_year(data),
        )
        if existing:
            con.execute(
                """
                UPDATE publications SET
                  document_id=?, source='zotero', zotero_key=?, item_type=?, category=?,
                  authors=?, title=?, venue=?, year=?, doi=?, pmid=?, url=?,
                  abstract=?, extra=?, raw_citation=?, confidence='high'
                WHERE id=?
                """,
                (*params, existing["id"]),
            )
        else:
            con.execute(
            """
            INSERT INTO publications (
              document_id, source, zotero_key, item_type, category, authors, title, venue,
              year, doi, pmid, url, abstract, extra, raw_citation, confidence
            ) VALUES (?, 'zotero', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'high')
            """,
                params,
            )
        imported_items.append(item)
        upserted += 1

    con.execute(
        """
        INSERT INTO import_warnings (document_id, warning_type, message)
        VALUES (?, 'zotero_sync', ?)
        """,
        (document_id, f"Synced {upserted} publication records from {sync_source_label(env)}; skipped {skipped_duplicates} duplicate Zotero items."),
    )
    if own_connection:
        con.commit()
        con.close()
    return {"upserted": upserted, "skipped_duplicates": skipped_duplicates, "fetched": len(items)}


if __name__ == "__main__":
    result = sync_zotero()
    result.update({f"maintenance_{key}": value for key, value in maintain().items()})
    print(json.dumps(result, indent=2))
