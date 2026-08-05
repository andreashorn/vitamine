"""Helpers for optional export toolchain steps."""

from __future__ import annotations

import html
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from vitamine.paths import tool_path


MC_IGNORABLE_ATTRIBUTE = re.compile(rb"\s+[A-Za-z_][\w.-]*:Ignorable=\"[^\"]*\"")
_NO_RESEARCHER_NAME = re.compile(r"(?!x)x")
_RESEARCHER_NAME_PATTERN = _NO_RESEARCHER_NAME


def configure_researcher_name(person: Any | None) -> None:
    """Configure name matching for one CV export process.

    Publication renderers receive author strings in several common forms.  The
    name to emphasize must always come from the CV being exported, never from
    a developer's sample data.
    """
    global _RESEARCHER_NAME_PATTERN
    values: list[str] = []
    if person is not None:
        for field in ("display_name", "full_name"):
            try:
                value = str(person[field] or "").strip()
            except (KeyError, TypeError, IndexError):
                value = ""
            if value and value not in values:
                values.append(value)
    variants = [re.escape(value) for value in values]
    for value in values:
        parts = value.replace(",", " ").split()
        if len(parts) < 2:
            continue
        surname = re.escape(parts[-1])
        initial = re.escape(parts[0][0])
        # Matches both "Surname A." and "Surname, A. B." citation forms.
        variants.append(rf"{surname},?\s+{initial}\.?(?:\s*[A-Z]\.?)*")
    if not variants:
        _RESEARCHER_NAME_PATTERN = _NO_RESEARCHER_NAME
        return
    _RESEARCHER_NAME_PATTERN = re.compile(
        r"(?<!\w)(?:" + "|".join(sorted(set(variants), key=len, reverse=True)) + r")(?!\w)",
        flags=re.IGNORECASE,
    )


def researcher_name_pattern() -> re.Pattern[str]:
    """Return the current CV owner's name pattern for export typography."""
    return _RESEARCHER_NAME_PATTERN


def sanitize_docx_compatibility_markup(path: Path) -> Path:
    """Remove stale mc:Ignorable declarations that make Word repair the DOCX.

    python-docx can discard namespace declarations that are referenced only by
    the QName-valued ``mc:Ignorable`` attribute in retained templates. The XML
    remains well-formed, but Word reports it as unreadable content. Removing
    that optional hint is safe because extension elements keep their namespace
    URIs and Word can still interpret them normally.
    """
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".docx", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(temporary, "w") as target:
            for item in source.infolist():
                payload = source.read(item.filename)
                if item.filename.endswith(".xml"):
                    payload = MC_IGNORABLE_ATTRIBUTE.sub(b"", payload)
                target.writestr(item, payload)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def compile_typst_if_available(typ_path: Path, pdf_path: Path, cwd: Path) -> tuple[Path | None, str | None]:
    typst = tool_path("typst")
    if typst is None:
        return None, "Typst is not installed or not on PATH; skipped PDF export."
    subprocess.run([typst, "compile", str(typ_path), str(pdf_path)], cwd=cwd, check=True)
    return pdf_path, None


def markdown_to_html_body(markdown: str, cwd: Path) -> tuple[str, str | None]:
    pandoc = tool_path("pandoc")
    if pandoc is not None:
        body = subprocess.check_output(
            [pandoc, "-f", "markdown", "-t", "html"],
            input=markdown,
            text=True,
            cwd=cwd,
        )
        return body, None
    return simple_markdown_to_html(markdown), "Pandoc is not installed or not on PATH; used a simple HTML preview fallback."


def convert_with_pandoc_if_available(source: Path, output: Path, cwd: Path, *args: str) -> tuple[Path | None, str | None]:
    pandoc = tool_path("pandoc")
    if pandoc is None:
        return None, f"Pandoc is not installed or not on PATH; skipped {output.suffix.lstrip('.').upper()} export."
    subprocess.run([pandoc, str(source), "-o", str(output), *args], cwd=cwd, check=True)
    return output, None


def simple_markdown_to_html(markdown: str) -> str:
    lines: list[str] = []
    in_list = False
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            if in_list:
                lines.append("</ul>")
                in_list = False
            continue
        if line.startswith("# "):
            if in_list:
                lines.append("</ul>")
                in_list = False
            lines.append(f"<h1>{inline_markdown(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_list:
                lines.append("</ul>")
                in_list = False
            lines.append(f"<h2>{inline_markdown(line[3:])}</h2>")
        elif line.startswith("### "):
            if in_list:
                lines.append("</ul>")
                in_list = False
            lines.append(f"<h3>{inline_markdown(line[4:])}</h3>")
        elif line.startswith("- "):
            if not in_list:
                lines.append("<ul>")
                in_list = True
            lines.append(f"<li>{inline_markdown(line[2:])}</li>")
        else:
            if in_list:
                lines.append("</ul>")
                in_list = False
            lines.append(f"<p>{inline_markdown(line)}</p>")
    if in_list:
        lines.append("</ul>")
    return "\n".join(lines)


def inline_markdown(value: str) -> str:
    return html.escape(value).replace("**", "").replace("__", "")
