#!/usr/bin/env python3
"""Monitor the VitaMine mailbox and expire old Trash/Junk messages."""

from __future__ import annotations

import datetime as dt
import imaplib
import os
import re
import sys


SPECIAL_USE_FLAGS = {"\\Trash", "\\Junk"}


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required setting: {name}")
    return value


def special_use_folders(client: imaplib.IMAP4_SSL) -> list[str]:
    status, rows = client.list()
    if status != "OK":
        raise RuntimeError("IMAP folder listing failed")
    folders: list[str] = []
    pattern = re.compile(rb'^\(([^)]*)\)\s+"[^"]*"\s+(?:"([^"]+)"|(.+))$')
    for row in rows or []:
        match = pattern.match(row or b"")
        if not match:
            continue
        flags = set(match.group(1).decode("utf-8", "replace").split())
        if not flags.intersection(SPECIAL_USE_FLAGS):
            continue
        raw_name = match.group(2) or match.group(3) or b""
        folders.append(raw_name.decode("utf-8", "replace"))
    return folders


def quota_usage(client: imaplib.IMAP4_SSL) -> tuple[int, int] | None:
    status, rows = client.getquotaroot("INBOX")
    if status != "OK":
        return None
    pending = list(rows or [])
    while pending:
        row = pending.pop(0)
        if isinstance(row, (list, tuple)):
            pending.extend(row)
            continue
        if not isinstance(row, bytes):
            continue
        match = re.search(rb"STORAGE\s+(\d+)\s+(\d+)", row)
        if match:
            return int(match.group(1)), int(match.group(2))
    return None


def expire_folder(client: imaplib.IMAP4_SSL, folder: str, before: dt.date) -> int:
    escaped = folder.replace("\\", "\\\\").replace('"', '\\"')
    status, _ = client.select(f'"{escaped}"')
    if status != "OK":
        raise RuntimeError(f"Could not select special-use folder: {folder}")
    status, rows = client.search(None, "BEFORE", before.strftime("%d-%b-%Y"))
    if status != "OK":
        raise RuntimeError(f"Could not search special-use folder: {folder}")
    message_ids = (rows[0] if rows else b"").split()
    if not message_ids:
        return 0
    for message_id in message_ids:
        status, _ = client.store(message_id, "+FLAGS.SILENT", "(\\Deleted)")
        if status != "OK":
            raise RuntimeError(f"Could not mark an old message for deletion in: {folder}")
    client.expunge()
    return len(message_ids)


def main() -> int:
    host = os.environ.get("VITAMINE_IMAP_HOST", "imap.strato.de").strip()
    port = int(os.environ.get("VITAMINE_IMAP_PORT", "993"))
    username = required("VITAMINE_SMTP_USERNAME")
    password = required("VITAMINE_SMTP_PASSWORD")
    retention_days = int(os.environ.get("VITAMINE_MAIL_RETENTION_DAYS", "30"))
    cutoff = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=retention_days)

    deleted = 0
    with imaplib.IMAP4_SSL(host, port) as client:
        client.login(username, password)
        quota = quota_usage(client)
        folders = special_use_folders(client)
        for folder in folders:
            deleted += expire_folder(client, folder, cutoff)
        client.logout()

    if quota:
        used_kib, limit_kib = quota
        percent = (100 * used_kib / limit_kib) if limit_kib else 0
        print(
            f"mailbox_maintenance storage_kib={used_kib} limit_kib={limit_kib} "
            f"usage_percent={percent:.2f} special_folders={len(folders)} deleted={deleted}"
        )
        if percent >= 90:
            print("mailbox_maintenance_warning usage is at least 90 percent", file=sys.stderr)
            return 2
    else:
        print(f"mailbox_maintenance quota=unavailable special_folders={len(folders)} deleted={deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
