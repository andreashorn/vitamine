# VitaMine cloud hurdles

Temporary working checklist, reconstructed from the current implementation,
architecture decisions, deployment notes, and tests on 31 July 2026.

Status notation:

- `[x]` — implemented and verified well enough for the current invite-only
  pilot.
- `[ ]` — still required, or intentionally deferred.
- `🌐` — proven in VitaMine under sustained public internet-scale conditions,
  supported by operational measurements or an appropriate external review.
- “Partial” — useful foundations exist, but the item is not yet complete at the
  stated level.

### Current internet-scale status

No item carries `🌐` yet. This is intentional. VitaMine uses mature components
and several sound architectural patterns, but the service itself has not yet
demonstrated sustained public traffic, concurrent heavy jobs, recovery from
realistic failures, or a reviewed security posture. A component being widely
used elsewhere does not make this particular deployment internet-scale proven.

The marker can be awarded per item once the relevant evidence exists. Depending
on the claim, that evidence should include one or more of:

- a recorded load or concurrency test against an explicit target;
- production monitoring over a meaningful period;
- a backup restore, failover, or incident-response drill;
- an accessibility, privacy, or independent security review;
- measured error rates and service objectives under representative use.

### The original seven hurdles at a glance

| Original hurdle | Honest current assessment | `🌐` |
| --- | --- | --- |
| Multi-user data isolation | Pilot-ready: PostgreSQL ownership, authenticated workspaces, and per-CV jobs are implemented. | No |
| Authentication and account security | Partial: core route authorization and sessions are implemented; recovery, verification, passkeys, and deletion are not. | No |
| Sensitive personal data and GDPR | Partial: private/public separation and some credential protection exist; policies, consent, retention, erasure, and off-site recovery remain. | No |
| LLM cost and abuse | Partial: the managed key, invite gate, cheap model, and account spending cap bound the pilot; per-user accounting and abuse controls remain. | No |
| Long-running processing | Partial: durable import and enrichment jobs exist and survive browser closure; not every integration/export path is yet a queued, measured worker job. | No |
| Zotero and other integrations | Partial: account-scoped integrations and ORCID OAuth work; Zotero OAuth, connection management, and operational monitoring remain. | No |
| Public profiles and social features | Public-profile MVP is pilot-ready; the social-network portion is intentionally not built. | No |

## Architecture and portability

- [x] Use PostgreSQL as the authoritative cloud datastore rather than treating
  a collection of user SQLite files as the hosted database.
- [x] Preserve `.vitamine` as a portable SQLite-based import, export, backup,
  and future desktop-app format.
- [x] Store account ownership, sessions, CV snapshots, normalized projections,
  public profiles, and workspace metadata centrally.
- [x] Isolate active workspaces, outputs, and background jobs by authenticated
  CV owner rather than relying on the local edition's global active database.
- [x] Keep the local and hosted editions in one codebase, with deployment
  behavior controlled by configuration.
- [x] Keep hosted CVs available after a browser window or server process closes.
- [ ] Partial — replace the temporary SQLite compatibility workspace with
  PostgreSQL-native editing services if concurrency or write amplification
  becomes a real constraint.
- [ ] Define synchronization and conflict behavior if the future desktop app
  should edit the same CV as the cloud service. Today, `.vitamine` supports
  deliberate import/export, not live two-way synchronization.
- [ ] Partial — introduce object storage for original uploads and generated
  exports if their volume or retention grows beyond what is sensible on the
  application host.

## Accounts, authentication, and authorization

- [x] Invite-only account creation for the early-access phase.
- [x] Password hashing with per-password salts and a memory-hard derivation
  function.
- [x] Revocable, hashed sessions stored server-side.
- [x] Secure, HttpOnly, SameSite cookies over HTTPS.
- [x] Authentication and CV ownership checks on private routes rather than only
  a single password gate in front of the application.
- [x] Logout and baseline login rate limiting.
- [x] Browser password-manager and Touch ID autofill compatibility.
- [ ] Email-address verification.
- [ ] Password recovery.
- [ ] Passkeys/WebAuthn, including account recovery and fallback behavior.
- [ ] A user-facing list of signed-in devices/sessions with remote revocation.
- [ ] Optional authenticator-app MFA and recovery codes if passkeys do not cover
  the eventual security model.

## User data, privacy, and consent

- [x] Persist private CV data in PostgreSQL under an authenticated owner.
- [x] Keep `.vitamine` download/export available so users are not locked into
  the hosted service.
- [x] Generate public profiles from an explicit public projection rather than
  exposing arbitrary private database fields.
- [x] Exclude private contact and address information from the public-profile
  whitelist.
- [x] Normalize uploaded portraits and store a bounded PNG representation.
- [x] Encrypt stored ORCID OAuth credentials with a server-side secret.
- [ ] Partial — private CV data is protected by host, database, and application
  access controls, but is not independently application-encrypted at rest.
- [ ] Account deletion with complete, verifiable erasure of CVs, portraits,
  sessions, public snapshots, and queued artifacts.
- [ ] A complete account-data export, if anything outside the `.vitamine` CV
  should become user-owned data.
- [ ] Explicit, versioned consent before using CV content or behavior for
  analytics, aggregate research, product improvement, or model development.
- [ ] A way to withdraw consent without having to delete the account.
- [ ] Documented and enforced retention periods for uploads, temporary
  workspaces, job records, access logs, and deleted accounts.
- [ ] A privacy policy, terms of service, imprint, and clear processor list
  covering at least Strato, OpenAI, ORCID, Zotero, OpenAlex, and future email or
  monitoring providers.
- [ ] A GDPR-oriented data-flow and controller/processor review before opening
  registration beyond a small friends-and-colleagues pilot.

## LLM processing and cost control

- [x] Keep the managed OpenAI key server-side so users do not need their own
  API configuration.
- [x] Remove hosted-only LLM configuration and onboarding steps from the user
  interface while preserving them in the local edition.
- [x] Use a comparatively inexpensive configured model for the pilot.
- [x] Run long imports and enrichment as durable background jobs that survive a
  closed browser and recover after a service restart.
- [x] Bound the current exposure through invite-only access and the API
  account's own spending limit.
- [ ] Tell users clearly when their uploaded CV content is sent to OpenAI and
  obtain the appropriate consent.
- [ ] Per-account and per-job quotas, rate limits, and token/cost accounting.
- [ ] An operator view for unusually expensive, repeated, failed, or stuck
  jobs.
- [ ] Robust job idempotency and cancellation so retries cannot silently repeat
  expensive work.
- [ ] A defined behavior for provider outages, rate limits, and malformed model
  output.
- [ ] Upload abuse controls and, before a public launch, malware scanning.

## Research-service integrations

- [x] Production ORCID OAuth over HTTPS.
- [x] Encrypt ORCID tokens and validate OAuth state.
- [x] ORCID, Zotero, and OpenAlex enrichment paths.
- [x] OpenAlex citation metrics and ROR-informed institution mapping.
- [x] Automatically refresh sensible institution and metric information rather
  than requiring unnecessary buttons.
- [ ] Zotero OAuth so users do not have to create and paste API keys.
- [ ] A connections page where users can inspect, refresh, or revoke linked
  services.
- [ ] Integration-specific monitoring and disciplined retry/backoff behavior
  for third-party rate limits and outages.
- [ ] Periodic review of third-party API terms, attribution requirements, and
  data-retention constraints.

## Public profiles and embeds

- [x] Opt-in public profile with a stable username slug.
- [x] Bio, publications, metrics, and collaborator-map blocks.
- [x] Owner-only block visibility and ordering controls.
- [x] Whole-profile and per-block embed support with native, simple, and dark
  styles.
- [x] Searchable, paginated publications with linked titles and DOIs.
- [x] Citation overview, profile portrait, and OpenStreetMap collaborator map.
- [x] Editable public heading independent of the private person record.
- [x] Keep the profile visually generic, with restrained “Made with VitaMine”
  attribution rather than prominent product branding.
- [ ] Move or alias canonical public profiles to `vita.space/{username}` once
  the domain is ready, with redirects from old URLs.
- [ ] Preview, unlisted, public, and disabled visibility states.
- [ ] Reserved usernames, impersonation handling, reporting, and moderation.
- [ ] SEO and social-card metadata, plus a formal accessibility audit.
- [ ] A stable, versioned embed contract and documented frame/CSP behavior so
  existing embeds do not break as the application evolves.
- [ ] Custom domains only if actual user demand justifies the operational cost.

## Operations, reliability, and scaling

- [x] Production domain, TLS, and HTTPS redirection.
- [x] Isolated loopback application service behind Apache.
- [x] Systemd service hardening and restricted writable paths.
- [x] PostgreSQL schema initialization and migration from the earlier hosted
  SQLite account store.
- [x] Durable background-job storage and restart recovery.
- [x] Health endpoint, service logs, and documented manual verification.
- [x] Daily PostgreSQL dumps retained for 14 days.
- [x] Deployment and rollback notes that account for unrelated projects on the
  shared Strato host.
- [ ] Encrypted off-site backups, separated from the VPS.
- [ ] A tested restore drill, with explicit recovery-point and recovery-time
  objectives.
- [ ] Automated, versioned database migrations with pre-deploy checks and a
  rollback strategy.
- [ ] External uptime monitoring, exception tracking, resource alerts, and
  notifications for backup or background-job failures.
- [ ] A separate staging environment and repeatable CI/CD deployment path.
- [ ] Load and capacity tests for concurrent editing, imports, enrichment,
  profile traffic, storage growth, and database connections.
- [ ] Account/storage/job resource quotas.
- [ ] A dependency and operating-system update cadence.
- [ ] A documented path away from the single-VPS failure domain if usage grows;
  there is no reason to move prematurely.

## Security hardening

- [x] TLS, secure cookies, content-security policy, authentication, ownership
  enforcement, and hardened service boundaries.
- [x] Bounded portrait processing and normalization rather than serving
  arbitrary original images.
- [x] Keep secrets in server environment configuration and out of source
  control.
- [ ] A written threat model and independent security review.
- [ ] Explicit CSRF review for every state-changing browser endpoint; SameSite
  cookies provide a useful baseline but are not a complete review.
- [ ] Global and endpoint-specific abuse rate limits, not only login throttling.
- [ ] Malware scanning and stricter validation for uploaded CV documents and
  `.vitamine` databases before a public launch.
- [ ] Automated dependency, static-analysis, and secret scanning in CI.
- [ ] A secret-rotation and incident-response runbook.
- [ ] Review and add the remaining browser security headers, including HSTS,
  Referrer-Policy, and Permissions-Policy where appropriate.
- [ ] Confirm and document least-privilege database roles rather than relying
  only on loopback access and host isolation.
- [ ] Security and integrity checks for backups.

## User experience

- [x] Account library, autosave, persistent CVs, and a clear route back to “My
  CVs.”
- [x] No need to remember to download the database before closing the browser.
- [x] Compatible `.vitamine` download for local ownership and a future desktop
  edition.
- [x] Background operations continue after navigation or closing the browser.
- [x] More restrained, academic visual direction for the account library and
  public profile.
- [ ] Cross-browser and cross-device test matrix, especially Safari, iOS,
  password managers, Touch ID, and mobile layouts.
- [ ] Accessibility audit covering keyboard navigation, focus management,
  contrast, labels, maps, charts, and reduced-motion preferences.
- [ ] Persistent in-app and optional email notifications when long-running jobs
  finish or fail.
- [ ] User-facing recovery guidance and useful error identifiers for support.
- [ ] A deliberate onboarding/support path for testers rather than relying on
  developer guidance.

## Repository and delivery hygiene

- [x] Deployment architecture and operating procedures are documented in
  `docs/strato-deployment.md`.
- [x] Architectural decisions record the move to PostgreSQL-hosted accounts
  while retaining `.vitamine` portability.
- [x] Automated tests cover substantial account, cloud, profile, export, and
  compatibility behavior.
- [ ] Establish a clean Git checkpoint for the cloud/account/public-profile
  work that is currently uncommitted.
- [ ] Run tests and secret/artifact checks automatically for every proposed
  change.
- [ ] Add continuous integration and protected, reviewable deployment
  artifacts.
- [ ] Adopt small, cohesive commits and push them after verification so the VPS
  is never the only copy of working code.
- [ ] Tag known-good deployments and record the deployed commit rather than
  relying mainly on timestamped server backups.

## Business, legal, and future social features

- [x] Invite-only access keeps the first operational and cost experiment small.
  It does not replace privacy, security, or legal work.
- [ ] Decide whether the service is free, quota-based, subscription-based, or
  institutionally funded before expanding access.
- [ ] Billing, invoices, tax handling, and a transparent LLM usage policy if
  users eventually pay.
- [ ] Acceptable-use, copyright, takedown, abuse-reporting, and moderation
  processes.
- [ ] Product design for any social-network features before collecting social
  graph data.
- [ ] For aggregate insights: explicit purpose limitation, consent versioning,
  provenance, withdrawal, pseudonymization, minimum group sizes, and review of
  re-identification risk.

## Proposed next steps

### 0. Create a safe source-control baseline

1. Exclude private databases, SQLite journals, secrets, build artifacts, and
   Finder metadata.
2. Run the complete test suite and a secret/artifact audit.
3. Commit the current cloud/account/profile implementation as a named
   checkpoint and push it to GitHub.
4. Add CI for tests, secret scanning, and basic dependency checks.

This is the immediate priority because the working implementation currently
exists mainly in the worktree and on the VPS.

### 1. Finish the pilot safety minimum

1. Add encrypted off-site PostgreSQL backups and perform a restore test.
2. Add external uptime, exception, disk, database, queue, and backup-failure
   monitoring.
3. Implement account deletion and confirm that `.vitamine` remains a complete
   CV export.
4. Add simple LLM usage accounting and conservative per-account limits, even
   while real spending remains capped.
5. Add concise early-access privacy/LLM disclosures and record consent to the
   relevant version.

These items give the largest reduction in irreversible loss, surprise cost,
and trust risk for a small group of real testers.

### 2. Make accounts self-service

1. Add email verification and password recovery.
2. Add passkeys with a carefully designed recovery path.
3. Add device/session management.
4. Add durable job notifications and better support-facing error information.

### 3. Prepare for a public beta

1. Add staging, versioned migrations, CI/CD, deployment tags, and automated
   security checks.
2. Complete the privacy/terms/imprint and data-flow review.
3. Add broader rate limits, upload scanning, quotas, and load tests.
4. Audit accessibility and the main Safari/mobile/browser paths.
5. Introduce `vita.space` canonical profile URLs, moderation basics, and stable
   embeds.
6. Add Zotero OAuth and a user-facing connections manager.

### 4. Scale only in response to evidence

1. Measure database, CPU, storage, queue, and LLM behavior during the pilot.
2. Replace compatibility SQLite operations with PostgreSQL-native services only
   where measurements show a bottleneck or correctness concern.
3. Split jobs, application servers, storage, or databases away from the single
   VPS only when availability or load warrants it.
4. Design aggregate-data or social features as separate, consent-led products
   rather than silently extending the current CV-management data model.
