import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from vitamine.upload_security import UploadSafetyError, inspect_cv_upload, inspect_sqlite_upload, scan_uploaded_file


class UploadSecurityTests(unittest.TestCase):
    def test_rejects_a_pdf_with_the_wrong_signature_before_parsing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cv.pdf"
            path.write_bytes(b"not a PDF")
            with self.assertRaisesRegex(UploadSafetyError, "valid PDF"):
                inspect_cv_upload(path, maximum_bytes=1024)

    def test_rejects_macro_enabled_docx_package(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cv.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("word/document.xml", "<document/>")
                archive.writestr("word/vbaProject.bin", b"macro")
            with self.assertRaisesRegex(UploadSafetyError, "Macro-enabled"):
                inspect_cv_upload(path, maximum_bytes=1024 * 1024)

    def test_rejects_sqlite_upload_with_a_wrong_signature(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cv.vitamine"
            path.write_bytes(b"not sqlite")
            with self.assertRaisesRegex(UploadSafetyError, "valid SQLite"):
                inspect_sqlite_upload(path, maximum_bytes=1024)

    def test_required_scanner_fails_closed_when_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cv.txt"
            path.write_text("CV", encoding="utf-8")
            with patch("vitamine.upload_security.shutil.which", return_value=None):
                with self.assertRaisesRegex(UploadSafetyError, "scanning is temporarily unavailable"):
                    scan_uploaded_file(path, required=True)

    def test_infected_result_is_rejected_without_scanner_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cv.txt"
            path.write_text("CV", encoding="utf-8")
            with (
                patch("vitamine.upload_security.shutil.which", return_value="/usr/bin/clamdscan"),
                patch(
                    "vitamine.upload_security.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 1),
                ),
            ):
                with self.assertRaisesRegex(UploadSafetyError, "could not be accepted"):
                    scan_uploaded_file(path, required=True)


if __name__ == "__main__":
    unittest.main()
