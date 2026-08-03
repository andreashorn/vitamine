# VitaMine project handoff

Before changing or deploying the hosted VitaMine service, read
[`docs/strato-deployment.md`](docs/strato-deployment.md). It records the current
server architecture, access path, deployment locations, verification commands,
and safety constraints.

Never commit or print the OpenAI API key, cloud pepper, invitation codes,
cookies, or private `.vitamine` files. The Strato host contains unrelated
science projects; inspect existing services before making server-wide changes
and keep VitaMine isolated unless the user explicitly approves otherwise.

## Git ownership

Codex owns routine Git hygiene for this project. After a coherent, verified
feature or deployment batch:

1. Inspect the complete worktree and preserve unrelated or user-created edits.
2. Audit the intended files for secrets, private databases, generated output,
   and machine-specific artifacts.
3. Run the relevant tests.
4. Stage explicit paths, create a concise descriptive commit, and push it to
   the configured GitHub remote.
5. For interactive changes that affect the hosted VitaMine application, deploy
   the pushed commit to production by default and complete the verification in
   `docs/strato-deployment.md`. Do not leave a finished hosted change merely
   committed or pushed unless the user explicitly requests a local-only or
   no-deployment handoff. Documentation and development-only files with no
   production artifact do not require a service deployment.

Prefer small cohesive commits over one commit per incidental edit. Never commit
`.env` files, API keys, cloud peppers, invitation codes, cookies, private
`.vitamine` files, SQLite journals, local build output, or user data. Ask before
publishing if ownership or scope is genuinely ambiguous; otherwise carry the
verified Git checkpoint through without requiring a separate reminder.
Treat commit, push, and (when applicable) deployment as part of completing the
requested change, rather than as optional follow-up work.

## Automated development queue

Unattended Codex runs may work only on unchecked tasks in the
“Automation-ready development queue” section of
[`docs/development-plan.md`](docs/development-plan.md). Use
`scripts/run_development_batch.sh`; do not select unchecked items elsewhere in
the roadmap automatically.

Each automated task must satisfy its documented acceptance criteria, run its
validation, update its own checkbox and evidence line, and produce one cohesive
local commit. Automated runs must not deploy, SSH to the server, push, purchase
or configure external services, use production secrets, or make external state
changes. Ambiguity, dirty state, missing credentials, destructive data changes,
or failed validation must stop the batch for human review.
