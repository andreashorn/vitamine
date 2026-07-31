# Vitamine export makeover

Status: DOCX-only foundation implemented; template-by-template Word work remains

## Outcome for the first phase

Vitamine now has one export promise: an editable Word document.

- No PDF generation.
- No HTML generation.
- No Typst dependency in the packaged export path.
- One academic-format catalogue in the Exports view.
- Four current formats installed by default.
- Four additional scientist-focused local preview entries.
- Local install/remove behavior with no account, network catalogue, or payment layer.

The catalogue distinguishes installation from quality. This lets the current formats remain
available without pretending that their Word layouts are equally mature.

## Current priorities

### 1. Tabular One Page CV

This is the only current Word-quality baseline. Complete it before redesigning the other
builders.

Required work:

- replace the remaining person-specific title/entry selection rules with semantic queries;
- make header affiliation data-driven;
- define the exact database fields accepted by each slot;
- make all unused slots disappear cleanly;
- use compact publication citations consistently;
- validate that the target truly remains one page in Microsoft Word;
- add a synthetic all-slots fixture and sparse-data fixture;
- move repeated direct formatting into stable Word styles where possible.

### 2. Formal Academic CV

Use the sanitized handmade long-CV source as the design authority. Do not attempt to reproduce
the Typst PDF visually in a new, unrelated Word layout.

Build a mapping for:

- personal/contact fields;
- education and postdoctoral training;
- academic, hospital, and professional appointments;
- local/international service;
- societies, review, and editorial work;
- honors and funding;
- teaching and mentoring;
- presentations;
- clinical, educational, and community contributions;
- publication groups, patents, theses, and narrative report.

For each section record required fields, optional fields, sort order, repeat pattern, empty
behavior, and intentionally ignored data.

### 3. Short Academic CV

After the long format has a reusable mapping engine, express Short Academic as selection and
layout rules over the same normalized data rather than another independent query/formatting
stack.

### 4. NIH-style Biosketch Draft

Keep this as an editable internal drafting format. Update its language and UI warning whenever
NIH rules change. Do not advertise direct NIH compliance: current submission forms are
generated in SciENcv.

## Local catalogue entries

The four extra entries are design briefs that can be installed into the local library:

- DFG Research CV;
- ERC CV & Track Record;
- Narrative Research CV (R4RI-inspired);
- Modern Publication-First.

Their preview icons are original schematic artwork. External sites are linked as provenance
and requirements/design references; their logos and page screenshots are not bundled.

An installed preview entry remains visibly “DOCX template planned” until it has a working Word
template and field mapping.

## Format metadata

Each catalogue record currently contains:

```json
{
  "id": "vitamine.formal-academic",
  "name": "Formal Academic CV",
  "summary": "A comprehensive medical-academic record.",
  "length": "Long",
  "focus": ["complete record", "promotion evidence"],
  "audience": "Medical-school promotion",
  "preview": "/static/export-previews/formal-academic.svg",
  "preinstalled": true,
  "exporter": "long",
  "quality": {
    "key": "experimental",
    "label": "Handmade design captured",
    "description": "The database-to-template mapping still needs rebuilding."
  },
  "source": {
    "kind": "guidance",
    "title": "Faculty of Medicine CV Guidelines",
    "url": "https://fa.hms.harvard.edu/faculty-medicine-cv-guidelines",
    "note": "No branding or endorsement.",
    "reviewed_on": "2026-07-28"
  }
}
```

Before remote distribution exists, this JSON record is enough. When working external Word
templates exist, promote the record to an immutable directory/package with `template.docx`,
`mapping.json`, license text, and preview artwork.

## Definition of Word-ready

A format is Word-ready only when:

- the DOCX opens without a repair warning;
- no source/template placeholders or unrelated prior content remain;
- all supported data maps to documented slots;
- sparse data removes unused structures cleanly;
- long values wrap without overlap or clipping;
- tables use deterministic widths and repeat header rows where applicable;
- headings do not orphan at page bottoms;
- page fields refresh correctly in Word;
- fonts do not unexpectedly substitute on a supported machine;
- a current Microsoft Word visual review passes;
- a privacy scan of the template and output passes.

Page-count claims such as “one page” or “maximum four pages” require a Word pagination check.
They cannot be inferred from text length or a non-Word renderer.

## Suggested implementation sequence

1. Finish the Tabular One Page mapping and Word pagination test.
2. Introduce a normalized `CVSnapshot` shared by all formats.
3. Introduce a validated selection/mapping layer for one-page output.
4. Generalize that layer against the sanitized Formal Academic source.
5. Migrate Short Academic to the shared layer.
6. Decide whether to update or retire the legacy biosketch builder.
7. Implement DFG and ERC formats only against current call-year requirements.
8. Add prompt-to-`ExportSpec` after deterministic selection and page validation exist.

This sequence keeps the visible library useful now while preventing the catalogue mockup from
dictating an overbuilt marketplace architecture.
