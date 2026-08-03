#!/usr/bin/env python3
"""Build the private-data-free bundled DFG Word-template asset."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from vitamine.custom_docx_templates import analyze_and_skeletonize
from vitamine.paths import create_blank_database


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    database = args.output_dir / ".dfg-template-build.vitamine"
    create_blank_database(database)
    try:
        with sqlite3.connect(database) as con:
            con.row_factory = sqlite3.Row
            skeleton, blueprint = analyze_and_skeletonize(
                args.source.read_bytes(),
                "DFG Research CV",
                con,
                settings={"provider": "none"},
            )
        (args.output_dir / "dfg-research-cv.docx").write_bytes(skeleton)
        (args.output_dir / "dfg-research-cv.json").write_text(
            json.dumps(blueprint, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    finally:
        database.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
