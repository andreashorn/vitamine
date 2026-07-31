# Template contract: NIH-style Biosketch Draft

## Reference

- Retained source:
  `/Users/andreashorn/Library/CloudStorage/Dropbox-Personal/aiprojects/hornacademic/projects/background_docs/Biosketch_Horn_08_12_2025.docx`
- Retained source SHA-256:
  `d8abb0b0719440e059557acc6120b3c6e351ea767fb3dddb37cf8c622004d82b`
- Bundled sanitized source: `vitamine/templates/nih-biosketch-legacy/template.docx`
- Current deterministic sanitized SHA-256:
  `793a48c6edbcbdb1ba93634358d363bc4f31eb397fdb06373d1f88a9f8260f01`
- Sections: 1
- Page count: source is multipage; exact count unresolved
- Render evidence: source and sanitized first pages inspected with macOS Quick Look
- Canonical full render: blocked because LibreOffice is not installed

This is a legacy drafting layout. Current NIH submission documents are created in SciENcv and
this template must not be labelled submission-compliant.

## Page system

- US Letter portrait: 8.50 × 11.00 inches
- Margins: 0.50 inch on every side
- One continuous section
- No visible header/footer content
- No fields or images

## Typography and components

- Built-in/source styles include `Title`, `Form Field Caption1`,
  `Data Field 11pt-Single`, `Heading Note`, and `Normal`.
- Education/training uses a 4-column grid.
- Selected project content uses a 2-column table.
- Contributions use a numbered title/narrative plus lettered citations.

## Slot map

- Name/degrees, eRA Commons username, and position title.
- Education/training repeat: institution/location, degree, completion date, field.
- Personal statement.
- Selected project dates and project role/relevance.
- Selected citations.
- Positions/appointments repeat.
- Honors repeat.
- Contributions-to-science repeat with title, narrative, and citations.
- Complete-bibliography URL.

The sanitized skeleton retains one education row, one selected-project row, one appointment,
one honor, one contribution narrative, and four citation slots.

## Package preservation

The sanitized package keeps its styles, numbering, theme, settings, font table, web settings,
header, footnotes, and endnotes. It removes all original scientist content, custom XML, custom
properties, external relationships, personal metadata, and revision-session IDs.

## Fidelity gates

- The UI must continue to show the draft/reference warning.
- No source scientist text may remain.
- No placeholder may survive a generated draft.
- Repeat counts and pagination must be reviewed in Word.
- If current NIH requirements are the goal, export structured data for SciENcv assistance
  instead of claiming that this DOCX is acceptable for submission.
