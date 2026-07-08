PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_format TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS person (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  full_name TEXT,
  display_name TEXT,
  degrees TEXT,
  position_title TEXT,
  office_address TEXT,
  home_address TEXT,
  work_phone TEXT,
  work_email TEXT,
  place_of_birth TEXT,
  era_commons TEXT,
  orcid_id TEXT,
  raw_json TEXT
);

CREATE TABLE IF NOT EXISTS person_identifiers (
  id INTEGER PRIMARY KEY,
  person_id INTEGER NOT NULL DEFAULT 1 REFERENCES person(id) ON DELETE CASCADE,
  platform TEXT NOT NULL,
  identifier_type TEXT NOT NULL,
  identifier_value TEXT,
  url TEXT NOT NULL,
  source TEXT NOT NULL,
  verified_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  notes TEXT,
  UNIQUE(person_id, platform, identifier_type, identifier_value)
);

CREATE TABLE IF NOT EXISTS sections (
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  section_key TEXT NOT NULL,
  title TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  raw_markdown TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cv_entries (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  section_key TEXT NOT NULL,
  subcategory TEXT,
  subcategory_de TEXT,
  start_date TEXT,
  end_date TEXT,
  title TEXT,
  title_de TEXT,
  organization TEXT,
  organization_de TEXT,
  location TEXT,
  location_de TEXT,
  role TEXT,
  role_de TEXT,
  amount TEXT,
  amount_de TEXT,
  description TEXT,
  description_de TEXT,
  raw_text TEXT NOT NULL,
  raw_text_de TEXT,
  confidence TEXT NOT NULL DEFAULT 'medium',
  include_extended INTEGER NOT NULL DEFAULT 1,
  include_long INTEGER NOT NULL DEFAULT 1,
  include_short INTEGER NOT NULL DEFAULT 0,
  include_biosketch INTEGER NOT NULL DEFAULT 0,
  language TEXT NOT NULL DEFAULT 'en',
  source_note TEXT
);

CREATE TABLE IF NOT EXISTS publications (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  source TEXT NOT NULL DEFAULT 'document',
  zotero_key TEXT,
  item_type TEXT,
  category TEXT NOT NULL,
  ordinal INTEGER,
  authors TEXT,
  title TEXT,
  venue TEXT,
  year TEXT,
  doi TEXT,
  pmid TEXT,
  url TEXT,
  abstract TEXT,
  extra TEXT,
  raw_citation TEXT NOT NULL,
  confidence TEXT NOT NULL DEFAULT 'medium',
  include_short INTEGER NOT NULL DEFAULT 0,
  include_ultrashort INTEGER NOT NULL DEFAULT 0,
  selected_order INTEGER,
  short_selected_order INTEGER,
  ultrashort_selected_order INTEGER,
  short_citation TEXT,
  impact_factor REAL,
  impact_factor_year TEXT,
  metric_source TEXT,
  suppress_display INTEGER NOT NULL DEFAULT 0,
  quality_note TEXT,
  orcid_put_code TEXT,
  orcid_source TEXT,
  orcid_last_modified TEXT,
  orcid_path TEXT
);

CREATE TABLE IF NOT EXISTS export_settings (
  profile TEXT PRIMARY KEY,
  publication_limit INTEGER NOT NULL DEFAULT 10,
  authorship_filter TEXT NOT NULL DEFAULT 'first_last'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_publications_zotero_key
ON publications(zotero_key)
WHERE zotero_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS biosketch_contributions (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  ordinal INTEGER,
  title TEXT NOT NULL,
  narrative TEXT NOT NULL,
  citations_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS biosketch_contribution_publications (
  id INTEGER PRIMARY KEY,
  contribution_id INTEGER NOT NULL REFERENCES biosketch_contributions(id) ON DELETE CASCADE,
  citation_label TEXT NOT NULL,
  publication_id INTEGER REFERENCES publications(id) ON DELETE SET NULL,
  raw_citation TEXT NOT NULL,
  pmid TEXT,
  doi TEXT,
  UNIQUE(contribution_id, citation_label)
);

CREATE TABLE IF NOT EXISTS narrative_reports (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  title TEXT NOT NULL DEFAULT 'Narrative Report',
  body TEXT NOT NULL DEFAULT '',
  title_de TEXT,
  body_de TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trainees (
  id INTEGER PRIMARY KEY,
  cv_entry_id INTEGER UNIQUE REFERENCES cv_entries(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  name_de TEXT,
  degree TEXT,
  degree_de TEXT,
  career_stage TEXT,
  career_stage_de TEXT,
  institution TEXT,
  institution_de TEXT,
  start_date TEXT,
  end_date TEXT,
  mentoring_role TEXT,
  mentoring_role_de TEXT,
  notes TEXT,
  notes_de TEXT
);

CREATE TABLE IF NOT EXISTS trainee_achievements (
  id INTEGER PRIMARY KEY,
  trainee_id INTEGER NOT NULL REFERENCES trainees(id) ON DELETE CASCADE,
  year TEXT,
  achievement_type TEXT NOT NULL,
  achievement_type_de TEXT,
  title TEXT NOT NULL,
  title_de TEXT,
  organization TEXT,
  organization_de TEXT,
  amount TEXT,
  amount_de TEXT,
  description TEXT,
  description_de TEXT,
  source TEXT NOT NULL DEFAULT 'manual',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(trainee_id, title, organization, amount)
);

CREATE TABLE IF NOT EXISTS import_warnings (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
  warning_type TEXT NOT NULL,
  message TEXT NOT NULL,
  raw_text TEXT
);
