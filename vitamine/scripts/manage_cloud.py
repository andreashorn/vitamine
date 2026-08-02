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


def grant_vitamine_plus(member_id: str, days: int = 365) -> str | None:
    initialize_database()
    paid_until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    with connect() as con:
        cursor = con.execute(
            "UPDATE members SET plus_paid_until=? WHERE id=?",
            (paid_until, member_id),
        )
    return paid_until if cursor.rowcount == 1 else None


def enable_plus_developer_toggle(member_id: str) -> bool:
    initialize_database()
    with connect() as con:
        cursor = con.execute(
            "UPDATE members SET plus_dev_toggle_enabled=1, plus_dev_override=1 WHERE id=?",
            (member_id,),
        )
    return cursor.rowcount == 1


def llm_usage_totals(days: int = 30) -> list[dict]:
    initialize_database()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with connect() as con:
        rows = con.execute(
            """
            SELECT member_id, operation, model, substr(created_at, 1, 10) AS day,
                   COUNT(*) AS responses,
                   SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                   SUM(COALESCE(cached_input_tokens, 0)) AS cached_input_tokens,
                   SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                   SUM(COALESCE(reasoning_tokens, 0)) AS reasoning_tokens,
                   SUM(COALESCE(wholesale_cost_microusd, 0)) AS wholesale_cost_microusd,
                   SUM(COALESCE(charged_cost_microusd, 0)) AS charged_cost_microusd
            FROM llm_usage_events
            WHERE created_at >= ?
            GROUP BY member_id, operation, model, substr(created_at, 1, 10)
            ORDER BY day DESC, member_id, operation, model
            """,
            (cutoff,),
        ).fetchall()
    return [dict(row) for row in rows]


def premium_account_totals() -> list[dict]:
    initialize_database()
    with connect() as con:
        rows = con.execute(
            """
            SELECT m.id AS member_id,
                   COALESCE(c.credited_microusd, 0) AS credited_microusd,
                   COALESCE(u.wholesale_microusd, 0) AS wholesale_microusd,
                   COALESCE(u.charged_microusd, 0) AS charged_microusd,
                   COALESCE(c.credited_microusd, 0) - COALESCE(u.charged_microusd, 0) AS balance_microusd
            FROM members m
            LEFT JOIN (
                SELECT member_id, SUM(amount_microusd) AS credited_microusd
                FROM premium_account_transactions GROUP BY member_id
            ) c ON c.member_id=m.id
            LEFT JOIN (
                SELECT member_id, SUM(COALESCE(wholesale_cost_microusd, 0)) AS wholesale_microusd,
                       SUM(COALESCE(charged_cost_microusd, 0)) AS charged_microusd
                FROM llm_usage_events GROUP BY member_id
            ) u ON u.member_id=m.id
            ORDER BY charged_microusd DESC, member_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


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

    grant_plus = subparsers.add_parser("grant-plus")
    grant_plus.add_argument("member_id")
    grant_plus.add_argument("--days", type=int, default=365)
    plus_dev = subparsers.add_parser("enable-plus-dev-toggle")
    plus_dev.add_argument("member_id")

    usage = subparsers.add_parser("llm-usage")
    usage.add_argument("--days", type=int, default=30)
    subparsers.add_parser("premium-accounts")

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
    if args.command == "grant-plus":
        if args.days < 1:
            parser.error("--days must be at least 1")
        paid_until = grant_vitamine_plus(args.member_id, args.days)
        if paid_until is None:
            parser.error("Member was not found.")
        print(f"VitaMine+ active until {paid_until} for {args.member_id}.")
        return 0
    if args.command == "enable-plus-dev-toggle":
        if not enable_plus_developer_toggle(args.member_id):
            parser.error("Member was not found.")
        print(f"Enabled the VitaMine+ developer toggle for {args.member_id}.")
        return 0
    if args.command == "llm-usage":
        if args.days < 1:
            parser.error("--days must be at least 1")
        print("day\tmember_id\toperation\tmodel\tresponses\tinput\tcached_input\toutput\treasoning\twholesale_usd\tcharged_usd")
        for row in llm_usage_totals(args.days):
            print(
                "\t".join(
                    str(row[key])
                    for key in (
                        "day", "member_id", "operation", "model", "responses", "input_tokens",
                        "cached_input_tokens", "output_tokens", "reasoning_tokens",
                    )
                ) + f"\t{row['wholesale_cost_microusd'] / 1_000_000:.6f}\t{row['charged_cost_microusd'] / 1_000_000:.6f}"
            )
        return 0
    if args.command == "premium-accounts":
        print("member_id\tcredited_usd\twholesale_usd\tcharged_usd\tbalance_usd")
        for row in premium_account_totals():
            print(
                f"{row['member_id']}\t{row['credited_microusd'] / 1_000_000:.6f}"
                f"\t{row['wholesale_microusd'] / 1_000_000:.6f}"
                f"\t{row['charged_microusd'] / 1_000_000:.6f}"
                f"\t{row['balance_microusd'] / 1_000_000:.6f}"
            )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
