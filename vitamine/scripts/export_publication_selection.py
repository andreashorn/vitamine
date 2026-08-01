"""Shared publication-selection fallback for compact CV exports."""

from __future__ import annotations

import re
import sqlite3
from typing import Any


def _value(row: sqlite3.Row | dict[str, Any] | None, key: str) -> Any:
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def researcher_name_terms(person: sqlite3.Row | dict[str, Any] | None) -> list[str]:
    terms: set[str] = set()
    for field in ("display_name", "full_name"):
        parts = [part for part in re.split(r"\s+", str(_value(person, field) or "").casefold()) if part]
        if not parts:
            continue
        terms.add(" ".join(parts))
        first, last = parts[0], parts[-1]
        if first != last:
            terms.update({f"{first} {last}", f"{last} {first}", f"{first[0]} {last}", f"{last} {first[0]}"})
        if len(last) > 3:
            terms.add(last)
    return sorted(terms, key=len, reverse=True)


def researcher_authorship(authors: str | None, terms: list[str]) -> str:
    author_list = [part.strip() for part in (authors or "").split(",") if part.strip()]
    if not author_list:
        return "other"

    def matches(author: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]+", " ", author.casefold()).strip()
        padded = f" {normalized} "
        return any(f" {re.sub(r'[^a-z0-9]+', ' ', term).strip()} " in padded for term in terms)

    first = matches(author_list[0])
    last = matches(author_list[-1])
    if first and last:
        return "first_last"
    if first:
        return "first"
    if last:
        return "last"
    return "other"


def fallback_score(row: sqlite3.Row, authorship: str) -> float:
    try:
        impact = float(_value(row, "impact_factor") or 0)
    except (TypeError, ValueError):
        impact = 0
    match = re.search(r"\d{4}", str(_value(row, "year") or ""))
    year = int(match.group()) if match else 0
    recency = max(0, year - 2010) * 0.7
    authorship_bonus = {"first_last": 8, "last": 7, "first": 6}.get(authorship, 0)
    try:
        citations = min(float(_value(row, "openalex_cited_by_count") or 0), 500) / 100
    except (TypeError, ValueError):
        citations = 0
    return round((impact * 2.5) + recency + authorship_bonus + citations, 3)


def selected_or_fallback_publications(
    con: sqlite3.Connection,
    *,
    profile: str,
    limit: int,
) -> list[sqlite3.Row]:
    """Use explicit selections, or recent high-impact first/last-author work."""
    if profile not in {"short", "ultrashort"}:
        raise ValueError(f"Unsupported compact export profile: {profile}")
    flag = "include_short" if profile == "short" else "include_ultrashort"
    order = "short_selected_order" if profile == "short" else "ultrashort_selected_order"
    selected = con.execute(
        f"""
        SELECT * FROM publications
        WHERE {flag}=1
          AND COALESCE(suppress_display, 0)=0
          AND category='peer_reviewed'
        ORDER BY COALESCE({order}, selected_order, 999), CAST(year AS INTEGER) DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if selected:
        return selected

    person = con.execute("SELECT full_name, display_name FROM person WHERE id=1").fetchone()
    terms = researcher_name_terms(person)
    candidates = con.execute(
        """
        SELECT * FROM publications
        WHERE COALESCE(suppress_display, 0)=0
          AND category='peer_reviewed'
        """
    ).fetchall()
    ranked = []
    for row in candidates:
        authorship = researcher_authorship(_value(row, "authors"), terms)
        if authorship not in {"first", "last", "first_last"}:
            continue
        ranked.append((fallback_score(row, authorship), row))
    ranked.sort(
        key=lambda item: (
            -item[0],
            -int(re.search(r"\d{4}", str(_value(item[1], "year") or "0")).group())
            if re.search(r"\d{4}", str(_value(item[1], "year") or ""))
            else 0,
            str(_value(item[1], "title") or ""),
        )
    )
    return [row for _, row in ranked[:limit]]
