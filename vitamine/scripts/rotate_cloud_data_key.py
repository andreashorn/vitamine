#!/usr/bin/env python3
"""Re-encrypt hosted CV snapshots with the active configured data key."""

from __future__ import annotations

from vitamine.cloud_app import (
    connect,
    decrypt_database_content,
    encrypt_database_content,
)
from vitamine.cloud_crypto import active_key_identifier, encrypted_key_identifier


def main() -> int:
    active = active_key_identifier()
    rotated = 0
    with connect() as con:
        rows = con.execute(
            "SELECT id, member_id, sqlite_blob FROM account_databases ORDER BY id"
        ).fetchall()
        for row in rows:
            content = bytes(row["sqlite_blob"])
            if encrypted_key_identifier(content) == active:
                continue
            plaintext = decrypt_database_content(
                content,
                member_id=str(row["member_id"]),
                database_id=str(row["id"]),
                allow_plaintext=True,
            )
            encrypted = encrypt_database_content(
                plaintext,
                member_id=str(row["member_id"]),
                database_id=str(row["id"]),
            )
            con.execute(
                "UPDATE account_databases SET sqlite_blob=? WHERE id=?",
                (encrypted, row["id"]),
            )
            rotated += 1
    print(f"CV snapshots rotated: {rotated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
