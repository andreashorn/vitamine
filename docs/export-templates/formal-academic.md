# Template contract: Formal Academic CV

## Reference

- Retained source:
  `/Users/andreashorn/Library/CloudStorage/Dropbox-Personal/aiprojects/hornacademic/projects/background_docs/Horn_08_05_25 CV.docx`
- Retained source SHA-256:
  `922ab7e0e8ca18a2218af42a944555561e9f0405e35ad851ec0eee951a15b002`
- Bundled sanitized source: `vitamine/templates/formal-academic/template.docx`
- Current deterministic sanitized SHA-256:
  `e09b0da24e05c8650b2093a404ed94c29b87544f423044eb6b7911cadf799de3`
- Sections: 1
- Source page count: unresolved; the source is a long flowing document
- Render evidence: source and sanitized first pages inspected with macOS Quick Look
- Canonical full-page render: blocked because LibreOffice is not installed

The unresolved full render means this file is a captured design source, not yet a certified
live database template.

## Page system

- US Letter portrait: 8.50 × 11.00 inches
- Margins: 0.80 inch on every side
- One section with different-first-page behavior
- Empty first-page footer
- Continuation footer contains a `PAGE` field
- No visible header content

## Typography and structure

- Dominant body family is the source sans-serif face shown by Word/Quick Look.
- Opening title is centred and bold.
- Metadata uses a 7 × 2 table.
- Section labels use the source `H2`, `No Spacing`, `Normal (Web)`, and related paragraph
  styles.
- Most career/service records are tables with date plus two or three semantic detail columns.
- Scholarship contains several publication groups and source numbering/list patterns.

## Sanitized repeat patterns

The sanitized copy preserves all 25 tables but reduces each repeated data table to one
representative row. Column patterns are:

- 4 columns: dates, role/degree, field/details, institution;
- 3 columns: dates, activity/role, institution/details;
- 2 columns: dates/title, details;
- 1 column: entry/publication content.

The metadata table preserves its labels and replaces every value. All original person,
address, phone, email, institution, grant, trainee, presentation, clinical, publication,
patent, thesis, and narrative content is removed.

## Section map

The source preserves headings for education, postdoctoral training, academic/hospital/other
appointments, committee service, societies, grant review, editorial work, honors, funded and
unfunded projects, teaching, mentoring, presentations, clinical innovation, education
innovation, community service, scholarship groups, theses, patents, and narrative report.

Each section still needs a mapping record defining required/optional fields, repeat anchor,
sort order, empty behavior, locale label, and intentionally ignored data.

## Package preservation

The sanitized package keeps page geometry, styles, numbering, theme, font table, settings,
web settings, footnote/endnote parts, and both footers. It removes all custom XML, custom
properties, external hyperlinks/relationships, personal metadata, and revision-session IDs.

## Fidelity gates

- Do not use as the live long-CV template until every repeat anchor is mapped.
- Preserve the different first-page footer and continuation `PAGE` field.
- Refresh page fields in Word after population.
- Compare first, middle, scholarship, and final pages to the retained source.
- Fail on any retained personal text or external relationship.
- Empty sections must be omitted without leaving their sample row behind.
