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

On a fresh installation VitaMine creates and opens an empty `data/default.vitamine`
database. The onboarding guide starts with CV import. If no language model has
been configured yet, VitaMine then offers the bundled local model, OpenAI, Ollama,
heuristic extraction, or another compatible API before continuing the queued import.

## Databases

- `data/example.vitamine` is the included synthetic example database.
- `data/default.vitamine` is created automatically for a new user and can be renamed in the Database panel.
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

Generated Word exports and logs are written to `output/` and are ignored by git. VitaMine
currently generates DOCX only so the editable document is always the primary artifact. Users
can create PDF or HTML copies from Word when needed.

The dashboard and public profile include a world map populated by OpenAlex institution locations collected during DOI metadata enrichment. A toggle switches between publication collaborations and a bounded sample of citing researchers, ranked by repeated citation links and shown at their publication affiliations. It uses OpenStreetMap tiles when online and keeps a simple built-in map fallback for offline use.

## Testing

Install the project and run the same complete test suite used by continuous
integration:

```sh
python3 -m pip install --editable .
python3 -m unittest discover -s tests
```

GitHub Actions runs this suite with Python 3.11 for every pull request and every
push to `main`. The workflow requires no repository secrets.

Before committing, run the privacy-safe repository audit:

```sh
python3 scripts/audit_repository.py
```

It checks tracked and non-ignored proposed files for private databases,
environment files, Finder metadata, private-key material, and several
high-confidence credential formats. Findings contain only a rule identifier
and path, never the suspected value. Exact synthetic/example files are
allowlisted per rule. This is a local guardrail; it complements rather than
replaces GitHub secret scanning or a dedicated secrets scanner.

## Render the landing-story movie

The signed-out landing page's "What is VitaMine?" visuals can be rendered as
an MP4 without screen recording. The renderer drives each scroll-linked scene
through Chrome's DevTools protocol, draws only its visual canvas, and encodes
the frames with `ffmpeg`.

```sh
python3 scripts/render_landing_story.py
```

The default output is `output/vitamine-landing-story.mp4`: a 1920×1080,
30fps, 21-second sequence. Google Chrome (or Chromium) and `ffmpeg` must be
available locally. Use `--scene problem` to render one scene for review, or
`--width`, `--height`, `--fps`, and `--seconds-per-scene` to create another
format. Run `python3 scripts/render_landing_story.py --help` for the complete
set of options.

## Build a macOS app

```sh
scripts/build_macos_app.sh
scripts/package_macos_dmg.sh
```

The app bundle is written to `dist/VitaMine.app`; the downloadable disk image is `dist/VitaMine.dmg`.
The build downloads `pandoc` into `vendor/export-tools/` for CV/background-document imports
and bundles it into the app, so users do not need to install it manually. You can refresh the
local tool cache without rebuilding:

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
