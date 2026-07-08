#!/usr/bin/env python3
"""Fetch open journal metrics for publication venues.

Clarivate Journal Impact Factors require a licensed Web of Science Journals API.
This script fills the local IF field with OpenAlex 2-year mean citedness, which is
an open, impact-factor-like journal metric.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from maintain_publications import maintain
from vitamine.paths import METRICS_CSV, active_db_path


DB = active_db_path()
OPENALEX_SOURCES = "https://api.openalex.org/sources"
SOURCE_NAME = "OpenAlex 2yr_mean_citedness"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def normalize_title(value: str | None) -> str:
    text = (value or "").casefold()
    text = text.replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_score(query: str, candidate: dict[str, Any]) -> int:
    query_norm = normalize_title(query)
    names = [candidate.get("display_name") or ""]
    names.extend(candidate.get("alternate_titles") or [])
    best = 0
    for name in names:
        name_norm = normalize_title(name)
        if not name_norm:
            continue
        if query_norm == name_norm:
            return 100
        query_words = set(query_norm.split())
        name_words = set(name_norm.split())
        if not query_words or not name_words:
            continue
        overlap = len(query_words & name_words) / max(len(query_words), len(name_words))
        best = max(best, int(overlap * 100))
    return best


def read_existing_metrics() -> dict[str, dict[str, str]]:
    if not METRICS_CSV.exists():
        return {}
    with METRICS_CSV.open(newline="", encoding="utf-8") as handle:
        return {
            (row.get("venue") or "").casefold(): row
            for row in csv.DictReader(handle)
            if (row.get("venue") or "").strip()
        }


def write_metrics(metrics: dict[str, dict[str, str]]) -> None:
    METRICS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with METRICS_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["venue", "impact_factor", "impact_factor_year", "metric_source"])
        writer.writeheader()
        for row in sorted(metrics.values(), key=lambda item: item["venue"].casefold()):
            writer.writerow(row)


def publication_venues(limit: int | None = None) -> list[str]:
    sql = """
        SELECT venue, COUNT(*) AS count
        FROM publications
        WHERE COALESCE(suppress_display, 0) = 0
          AND venue IS NOT NULL
          AND venue != ''
        GROUP BY venue
        ORDER BY
          CASE WHEN MAX(impact_factor) IS NULL THEN 0 ELSE 1 END,
          count DESC,
          lower(venue)
    """
    params: tuple[Any, ...] = ()
    if limit:
        sql += " LIMIT ?"
        params = (limit,)
    with connect() as con:
        return [row["venue"] for row in con.execute(sql, params).fetchall()]


def openalex_search(venue: str, email: str | None = None) -> dict[str, Any] | None:
    params = {
        "search": venue,
        "filter": "type:journal",
        "per-page": "5",
        "select": "id,display_name,alternate_titles,summary_stats,updated_date",
    }
    if email:
        params["mailto"] = email
    url = f"{OPENALEX_SOURCES}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "vitamine/0.1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    candidates = payload.get("results") or []
    if not candidates:
        return None
    candidates = sorted(candidates, key=lambda item: title_score(venue, item), reverse=True)
    best = candidates[0]
    if title_score(venue, best) < 55:
        return None
    metric = (best.get("summary_stats") or {}).get("2yr_mean_citedness")
    if metric is None:
        return None
    return best


def metric_year() -> str:
    return str(time.localtime().tm_year - 1)


def fetch_metrics(limit: int | None = None, overwrite: bool = False, email: str | None = None) -> dict[str, int]:
    metrics = read_existing_metrics()
    fetched = 0
    skipped_existing = 0
    unmatched = 0
    errors = 0
    for venue in publication_venues(limit):
        existing = metrics.get(venue.casefold())
        if existing and existing.get("impact_factor") and not overwrite:
            skipped_existing += 1
            continue
        try:
            source = openalex_search(venue, email=email)
        except Exception:
            errors += 1
            continue
        if not source:
            unmatched += 1
            continue
        value = (source.get("summary_stats") or {}).get("2yr_mean_citedness")
        metrics[venue.casefold()] = {
            "venue": venue,
            "impact_factor": f"{float(value):.3f}".rstrip("0").rstrip("."),
            "impact_factor_year": metric_year(),
            "metric_source": SOURCE_NAME,
        }
        fetched += 1
        time.sleep(0.12)
    write_metrics(metrics)
    applied = maintain()
    return {
        "fetched": fetched,
        "skipped_existing": skipped_existing,
        "unmatched": unmatched,
        "errors": errors,
        **applied,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="maximum number of venues to query")
    parser.add_argument("--overwrite", action="store_true", help="replace existing metric rows")
    parser.add_argument("--email", help="optional email for OpenAlex polite pool")
    args = parser.parse_args()
    print(json.dumps(fetch_metrics(args.limit, args.overwrite, args.email), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
