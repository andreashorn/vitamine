"""Helpers for optional export toolchain steps."""

from __future__ import annotations

import html
import shutil
import subprocess
from pathlib import Path


def compile_typst_if_available(typ_path: Path, pdf_path: Path, cwd: Path) -> tuple[Path | None, str | None]:
    if shutil.which("typst") is None:
        return None, "Typst is not installed or not on PATH; skipped PDF export."
    subprocess.run(["typst", "compile", str(typ_path), str(pdf_path)], cwd=cwd, check=True)
    return pdf_path, None


def markdown_to_html_body(markdown: str, cwd: Path) -> tuple[str, str | None]:
    if shutil.which("pandoc") is not None:
        body = subprocess.check_output(
            ["pandoc", "-f", "markdown", "-t", "html"],
            input=markdown,
            text=True,
            cwd=cwd,
        )
        return body, None
    return simple_markdown_to_html(markdown), "Pandoc is not installed or not on PATH; used a simple HTML preview fallback."


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
