# Compact Research CV

Install **Compact Research CV** from the export template library. The English
Word export uses the current CV's **Short CV** selections. It preserves the
compact layout's right-aligned title, magenta section headings, borderless
date columns and full-width activity, funding and publication lists.

The template usually produces two pages, depending on the selected content.
It does not truncate entries or shrink the font to enforce a page limit.
Every page has native Word `PAGE` / `NUMPAGES` fields; fields refresh when
opened in Word or converted to PDF. Empty fields and sections disappear.

## Field map

| Template field or section | Database source |
| --- | --- |
| Name | `person.display_name`, falling back to `full_name` |
| Title | `person.degrees` |
| ORCID | `person.orcid_id` |
| Current position | `person.position_title` |
| Professional address | `person.office_address`, falling back to `own_institution_name` |
| Education/Degrees | `cv_entries.section_key = education` |
| Past and present positions | `postdoctoral_training`, `academic_appointments`, `hospital_appointments`, `professional_positions`, `research_experience` |
| Prizes and awards | `honors` |
| Activities in the Research System | `editorial_activities`, `grant_review`, `committee_service`, `professional_societies`, `community_service` |
| Active funding and grants | `funding`, filtered to current funded grants |
| Five key papers | Up to five visible peer-reviewed `publications`, using the existing Short CV selection order and fallback |

Entry sections require `include_short = 1`. Records preserve their structured
`start_date`, `end_date`, `title`, `organization`, `location`, `role`, `amount`
and `description`; repeated identical details are collapsed. Records appear
in reverse date order, with undated records last. No content is invented from
raw import text, source notes or person JSON.

Funding excludes planned, submitted, rejected, past, future-starting and
expired grants. Year-only end dates remain current through December 31;
month-only dates remain current through the end of that month. A funded
grant with an unspecified or unparseable end date follows its stored funded
status. An invalid calendar date is omitted. Legacy grant-application rows
without a status are treated as submitted.

Publications respect `suppress_display`, the peer-reviewed category and the
saved Short CV publication order. With no explicit selections, VitaMine uses
its existing ranking of the researcher's first/last-author papers. Explicit
selections are never padded. Stored full citations preserve volume, issue
and pages when their structured bibliographic fields still agree; otherwise
the citation is rebuilt from the current authors, title, venue and year.
The DOI is included when available. The researcher's name is emphasized.
With fewer than five papers the heading becomes **Key papers**.

## Packaging and verification

The installable format ID is `vitamine.compact-research-cv`. Its renderer is
`vitamine/compact_research_cv.py`; the Word skeleton and generic SVG preview
are under `vitamine/static/export-templates` and `export-previews`.

The package contains only fixed generic labels, placeholder tokens and
formatting. The reference's personal content, manually dated footer,
document properties, notes, revision identifiers and external relationships
are removed. The original reference and its renders are not retained in the
repository or deployed. Tests use synthetic records and audit every XML
part against a text allowlist without recording any reference CV strings.

Run `python3 -m unittest tests.test_compact_research_cv
tests.test_compact_research_cv_api tests.test_export_format_catalog
tests.test_cloud_export_download tests.test_custom_export_templates`.
Visual verification covers two- and three-page synthetic exports, including
`1/2`, `2/2`, `1/3`, `2/3` and `3/3` footers.

Deploy the renderer, skeleton, preview, catalog, `app.py`, `app.js` and the
matching `index.html` cache version together. This is a gateway/workspace
export change with no database migration or job-runner restart requirement.
