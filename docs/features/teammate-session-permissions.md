# Teammate Session Permissions

**Status:** Shipped
**Tracking:** [#1410](https://github.com/tomcounsell/ai/issues/1410)

Teammate sessions (`SESSION_TYPE=teammate`) get a single hard rule enforced
in code: **writes to source-code paths require spawning a Dev session.**
Everything else — running scripts, restarting services, editing docs, updating
`.claude/` skills, writing to the knowledge base — is in scope. Before this
feature, teammate behavior was governed by prose-only constraints in
`build_teammate_instructions()`, which a motivated or forgetful model could
bypass. This feature replaces prose-only constraints with a code-level
allowlist plus a capable prompt that encourages operational work.

## The Hard Rule

The `pre_tool_use` hook (`agent/hooks/pre_tool_use.py`) checks
`SESSION_TYPE=teammate` and runs writes through
`_teammate_is_allowed_write()`. Writes to anything outside the universal
allowlist are blocked with a redirect message that tells the model how to
propose spawning a Dev session.

## Universal Allowlist

Teammates may write to:

| Surface | Rule | Rationale |
|---------|------|-----------|
| `docs/` | Anchored top-level directory; nested OK | Plans, feature docs, ADRs — explicitly teammate territory |
| `.claude/` | Anchored top-level directory; nested OK | Skills, commands, hooks, settings — teammate tunes the agent |
| `.github/` | Anchored top-level directory; nested OK | Workflows, issue templates, CODEOWNERS — repo metadata |
| `wiki/` | Anchored top-level directory; nested OK | Project wiki entries |
| `skills/` | Anchored top-level directory; nested OK | Repos that publish reusable skills |
| Top-level meta files | Exact filename at project root (depth 1) | `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, `OPENCLAW.md`, `SWARM.md`, `PLAN.md`, `TODO.md`, `ROADMAP.md`, `CONTRIBUTING.md`, `SECURITY.md`, `MAINTENANCE.md`, `DEPLOYMENT.md`, `INSTRUCTIONS.md`, `LICENSE`, `NOTICE`, `CNAME`, `.gitignore`, `.gitattributes`, `.editorconfig` |
| Top-level `*.md` | Any `*.md` at depth 1 | Catches `PHASE_1.md`, `MODERNIZATION_PLAN.md`, etc. without listing each |
| `~/work-vault/` | Absolute prefix | The knowledge base lives outside the repo |

The constants are defined in `agent/hooks/pre_tool_use.py` near the top of
the file (`TEAMMATE_ALLOWED_DIR_NAMES_AT_ROOT`,
`TEAMMATE_ALLOWED_TOPLEVEL_NAMES`, `TEAMMATE_ALLOWED_TOPLEVEL_EXTENSIONS`,
`TEAMMATE_ALLOWED_ABSOLUTE_PREFIXES`).

## Two-Pass Allowlist Algorithm

`_teammate_is_allowed_write(file_path)` runs two passes; both must agree:

1. **Pass 1 — `os.path.normpath`.** Defeats syntactic path-traversal via
   `..`. Without this, `docs/../agent/foo.py` would slip through a naive
   substring match because `/docs/` appears in the input even though the
   resolved path is `agent/foo.py`.
2. **Pass 2 — `os.path.realpath` on the parent directory.** Defeats
   symlink-escape. Without this, a Bash call (`ln -s ../agent docs/escape`)
   followed by a Write to `docs/escape/sdk_client.py` would land in
   `agent/sdk_client.py`. Pass 2 resolves `docs/escape` to `agent/` and
   rejects.

Realpath is applied to the parent directory (not the full path) so the
algorithm works for files that don't yet exist — `Write` creates them.

## Directory Anchoring (Not Substring Match)

The directory rule is anchored to `parts[0]` of the project-root-relative
path, not a substring match. This means:

- `agent/docs_handler/foo.py` does **not** match the `docs/` rule (would be
  a positional promiscuity bug otherwise).
- `tools/wiki_scraper.py` does **not** match the `wiki/` rule.
- `agent/byob_skill_triggers.py` does **not** match the `skills/` rule.

A `len(parts) > 1` guard ensures a bare file literally named `docs` (no
extension) at the project root does **not** match the directory rule.
Bare top-level files go through the explicit filename / extension
allowlist instead.

## Block Message (Redirect, Not Refusal)

When a teammate hits a disallowed path, the block message includes the
exact Dev-session redirect command:

```
Blocked: teammate sessions cannot write to '<path>'. This path looks like
source code, which requires a Dev session. To proceed:

  valor-session create --role eng --slug <slug> --message "<task description>"

Suggest this to the human first and wait for explicit confirmation before
spawning the Dev session. Teammates may write to: docs/, .claude/,
.github/, wiki/, skills/, top-level *.md and meta files, and
~/work-vault/.
```

The block is a **routing decision, not a refusal.** The teammate's job on
a block is to (1) restate what the human asked for, (2) propose the exact
`valor-session create` command, (3) wait for human go-ahead. The prompt
reinforces this in its WHEN BLOCKED section.

## Bash Audit Log

Bash is **not** blocked for teammate sessions. Instead, every command is
logged via `logger.info("[teammate-audit] bash command=...")` at INFO
level. Commands are truncated to 500 chars. The audit call is wrapped in
try/except so an audit failure can never block the user's command (matches
the liveness-writer pattern).

Greppable in `logs/worker.log`:

```bash
grep '\[teammate-audit\]' logs/worker.log
```

If volume becomes a problem, we can migrate to a Redis stream — but
that's deliberately YAGNI today. See the Rabbit Holes section of
`docs/plans/teammate-allowlist-enforce.md` for the reasoning.

## Acceptance of Limitations

The Bash route is intentionally open. A motivated teammate could write
`sed -i 's/x/y/' agent/sdk_client.py` and we wouldn't block it — but we
**would** log it under `[teammate-audit]`. We accept this trade-off:

- Trying to parse arbitrary Bash for path-mutating intent is brittle
  (`cp`, `mv`, `tee`, `sed`, `awk`, redirection, heredocs, `git apply`...).
- The audit log gives us after-the-fact visibility.
- The model is prompted to spawn a Dev session for code changes — it just
  isn't physically prevented from doing it via Bash.

The `ln -s ../agent docs/escape && Write docs/escape/sdk_client.py`
escape **is** closed by the realpath pass — but
`ln -s ../agent docs/escape && sed -i ... docs/escape/sdk_client.py` is
not. Same trade-off: audit-only.

## MultiEdit Coverage

Before this feature, the Write/Edit branch did not cover `MultiEdit`,
which meant PM sessions could bypass the docs/ allowlist via MultiEdit
(latent bug). The teammate enforcement work also fixed this gap: the
branch is now `tool_name in ("Write", "Edit", "MultiEdit")`. A
regression test in `tests/unit/test_pm_session_permissions.py` locks the
PM fix in.

## Prompt Rewrite

`build_teammate_instructions()` in `agent/teammate_handler.py` was
rewritten to drop the prior restrictive prose (the no-write / no-Agent /
no-Dev-spawn prohibitions) in favor of three new blocks that explain
the code-level enforcement and encourage operational work:

- **TOOL POSTURE** — describes the one-rule enforcement and the audit log.
  Tells the model to suggest spawning a Dev session when it hits a block,
  not to spawn one unilaterally.
- **OPERATIONAL WORK ENCOURAGED** — explicitly lists in-scope work:
  running scripts, restarting services, querying state, updating docs,
  editing `.claude/` skills, GitHub PM actions, knowledge-base management.
- **WHEN BLOCKED** — instructs the model to treat blocks as routing
  decisions, surface the redirect command to the human, and wait for
  confirmation.

The IDENTITY, CONVERSATIONAL RULES, RESEARCH FIRST, and DELIVERY REVIEW
sections are preserved verbatim. The delivery-review section in
particular comes from PR #1333 (tool-call contract) and must not drift.

## Key Files

| File | Role |
|------|------|
| `agent/hooks/pre_tool_use.py` | Allowlist constants, `_is_teammate_session()`, `_teammate_is_allowed_write()`, Write/Edit/MultiEdit gate, Bash audit log |
| `agent/teammate_handler.py` | `build_teammate_instructions()` — the prompt the model sees |
| `tests/unit/test_teammate_write_restriction.py` | Full allow/deny matrix including symlink escape |
| `tests/unit/test_qa_handler.py` | Prompt-marker assertions (TOOL POSTURE, OPERATIONAL WORK ENCOURAGED, WHEN BLOCKED, redirect command) |
| `tests/unit/test_pm_session_permissions.py` | PM MultiEdit regression |

## See Also

- [Eng Session Architecture](eng-session-architecture.md) — teammate
  fits into the broader session-type model alongside PM and Dev.
- [Composed Persona System](composed-persona-system.md) — composes the
  teammate prompt with the project's persona overlay.
