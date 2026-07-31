"""Execute one hosted VitaMine background job in an isolated subprocess."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import traceback
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def progress(path: Path, phase: str, message: str, percent: int) -> None:
    write_json(
        path,
        {
            "phase": phase,
            "message": message,
            "percent": max(0, min(100, int(percent))),
        },
    )


def run_cv_import(payload: dict[str, Any], job_directory: Path, progress_path: Path) -> dict[str, Any]:
    from vitamine import app as worker_app

    files = [item for item in payload.get("files", []) if isinstance(item, dict)]
    if not files:
        raise RuntimeError("No CV documents were attached to this import job.")
    results: list[dict[str, Any]] = []
    with worker_app.connect() as con:
        settings = worker_app.cv_import_settings(con, include_secret=True)
        settings["review_mode"] = "inbox"
        for index, item in enumerate(files, start=1):
            stored_name = Path(str(item.get("stored_name") or "")).name
            original_name = Path(str(item.get("original_name") or stored_name)).name
            source_path = job_directory / "uploads" / stored_name
            if not source_path.is_file():
                raise RuntimeError(f"An uploaded CV document is unavailable: {original_name}")
            percent = 8 + round(((index - 1) / max(1, len(files))) * 78)
            progress(
                progress_path,
                "importing",
                f"Parsing {original_name} ({index} of {len(files)})",
                percent,
            )
            results.append(worker_app.import_cv_file(con, source_path, original_name, settings))
        con.commit()
    progress(progress_path, "saving", "Saving imported candidates to your account", 90)
    warnings = [warning for result in results for warning in result.get("warnings", [])]
    return {
        "ok": True,
        "job_kind": "cv_import",
        "documents_imported": len(results),
        "entries_inserted": sum(int(result.get("entries_inserted") or 0) for result in results),
        "contributions_inserted": sum(
            int(result.get("contributions_inserted") or 0) for result in results
        ),
        "publications_inserted": sum(
            int(result.get("publications_inserted") or 0) for result in results
        ),
        "narratives_imported": sum(
            int(result.get("narrative_imported") or 0) for result in results
        ),
        "candidates_staged": sum(int(result.get("candidates_staged") or 0) for result in results),
        "staged": {
            key: sum(int((result.get("staged") or {}).get(key) or 0) for result in results)
            for key in (
                "entries",
                "publications",
                "contributions",
                "person",
                "narrative",
                "remembered_rejections",
            )
        },
        "person_fields": max([int(result.get("person_fields") or 0) for result in results] or [0]),
        "used_llm": any(bool(result.get("used_llm")) for result in results),
        "provider": results[0].get("provider") if results else "none",
        "review_mode": any(bool(result.get("review_mode")) for result in results),
        "warnings": warnings,
        "results": results,
    }


def run_enrichment(progress_path: Path) -> dict[str, Any]:
    from vitamine import app as worker_app

    progress(
        progress_path,
        "enriching",
        "Enriching publications from connected databases and online sources",
        8,
    )
    result = worker_app.enrich_cv_job(update_last_run=True)
    progress(progress_path, "metrics", "Refreshing journal metrics", 88)
    metrics = worker_app.run_script("fetch_journal_metrics.py")
    if metrics.returncode != 0:
        result.setdefault("warnings", []).append(
            metrics.stderr[-1200:] or "Journal metrics could not be refreshed."
        )
    result["job_kind"] = "enrich_cv"
    return result


def execute(
    *,
    kind: str,
    database_path: Path,
    payload_path: Path,
    result_path: Path,
    progress_path: Path,
) -> int:
    os.environ["VITAMINE_DB"] = str(database_path)
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("The background-job payload is invalid.")
    progress(progress_path, "starting", "Starting the VitaMine background worker", 5)
    if kind == "cv_import":
        result = run_cv_import(payload, payload_path.parent, progress_path)
    elif kind == "enrich_cv":
        result = run_enrichment(progress_path)
    else:
        raise RuntimeError(f"Unsupported background job: {kind}")
    with sqlite3.connect(database_path) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
              key TEXT PRIMARY KEY,
              value TEXT,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        con.execute(
            """
            INSERT INTO app_settings(key, value, updated_at)
            VALUES ('cloud_background_job_last_completed', ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE
            SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (kind,),
        )
        con.commit()
    write_json(result_path, result)
    progress(progress_path, "saving", "Committing the finished CV to your account", 96)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", required=True, choices=("cv_import", "enrich_cv"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    args = parser.parse_args()
    try:
        return execute(
            kind=args.kind,
            database_path=args.database.resolve(),
            payload_path=args.payload.resolve(),
            result_path=args.result.resolve(),
            progress_path=args.progress.resolve(),
        )
    except Exception as exc:
        traceback.print_exc()
        write_json(args.result.resolve(), {"ok": False, "error": str(exc)[-4000:]})
        progress(args.progress.resolve(), "failed", str(exc)[-1000:] or "Background job failed", 100)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
