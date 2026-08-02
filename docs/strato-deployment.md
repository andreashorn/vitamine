# VitaMine hosted prototype on Strato

Last updated: 2026-08-02.

This is the project handoff for future Codex sessions. It intentionally contains
no API keys, invite codes, cookies, or other credentials.

## Current status

- The invite-only prototype runs on the user's existing Strato VPS.
- Host: `87.106.232.66` (Ubuntu 22.04, 4 CPU cores, 8 GB RAM, 300 GB disk).
- SSH user: `vitamine-deploy`.
- Local SSH identity: `~/.ssh/vitamine_strato_ed25519`.
- The deploy user has sudo access. Do not use root SSH.
- Primary hosted domain: `https://vitamine.cloud`.
- `www.vitamine.cloud` and plain HTTP redirect to the primary HTTPS URL.
- The Let's Encrypt certificate covers both names and renews automatically.
- `vitamine.cloud` is the long-term canonical application domain for accounts,
  authentication, APIs, OAuth callbacks, private CV workspaces, and operational
  website pages, including public profiles at
  `https://vitamine.cloud/<username>`.
- The attempted `scientific.bio` registration failed because the registry
  reported that the domain was already taken. It is not part of the planned
  architecture.
- `vita.space` is not part of the planned architecture because its premium
  registration/transfer pricing is not economical.
- The older local Dock preview/tunnel is no longer required for normal use now
  that `vitamine.cloud` has production HTTPS.

## Architecture

- Gateway application: `vitamine.cloud_app:app`.
- The gateway listens only on `127.0.0.1:8766`.
- systemd unit: `vitamine-cloud.service`.
- Installed source: `/srv/vitamine-cloud/current`.
- Python environment: `/srv/vitamine-cloud/venv`.
- Runtime-only temporary sessions: `/run/vitamine-cloud/sessions`.
- Encrypted persistent background-job inputs: `/var/lib/vitamine-cloud/jobs`.
- Runtime-only decrypted job workspaces: `/run/vitamine-cloud/jobs`.
- Authoritative hosted database: PostgreSQL database `vitamine`, owned by the
  login role `vitamine_app` and reachable only on the VPS loopback interface.
- Legacy gateway database and cutover source:
  `/var/lib/vitamine-cloud/vitamine-cloud.sqlite`.
- Secrets/environment: `/etc/vitamine-cloud.env`.
- Hosted deployment profile:
  `/srv/vitamine-cloud/current/deploy/strato/vitamine-hosted.json`.

An invite is redeemed once to create an email/password account and persistent
HTTP-only device cookie. Each private gateway route checks that account and the
ownership of the selected CV; the invite is not merely a landing-page gate.

PostgreSQL owns accounts, credentials, CV ownership, encrypted `.vitamine`
snapshots, revisions, public-profile snapshots, and workspace metadata. Private
CV snapshots use versioned AES-256-GCM application encryption; the earlier
plaintext person/entry/publication projections are kept empty. Passwords use
salted scrypt hashes.
Opening a CV materializes its PostgreSQL snapshot as a temporary SQLite
compatibility copy and starts an isolated Python/Uvicorn worker with the
original VitaMine UI. Successful mutating requests are backed up through
SQLite's snapshot API and committed to PostgreSQL. Closing or expiring a
workspace removes only the temporary copy; reopening rematerializes it from
PostgreSQL. After a workspace is created, the account UI navigates through
`/gateway/workspace/enter`, which validates the workspace and redirects to `/`.
The root response is marked `no-store` and varies by cookie because `/` serves
either the account library or the private workspace depending on the workspace
cookie. Do not replace this transition with same-URL client navigation; Safari
may reuse the cached account document without consulting the gateway.

Inside an open workspace, the top-left VitaMine logo and the account menu’s
`My CVs` action share the authenticated `DELETE /gateway/workspace` flow before
navigating to `/`. This persists the saved CV, clears the workspace cookie, and
allows the account library to render. The same logo is only a Dashboard
shortcut in the desktop/local deployment.

Hosted CV imports and full enrichment runs use the PostgreSQL
`background_jobs` queue. A supervised runner creates an isolated job database,
runs the shared VitaMine importer/enrichment code in a subprocess, and commits
the completed snapshot back to PostgreSQL before refreshing any open
workspace. Closing a tab, returning to My CVs, or signing out does not cancel
these jobs. Queued/running jobs are requeued after a service restart; an
interrupted LLM step may restart from the beginning. The browser polls
authenticated job-status routes and restores progress after reopening.

Import and enrichment submissions use a fresh browser-generated
`Idempotency-Key` for each intentional operation. If the browser loses the
response, its single automatic transport retry reuses that key. The gateway
stores a hash of the canonical request metadata and returns the original job
for an identical owner/CV/request; conflicting reuse receives HTTP 409. Keys
are scoped to both the authenticated account and CV. Legacy clients without a
key retain the existing one-active-job-per-CV behavior.

Institution geocoding is a separate lightweight worker task. It runs
opportunistically after a saved institution, accepted imported person field,
or ORCID link/refresh. Existing complete coordinates are treated as manual
unless VitaMine previously generated them, so automatic refreshes do not
silently replace a deliberate map position. The hosted gateway snapshots a
completed result when the browser's authenticated status poll observes it;
normal workspace close/expiry also persists the temporary copy.

Collaboration-map institutions are derived from publication-level OpenAlex
authorship affiliations, not from an author's current employer. OpenAlex
institution suggestions are checked against the raw affiliation string.
Ambiguous suggestions are passed through ROR's affiliation matcher and accepted
only when ROR marks a result as chosen; weak matches are omitted rather than
guessed. A metadata refresh replaces the stored rows for each publication so
previous false matches do not survive.

The Person form autosaves after a short debounce and serializes requests so a
slower older response cannot overwrite newer typing. Leaving a field flushes
the pending edit immediately; there is no manual Save Person button.

The Person tab can also store one optional portrait inside the `.vitamine`
database. The upload route accepts validated JPEG or PNG originals up to 25 MB
and 50 megapixels. Pillow corrects EXIF orientation, removes metadata, limits
the longest side to 2,000 pixels, and progressively resizes until the optimized
PNG is at most 5 MB. The normalized image bytes, PNG MIME type, normalized
filename, and dimensions live in dedicated person columns. The private person
JSON route exposes only portrait metadata, while an authenticated byte route
serves the image. Keeping the source portrait in the portable database also
lets future PDF/export templates reuse it without introducing a second private
asset store.

`.vitamine` remains the desktop/local format and the hosted import/export
format, but it is no longer the authoritative hosted storage architecture.
This supersedes the hosted-storage assumptions in the older local-first ADR.
The account menu describes this download as a personal SQLite copy
(`.vitamine`); it is a portable binary SQLite database, not a MySQL database or
a textual `.sql` dump.

Public profile routes and ownership scaffolding exist, but publishing is still
an early prototype.

The cloud gateway supports ORCID's OAuth authorization-code flow with the
Public API `/authenticate` scope. The exact production redirect URI is
`https://vitamine.cloud/gateway/orcid/oauth/callback`. OAuth state is one-time
and account/CV-bound; returned access and refresh tokens are encrypted at rest
using a key derived from the cloud pepper. Configure `ORCID_OAUTH_CLIENT_ID`,
`ORCID_OAUTH_CLIENT_SECRET`, `ORCID_OAUTH_BASE_URL`, and
`ORCID_OAUTH_REDIRECT_URI` only in `/etc/vitamine-cloud.env`. Do not put ORCID
credentials or tokens in the repository or deployment notes. Manual ORCID iD
entry remains available to the desktop app.

As of 2026-07-30, production ORCID client settings are present in
`/etc/vitamine-cloud.env`, and ORCID's production token endpoint accepts the
configured pair with the `/read-public` scope. The service has been restarted
with those settings, so the hosted dialog should expose the secure ORCID
authorization action. The dialog explicitly reports when settings are absent
and leaves the manual fallback available. The main Sync-panel action and the
secure OAuth action both carry the compact green ORCID iD mark.

The hosted gateway also supports Zotero's OAuth 1.0a key-exchange flow. Its
production callback is
`https://vitamine.cloud/gateway/zotero/oauth/callback`. Configure
`ZOTERO_OAUTH_CLIENT_KEY`, `ZOTERO_OAUTH_CLIENT_SECRET`, and
`ZOTERO_OAUTH_CALLBACK_URL` only in `/etc/vitamine-cloud.env`. The resulting
read-only Zotero API key is encrypted in PostgreSQL and injected into the
owner's isolated workspace and background-job processes; it is never written
to the portable `.vitamine` database. Desktop users retain manual API-key
setup. Connecting or disconnecting Zotero restarts only the current private
workspace worker so its runtime environment receives the updated credential.

As of 2026-08-01, Zotero OAuth is deployed in production with cloud schema
version 10. The configured client credentials and callback completed Zotero's
temporary-credential handshake successfully. No token value was printed or
stored by that verification; the temporary credential was left to expire.
Hosted workers prefer the account OAuth credential over any older manual key
embedded in an imported CV. OAuth makes the personal library and authorized
groups available in the selector, but each CV still syncs one selected Zotero
library/source at a time rather than merging every accessible library.

## PostgreSQL and backups

- PostgreSQL 14 runs as the standard Ubuntu service and listens on loopback
  port 5432.
- `VITAMINE_DATABASE_URL` is stored only in `/etc/vitamine-cloud.env`.
- Daily custom-format dumps run through
  `vitamine-cloud-backup.timer`, normally around 03:20 UTC with a randomized
  delay.
- Dumps are root-inaccessible-to-others files below
  `/var/backups/vitamine-cloud/postgres` and are retained for 14 days.
- The first dump was verified with `pg_restore --list`.
- These are same-server recovery backups, not yet encrypted off-site disaster
  recovery.

## Required system packages

The hosted importer requires `pandoc` in addition to the Python environment.
Do not rely on the `python-docx` fallback for production imports: it appends
table contents after ordinary paragraphs and therefore destroys the section
ordering of table-heavy academic CVs.

```sh
sudo apt-get install pandoc postgresql postgresql-contrib
command -v pandoc
```

Useful checks:

```sh
sudo systemctl status postgresql vitamine-cloud-backup.timer
sudo systemctl start vitamine-cloud-backup.service
sudo -u postgres psql -d vitamine -c '\dt'
sudo find /var/backups/vitamine-cloud/postgres -maxdepth 1 \
  -type f -name 'vitamine-*.dump' -printf '%TY-%Tm-%Td %s %f\n'
```

## Hosted versus desktop LLM behavior

Both deployments use the same application code:

- `config/vitamine-desktop.json` retains user-selectable local/API providers.
- `deploy/strato/vitamine-hosted.json` forces the shared OpenAI provider and
  `gpt-5.4-nano` with low reasoning effort and an 8,192-token completion
  ceiling, hides user configuration, rejects settings updates, and skips the
  LLM configuration step in onboarding.
- The shared key is stored only in `/etc/vitamine-cloud.env`.
- Never copy the key into the repository, command output, logs, or chat.

Private custom Word export templates use the same managed LLM configuration for ambiguous
heading mapping and a deterministic fallback for common academic CV structures. DOCX uploads are
limited to 20 MB. VitaMine stores the sanitized Word skeleton, classification, and semantic slot
blueprint in the active `.vitamine` database's `export_templates` table. On the hosted service this
means the template is covered by the existing encrypted PostgreSQL CV snapshot and persists through
the normal successful POST/PUT/DELETE workspace snapshot flow; no separate unencrypted template
directory or cloud schema migration is involved. Deploy `vitamine/custom_docx_templates.py`,
`vitamine/app.py`, `vitamine/schema.sql`, and the export UI assets together when this pipeline changes.

## Existing server workloads

The VPS also hosts unrelated small science projects. VitaMine was deliberately
isolated on its own loopback port, systemd service, source directory, runtime
directory, state directory, and Apache virtual-host file. Inspect Apache,
ports, and services before making global changes. Do not replace existing
virtual hosts or firewall rules casually.

Relevant repository files:

- `deploy/strato/vitamine-cloud.service`
- `deploy/strato/apache-vita.space.conf` (legacy prototype template)
- `deploy/strato/apache-vitamine.cloud.conf`
- `deploy/strato/requirements-cloud.txt`
- `deploy/strato/vitamine-cloud.env.example`
- `deploy/strato/vitamine-hosted.json`

## Safe access and checks

```sh
ssh -i ~/.ssh/vitamine_strato_ed25519 \
  -o IdentitiesOnly=yes \
  vitamine-deploy@87.106.232.66
```

Useful read-only checks:

```sh
sudo systemctl status vitamine-cloud.service
curl -fsS http://127.0.0.1:8766/health
sudo journalctl -u vitamine-cloud.service -n 100 --no-pager
sudo ss -ltnp
```

Workspace worker logs are runtime-only under:

```text
/run/vitamine-cloud/sessions/<session-id>/worker.log
```

Background-job files exist only while a job is queued or running:

```text
/var/lib/vitamine-cloud/jobs/<job-id>/
```

Queued CV uploads in that directory are authenticated ciphertext. Decrypted
job inputs, databases, and logs exist below `/run/vitamine-cloud/jobs/<job-id>/`
only while processing. Active editor workspaces are likewise under `/run`.

Uvicorn access-log entries appear only after a request completes. During a long
CV import, also inspect the uploaded file, worker process, and outbound
connection before concluding that the progress UI is stuck.

### Support identifiers and privacy-safe failures

Unexpected gateway failures and failed background jobs show the tester a
random support identifier such as `VM-…`. The same identifier is recorded in a
single-line JSON log entry with structural metadata only: timestamp, event,
high-level failure category, and either the endpoint/method or job ID/kind.
Exception messages, tracebacks, request bodies and headers, CV content,
filenames, credentials, invitation codes, and cookies are deliberately not
included. Background-job identifiers are also stored in
`background_jobs.support_id`.

Find the corresponding gateway record without broad log disclosure:

```sh
sudo journalctl -u vitamine-cloud.service --no-pager | grep -F 'VM-PASTE-ID-HERE'
```

For a background job, correlate its safe structural state in PostgreSQL:

```sh
sudo -u postgres psql -d vitamine -c \
  "SELECT id, kind, status, support_id, created_at, finished_at FROM background_jobs WHERE support_id = 'VM-PASTE-ID-HERE';"
```

It is safe to ask a tester for the support ID, approximate time, operation they
were attempting, browser/version, and reproduction steps. Do not ask them to
send a CV, database, password, API/OAuth credential, invitation code, cookie,
or full request/response headers merely to investigate a support ID.

## Private-data encryption key

Production must set `VITAMINE_DATA_ENCRYPTION_KEY` in
`/etc/vitamine-cloud.env` to a URL-safe base64 encoding of exactly 32 random
bytes. Generate it without placing the value in shell history, keep a protected
offline recovery copy, and never print it in logs or deployment notes. Losing
all copies makes every private CV snapshot unrecoverable.

The first deployment applies cloud migration 5, which encrypts existing CV
snapshots transactionally and deletes the obsolete plaintext projections. Take
and verify the required pre-migration PostgreSQL backup first. The backup made
before this migration still contains plaintext private CVs and must retain its
existing restricted permissions and retention policy.

For rotation, configure the new key as `VITAMINE_DATA_ENCRYPTION_KEY`, retain
the old key temporarily in comma-separated
`VITAMINE_DATA_ENCRYPTION_PREVIOUS_KEYS`, restart successfully, and run:

```sh
sudo /bin/bash -c 'set -a; . /etc/vitamine-cloud.env; set +a; \
  exec runuser -u vitamine-deploy -- /srv/vitamine-cloud/venv/bin/python \
  -m vitamine.scripts.rotate_cloud_data_key'
```

Verify downloads and workspaces before removing the previous key. Database
encryption does not make the service zero-knowledge: the application can
decrypt data while serving the owner, account metadata remains readable, and
explicitly published profiles remain public.

Migration 5 was deployed on 2026-07-31. Both active CV snapshots were
authenticated successfully after migration; PostgreSQL contained two encrypted
snapshots, zero SQLite plaintext headers, and zero rows in the retired private
projection tables. A post-migration custom-format backup was created and
verified with `pg_restore --list`.

The data key currently resides in the root-readable service environment. This
protects an isolated database dump, backup copy, or database-only disclosure,
but not an attacker who obtains both the server filesystem and its environment
file. Protecting against complete-host or root compromise would require an
external key service, manual unlock procedure, or provider-level encrypted
volume and is outside this application-encryption boundary.

## Deployment pattern

Run relevant tests locally first. Preserve the dirty worktree and deploy only
the intended files. Copy source into the matching paths below
`/srv/vitamine-cloud/current`; do not flatten nested directories. Install files
as `vitamine-deploy:vitamine-deploy` with readable modes rather than preserving
numeric ownership from a development machine. Workspace workers run as that
account and otherwise may fail while reading application or static files.

The evidence-based researcher-profile resolver spans `vitamine/app.py`,
`vitamine/cloud_job_runner.py`, `vitamine/identifiers.py`,
`vitamine/profile_resolver.py`, and `vitamine/scripts/import_uploaded_cv.py`.
Deploy that set together when profile discovery changes; verify the installed
resolver module exists before testing Enrich CV.

### Cloud schema migration procedure

The gateway records its cloud-store version in `cloud_schema_metadata`.
Application startup applies ordered additive migrations in one transaction and
refuses to open a schema newer than the running code understands. Migration
logs contain only the backend and numeric version.

Before deploying code with a new cloud migration:

1. Confirm there are no running background jobs.
2. Run and verify a fresh PostgreSQL backup:

   ```sh
   sudo systemctl start vitamine-cloud-backup.service
   sudo journalctl -u vitamine-cloud-backup.service -n 30 --no-pager
   sudo find /var/backups/vitamine-cloud/postgres -maxdepth 1 \
     -type f -name 'vitamine-*.dump' -printf '%TY-%Tm-%Td %TH:%TM %s %p\n'
   ```

3. Record the pre-deployment application revision and database schema version:

   ```sh
   git -C /srv/vitamine-cloud/current rev-parse HEAD
   sudo -u postgres psql -d vitamine -c \
     'SELECT version, updated_at FROM cloud_schema_metadata WHERE singleton=1;'
   ```

After restart, verify the service health, migration log, current version, and
representative account/CV operations:

```sh
curl -fsS http://127.0.0.1:8766/health
sudo journalctl -u vitamine-cloud.service -n 100 --no-pager \
  | grep cloud_schema_migration
sudo -u postgres psql -d vitamine -c \
  'SELECT version, updated_at FROM cloud_schema_metadata WHERE singleton=1;'
```

Migrations are additive, but rolling application code back across a schema
version is not assumed safe. If validation fails, stop VitaMine, preserve the
failed database for diagnosis, restore the verified pre-migration dump using
the commands below, redeploy the recorded application revision, and only then
restart the service. Never attempt to lower the version number manually.

```sh
sudo systemctl stop vitamine-cloud.service
sudo -u postgres pg_dump --format=custom --file=/tmp/vitamine-failed.dump vitamine
sudo -u postgres pg_restore --clean --if-exists --exit-on-error \
  --dbname=vitamine /var/backups/vitamine-cloud/postgres/<verified-pre-migration.dump>
sudo systemctl start vitamine-cloud.service
curl -fsS http://127.0.0.1:8766/health
```

Treat `/tmp/vitamine-failed.dump` as sensitive and move it to protected backup
storage or remove it after diagnosis.

Static HTML/JS changes can be copied without restarting the service. Bump the
asset query string in `index.html` when JavaScript changes so browsers do not
reuse an old script.

For Python or service changes:

1. Copy the intended files.
2. If the unit changed, stage it in `/tmp`, install it to
   `/etc/systemd/system/vitamine-cloud.service`, and run
   `sudo systemctl daemon-reload`.
3. Restart `vitamine-cloud.service`.
4. Verify `systemctl is-active`, `/health`, recent logs, and the affected API.

If gateway metadata must be recopied from the pre-account SQLite database,
load `/etc/vitamine-cloud.env` without printing it and run:

```sh
cd /srv/vitamine-cloud/current
PYTHONPATH=/srv/vitamine-cloud/current \
  /srv/vitamine-cloud/venv/bin/python \
  -m vitamine.scripts.migrate_cloud_sqlite_to_postgres \
  --source /var/lib/vitamine-cloud/vitamine-cloud.sqlite
```

The plaintext-era PostgreSQL dumps and the account-cutover rollback archive
were deliberately deleted on 2026-07-31 after the encrypted migration and its
post-migration backup were verified. Do not expect
`/var/backups/vitamine-cloud/2026-07-29-accounts-cutover` to exist.

Avoid restarting during an active job when practical. Jobs recover
automatically, but an interrupted LLM request may be repeated and incur a
second API charge.

## Invite administration

Use `vitamine.scripts.manage_cloud` on the server to create, list, or revoke
invitations. Invitation codes are secrets: show a newly created code only to the
user who requested it and never add it to this file or git.

## Background-job checks

Do not print job payloads because filenames can contain personal information.
Safe aggregate checks include:

```sh
sudo -u postgres psql -d vitamine -c \
  "SELECT kind, status, count(*) FROM background_jobs GROUP BY kind, status ORDER BY kind, status"
sudo find /var/lib/vitamine-cloud/jobs -mindepth 1 -maxdepth 1 -type d | wc -l
```

CV imports can still spend several minutes waiting on OpenAI. Current progress
is phase-based rather than per-LLM-chunk; the durable job remains visible after
the browser closes.

Managed background jobs record only provider-reported token counts and
structural accounting fields in `llm_usage_events`. Prompts, CV text, model
outputs, filenames, and credentials are not copied into this ledger. Report
daily totals by opaque account ID, operation, and model with:

```sh
sudo /bin/bash -c 'set -a; . /etc/vitamine-cloud.env; set +a; \
  cd /srv/vitamine-cloud/current; \
  exec runuser -u vitamine-deploy -- env PYTHONPATH=/srv/vitamine-cloud/current \
  /srv/vitamine-cloud/venv/bin/python \
  -m vitamine.scripts.manage_cloud llm-usage --days 30'
```

Summarize credit, underlying cost, account debit, and balance for every opaque
account ID with the same environment wrapper and
`-m vitamine.scripts.manage_cloud premium-accounts`.

Token counts are operational measurements only. VitaMine does not enforce
quotas from this table during the pilot.

Schema migration 8 adds the unencrypted central Premium Features Account
ledger. Each existing and new account receives a $3.00 early-access credit.
Usage events snapshot the applicable standard OpenAI token prices, the
underlying API cost, and a 2× account debit. The initial pricing version is
`openai-standard-2026-07-31`; its source is the official
[OpenAI API pricing page](https://developers.openai.com/api/docs/pricing).
Cached input is priced separately, while reasoning tokens are already included
in the provider's output-token count and are not charged twice. Unknown models
remain visibly unpriced rather than receiving a guessed charge.

These accounting tables are deliberately not application-encrypted: they
contain opaque account/CV/job identifiers, model names, token counts, prices,
and timestamps, but no prompts, CV text, model output, filename, or credential.
The account library shows the user's current balance, rounded to two decimal
places, in the signed-in header. Per-operation charges, the internal markup,
and usage history are not exposed in the user interface. Negative balances do
not prevent imports, enrichment, or other premium features during the pilot.

Schema migration 11 adds an append-only audit table for the trusted-beta
PayPal top-up flow. Set `VITAMINE_PAYPAL_BETA_TOPUP_URL` to a complete HTTPS
PayPal.Me payment link to enable the authenticated UI; leaving it empty keeps
the feature hidden. The fixed $5 top-up is intentionally self-attested: after
opening PayPal, the tester selects “I've paid” and receives credit immediately.
VitaMine does not call PayPal or independently verify settlement. Each browser
confirmation uses an idempotency key, so a retry cannot credit the same claim
twice. The audit table labels the confirmation mode explicitly and stores only
the opaque account ID, hashed claim key, amount, currency, and timestamp.

## Public profiles

`vitamine.cloud` is the canonical origin for the application and public
profiles. Profile URLs remain `https://vitamine.cloud/<username>`, with embeds
served from the same origin. This avoids split search indexing, ambiguous
sharing links, and unnecessary cookie/CORS complexity. A separate profile
domain is not currently planned; custom domains can remain a later paid feature
if real demand and revenue justify their operational cost.

Public profiles are opt-in projections of an account CV, not direct access to
the private `.vitamine` database. An owner publishes a chosen saved CV and
username from the account library. The gateway materializes a whitelisted JSON
snapshot in PostgreSQL and serves it at:

```text
/<username>
/api/public/<username>
/embed/<username>?block=<block>&theme=<theme>
```

The supported blocks are `bio`, `metrics`, `publications`, and
`collaborators`; the owner can reorder or hide whole blocks. Embed URLs accept
`native`, `simple`, or `dark` themes. Omitting `block` embeds the complete
visible profile. Publication search paginates matching results at 25 records
per page. Public publication rows use a compact year-and-record layout, clamp
long author lists to two visible lines, and expose both title and DOI links.
The collaborator block uses browser-fetched OpenStreetMap tiles with
visible attribution and draws VitaMine's institution markers and publication
links over them. The public metrics block is titled `Citations`; it deliberately
omits OpenAlex branding and citation-coverage notes. Its year chart fits all
columns at desktop widths and becomes horizontally scrollable on narrow
screens.

The standalone profile deliberately has no VitaMine logo or product navigation
above the researcher's content; a small `Made with VitaMine` credit remains in
the footer. Owners can edit the public profile name alongside the block layout.
That title is a public-only customization, survives automatic source-CV
refreshes, and does not alter the person record in the private CV.

When the source CV contains a portrait, the whitelisted public projection
copies that image into separate `public_profiles` binary columns and publishes
a versioned image URL. Raw image bytes are never placed in the public JSON
snapshot. The native About block displays the portrait beside the biography
as a 4:5 top-right float, so long biography text wraps beside it and returns to
the full text width below it. The portrait becomes an ordinary block above the
text on small screens. Removing or replacing the source portrait refreshes the
public copy along with the rest of the profile.

The public bio excludes private addresses, email, phone, birthplace, raw JSON,
and every other non-whitelisted person field. The public API also omits the
internal source database ID. Deleting the source CV is blocked until its profile
is unpublished or moved to another CV.

Whenever the authoritative saved CV is persisted, its public snapshot is
rebuilt automatically. A projection failure is logged but never prevents the
private CV from being saved. Owners can also request an immediate refresh from
the profile toolbar. Public requests also rebuild snapshots created by an older
projection schema, so deploying a new public-profile field does not leave the
owner with a manual maintenance task. No profile is published merely by
creating an account or uploading a CV.

The account library presents CV creation, upload, and saved CV cards as the
primary workspace, with public-profile management in a separate secondary
panel. The source CV does not repeat a View profile action because the profile
panel is its single management entry point; other CVs can still be selected as
the profile source. The account surface and native public-profile theme use a
restrained institutional visual system: neutral page backgrounds, white
bordered panels, modest corner radii and shadows, sans-serif headings, and
VitaMine green as the functional accent. The `simple` and `dark` embed themes
remain available.
