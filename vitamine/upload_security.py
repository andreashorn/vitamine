"""Conservative safety checks for private, user-supplied files."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


MAX_DOCX_MEMBERS = 512
MAX_DOCX_UNCOMPRESSED_BYTES = 80 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 100
MALWARE_SCAN_TIMEOUT_SECONDS = 60


class UploadSafetyError(ValueError):
    """A safe rejection reason which intentionally contains no file details."""


def environment_flag(name: str, *, default: bool = False) -> bool:
    value = str(os.environ.get(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def scan_uploaded_file(path: Path, *, required: bool = False) -> bool:
    """Scan one private file with clamdscan without retaining scanner output.

    The caller opens a private temporary file before invoking this function.
    `--fdpass` lets the daemon inspect that already-openable file without
    weakening the permissions of a VitaMine workspace directory.
    """
    scanner_name = str(os.environ.get("VITAMINE_MALWARE_SCANNER") or "clamdscan").strip()
    scanner = shutil.which(scanner_name) if scanner_name and "/" not in scanner_name else scanner_name
    if not scanner:
        if required:
            raise UploadSafetyError("Upload scanning is temporarily unavailable. Please try again later.")
        return False
    try:
        completed = subprocess.run(
            [scanner, "--fdpass", "--no-summary", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=MALWARE_SCAN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        if required:
            raise UploadSafetyError("Upload scanning is temporarily unavailable. Please try again later.") from None
        return False
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        raise UploadSafetyError("This upload could not be accepted.")
    if required:
        raise UploadSafetyError("Upload scanning is temporarily unavailable. Please try again later.")
    return False


def scan_uploaded_bytes(data: bytes, *, required: bool = False) -> bool:
    """Scan in-memory uploads through a mode-0600 runtime file."""
    with tempfile.NamedTemporaryFile(prefix="vitamine-upload-", delete=True) as temporary:
        temporary.write(data)
        temporary.flush()
        return scan_uploaded_file(Path(temporary.name), required=required)


def _reject_if_size_exceeds(path: Path, maximum_bytes: int) -> None:
    if path.stat().st_size > maximum_bytes:
        raise UploadSafetyError("This upload exceeds the allowed size.")


def _inspect_docx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_DOCX_MEMBERS:
                raise UploadSafetyError("This Word document has too many embedded files.")
            names = {member.filename for member in members}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise UploadSafetyError("This is not a valid Word .docx document.")
            total_uncompressed = 0
            for member in members:
                normalized = member.filename.replace("\\", "/")
                if normalized.startswith("/") or ".." in normalized.split("/"):
                    raise UploadSafetyError("This Word document has an unsafe archive layout.")
                if member.flag_bits & 0x1:
                    raise UploadSafetyError("Password-protected Word documents cannot be imported.")
                if normalized.casefold().endswith("vbaproject.bin"):
                    raise UploadSafetyError("Macro-enabled Word documents cannot be imported.")
                total_uncompressed += member.file_size
                if total_uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise UploadSafetyError("This Word document expands beyond the allowed size.")
                if member.file_size > 0 and member.compress_size == 0:
                    raise UploadSafetyError("This Word document has an unsafe archive entry.")
                if member.compress_size > 0 and member.file_size / member.compress_size > MAX_DOCX_COMPRESSION_RATIO:
                    raise UploadSafetyError("This Word document has an unsafe compression ratio.")
    except UploadSafetyError:
        raise
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        raise UploadSafetyError("This is not a valid Word .docx document.") from None


def inspect_cv_upload(path: Path, *, maximum_bytes: int, scan_required: bool = False) -> None:
    """Validate the actual content of an uploaded CV before any parser runs."""
    _reject_if_size_exceeds(path, maximum_bytes)
    suffix = path.suffix.casefold()
    try:
        with path.open("rb") as source:
            header = source.read(16)
    except OSError:
        raise UploadSafetyError("The uploaded document could not be read.") from None
    if suffix == ".pdf":
        if not header.startswith(b"%PDF-"):
            raise UploadSafetyError("This is not a valid PDF document.")
    elif suffix == ".docx":
        if not header.startswith(b"PK\x03\x04"):
            raise UploadSafetyError("This is not a valid Word .docx document.")
        _inspect_docx(path)
    elif suffix in {".txt", ".md"}:
        if b"\x00" in header:
            raise UploadSafetyError("Text uploads cannot contain binary data.")
    else:
        raise UploadSafetyError("Please upload a DOCX, PDF, TXT, or Markdown CV.")
    scan_uploaded_file(path, required=scan_required)


def inspect_sqlite_upload(path: Path, *, maximum_bytes: int, scan_required: bool = False) -> None:
    """Check a portable VitaMine database before SQLite opens it."""
    _reject_if_size_exceeds(path, maximum_bytes)
    try:
        with path.open("rb") as source:
            header = source.read(16)
    except OSError:
        raise UploadSafetyError("The uploaded database could not be read.") from None
    if header != b"SQLite format 3\x00":
        raise UploadSafetyError("This is not a valid SQLite database.")
    scan_uploaded_file(path, required=scan_required)
