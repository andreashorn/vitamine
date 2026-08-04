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
  raw_json TEXT,
  own_institution_name TEXT,
  own_institution_country TEXT,
  own_institution_country_code TEXT,
  own_institution_latitude REAL,
  own_institution_longitude REAL,
  portrait_image BLOB,
  portrait_mime_type TEXT,
  portrait_filename TEXT,
  portrait_width INTEGER,
  portrait_height INTEGER
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

CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS journal_metrics (
  venue TEXT PRIMARY KEY,
  impact_factor REAL,
  impact_factor_year TEXT,
  metric_source TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
  grant_status TEXT,
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
  orcid_path TEXT,
  metadata_source TEXT,
  metadata_enriched_at TEXT,
  openalex_work_id TEXT,
  openalex_cited_by_count INTEGER,
  openalex_counts_by_year_json TEXT,
  openalex_citation_geography_enriched_at TEXT
);

CREATE TABLE IF NOT EXISTS export_settings (
  profile TEXT PRIMARY KEY,
  publication_limit INTEGER NOT NULL DEFAULT 10,
  authorship_filter TEXT NOT NULL DEFAULT 'first_last'
);

CREATE TABLE IF NOT EXISTS export_templates (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  source_filename TEXT NOT NULL,
  source_docx BLOB NOT NULL,
  content_profile TEXT NOT NULL CHECK (content_profile IN ('long', 'short', 'one_page', 'biosketch')),
  blueprint_json TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_publications_zotero_key
ON publications(zotero_key)
WHERE zotero_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS collaboration_institutions (
  id INTEGER PRIMARY KEY,
  publication_id INTEGER NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
  openalex_work_id TEXT,
  publication_title TEXT,
  publication_year TEXT,
  author_name TEXT,
  author_position TEXT,
  institution_id TEXT NOT NULL,
  institution_name TEXT NOT NULL,
  ror TEXT,
  country_code TEXT,
  country TEXT,
  latitude REAL,
  longitude REAL,
  source TEXT NOT NULL DEFAULT 'openalex',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(publication_id, author_name, institution_id)
);

CREATE INDEX IF NOT EXISTS idx_collaboration_institutions_pub
ON collaboration_institutions(publication_id);

CREATE INDEX IF NOT EXISTS idx_collaboration_institutions_inst
ON collaboration_institutions(institution_id);

CREATE TABLE IF NOT EXISTS citation_institutions (
  id INTEGER PRIMARY KEY,
  publication_id INTEGER NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
  cited_openalex_work_id TEXT,
  citing_openalex_work_id TEXT NOT NULL,
  citing_work_title TEXT,
  citing_work_year TEXT,
  author_id TEXT,
  author_name TEXT NOT NULL,
  institution_id TEXT NOT NULL,
  institution_name TEXT NOT NULL,
  ror TEXT,
  country_code TEXT,
  country TEXT,
  latitude REAL,
  longitude REAL,
  source TEXT NOT NULL DEFAULT 'openalex',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(publication_id, citing_openalex_work_id, author_name, institution_id)
);

CREATE INDEX IF NOT EXISTS idx_citation_institutions_pub
ON citation_institutions(publication_id);

CREATE INDEX IF NOT EXISTS idx_citation_institutions_author
ON citation_institutions(author_id, author_name);

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

CREATE TABLE IF NOT EXISTS import_inbox_items (
  id INTEGER PRIMARY KEY,
  document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
  source TEXT NOT NULL,
  target_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  confidence TEXT NOT NULL DEFAULT 'medium',
  duplicate_of_type TEXT,
  duplicate_of_id INTEGER,
  title TEXT,
  subtitle TEXT,
  raw_text TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  reviewed_at TEXT,
  review_note TEXT
);

CREATE INDEX IF NOT EXISTS idx_import_inbox_items_status
ON import_inbox_items(status, target_type, created_at);

CREATE TABLE IF NOT EXISTS cleanup_change_log (
  id INTEGER PRIMARY KEY,
  inbox_item_id INTEGER NOT NULL UNIQUE REFERENCES import_inbox_items(id) ON DELETE RESTRICT,
  operation TEXT NOT NULL,
  record_type TEXT NOT NULL,
  record_id INTEGER NOT NULL,
  related_record_id INTEGER,
  field_name TEXT,
  old_text TEXT,
  new_text TEXT,
  applied_json TEXT NOT NULL DEFAULT '{}',
  orcid_sync_status TEXT NOT NULL DEFAULT 'not_applicable',
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cleanup_change_log_orcid
ON cleanup_change_log(orcid_sync_status, applied_at);

CREATE TABLE IF NOT EXISTS field_locks (
  id INTEGER PRIMARY KEY,
  target_type TEXT NOT NULL,
  target_id INTEGER NOT NULL,
  field_name TEXT NOT NULL,
  locked_value TEXT NOT NULL,
  source TEXT NOT NULL,
  source_inbox_item_id INTEGER REFERENCES import_inbox_items(id) ON DELETE SET NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(target_type, target_id, field_name)
);

CREATE INDEX IF NOT EXISTS idx_field_locks_target
ON field_locks(target_type, target_id);

CREATE TABLE IF NOT EXISTS enrichment_change_candidates (
  id INTEGER PRIMARY KEY,
  publication_id INTEGER NOT NULL REFERENCES publications(id) ON DELETE CASCADE,
  field_name TEXT NOT NULL,
  old_value TEXT NOT NULL,
  new_value TEXT NOT NULL,
  source TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  rationale TEXT,
  confidence TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(publication_id, field_name, source, new_value)
);

CREATE INDEX IF NOT EXISTS idx_enrichment_change_candidates_pending
ON enrichment_change_candidates(status, created_at);

CREATE TABLE IF NOT EXISTS discovery_rejections (
  id INTEGER PRIMARY KEY,
  fingerprint TEXT NOT NULL UNIQUE,
  target_type TEXT NOT NULL,
  normalized_key TEXT NOT NULL,
  source TEXT NOT NULL,
  reason TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  seen_count INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_discovery_rejections_type_key
ON discovery_rejections(target_type, normalized_key);

CREATE TABLE IF NOT EXISTS profile_sync_remote_records (
  id INTEGER PRIMARY KEY,
  service TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  remote_id TEXT NOT NULL,
  normalized_key TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(service, entity_type, remote_id)
);

CREATE INDEX IF NOT EXISTS idx_profile_sync_remote_key
ON profile_sync_remote_records(service, entity_type, normalized_key);

CREATE TABLE IF NOT EXISTS profile_sync_service_state (
  service TEXT PRIMARY KEY,
  last_observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS profile_sync_recommendations (
  id INTEGER PRIMARY KEY,
  service TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  direction TEXT NOT NULL CHECK(direction IN ('add_remote', 'remove_remote')),
  entity_key TEXT NOT NULL,
  publication_id INTEGER REFERENCES publications(id) ON DELETE SET NULL,
  source_inbox_id INTEGER REFERENCES import_inbox_items(id) ON DELETE SET NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'skipped', 'completed', 'resolved')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TEXT,
  UNIQUE(service, entity_type, direction, entity_key)
);

CREATE INDEX IF NOT EXISTS idx_profile_sync_recommendations_pending
ON profile_sync_recommendations(service, status, direction, created_at);

CREATE TABLE IF NOT EXISTS r4ri_contribution_sections (
  section_key TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  title_de TEXT,
  body_de TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
