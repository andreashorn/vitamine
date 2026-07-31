# VitaMine hosted prototype on Strato

Last updated: 2026-07-30.

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
- `vita.space` has also been purchased but was still pending at the last check.
- Until the domain is live, the local Dock application `VitaMine Preview.app`
  runs `scripts/open_vitamine_cloud_preview.sh`, which forwards local port 8766
  to the server and opens `http://127.0.0.1:8766/`.

## Architecture

- Gateway application: `vitamine.cloud_app:app`.
- The gateway listens only on `127.0.0.1:8766`.
- systemd unit: `vitamine-cloud.service`.
- Installed source: `/srv/vitamine-cloud/current`.
- Python environment: `/srv/vitamine-cloud/venv`.
- Restart-safe temporary sessions: `/var/lib/vitamine-cloud/sessions`.
- Persistent background-job scratch space: `/var/lib/vitamine-cloud/jobs`.
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

PostgreSQL owns accounts, credentials, CV ownership, exact `.vitamine`
snapshots, revisions, normalized person/entry/publication projections, public
profile snapshots, and workspace metadata. Passwords use salted scrypt hashes.
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
  `gpt-4.1-mini`, hides user configuration, rejects settings updates, and skips
  the LLM configuration step in onboarding.
- The shared key is stored only in `/etc/vitamine-cloud.env`.
- Never copy the key into the repository, command output, logs, or chat.

## Existing server workloads

The VPS also hosts unrelated small science projects. VitaMine was deliberately
isolated on its own loopback port, systemd service, source directory, runtime
directory, state directory, and Apache virtual-host file. Inspect Apache,
ports, and services before making global changes. Do not replace existing
virtual hosts or firewall rules casually.

Relevant repository files:

- `deploy/strato/vitamine-cloud.service`
- `deploy/strato/apache-vita.space.conf`
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

Workspace worker logs are under:

```text
/var/lib/vitamine-cloud/sessions/<session-id>/worker.log
```

Background-job files exist only while a job is queued or running:

```text
/var/lib/vitamine-cloud/jobs/<job-id>/
```

Uvicorn access-log entries appear only after a request completes. During a long
CV import, also inspect the uploaded file, worker process, and outbound
connection before concluding that the progress UI is stuck.

## Deployment pattern

Run relevant tests locally first. Preserve the dirty worktree and deploy only
the intended files. Copy source into the matching paths below
`/srv/vitamine-cloud/current`; do not flatten nested directories.

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

The account cutover rollback files are under
`/var/backups/vitamine-cloud/2026-07-29-accounts-cutover`.

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

## Public profiles

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
the profile toolbar. No profile is published merely by creating an account or
uploading a CV.

The account library presents CV creation, upload, and saved CV cards as the
primary workspace, with public-profile management in a separate secondary
panel. The source CV does not repeat a View profile action because the profile
panel is its single management entry point; other CVs can still be selected as
the profile source. The account surface and native public-profile theme use a
restrained institutional visual system: neutral page backgrounds, white
bordered panels, modest corner radii and shadows, sans-serif headings, and
VitaMine green as the functional accent. The `simple` and `dark` embed themes
remain available.
