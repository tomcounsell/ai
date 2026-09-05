# Codex guidance for Valor

Codex is a collaborator working on Valor, not the Valor persona. Follow the user's
current request and existing authorization. Markdown is the default for docs.

## Skills

Project Codex skills live in `.agents/skills/`. General skills are maintained in
`.agents/skills-global/` and installed into the user skill directory with
`python3 scripts/codex_skills.py install`. Their descriptions are then available
across projects without duplicating them in repo discovery.
Select them naturally from their descriptions and read the relevant SKILL.md when
needed; `$name` is an explicit invocation. Read linked references progressively.
Do not load the whole catalog or require the user to name a skill.

Use real available Codex tools. Claude Skill/Task/AskUserQuestion calls, model
names, fork metadata, hooks, and environment-injected arguments are not Codex
APIs. Apply another skill by reading it and following its procedure. Work directly
unless delegation is useful and authorized; never assume a PM will resume a task.

The native SKILL.md defines each workflow. Supporting references describe domain
procedures and the target application; they do not override Codex tools, user
scope, authorization, or these runtime adaptations. Explicit user instructions
also take precedence over skill guidelines. A gate applies only when its actual
condition holds; do not invent approval requirements from advisory wording.

Repo-specific facts are maintained in `.claude/skill-context/<name>.md` and
`docs/sdlc/<name>.md`. Read these only for the integration you are operating.
Their real CLI flags, data contracts, leases, freshness checks, and configuration
locations remain applicable. Their Claude tool orchestration, persona, model,
implicit message-sending, and hook-injection assumptions do not apply to Codex.
Managed SDLC state writes require an explicitly active lane and its real run ID.
A normal Codex task does not become a managed lane merely because sdlc-tool exists.

## Operational invariants

- Read `docs/tools-reference.md` and CLI `--help` for current commands.
- `.python-version` is the authoritative interpreter pin. Use
  `scripts/pytest-clean.sh`, never bare pytest. Provision an isolated worktree
  venv matching the pin; the wrapper verifies it and pins PYTHONPATH to this checkout.
- Never kill pytest processes by pattern. `scripts/reap-xdist.sh --apply` is the
  documented worker sweep; understand its scope before using it.
- Access Popoto-managed data through the ORM, never raw Redis. Debug test data
  must use an explicitly assigned test database via `tests/db_claim.py`, with
  the resolved database asserted before writes. Ambient `setdefault` can leave
  production REDIS_URL in effect. Scope temporary records and cleanup by project.
- Secrets live in `~/Desktop/Valor/.env`, never committed repo files. Do not print
  secrets or prefixes. Follow current 1Password service-account conventions;
  do not add interactive authentication to unattended workflows.
- Hook registrations are generated from `.claude/hooks/manifest.toml`; edit the
  manifest/source and regenerate rather than hand-editing generated hooks blocks.
  These hooks protect Claude application sessions, not necessarily this Codex task.
- Each project's bridge contacts belong to exactly one configured machine.
  Validate projects.json before service changes. Operate services only from the
  designated service checkout, not an implementation/review worktree.
- For authorized runtime deployments, use `scripts/valor-service.sh` and verify
  bridge/worker health and Telegram connection. Documentation/skill changes alone
  do not require a production restart.
- Preserve the user's worktree and branch choices; otherwise use `codex/` branches.
  Do not stash, checkout over, clean, or commit another task's changes. Keep commit
  hooks enabled. Fetch and merge an explicit named ref rather than shared FETCH_HEAD.

See `docs/features/codex-skills.md` for coverage, installation, and validation.
