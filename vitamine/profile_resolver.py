"""Conservative researcher-profile discovery and evidence scoring."""

from __future__ import annotations

import html
import json
import re
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from typing import Any

from .identifiers import canonical_platform, identifier_value_from_url, normalize_identifier


PROFILE_URL_RE = re.compile(r"https?://[^\s<>\])}\"']+", flags=re.I)
SEARCH_TIMEOUT_SECONDS = 15
SEARCH_USER_AGENT = "vitamine/0.1 (researcher profile resolution)"
WIKIDATA_PROFILE_PROPERTIES = {
    "P1960": ("Google Scholar", "Google Scholar user", "https://scholar.google.com/citations?user={}"),
    "P496": ("ORCID", "ORCID iD", "https://orcid.org/{}"),
    "P1153": ("Scopus", "Scopus Author ID", "https://www.scopus.com/authid/detail.uri?authorId={}"),
    "P2456": ("DBLP", "DBLP author ID", "https://dblp.org/pid/{}"),
    "P4012": ("Semantic Scholar", "Semantic Scholar author ID", "https://www.semanticscholar.org/author/{}"),
}


def normalized_words(value: str) -> set[str]:
    plain = unicodedata.normalize("NFKD", value or "")
    plain = "".join(char for char in plain if not unicodedata.combining(char)).casefold()
    return {word for word in re.findall(r"[a-z0-9]+", plain) if len(word) > 2}


def normalized_title(value: str) -> str:
    return " ".join(sorted(normalized_words(value)))


def profile_url(url: str) -> dict[str, Any] | None:
    cleaned = html.unescape(url).rstrip(".,;:")
    parsed = urllib.parse.urlparse(cleaned)
    platform = canonical_platform("", cleaned)
    if not platform or platform == parsed.netloc:
        return None
    value = identifier_value_from_url(platform, cleaned)
    if not value:
        return None
    canonical_url = cleaned
    if platform == "Google Scholar":
        canonical_url = f"https://scholar.google.com/citations?user={urllib.parse.quote(value)}"
    return normalize_identifier(
        {
            "platform": platform,
            "identifier_type": f"{platform} identifier",
            "identifier_value": value,
            "url": canonical_url,
        }
    )


def cv_profile_candidates(text: str) -> list[dict[str, Any]]:
    """Find stable profile IDs in CV links, grouping repeated citation links."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    counts: Counter[tuple[str, str]] = Counter()
    for match in PROFILE_URL_RE.finditer(text or ""):
        candidate = profile_url(match.group(0))
        if not candidate:
            continue
        key = (str(candidate["platform"]).casefold(), str(candidate["identifier_value"]).casefold())
        grouped[key] = candidate
        counts[key] += 1
    rows: list[dict[str, Any]] = []
    for key, candidate in grouped.items():
        occurrences = counts[key]
        rows.append(
            {
                **candidate,
                "source": "CV hyperlinks",
                "confidence": "high" if occurrences >= 3 else "medium",
                "auto_accept": occurrences >= 3,
                "evidence": {"cv_link_occurrences": occurrences, "shared_publications": 0, "institution_match": False},
            }
        )
    return rows


def _get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": SEARCH_USER_AGENT})
    with urllib.request.urlopen(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
        return json.load(response)


def _publication_titles(publications: list[dict[str, Any]]) -> set[str]:
    return {title for row in publications if (title := normalized_title(str(row.get("title") or "")))}


def _evidence(candidate_titles: list[str], affiliations: list[str], publications: list[dict[str, Any]], institution: str) -> dict[str, Any]:
    cv_titles = _publication_titles(publications)
    shared = len(cv_titles & {title for value in candidate_titles if (title := normalized_title(value))})
    institution_words = normalized_words(institution)
    institution_match = bool(
        institution_words
        and any(len(institution_words & normalized_words(value)) >= min(2, len(institution_words)) for value in affiliations)
    )
    return {"cv_link_occurrences": 0, "shared_publications": shared, "institution_match": institution_match}


def _verified(evidence: dict[str, Any]) -> bool:
    shared = int(evidence.get("shared_publications") or 0)
    return shared >= 3 or (shared >= 2 and bool(evidence.get("institution_match")))


def search_openalex(name: str, institution: str, publications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    url = "https://api.openalex.org/authors?" + urllib.parse.urlencode({"search": name, "per-page": 5})
    payload = _get_json(url)
    rows: list[dict[str, Any]] = []
    for author in payload.get("results") or []:
        author_id = str(author.get("id") or "").rstrip("/").rsplit("/", 1)[-1]
        if not re.fullmatch(r"A\d+", author_id, flags=re.I):
            continue
        works_url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
            {"filter": f"author.id:{author_id}", "per-page": 50, "select": "title"}
        )
        works = _get_json(works_url).get("results") or []
        affiliation_rows = author.get("last_known_institutions") or []
        if not affiliation_rows and isinstance(author.get("last_known_institution"), dict):
            affiliation_rows = [author["last_known_institution"]]
        affiliations = [str(item.get("display_name") or "") for item in affiliation_rows if isinstance(item, dict)]
        evidence = _evidence([str(item.get("title") or "") for item in works], affiliations, publications, institution)
        if int(evidence["shared_publications"]) < 1:
            continue
        verified = _verified(evidence)
        rows.append(
            {
                "platform": "OpenAlex",
                "identifier_type": "OpenAlex author ID",
                "identifier_value": author_id.upper(),
                "url": f"https://openalex.org/{author_id.upper()}",
                "source": "OpenAlex author search",
                "confidence": "high" if verified else "medium",
                "auto_accept": verified,
                "evidence": evidence,
            }
        )
        orcid = str((author.get("ids") or {}).get("orcid") or "").rstrip("/").rsplit("/", 1)[-1]
        if verified and orcid:
            rows.append(
                {
                    "platform": "ORCID", "identifier_type": "ORCID iD", "identifier_value": orcid,
                    "url": f"https://orcid.org/{orcid}", "source": "OpenAlex verified author match",
                    "confidence": "high", "auto_accept": True, "evidence": evidence,
                }
            )
    return rows


def search_semantic_scholar(name: str, institution: str, publications: list[dict[str, Any]]) -> list[dict[str, Any]]:
    url = "https://api.semanticscholar.org/graph/v1/author/search?" + urllib.parse.urlencode(
        {"query": name, "limit": 5, "fields": "name,url,affiliations,papers.title,papers.year"}
    )
    payload = _get_json(url)
    rows: list[dict[str, Any]] = []
    for author in payload.get("data") or []:
        author_id = str(author.get("authorId") or "").strip()
        evidence = _evidence(
            [str(item.get("title") or "") for item in author.get("papers") or []],
            [str(value) for value in author.get("affiliations") or []], publications, institution,
        )
        if not author_id or int(evidence["shared_publications"]) < 1:
            continue
        verified = _verified(evidence)
        rows.append(
            {
                "platform": "Semantic Scholar", "identifier_type": "Semantic Scholar author ID",
                "identifier_value": author_id, "url": str(author.get("url") or f"https://www.semanticscholar.org/author/{author_id}"),
                "source": "Semantic Scholar author search", "confidence": "high" if verified else "medium",
                "auto_accept": verified, "evidence": evidence,
            }
        )
    return rows


def _claim_values(entity: dict[str, Any], property_id: str) -> list[str]:
    values: list[str] = []
    for claim in (entity.get("claims") or {}).get(property_id) or []:
        value = (((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value"))
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return values


def search_wikidata(name: str, trusted_orcids: set[str]) -> list[dict[str, Any]]:
    """Use Wikidata as a name-search index for profile IDs, then require ORCID corroboration."""
    search_url = "https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "wbsearchentities", "search": name, "language": "en", "format": "json", "limit": 5, "type": "item"}
    )
    search_results = _get_json(search_url).get("search") or []
    rows: list[dict[str, Any]] = []
    for result in search_results:
        entity_id = str(result.get("id") or "")
        if not re.fullmatch(r"Q\d+", entity_id):
            continue
        entity_payload = _get_json(f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json")
        entity = (entity_payload.get("entities") or {}).get(entity_id) or {}
        entity_orcids = set(_claim_values(entity, "P496"))
        corroborated = bool(trusted_orcids & entity_orcids)
        evidence = {
            "cv_link_occurrences": 0,
            "shared_publications": 0,
            "institution_match": False,
            "orcid_match": corroborated,
            "wikidata_entity": entity_id,
        }
        for property_id, (platform, identifier_type, url_template) in WIKIDATA_PROFILE_PROPERTIES.items():
            for value in _claim_values(entity, property_id):
                rows.append(
                    {
                        "platform": platform,
                        "identifier_type": identifier_type,
                        "identifier_value": value,
                        "url": url_template.format(urllib.parse.quote(value, safe="/")),
                        "source": "Wikidata researcher search",
                        "confidence": "high" if corroborated else "medium",
                        "auto_accept": corroborated,
                        "evidence": evidence,
                    }
                )
    return rows


def resolve_profiles(
    text: str,
    person: dict[str, Any],
    publications: list[dict[str, Any]],
    *,
    search_web: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    candidates = cv_profile_candidates(text)
    warnings: list[str] = []
    name = str(person.get("full_name") or person.get("display_name") or "").strip()
    institution = str(person.get("own_institution_name") or "").strip()
    if search_web and name and publications:
        for label, search in (("OpenAlex", search_openalex), ("Semantic Scholar", search_semantic_scholar)):
            try:
                candidates.extend(search(name, institution, publications))
            except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
                warnings.append(f"{label} profile search failed: {exc}")
        trusted_orcids = {
            str(candidate.get("identifier_value") or "")
            for candidate in candidates
            if candidate.get("auto_accept") and str(candidate.get("platform") or "").casefold() == "orcid"
        }
        person_orcid = str(person.get("orcid_id") or "").strip()
        if person_orcid:
            trusted_orcids.add(person_orcid)
        try:
            candidates.extend(search_wikidata(name, trusted_orcids))
        except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
            warnings.append(f"Wikidata profile search failed: {exc}")
    by_platform_value: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        normalized = normalize_identifier(candidate)
        row = {**candidate, **normalized}
        key = (
            str(normalized.get("platform") or "").casefold(),
            str(normalized.get("identifier_value") or "").casefold(),
        )
        current = by_platform_value.get(key)
        rank = (
            bool(row.get("auto_accept")),
            int((row.get("evidence") or {}).get("shared_publications") or 0),
            int((row.get("evidence") or {}).get("cv_link_occurrences") or 0),
        )
        current_rank = (
            bool(current and current.get("auto_accept")),
            int(((current or {}).get("evidence") or {}).get("shared_publications") or 0),
            int(((current or {}).get("evidence") or {}).get("cv_link_occurrences") or 0),
        )
        if not current or rank > current_rank:
            by_platform_value[key] = row
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for (platform, _value), row in by_platform_value.items():
        by_platform.setdefault(platform, []).append(row)
    resolved: list[dict[str, Any]] = []
    for rows in by_platform.values():
        rows.sort(
            key=lambda row: (
                bool(row.get("auto_accept")),
                int((row.get("evidence") or {}).get("shared_publications") or 0),
                int((row.get("evidence") or {}).get("cv_link_occurrences") or 0),
            ),
            reverse=True,
        )
        winner = rows[0]
        if len(rows) > 1:
            winner_score = int((winner.get("evidence") or {}).get("shared_publications") or 0)
            runner_up_score = int((rows[1].get("evidence") or {}).get("shared_publications") or 0)
            equally_verified = bool(rows[1].get("auto_accept")) == bool(winner.get("auto_accept"))
            if equally_verified and runner_up_score >= winner_score - 1:
                winner["auto_accept"] = False
                winner["confidence"] = "medium"
                winner["ambiguity"] = "A competing profile had similarly strong publication evidence."
        resolved.append(winner)
    return resolved, warnings
