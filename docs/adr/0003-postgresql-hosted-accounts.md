# ADR 0003: PostgreSQL-backed hosted accounts

- Status: accepted
- Date: 2026-07-29
- Supersedes: the hosted-storage and anonymous-identity portions of ADR 0002

## Context

The first cloud prototype used invitation-derived device identities and
temporary server-side `.vitamine` files. This made early testing simple, but a
user had to remember to download a file before the workspace expired. Keeping
one authoritative SQLite database per hosted user would preserve the desktop
format, but would make cross-user querying, ownership rules, public publishing,
schema evolution, backups, and future social features unnecessarily awkward.

The original editor is nevertheless mature and expects a SQLite connection.
Rewriting all editor queries before accounts launch would add substantial risk.

## Decision

PostgreSQL is the authoritative hosted database.

It stores:

- invited user accounts and salted password hashes;
- revocable device sessions;
- CV ownership and metadata;
- an exact binary `.vitamine` snapshot and revision for each CV;
- normalized person, CV-entry, and publication projections;
- public-profile snapshots and publishing ownership;
- temporary workspace metadata and authentication-at-every-route state.

When a hosted CV is opened, the gateway creates a temporary SQLite
compatibility copy and runs the existing editor against it. After every
successful mutating request, the gateway creates a consistent SQLite backup,
updates the exact PostgreSQL snapshot, and refreshes the normalized projections
in one PostgreSQL transaction. Closing or expiring a workspace removes the
temporary copy, not the PostgreSQL CV.

`.vitamine` remains:

- the desktop application's native local database;
- the hosted import format;
- the hosted export and user-portability format;
- the compatibility boundary during the gradual move toward PostgreSQL-native
  editor services.

An invitation authorizes account creation. It does not by itself authorize
private application routes. Every private gateway and proxied editor request
requires a valid account session, and CV access additionally checks ownership.

## Consequences

- Users can close the browser and later reopen their CVs.
- The hosted service can query normalized data without opening arbitrary SQLite
  files.
- Desktop and hosted users can exchange `.vitamine` exports.
- Public profiles and future consented aggregate features have a conventional
  ownership model.
- The compatibility snapshot adds write amplification and is not the final
  high-scale editor architecture.
- PostgreSQL, migration, monitoring, and backups become operational
  requirements.
- Email verification, automated password recovery, off-site encrypted backups,
  account deletion/export workflows, consent records, and audit logging remain
  follow-up work before a broad public launch.
