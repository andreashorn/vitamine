# ADR 0001: DOCX-only export and a local format catalogue

- Status: Accepted for the first export makeover
- Date: 2026-07-28
- Scope: Word export, bundled format entries, and future Word template authoring

## Decision

Vitamine generates one editable DOCX and no PDF or HTML.

```text
CV database
  -> selected academic format
  -> deterministic content selection
  -> Word-native layout
  -> final.docx
```

Users can make final edits in Word and use Word to create PDF or HTML copies. This removes the
conversion engine, Word/PDF parity work, PDF packaging, and the false promise that two
independent layout systems will paginate identically.

The application exposes formats through one local catalogue. There is no remote marketplace,
account, payment, or executable third-party template code in this phase.

## Product rules

1. The export UI promises DOCX only.
2. The four existing formats are installed by default.
3. “Installed” and “Word-ready” are separate states.
4. Only the Tabular One Page CV is the current Word-quality baseline.
5. Short Academic CV and Formal Academic CV remain editable experimental outputs.
6. The legacy NIH-style biosketch is a drafting aid, not a current submission format.
7. Additional locally bundled entries can be installed or removed from the library.
8. A preview-only entry must not pretend that a working Word exporter exists.
9. Every retained Word design source is sanitized before it is bundled.
10. All formats are for scientists and academic research use; employment résumé formats are
    outside product scope.

## Initial local catalogue

| Format | Initial state | Length | Focus | DOCX status |
|---|---|---|---|---|
| Tabular One Page CV | Preinstalled | Target 1 page | Appointments, distinctions, selected publications | Word-ready baseline |
| Short Academic CV | Preinstalled | Usually 2–4 pages | Career overview and selected output | Experimental |
| Formal Academic CV | Preinstalled | Long | Complete medical-academic dossier | Experimental; handmade design source captured |
| NIH-Style Biosketch Draft | Preinstalled | Legacy short form | Project fit and contributions | Draft/reference only |
| DFG Research CV | Local, installable | Maximum 4 pages | Qualitative record and up to 10 works | Working draft against the 07/25 form |
| ERC CV & Track Record | Local, installable | Maximum 4 pages | Independence, outputs, track record | Preview package |
| Narrative Research CV | Local, installable | Call-dependent | Knowledge, people, culture, society | Original editable R4RI-inspired working draft |
| Modern Publication-First | Local, installable | Usually 3–6 pages | Research identity and selected output | Original editable Word export |

Catalogue metadata lives in `vitamine/static/export-formats.json`. It contains stable IDs,
descriptions, length/focus/audience metadata, quality state, preview artwork, source provenance,
and the optional built-in exporter key.

Installed-format IDs are application preferences, not CV-database content. If no preference
exists, the four `preinstalled` entries are installed. The catalogue order is canonical.

## Amendment: private user-imported Word templates

As of 2026-08-02, the local catalogue is supplemented by private Word templates stored inside
the active `.vitamine` database. A DOCX uploaded in the Exports view is analyzed into semantic
section slots, classified as Long, Short, Ultrashort (`one_page` internally), or Biosketch, and
added directly to “Your formats.” The user supplies or later changes its display name.

The original Word package is converted into a private layout skeleton: recognized person values
and section content become placeholders while page geometry, paragraph and table formatting,
styles, headers, footers, and embedded visual assets remain Word-native. External relationships,
custom XML, comments, tracked deletions, and personal document properties are removed. Export
uses the deterministic builder for the classified content profile and pours its current content
into the imported layout slots. Structured LLM analysis improves unfamiliar heading mappings;
classification and common academic headings retain a deterministic fallback.

Unlike bundled catalogue entries, private templates and their installed state belong to the CV
database rather than machine preferences. This makes them portable with desktop `.vitamine`
files and part of the encrypted hosted CV snapshot. There is still no remote marketplace or
executable third-party template code.

## Current Word sources

### Tabular One Page CV

`vitamine/onepage_tabular/ultrashort_tabular_template.docx` is the active template. Its former
example content has been replaced with placeholders. The builder clears every dynamic slot,
including unused table rows, award lines, and publication lines, before saving.

This template is the first target for:

- a complete database-field mapping;
- removal of person-specific selection rules;
- true one-page overflow validation;
- named Word styles and accessibility improvements;
- a redacted synthetic regression fixture.

### Formal Academic CV

The retained handmade source is:

`/Users/andreashorn/Library/CloudStorage/Dropbox-Personal/aiprojects/hornacademic/projects/background_docs/Horn_08_05_25 CV.docx`

Its SHA-256 at distillation was:

`922ab7e0e8ca18a2218af42a944555561e9f0405e35ad851ec0eee951a15b002`

The source is never modified. A privacy-safe skeleton preserving its page geometry, styles,
section sequence, tables, and page-number footer is bundled at:

`vitamine/templates/formal-academic/template.docx`

The design is informed by public medical-faculty CV guidance, but Vitamine must not use Harvard
branding or imply Harvard approval.

### NIH-style biosketch

The retained handmade source is:

`/Users/andreashorn/Library/CloudStorage/Dropbox-Personal/aiprojects/hornacademic/projects/background_docs/Biosketch_Horn_08_12_2025.docx`

Its SHA-256 at distillation was:

`d8abb0b0719440e059557acc6120b3c6e351ea767fb3dddb37cf8c622004d82b`

A privacy-safe skeleton is bundled at:

`vitamine/templates/nih-biosketch-legacy/template.docx`

This is a legacy drafting layout. As of 2026, current NIH biographical sketch submissions use
the Common Form plus NIH Supplement through SciENcv. Vitamine must not label its DOCX as
submission-compliant.

## Sanitization contract

`scripts/prepare_docx_template_sources.py` creates bundled design sources without modifying
their retained originals. The output:

- replaces person, address, username, affiliation, grant, narrative, and publication content;
- reduces repeated data tables to representative rows where appropriate;
- preserves page geometry, styles, numbering, headers, footers, and representative blocks;
- sets neutral VitaMine document properties;
- removes custom properties and all custom XML;
- removes external relationships;
- removes Word revision-session identifiers.

After generation, the unzipped OOXML packages are searched for names, addresses, usernames,
institutions, publication terms, external relationships, and custom XML. Shipping fails on any
match that originates from the retained CV content.

## Local catalogue versus future template packages

The first catalogue is deliberately static. An install action records that a bundled entry
belongs in the user’s library. It does not download code or contact a server.

The future package boundary can remain:

```text
format-id/
├── manifest.json
├── template.docx
├── mapping.json
├── preview.svg
└── LICENSE.txt
```

The local catalogue already uses the metadata needed by a future package manifest. We should
not add signing, remote indexes, publisher accounts, ratings, payments, or update channels
until more than one external author actually needs them.

Templates remain data. A later mapping vocabulary may support values, optional blocks,
repeating rows, section ordering, and bounded limits. It must not support SQL, network access,
filesystem paths, Python, JavaScript, macros, or arbitrary expressions.

## Prompt-driven custom formats

A future prompt feature should produce a validated `ExportSpec`, not DOCX XML or template
code. The model may select sections, records, ordering, limits, page intent, and an installed
format. Deterministic code then generates the DOCX and reports any unmet constraint.

For example, “maximum five pages and ten publications” becomes explicit limits and a selected
publication ID list. The model may recommend reductions, but it may not silently invent facts,
rewrite citations, or claim page-limit compliance without opening the result in Word.

## Verification status

- Catalogue schema: 8 unique entries, 4 preinstalled.
- Install/remove preference behavior: tested with an isolated preference file.
- Default exporters: all four return DOCX and no PDF/HTML/Typst/Markdown links.
- Additional working exports: DFG, Narrative Research CV, and Modern Publication-First return original editable DOCX files; ERC remains a preview until current call-year requirements are implemented.
- Sanitized templates: load successfully with `python-docx`; no retained personal content,
  custom XML, external relationships, or personal metadata found.
- Tabular One Page: generated from the sanitized template with no leaked placeholders.
- Canonical LibreOffice PNG rendering: unavailable on this Mac because `soffice` is not
  installed.
- macOS Quick Look first-page checks: completed for the one-page output and sanitized design
  sources.
- Full in-app browser screenshot pass: pending because no browser-control surface was
  available in the implementation session.

## Next gate

Do not call all four formats Word-ready. The next implementation milestone is to make the
Tabular One Page mapping generic, add a synthetic fixture that stresses every slot, open the
result in current Microsoft Word, and enforce the one-page constraint. Then rebuild the Formal
Academic CV against its sanitized handmade design source.
