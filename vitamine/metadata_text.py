"""Safe normalization for human-readable text received from metadata providers."""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any


def decode_metadata_text(
    value: Any,
    *,
    strip_markup: bool = False,
    collapse_whitespace: bool = True,
) -> str:
    """Decode entities without ever treating the resulting text as executable markup."""
    if isinstance(value, list):
        value = value[0] if value else ""
    text = str(value or "")
    for _ in range(3):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    if strip_markup:
        text = re.sub(r"<[^>]+>", "", text)
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"\s+", " ", text).strip() if collapse_whitespace else text.strip()


def decode_publication_payload(payload: dict[str, Any]) -> dict[str, Any]:
    decoded = dict(payload)
    for field in (
        "authors",
        "title",
        "venue",
        "abstract",
        "extra",
        "raw_citation",
        "short_citation",
        "quality_note",
    ):
        if field in decoded:
            decoded[field] = decode_metadata_text(decoded[field], strip_markup=field == "abstract")
    for field in ("_author_records", "_pubmed_author_records"):
        records = decoded.get(field)
        if not isinstance(records, list):
            continue
        normalized_records = []
        for record in records:
            if not isinstance(record, dict):
                normalized_records.append(record)
                continue
            normalized_records.append(
                {
                    **record,
                    "name": decode_metadata_text(record.get("name")),
                    "affiliations": [
                        decoded_affiliation
                        for affiliation in record.get("affiliations", [])
                        if (decoded_affiliation := decode_metadata_text(affiliation))
                    ],
                }
            )
        decoded[field] = normalized_records
    return decoded
