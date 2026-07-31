import unittest

from vitamine.scripts.enrich_publications_by_doi import (
    author_surnames,
    authoritative_metadata,
    metadata_match_is_safe,
    metadata_resolution_is_confident,
)


def publication(**overrides):
    row = {
        "title": "Network mapping in neurological disease",
        "venue": "Brain",
        "year": "2024",
        "authors": "Andreas Horn, Alzheimer’s Disease Neuroimaging Initiative",
        "doi": "10.1000/example",
        "raw_citation": "Andreas Horn. Network mapping in neurological disease. Brain. 2024",
    }
    row.update(overrides)
    return row


class PublicationEnrichmentTests(unittest.TestCase):
    def test_author_surnames_match_full_and_pubmed_inverted_names(self):
        self.assertEqual(
            author_surnames("Bassam Al-Fatly, Siobhan Ewert, Andreas Horn"),
            {"al fatly", "ewert", "horn"},
        )
        self.assertEqual(
            author_surnames("Al-Fatly B, Ewert S, Horn A"),
            {"al fatly", "ewert", "horn"},
        )

    def test_pubmed_compact_authors_replace_flattened_crossref_consortium(self):
        crossref = {
            "title": publication()["title"],
            "venue": "Brain",
            "year": "2024",
            "doi": "10.1000/example",
            "authors": ", ".join(f"Collaborator {index}" for index in range(976)),
            "_author_count": 976,
            "_has_collective_author": True,
            "_author_records": [
                {"name": "Andreas Horn", "orcid": "", "affiliations": [], "collective": False},
                {"name": "Alzheimer’s Disease Neuroimaging Initiative", "orcid": "", "affiliations": [], "collective": True},
            ],
        }
        pubmed = {
            "title": publication()["title"],
            "authors": "Horn A, Alzheimer’s Disease Neuroimaging Initiative",
            "pmid": "12345678",
            "_author_records": [
                {"name": "Horn A", "orcid": "", "affiliations": [], "collective": False},
                {"name": "Alzheimer’s Disease Neuroimaging Initiative", "orcid": "", "affiliations": [], "collective": True},
            ],
        }
        metadata = authoritative_metadata(publication(), crossref, pubmed)
        self.assertEqual(metadata["authors"], pubmed["authors"])
        self.assertEqual(metadata["_author_source"], "pubmed")

    def test_manual_authors_survive_suspicious_crossref_expansion_without_pubmed(self):
        row = publication()
        crossref = {
            "title": row["title"],
            "doi": row["doi"],
            "authors": ", ".join(f"Collaborator {index}" for index in range(150)),
            "_author_count": 150,
            "_has_collective_author": True,
        }
        metadata = authoritative_metadata(row, crossref, {})
        self.assertEqual(metadata["authors"], row["authors"])
        self.assertEqual(metadata["_author_source"], "manual-preserved")

    def test_matching_doi_does_not_override_a_conflicting_title(self):
        row = publication()
        metadata = {
            "title": "A completely unrelated chemistry paper",
            "authors": "Andreas Horn, Someone Else",
            "year": "2024",
            "doi": row["doi"],
        }
        safe, _score, _parts, reason = metadata_match_is_safe(row, metadata)
        self.assertFalse(safe)
        self.assertIn("title", reason.lower())

    def test_exact_title_and_authors_can_resolve_a_sparse_local_row(self):
        self.assertTrue(
            metadata_resolution_is_confident(
                0.74,
                {"title": 1.0, "authors": 1.0, "year": 0.0, "venue": 0.0},
            )
        )
        self.assertFalse(
            metadata_resolution_is_confident(
                0.74,
                {"title": 1.0, "authors": 0.0, "year": 0.0, "venue": 0.0},
            )
        )


if __name__ == "__main__":
    unittest.main()
