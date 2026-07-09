# VitaMine

VitaMine is a local CV and biosketch workbench for curating academic profile data, publications, and export-ready CV variants.

## Start

```sh
python3 -m vitamine.scripts.open_cv_tool
```

The app opens at:

```text
http://127.0.0.1:8765
```

By default VitaMine loads `data/example.vitamine`, a synthetic example database for trying the app.

## Databases

- `data/example.vitamine` is the included synthetic example database.
- The active database path is stored in `~/Library/Preferences/de.netstim.vitamine.json`; old `data/active_db.txt` files are read once for migration.
- Use the Database panel in the app to create a new blank database, import an existing `.vitamine` or `.sqlite` file, or switch back to the example database.
- `.vitamine` files are SQLite databases with a VitaMine-specific extension.
- Journal metrics, ORCID iD, Zotero connection settings, and the selected Zotero source/collection are stored inside the active `.vitamine` database.
- Zotero sync defaults to a user's private library and can use Zotero's "My Publications", a chosen collection, or the whole library. Group libraries are still supported for lab/group use cases.

You can also override the active database for a process:

```sh
VITAMINE_DB=/path/to/my.vitamine python3 -m vitamine.scripts.open_cv_tool
```

## Notes

Generated exports and logs are written to `output/` and are ignored by git.

The dashboard includes a collaboration map populated by OpenAlex institution locations collected during DOI metadata enrichment. It uses OpenStreetMap tiles when online and keeps a simple built-in map fallback for offline use.

## Build a macOS app

```sh
scripts/build_macos_app.sh
scripts/package_macos_dmg.sh
```

The app bundle is written to `dist/VitaMine.app`; the downloadable disk image is `dist/VitaMine.dmg`.
The build downloads `typst` and `pandoc` into `vendor/export-tools/` and bundles those binaries into the app, so users do not need to install them manually. You can refresh the local tool cache without rebuilding:

```sh
python3 scripts/install_export_tools.py --force
```

The same tool cache can also bundle CV-import helpers:

- `pdftotext` from Poppler for stronger PDF text extraction.
- `llama-server` from llama.cpp for the bundled local LLM importer.
- An optional GGUF model at `vendor/models/vitamine-import.gguf`.

Build the normal app bundle with bundled PDF/runtime tools:

```sh
scripts/build_macos_app.sh
```

Build a larger self-contained local-LLM bundle by downloading the default GGUF model first:

```sh
VITAMINE_INCLUDE_LOCAL_LLM=1 scripts/build_macos_app.sh
```

The bundled local model is currently `bartowski/Phi-3.5-mini-instruct-GGUF` / `Phi-3.5-mini-instruct-Q4_K_M.gguf`, stored locally as `vendor/models/vitamine-import.gguf`.

For public distribution, sign and notarize the app or DMG with an Apple Developer ID before attaching it to a GitHub Release.
