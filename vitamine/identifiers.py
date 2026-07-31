"""Canonical researcher-profile identifiers and conservative URL value extraction."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any


PLATFORM_ALIASES = {
    "bluesky handle": "Bluesky",
    "google scholar user": "Google Scholar",
    "scholargps id": "ScholarGPS",
    "scopus author id": "Scopus",
    "semantic scholar author": "Semantic Scholar",
    "web of science researcherid": "Web of Science",
    "researcherid": "Web of Science",
}

HOST_PLATFORMS = {
    "bsky.app": "Bluesky",
    "scholar.google.com": "Google Scholar",
    "scholar.google.de": "Google Scholar",
    "researchgate.net": "ResearchGate",
    "www.researchgate.net": "ResearchGate",
    "loop.frontiersin.org": "Loop",
    "www.scopus.com": "Scopus",
    "scopus.com": "Scopus",
    "www.webofscience.com": "Web of Science",
    "webofscience.com": "Web of Science",
    "researcherid.com": "Web of Science",
    "www.semanticscholar.org": "Semantic Scholar",
    "semanticscholar.org": "Semantic Scholar",
    "scholargps.com": "ScholarGPS",
    "www.scholargps.com": "ScholarGPS",
}


def canonical_platform(platform: str, url: str = "") -> str:
    label = re.sub(r"\s+", " ", (platform or "").strip())
    alias = PLATFORM_ALIASES.get(label.casefold())
    if alias:
        return alias
    host = urllib.parse.urlparse(url).netloc.casefold().split(":", 1)[0]
    if host in HOST_PLATFORMS:
        return HOST_PLATFORMS[host]
    # Labels such as "Scopus Author ID" and "Google Scholar user" should
    # collapse with their canonical service, even if their URL is absent.
    for fragment, canonical in (
        ("bluesky", "Bluesky"),
        ("google scholar", "Google Scholar"),
        ("researchgate", "ResearchGate"),
        ("semantic scholar", "Semantic Scholar"),
        ("scopus", "Scopus"),
        ("web of science", "Web of Science"),
        ("researcherid", "Web of Science"),
    ):
        if fragment in label.casefold():
            return canonical
    return label


def identifier_value_from_url(platform: str, url: str) -> str:
    """Return a value only when the service URL structure is unambiguous."""
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    canonical = canonical_platform(platform, url)
    if canonical == "Bluesky" and len(parts) >= 2 and parts[0].casefold() == "profile":
        return parts[1].lstrip("@")
    if canonical == "Google Scholar":
        return (query.get("user") or [""])[0]
    if canonical == "ResearchGate" and len(parts) >= 2 and parts[0].casefold() == "profile":
        return parts[1]
    if canonical == "Scopus":
        return (query.get("authorId") or query.get("authorid") or [""])[0]
    if canonical == "Loop" and len(parts) >= 2 and parts[0].casefold() in {"people", "profiles"}:
        return parts[1]
    if canonical == "Semantic Scholar":
        for marker in ("author",):
            if marker in [part.casefold() for part in parts]:
                index = [part.casefold() for part in parts].index(marker)
                if len(parts) > index + 1:
                    candidate = parts[-1]
                    match = re.search(r"(\d+)$", candidate)
                    return match.group(1) if match else candidate
    if canonical == "Web of Science":
        for candidate in reversed(parts):
            if re.fullmatch(r"[A-Z]-?\d{4}-\d{4}", candidate, flags=re.I):
                return candidate.upper()
    return ""


def normalize_identifier(identifier: dict[str, Any]) -> dict[str, Any]:
    row = dict(identifier)
    url = str(row.get("url") or "").strip()
    platform = canonical_platform(str(row.get("platform") or ""), url)
    value = str(row.get("identifier_value") or "").strip()
    if not value:
        value = identifier_value_from_url(platform, url)
    row["platform"] = platform
    row["identifier_value"] = value or None
    if not str(row.get("identifier_type") or "").strip():
        row["identifier_type"] = f"{platform} identifier"
    return row
