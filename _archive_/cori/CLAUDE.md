# CLAUDE.md

Design repository for Cori, with the M0 groundwork started. The README says what Cori is; `docs/` says how it is made; `VOICE.md` says how it speaks.

## Local setup

- `uv sync` gives Python 3.14 and every dependency. `uv run pytest`, `uv run black .`, `uv run lint-imports`.
- Postgres 18 with pgvector runs under `brew services` on 5432 (the spikes use their own cluster on 5499). Database `cori`, trust auth on localhost, roles from `migrations/`. Apply with `uv run alembic upgrade head`.
- Redis runs under `brew services` on 6379, bound to localhost. Tests use db 1.
- Secrets are in the macOS Keychain under service `cori` (`infra/secrets.py`): `anthropic_api_key`, `logfire_token`.
- Sandboxes: `container system start`, image from `infra/sandbox/base.Dockerfile`, network `cori-hostonly`, the only one a profile uses (spike 06 has the commands; `cori-egress` was a spike artifact).
- The trust boundary is `.github/CODEOWNERS`; the ruleset for `main` is in `.github/rulesets/`.

## Working here

- Read before proposing, propose before building. The reading order is README.md, VOICE.md, docs/architecture.md, docs/tech-stack.md, docs/reviews/, spikes/README.md.
- Nothing here is canon by inheritance. Every decision carries a status. If something reads as settled and you cannot find the reason, ask.
- Every design assumption cites REFERENCES.md. An assumption without a source is a gap to fill or a claim to remove.
- Naming: Cori is the system, never a character. Never write "he" or "she" for it, and never have it name itself. The loop inside is the supervisor.
- Writing: no em dashes; say what is true rather than what is not; no AI-writing tells.
- Code: Python 3.14, uv, black formatting only, no linters. Spikes live under `spikes/` and never edit the design documents.
- Build plans live in `docs/plans/`, one per component, written with `/plan <slug>` (see `docs/plans/README.md`). A plan never edits a design document. The build stage is run by the lead with `/build-m0`; each component is built by an agent running `/build <slug>` in its own worktree (see the Building section of the plans index).
- Commit as you go with plain messages that say what was decided. No push unless asked; no co-author.
