# ADR 0002: Local-first cloud publishing

## Status

Accepted for the invite-only prototype.

## Decision

VitaMine's hosted service does not upload or retain private `.vitamine`
databases. The browser-side application owns the local database. The hosted
service stores only:

- invitation records and hashed invitation codes;
- anonymous member and hashed device credentials;
- a user-selected public profile snapshot; and
- operational metadata required to update or remove that snapshot.

An invitation is exchanged once for a high-entropy device credential. The
credential is intended to be persisted in the local `.vitamine` database once
browser-side database writing is introduced. Possession of that database then
authorizes profile updates without a conventional account.

Public profile slugs are unique. One prototype member owns at most one slug.
The public JSON endpoint permits cross-origin reads so personal websites can
embed a profile. The HTML embed endpoint may be placed in an iframe.

## Consequences

- Private CV data can remain on the user's computer.
- The publishing service cannot recover a lost device credential initially.
- Published fields are necessarily stored on the server and must be clearly
  distinguished from private fields in the user interface.
- A later email recovery or account layer can attach to the anonymous member
  record without changing public profile URLs.
- LLM proxy requests are a separate feature because their content leaves the
  device even when it is not retained.
