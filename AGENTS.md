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

Prefer small cohesive commits over one commit per incidental edit. Never commit
`.env` files, API keys, cloud peppers, invitation codes, cookies, private
`.vitamine` files, SQLite journals, local build output, or user data. Ask before
publishing if ownership or scope is genuinely ambiguous; otherwise carry the
verified Git checkpoint through without requiring a separate reminder.
