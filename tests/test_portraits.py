import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from vitamine.app import app
from vitamine.paths import create_blank_database
from vitamine.portrait import MAX_PORTRAIT_BYTES, normalize_portrait_image


_png_buffer = io.BytesIO()
Image.new("RGB", (1, 1), "#178064").save(_png_buffer, format="PNG")
PNG_1X1 = _png_buffer.getvalue()


class PortraitTests(unittest.TestCase):
    def test_png_normalization_records_reusable_image_metadata(self):
        normalized = normalize_portrait_image(
            PNG_1X1,
            filename="../../Ada portrait.png",
        )
        metadata = normalized.metadata
        self.assertEqual(metadata.mime_type, "image/png")
        self.assertEqual(metadata.filename, "Ada portrait.png")
        self.assertEqual((metadata.width, metadata.height), (1, 1))
        self.assertTrue(normalized.data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_jpeg_orientation_is_corrected_and_output_is_png(self):
        source = Image.new("RGB", (2, 3), "#178064")
        exif = Image.Exif()
        exif[274] = 6
        payload = io.BytesIO()
        source.save(payload, format="JPEG", exif=exif)

        normalized = normalize_portrait_image(
            payload.getvalue(),
            filename="camera portrait.jpg",
        )

        self.assertEqual(normalized.metadata.mime_type, "image/png")
        self.assertEqual(normalized.metadata.filename, "camera portrait.png")
        self.assertEqual(
            (normalized.metadata.width, normalized.metadata.height),
            (3, 2),
        )
        with Image.open(io.BytesIO(normalized.data)) as result:
            self.assertEqual(result.format, "PNG")

    def test_person_portrait_is_stored_in_database_but_not_returned_as_json(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "portrait.vitamine"
            create_blank_database(database)
            with patch("vitamine.app.active_db_path", return_value=database):
                with TestClient(app) as client:
                    uploaded = client.put(
                        "/api/person/portrait",
                        files={"file": ("ada.png", PNG_1X1, "image/png")},
                    )
                    self.assertEqual(uploaded.status_code, 200, uploaded.text)
                    self.assertEqual(uploaded.json()["portrait_width"], 1)

                    person = client.get("/api/person")
                    self.assertEqual(person.status_code, 200, person.text)
                    self.assertTrue(person.json()["portrait_available"])
                    self.assertNotIn("portrait_image", person.json())

                    portrait = client.get("/api/person/portrait")
                    self.assertEqual(portrait.status_code, 200, portrait.text)
                    self.assertEqual(portrait.headers["content-type"], "image/png")
                    self.assertTrue(portrait.content.startswith(b"\x89PNG\r\n\x1a\n"))
                    stored_portrait = portrait.content

                    updated = client.put("/api/person", json={"display_name": "Ada"})
                    self.assertEqual(updated.status_code, 200, updated.text)
                    self.assertEqual(
                        client.get("/api/person/portrait").content,
                        stored_portrait,
                    )

                    removed = client.delete("/api/person/portrait")
                    self.assertEqual(removed.status_code, 200, removed.text)
                    self.assertEqual(client.get("/api/person/portrait").status_code, 404)

    def test_non_image_upload_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "portrait.vitamine"
            create_blank_database(database)
            with patch("vitamine.app.active_db_path", return_value=database):
                with TestClient(app) as client:
                    response = client.put(
                        "/api/person/portrait",
                        files={"file": ("portrait.svg", b"<svg></svg>", "image/svg+xml")},
                    )
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn("JPEG or PNG", response.json()["detail"])

    def test_upload_above_five_mb_is_resized_to_an_optimized_png(self):
        source = Image.frombytes("RGB", (2100, 1200), os.urandom(2100 * 1200 * 3))
        payload = io.BytesIO()
        source.save(payload, format="PNG", compress_level=0)
        original = payload.getvalue()
        self.assertGreater(len(original), MAX_PORTRAIT_BYTES)

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "large-portrait.vitamine"
            create_blank_database(database)
            with patch("vitamine.app.active_db_path", return_value=database):
                with TestClient(app) as client:
                    uploaded = client.put(
                        "/api/person/portrait",
                        files={"file": ("large camera.png", original, "image/png")},
                    )
                    self.assertEqual(uploaded.status_code, 200, uploaded.text)
                    self.assertEqual(uploaded.json()["portrait_mime_type"], "image/png")
                    self.assertEqual(uploaded.json()["portrait_filename"], "large camera.png")

                    portrait = client.get("/api/person/portrait")
                    self.assertEqual(portrait.status_code, 200, portrait.text)
                    self.assertLessEqual(len(portrait.content), MAX_PORTRAIT_BYTES)
                    with Image.open(io.BytesIO(portrait.content)) as stored:
                        self.assertEqual(stored.format, "PNG")
                        self.assertLessEqual(max(stored.size), 2000)


if __name__ == "__main__":
    unittest.main()
