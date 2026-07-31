#!/usr/bin/env python3
"""Privacy-safe audit of files that could enter the VitaMine repository."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


RULE_ALLOWLISTS = {
    "ENV_FILE": {"deploy/strato/vitamine-cloud.env.example"},
    "PRIVATE_DATABASE": {"data/example.vitamine"},
    "CREDENTIAL_ASSIGNMENT": {"deploy/strato/vitamine-cloud.env.example"},
}

DATABASE_SUFFIXES = (
    ".vitamine",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".vitamine-journal",
    ".sqlite-journal",
    ".db-journal",
    ".vitamine-shm",
    ".sqlite-shm",
    ".db-shm",
    ".vitamine-wal",
    ".sqlite-wal",
    ".db-wal",
)

CONTENT_RULES = {
    "PRIVATE_KEY_MATERIAL": re.compile(
        rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
    ),
    "OPENAI_API_KEY": re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"),
    "GITHUB_TOKEN": re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "AWS_ACCESS_KEY": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
}

CREDENTIAL_ASSIGNMENT = re.compile(
    rb"(?m)^[ \t]*(?:"
    rb"OPENAI_API_KEY|ORCID_OAUTH_CLIENT_SECRET|VITAMINE_CLOUD_PEPPER|"
    rb"DATABASE_URL|VITAMINE_DATABASE_URL"
    rb")[ \t]*=[ \t]*[\"']?([^\"'\s#]{12,})"
)

PLACEHOLDER_FRAGMENTS = (
    b"example",
    b"placeholder",
    b"change-me",
    b"replace-me",
    b"your-",
    b"${",
    b"<",
)


@dataclass(frozen=True, order=True)
class Finding:
    rule: str
    path: str


def repository_files(root: Path) -> list[Path]:
    """Return tracked and non-ignored untracked files, without file contents."""
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return [
        root / Path(raw.decode("utf-8", errors="surrogateescape"))
        for raw in result.stdout.split(b"\0")
        if raw
    ]


def allowed(rule: str, relative_path: str) -> bool:
    return relative_path in RULE_ALLOWLISTS.get(rule, set())


def path_rules(relative_path: str) -> set[str]:
    path = Path(relative_path)
    name = path.name.casefold()
    rules: set[str] = set()
    if name == ".env" or ".env." in name:
        rules.add("ENV_FILE")
    if name == ".ds_store" or name.startswith("._") or name == "icon\r":
        rules.add("FINDER_METADATA")
    if name.endswith(DATABASE_SUFFIXES):
        rules.add("PRIVATE_DATABASE")
    if (
        name in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
        or name.endswith((".pem", ".key"))
        and any(part in name for part in ("private", "secret", "id_"))
    ):
        rules.add("PRIVATE_KEY_FILE")
    return rules


def content_rules(content: bytes) -> set[str]:
    rules = {rule for rule, pattern in CONTENT_RULES.items() if pattern.search(content)}
    for match in CREDENTIAL_ASSIGNMENT.finditer(content):
        value = match.group(1).lower()
        if not any(fragment in value for fragment in PLACEHOLDER_FRAGMENTS):
            rules.add("CREDENTIAL_ASSIGNMENT")
            break
    if content.startswith(b"SQLite format 3\0"):
        rules.add("PRIVATE_DATABASE")
    return rules


def audit_paths(paths: Iterable[Path], root: Path) -> list[Finding]:
    findings: set[Finding] = set()
    for path in paths:
        if not path.is_file():
            continue
        try:
            relative_path = path.relative_to(root).as_posix()
        except ValueError:
            relative_path = path.as_posix()
        rules = path_rules(relative_path)
        try:
            content = path.read_bytes()
        except OSError:
            rules.add("UNREADABLE_FILE")
            content = b""
        rules.update(content_rules(content))
        for rule in rules:
            if not allowed(rule, relative_path):
                findings.add(Finding(rule=rule, path=relative_path))
    return sorted(findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit tracked and non-ignored proposed files without printing secrets."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Git worktree to inspect (defaults to this repository).",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        findings = audit_paths(repository_files(root), root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"AUDIT_ERROR {type(exc).__name__}", file=sys.stderr)
        return 2
    if findings:
        for finding in findings:
            print(f"{finding.rule} {finding.path}")
        print(
            f"Repository audit failed with {len(findings)} privacy-sensitive finding(s).",
            file=sys.stderr,
        )
        return 1
    print("Repository audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
