"""Administrative commands for the invite-only VitaMine cloud service."""

from __future__ import annotations

import argparse
import secrets
from datetime import datetime, timedelta, timezone

from vitamine.cloud_app import connect, initialize_database, normalize_invite_code, secret_hash, utc_now


def create_invitation(label: str, max_uses: int, expires_days: int | None) -> str:
    initialize_database()
    code = f"VITA-{secrets.token_hex(3).upper()}-{secrets.token_hex(3).upper()}"
    expires_at = None
    if expires_days is not None:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat()
    with connect() as con:
        con.execute(
            """
            INSERT INTO invitations (label, code_hash, max_uses, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (label, secret_hash(normalize_invite_code(code)), max_uses, expires_at, utc_now()),
        )
    return code


def list_invitations() -> list[dict]:
    initialize_database()
    with connect() as con:
        rows = con.execute(
            """
            SELECT id, label, max_uses, use_count, expires_at, revoked_at, created_at
            FROM invitations
            ORDER BY id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def revoke_invitation(invitation_id: int) -> bool:
    initialize_database()
    with connect() as con:
        cursor = con.execute(
            "UPDATE invitations SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
            (utc_now(), invitation_id),
        )
    return cursor.rowcount > 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-invite")
    create.add_argument("--label", required=True)
    create.add_argument("--max-uses", type=int, default=20)
    create.add_argument("--expires-days", type=int, default=30)

    subparsers.add_parser("list-invites")

    revoke = subparsers.add_parser("revoke-invite")
    revoke.add_argument("invitation_id", type=int)

    args = parser.parse_args()
    if args.command == "create-invite":
        if args.max_uses < 1:
            parser.error("--max-uses must be at least 1")
        print(create_invitation(args.label, args.max_uses, args.expires_days))
        return 0
    if args.command == "list-invites":
        for invitation in list_invitations():
            print(
                f"{invitation['id']}: {invitation['label']} "
                f"({invitation['use_count']}/{invitation['max_uses']} uses, "
                f"expires={invitation['expires_at'] or 'never'}, "
                f"revoked={bool(invitation['revoked_at'])})"
            )
        return 0
    if args.command == "revoke-invite":
        if not revoke_invitation(args.invitation_id):
            parser.error("Invitation was not found or was already revoked.")
        print(f"Revoked invitation {args.invitation_id}.")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
