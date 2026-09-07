# Template contract: Tabular One Page CV

## Reference

- Retained source: `vitamine/onepage_tabular/ultrashort_tabular_template.docx`
- Original pre-sanitization SHA-256:
  `8b9fea7db7dacf45aafeb8342fd2f90e662e11d2db2df0c0ed2564b5168ad9c2`
- Current deterministic sanitized source SHA-256:
  `7a11098a6fb1332606b9cc6f1df2cb4a023977252ce4ef8340eb2980ced4b3ee`
- Sections: 1
- Page count: target 1; must be confirmed in Microsoft Word for every export
- Render evidence: macOS Quick Look first-page review completed
- Canonical PNG render: unresolved because LibreOffice is not installed

## Page system

- A4 portrait: 8.27 × 11.69 inches
- Margins: left/right 1.00 inch, top 0.75 inch, bottom 0.75 inch
- One section; no distinct first-page header/footer
- No header/footer content or page-number field

## Typography and components

- Times New Roman is the dominant directly formatted font.
- All visible body paragraphs have zero explicit paragraph spacing and use the source line rhythm.
- Name and section labels are bold.
- Education and appointments use border-light tables.
- Awards use the source bullet numbering.
- Publications use the source numbered-list definition and hanging indent.

Direct formatting is intentionally preserved for the first migration. Replacing it with named
styles is a later, explicitly tested change.

## Slot map

| Location | Semantic slot | Capacity | Builder rule |
|---|---|---:|---|
| Body paragraph 0 | Name and degrees | 1 line | Required |
| Body paragraph 1 | Position and affiliation | Up to 3 lines | Remove blank lines |
| Table 0 row 0 | Education header | Fixed | Preserve and mark as header |
| Table 0 rows 1–4 | Education/training record | 4 | Clear unused rows |
| Table 1 row 0 | Appointment header | Fixed | Preserve and mark as header |
| Table 1 rows 1–6 | Appointment record | 6 | Clear unused rows |
| Body paragraphs 5–10 | Award/funding entry | 6 | Clear unused paragraphs |
| Body paragraphs 12–21 | Selected publication | 10 | Prefer curated compact citation; clear unused paragraphs |

## Package preservation

The sanitized template keeps the source styles, numbering, theme, settings, font table,
web settings, relationships, and document geometry. It has no custom XML, custom document
properties, external relationships, images, fields, headers, or footers.

## Fidelity gates

- No placeholder token may remain in a generated output.
- Both table header rows must carry `w:tblHeader`.
- Empty slots must be blank, not retained template text.
- All ten publication slots must remain editable list paragraphs.
- The last visible line must remain above Word’s bottom margin.
- A one-page claim requires a current Microsoft Word page-count check.
