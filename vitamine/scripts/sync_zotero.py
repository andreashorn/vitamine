#!/usr/bin/env python3
"""Sync publication metadata from the Netstim Publications Zotero group."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import urllib.parse

from maintain_publications import maintain
from import_background_docs import (
    DATA,
    DB,
    ROOT,
    ZOTERO_ITEM_CATEGORIES,
    creator_is_andreas_horn,
    creator_name,
    fetch_zotero_items,
    load_env,
    zotero_dedupe_key,
    zotero_pmid,
    zotero_raw_citation,
    zotero_request,
    zotero_year,
)


DEFAULT_GROUP_NAME = "Netstim Publications"


def discover_group_id(env: dict[str, str], group_name: str = DEFAULT_GROUP_NAME) -> str | None:
    api_key = env.get("ZOTERO_API_KEY")
    if not api_key:
        return None
    current, _ = zotero_request("https://api.zotero.org/keys/current", api_key)
    user_id = current.get("userID") if isinstance(current, dict) else None
    if not user_id:
        return None
    params = urllib.parse.urlencode({"format": "json", "limit": 100})
    groups, _ = zotero_request(f"https://api.zotero.org/users/{user_id}/groups?{params}", api_key)
    for group in groups:
        data = group.get("data", {})
        if data.get("name") == group_name:
            return str(data.get("id"))
    return None


def netstim_zotero_env() -> dict[str, str]:
    env = load_env(ROOT / ".env")
    group_id = discover_group_id(env)
    if group_id:
        env["ZOTERO_LIBRARY_TYPE"] = "groups"
        env["ZOTERO_LIBRARY_ID"] = group_id
        env["ZOTERO_GROUP_NAME"] = DEFAULT_GROUP_NAME
    return env


def ensure_zotero_document(con: sqlite3.Connection) -> int:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    con.execute(
        """
        INSERT INTO documents (slug, title, source_path, source_format, imported_at, notes)
        VALUES ('zotero_netstim_publications', 'Netstim Publications Zotero group', 'Zotero group: Netstim Publications', 'zotero-api', ?, 'Synced from Zotero Web API; credentials are read from .env and not stored.')
        ON CONFLICT(slug) DO UPDATE SET imported_at=excluded.imported_at, notes=excluded.notes
        """,
        (now,),
    )
    return int(con.execute("SELECT id FROM documents WHERE slug='zotero_netstim_publications'").fetchone()[0])


def sync_zotero(con: sqlite3.Connection | None = None) -> dict[str, int]:
    own_connection = con is None
    if con is None:
        con = sqlite3.connect(DB)
    env = netstim_zotero_env()
    items = fetch_zotero_items(env)
    document_id = ensure_zotero_document(con)
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
        if creators and not any(creator_is_andreas_horn(c) for c in creators):
            continue
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
        existing = con.execute("SELECT id FROM publications WHERE zotero_key = ?", (key,)).fetchone()
        if existing:
            con.execute(
                """
                UPDATE publications SET
                  document_id=?, source='zotero', zotero_key=?, item_type=?, category=?,
                  authors=?, title=?, venue=?, year=?, doi=?, pmid=?, url=?,
                  abstract=?, extra=?, raw_citation=?, confidence='high'
                WHERE id=?
                """,
                (*params, existing[0]),
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

    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "zotero_imported_items.json").write_text(json.dumps(imported_items, ensure_ascii=False, indent=2), encoding="utf-8")
    con.execute(
        """
        INSERT INTO import_warnings (document_id, warning_type, message)
        VALUES (?, 'zotero_sync', ?)
        """,
        (document_id, f"Synced {upserted} publication records from Netstim Publications; skipped {skipped_duplicates} duplicate Zotero items."),
    )
    if own_connection:
        con.commit()
        con.close()
    return {"upserted": upserted, "skipped_duplicates": skipped_duplicates, "fetched": len(items)}


if __name__ == "__main__":
    result = sync_zotero()
    result.update({f"maintenance_{key}": value for key, value in maintain().items()})
    print(json.dumps(result, indent=2))
