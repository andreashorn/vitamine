import unittest

from vitamine.metadata_text import decode_metadata_text, decode_publication_payload


class MetadataTextTests(unittest.TestCase):
    def test_decodes_named_numeric_and_double_encoded_entities(self):
        self.assertEqual(
            decode_metadata_text("Parkinsonism &amp; Related Disorders"),
            "Parkinsonism & Related Disorders",
        )
        self.assertEqual(decode_metadata_text("Parkinson&#39;s disease"), "Parkinson's disease")
        self.assertEqual(decode_metadata_text("A &amp;amp; B"), "A & B")

    def test_normalizes_unicode_and_encoded_markup_without_executing_it(self):
        self.assertEqual(decode_metadata_text("&lt;i&gt;Title&lt;/i&gt;", strip_markup=True), "Title")
        self.assertEqual(decode_metadata_text("&lt;script&gt;x&lt;/script&gt;"), "<script>x</script>")

    def test_publication_payload_and_nested_author_records_are_normalized(self):
        payload = decode_publication_payload(
            {
                "title": "Research &amp; practice",
                "venue": "A &amp;amp; B",
                "raw_citation": "Research &amp; practice. A &amp;amp; B.",
                "_author_records": [{"name": "Doe &amp; Roe", "affiliations": ["A &amp; B"]}],
            }
        )
        self.assertEqual(payload["venue"], "A & B")
        self.assertEqual(payload["_author_records"][0]["name"], "Doe & Roe")
        self.assertEqual(payload["_author_records"][0]["affiliations"], ["A & B"])


if __name__ == "__main__":
    unittest.main()
