---
tracking: https://github.com/tomcounsell/ai/issues/630
status: Shipped
---

# Hooks Best Practices & Audit

Claude Code hooks fire at lifecycle events (UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop, PreCompact, PostCompact). This project has hooks for validation, memory, calendar logging, SDLC tracking, compaction hardening, and post-compaction re-grounding. The `/audit-hooks` skill and accompanying best practices document ensure all hooks follow consistent safety patterns.

## The Manifest Is the Source of Truth

**`.claude/settings.json`'s `hooks` block and `~/.claude/settings.json`'s `hooks` block are both generated, not hand-edited.** The single declaration lives in `.claude/hooks/manifest.toml`, loaded by `scripts/update/hook_manifest.py::load_hook_manifest()` and projected into both scopes by the two generators in `scripts/update/hardlinks.py` (`generate_project_hooks()`/`sync_project_hooks()` for the project scope, `sync_user_hooks()`/`_merge_hook_settings()` for the user scope) every time `/update` runs. To add, change, or remove a hook, edit the manifest — a hand-edit to either `settings.json`'s `hooks` block will be overwritten (project scope) or diverge silently until the next removal pass reconciles it (user scope). See [Hook Manifest](hook-manifest.md) for the full field reference and both generators' semantics.

## Dispatcher Pattern

Where multiple hooks used to share one event/matcher (historically 7 separate `PreToolUse`/`Bash` validators, each its own interpreter start), the manifest now registers a single **dispatcher** entry that fans out in-process instead. `.claude/hooks/dispatch/pre_tool_use_bash.py` reads the hook JSON from stdin once and calls each validator's predicate function directly, in manifest declaration order, emitting one `{"decision": "block", ...}` on the first block (first-block-wins). Each validator gets its own `try/except`: all but one fail open (log via `log_hook_error()`, continue to the next validator — a crash in one must never skip the rest), while `validate_merge_guard` stays fail-closed (an exception there produces a block, never a silent allow) to preserve its pre-existing enforcement contract. New hooks that fire on the same event/matcher as an existing dispatcher should be added as another predicate inside the dispatcher, not as a second standalone registration, to avoid re-fragmenting back into N processes per call.

## The Interpreter Is Owned by the Scope, Not the Hook

Claude Code runs hook commands through a non-interactive `/bin/sh` with no `PATH` and no shell aliases, so a command starting with a bare `python` exits 127 — and because only **exit 2** blocks, that is a silently disabled guard, not a loud failure. Neither generator hardcodes an interpreter token; each supplies its scope's, because the right answer differs:

- **Project scope** → `"$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_python`, a committed shim that resolves the **main checkout's** venv at hook-execution time (so hooks behave identically from a `.worktrees/{slug}/` checkout).
- **Global scope** → an absolute system `python3`, resolved per machine by execution probe at generation time and baked into `~/.claude/settings.json`.

**When adding a hook you do not choose an interpreter** — you choose a `scope` in the manifest and the generator supplies the rest. Two obligations follow from that choice:

- A **global-scope** script must import and run clean under `MIN_GLOBAL_PYTHON` (3.9, the version of `/usr/bin/python3` on macOS): stdlib-only imports, no PEP 604 (`X | None`) annotations without `from __future__ import annotations`, and none of `datetime.UTC`, `tomllib`, `typing.Self`, `ExceptionGroup`, or `asyncio.timeout`. Annotation syntax is evaluated at **import** time, so a violation crashes before `main()` runs and the usual `try/except` fail-open guard never executes. Enforced by an always-on AST test.
- `uv run` is **forbidden** in any hook registration — `scripts/update/hooks.py` audits for it and fails (it can corrupt the venv and triggers slow dependency resolution on every tool call).

Full rationale, the fail-open behavior, and the `env -i` verification recipe: [Hook Manifest → Interpreter Contract](hook-manifest.md#interpreter-contract).

## Quick Reference

- **Run audit:** `/audit-hooks` in Claude Code
- **Best practices:** `.claude/skills-global/audit-hooks/BEST_PRACTICES.md`
- **Repo-specific audit declarations:** `.claude/skill-context/audit-hooks.md`
- **Hook manifest (source of truth):** `.claude/hooks/manifest.toml`
- **Generated hook settings:** `.claude/settings.json` (project), `~/.claude/settings.json` (user) — do not hand-edit
- **Error log:** `logs/hooks.log` (project scope), `~/.claude/logs/hooks.log` (user/detached-worker scope)
- **Hook scripts:** `.claude/hooks/` (Python) and `scripts/` (bash)

## Key Rules

1. **Stop hooks** must have `|| true` (prevents session hangs) — enforced by the manifest's `blocking=false` field (the generator appends the guard automatically) and checked in both scopes by the `hooks-audit` reflection below
2. **Advisory hooks** must have `|| true` (logging, memory, calendar never block) — same `blocking=false` mechanism
3. **Validator hooks** must NOT have `|| true` (they exist to enforce rules) — declare `blocking=true` in the manifest
4. **All `|| true` hooks** must call `log_hook_error()` on failure (no silent swallowing)
5. **Bash hooks** must use `set +e` and prefer venv binaries
6. **Python hooks** must minimize top-level imports (keep baseline <50ms)
7. **Timeouts** must match workload (5s simple, 10s git, 15s API)

See `.claude/skills-global/audit-hooks/BEST_PRACTICES.md` for full rules with examples, and `.claude/skill-context/audit-hooks.md` for this repo's declarations (validator inventory, log path, interpreter contract).

## Reflections Integration

The reflection scheduler registers `hooks-audit` (`reflections.auditing.run_hooks_audit`), which iterates `load_local_projects()` and runs once per project that has either `logs/hooks.log` or `.claude/settings.json` on disk (projects with neither are skipped silently). For each qualifying project the audit:
- Scans the project's `logs/hooks.log` for errors in the last 24 hours
- Raises a **dedicated** finding (distinct from that aggregate error count) when the log carries the `hook_python` shim's fail-open marker, `hook_python: no repo venv interpreter found` — the signal that project hooks are silently disabled because the main checkout was relocated, renamed, or lost its `.venv`. The shim writes that record itself: its fail-open branch runs no Python, so nothing else can
- Validates hook configuration consistency in **both** the project's `.claude/settings.json` and the shared `~/.claude/settings.json` (user scope), each check prefixed `[project]`/`[user]` — Stop/SubagentStop hooks must carry `|| true` and every registered script path must exist, in either scope
- Scans `.claude/agents/*.md` for agent-level `hooks:` frontmatter blocks and reports them informationally (that surface is declared per-agent-file, not owned by the manifest, but must never be silently omitted)
- Returns findings prefixed with `[{slug}]`, aggregated into a single run record with a per-project breakdown — see [reflections.md → Per-Project Audit Iteration](reflections.md#per-project-audit-iteration)

## Hook Classification

| Type | Purpose | `|| true` | Example |
|------|---------|-----------|---------|
| Validator | Block invalid operations | No | `validate_commit_message.py` |
| Advisory | Observe and enrich | Yes | `post_tool_use.py`, `stop.py` |
| Stop | Session cleanup | Yes | `calendar_hook.sh`, `stop.py` |

## Adding New Hooks

When adding a new hook:
1. Classify it (validator or advisory)
2. Add a `[[hook]]` entry to `.claude/hooks/manifest.toml` — do not hand-edit `.claude/settings.json` or `~/.claude/settings.json` directly; both are generated from the manifest on the next `/update`
3. Pick its `scope`. If `global`, the script must be dependency-free and clean under `MIN_GLOBAL_PYTHON` (see the interpreter section above) — never write an interpreter into the command yourself
4. Follow the patterns in `BEST_PRACTICES.md`
5. Add `log_hook_error()` error handling for advisory hooks
6. Run `/audit-hooks` to verify compliance

## Related

- [Claude Code hooks documentation](https://docs.anthropic.com/en/docs/claude-code/hooks)
- [Hook Manifest](hook-manifest.md) — the manifest schema, generators, dispatcher, and migration this doc's rules are enforced by
- [Session Transcripts](session-transcripts.md) — how hook-captured data is used
- [Subconscious Memory](subconscious-memory.md) — memory hooks (PostToolUse, Stop)
- [Google Calendar Integration](google-calendar-integration.md) — calendar hooks
