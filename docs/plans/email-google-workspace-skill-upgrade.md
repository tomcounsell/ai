---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-06-11
tracking: https://github.com/tomcounsell/ai/issues/1615
last_comment_id:
---

# Upgrade to General-Purpose Email and Google Workspace Skills

## Problem

Agents working in repos other than `ai` (e.g. `cuttlefish`, `psyoptimal`) fall back to BYOB browser automation for email and Google Workspace tasks, even though faster, more reliable CLI tools exist. Three gaps cause this:

1. The `gws` CLI is not installed on any machine — `which gws` returns nothing, and the path documented in `CLAUDE.md` (`~/src/node_modules/.bin/gws`) does not exist on the skills machine.
2. The existing `google-workspace` skill is `user-invocable: false`, MCP-only, and lives in the project-only skills directory (never synced to `~/.claude/skills/`), so agents in other repos cannot reach it.
3. No `/email` skill exists to tell an agent which tool to reach for first when reading or sending email.

**Current behavior:** An agent in `cuttlefish` that needs to read email has no documented tool path. It defaults to BYOB browser automation — slow, flaky, burns browser context.

**Desired outcome:** Any agent, in any repo, reads and sends email and uses Google Workspace via the lightest available tool (CLI → MCP → browser). The tool hierarchy is enforced by globally-synced skills, and `gws` is installed and verified on every machine by the `/update` orchestrator.

## Freshness Check

**Baseline commit:** `d04a7c98c4bb545ea9cd939d5be9bd93ea6a6b96`
**Issue filed at:** `2026-06-09T05:07:32Z`
**Disposition:** Major drift (issue's Part 1 premise corrected — see below)

**File:line references re-verified:**
- `scripts/update/npm_tools.py:44-46` (`MANAGED_PACKAGES`) — issue claims this is the insertion point for the gws prereq — **still holds.** Single-entry list (`@moona3k/excalidraw-export`); the install/verify/skip loop in `install_or_update()` is generic and handles any additional package.
- `scripts/update/verify.py:184-199` (`check_system_tools`) and `:221-310` (`check_valor_tools`) — issue claims `verify.py` is where CLI checks live — **still holds.** `check_command(name)` (line 80) is the generic non-fatal `shutil.which` + `--version` check; `check_valor_tools` is where project CLIs (`valor-email`, etc.) are verified.
- `.claude/skills/google-workspace/SKILL.md:5` — issue claims `user-invocable: false` — **still holds** (confirmed line 5).

**Cited sibling issues/PRs re-checked:**
- #1067 — closed; shipped `valor-email read/send/threads` via PR #1094. This is the foundation `/email` builds on. Still the correct dependency.

**Commits on main since issue was filed (touching referenced files):** None touching `npm_tools.py`, `verify.py`, or `hardlinks.py` since `2026-06-09`.

**Active plans in `docs/plans/` overlapping this area:** `google-workspace-email-composition-guidelines.md` (composition/draft-first rules — complementary, not overlapping), `valor-email-cli.md` (the shipped CLI this builds on), `incoming_email_attachments.md` (attachment handling — orthogonal). No conflict; this plan is the skill layer above all three.

**Major drift — Part 1 premise corrected:** The issue says "add gws to `MANAGED_PACKAGES`... same pattern as `@moona3k/excalidraw-export`" and "the gws npm package name is unknown — must be rediscovered." Research (below) resolved the package name and found that **the bare `gws` npm package is an unrelated combinatorial-testing tool** — installing it would be wrong. The correct package is **`@googleworkspace/cli`**, which exposes the `gws` binary. The npm-install approach itself is sound; only the package name was wrong in the recon. This does not stop the plan — it sharpens Part 1.

## Prior Art

- **#1067 / PR #1094**: "Add valor-email CLI tool (read/send/threads)" — shipped the `valor-email` CLI this plan's `/email` skill puts at priority 1. Succeeded; in production.
- **#851**: "Fix half-baked calendar auth plugin — complete Google Workspace OAuth flow" — prior Google Workspace auth work. Relevant context for the `gws auth` story but `gws` carries its own credential model (see Research).
- **#624**: "Vet and install OfficeCLI on all bridge machines" — the canonical precedent for installing a CLI tool as a machine prereq and verifying it via `/update`. The same shape applies here.
- No prior issue attempted to install `gws` or build an `/email` skill. This is the first attempt.

## Research

**Queries used:**
- `googleworkspace/cli gws install binary github releases authentication`
- (registry) `npm view gws` and `npm view @googleworkspace/cli`
- (filesystem) located the existing wrapper at `~/src/gato/bin/gws`

**Key findings:**

1. **The `gws` binary comes from `@googleworkspace/cli`, NOT a package named `gws`.**
   - `npm view @googleworkspace/cli` → version `0.22.5`, `bin = { gws: 'run.js' }`. This is Google's official Workspace CLI (Rust core, dynamically built from the Discovery Service; the npm package downloads the right prebuilt binary from GitHub releases).
   - `npm view gws` → version `1.0.17`, *"E2E Combinatorial Testing Tool"* — completely unrelated. **Installing bare `gws` would be a mistake.** This is the single most important correction to the issue.
   - Source: <https://github.com/googleworkspace/cli>, npm registry.

2. **Install path after `npm install -g @googleworkspace/cli`:** the `gws` binary lands at `$(npm prefix -g)/bin/gws`. On the skills machine the npm global prefix is `/Users/tomcounsell/.nvm/versions/node/v23.10.0` (nvm), so `gws` resolves at `.../bin/gws` — already on PATH for nvm-managed Node. The `CLAUDE.md` documented path (`~/src/node_modules/.bin/gws`) is stale and must be corrected to "`gws` (on PATH after `npm install -g @googleworkspace/cli`)".

3. **`gws` authentication is independent of the npm install.** `gws` requires a Google Cloud OAuth project; auth is `gws auth setup` then `gws auth login`. Credentials are encrypted at rest in the OS keyring (or `~/.config/gws/.encryption_key`). Source: cli README. **Implication:** installing the binary does NOT make it authenticated. The acceptance criterion "`which gws` succeeds after `/update`" is about *presence*, not *auth*. Auth is an `[EXTERNAL]` human step (see No-Gos) — the skill must degrade gracefully when `gws` is present-but-unauthenticated by falling through to the next tool in the hierarchy.

4. **A pre-existing `gws` wrapper lives at `~/src/gato/bin/gws`** (the `tomcounsell/gato` Nix monorepo). It is a bash shim that injects SOPS-decrypted credentials and execs `$GATO_REAL_GWS_BIN` (the same `googleworkspace/cli` binary, built via the `github:googleworkspace/cli` flake input). It only works inside gato's Nix dev shell, so it is NOT a viable cross-repo install path — but it confirms the upstream identity and that this machine's owner already uses this exact CLI elsewhere. We do not depend on gato; we install the clean npm package.

Memory saved: package-name correction (`gws` → `@googleworkspace/cli`) for future plan reuse.

## Data Flow

This is a tooling/skill change; the "data flow" is the agent's tool-selection decision path.

1. **Entry point**: An agent (in any repo) receives a request like "read my latest email" or "what's on my calendar."
2. **Skill load**: The agent loads `/email` (for mail) or `/google-workspace` (for Workspace services). Both are now globally synced, so they resolve outside the `ai` repo.
3. **Tool-selection decision tree** (the skill body): the agent walks a priority ladder, trying the lightest available tool first and falling through on absence/failure:
   - Email: `valor-email` (Redis-cached, fastest) → `gws gmail` (direct API) → Gmail MCP (`mcp__claude_ai_Gmail__*`, interactive sessions only) → BYOB (last resort).
   - Workspace (per service): `gws <service>` → MCP (`mcp__claude_ai_*` where available) → BYOB.
4. **Output**: The agent invokes the chosen tool via its Bash tool (CLI) or MCP, and returns the result — without ever reaching BYOB for simple read/send.

The `/update` data flow for Part 1: `run.py` → `npm_tools.install_or_update()` (installs `@googleworkspace/cli` if missing) → `verify.py` checks surface a non-fatal warning if `gws` is absent.

## Architectural Impact

- **New dependencies**: one new npm global package (`@googleworkspace/cli`) added to the update manifest. No new Python deps.
- **Interface changes**: none. `MANAGED_PACKAGES` gains one tuple; `verify.py` gains one check function call; two skills change frontmatter and move directories.
- **Coupling**: decreases agent coupling to BYOB for email/Workspace by giving lighter documented paths. Adds a soft dependency on `gws` being present (the skills degrade gracefully when it is not).
- **Data ownership**: unchanged. `gws` manages its own credentials; the repo stores no Google secrets for it.
- **Reversibility**: high. Revert the `MANAGED_PACKAGES` entry, the verify check, the two skill moves, and the doc edits. `npm uninstall -g @googleworkspace/cli` cleans the binary.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM (orchestration), code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm package-name correction landed; confirm skill-move vs. flag-flip approach)
- Review rounds: 1

The coding is small (one manifest entry, one verify check, two skill files, doc edits). The communication overhead is the package-name correction and the skills-global move decision — both resolved in this plan, so build should be smooth.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `npm` present | `command -v npm` | `@googleworkspace/cli` install path requires npm |
| `valor-email` CLI on PATH | `.venv/bin/valor-email --help` | Priority-1 tool referenced by the `/email` skill must exist |

Run all checks: `python scripts/check_prerequisites.py docs/plans/email-google-workspace-skill-upgrade.md`

## Solution

### Key Elements

- **`@googleworkspace/cli` as a managed npm prereq**: add to `MANAGED_PACKAGES` in `npm_tools.py` so `/update` installs it on every machine. Use `None` (latest) to track upstream — same convention as the existing entry.
- **`gws` presence check in `verify.py`**: a non-fatal `check_command("gws", "--version")` surfaced as a warning when missing, consistent with the optional-tool pattern (`sentry-cli` at line 197).
- **New `/email` skill** (`.claude/skills-global/email/SKILL.md`): a lightweight decision-tree skill, `user-invocable: true`, that documents the email tool ladder. Created in `skills-global/` so the existing hardlink sync propagates it to `~/.claude/skills/` automatically.
- **Upgraded `google-workspace` skill**: `user-invocable: true`, a new per-service tool-selection preamble (CLI → MCP → BYOB), and **moved** from project-only `.claude/skills/` to `.claude/skills-global/` so it syncs globally. Removing it from `PROJECT_ONLY_SKILLS` and the retired-skills tombstone list in `hardlinks.py` is part of this.
- **Doc corrections**: fix the stale `~/src/node_modules/.bin/gws` path in `CLAUDE.md`; add a `gws` line to the global `~/.claude/CLAUDE.md` so the tool is visible to agents in all repos.

### Flow

Agent in cuttlefish needs email → loads `/email` (resolves globally) → tries `valor-email read` → if unavailable, `gws gmail users messages list` → if `gws` unauthenticated/absent, Gmail MCP → BYOB never reached → result returned.

### Technical Approach

- **Part 1 (prereq install + verify):**
  - `npm_tools.py`: append `("@googleworkspace/cli", None)` to `MANAGED_PACKAGES`. The existing `install_or_update()` loop handles install/skip/update with no other changes.
  - `verify.py`: add a `gws` check. It is a **system/optional** tool, not a venv tool — model it on the `sentry-cli` optional pattern in `check_system_tools` (only append the check if relevant) OR add an always-present non-fatal `check_command("gws", "--version")` that reports `available=False` with a warning when missing. Decision: always-present non-fatal check (the issue wants the warning to surface on every `/update`, even pre-install). Place it in `check_system_tools` so it rides the existing aggregation into `VerificationResult`.
  - Confirm the result is **non-fatal**: `check_command` returns a `ToolCheck` with `available=False`; the update flow must not raise on it. Verify against how `valor-email`'s `available=False` is currently treated (warning, not abort).
- **Part 2 (`/email` skill):**
  - Author `.claude/skills-global/email/SKILL.md` with frontmatter `name: email`, `description:` (trigger phrases for reading/sending mail), `allowed-tools: Bash`, `user-invocable: true`.
  - Body: the four-tier ladder with one concrete invocation example per tier, plus an explicit "never use BYOB for simple read/send" rule and a "fall through on tool absence or auth failure" rule.
- **Part 3 (`google-workspace` upgrade + move):**
  - Move the file: `git mv .claude/skills/google-workspace/ .claude/skills-global/google-workspace/`.
  - Flip frontmatter `user-invocable: false` → `true`.
  - Prepend a tool-selection preamble enumerating, per service (Gmail, Calendar, Drive, Docs, Sheets, Slides, People, Chat, Forms, Keep), the priority order `gws <service>` → MCP (`mcp__claude_ai_*` where it exists) → BYOB.
  - `hardlinks.py`: remove `"google-workspace"` from `PROJECT_ONLY_SKILLS` (line 71) and remove the retired-skills tombstone `("skills", "google-workspace")` (line 58) so the sync hardlinks the new `skills-global/` copy instead of pruning it.
- **Part 4 (docs):**
  - `CLAUDE.md`: correct the `gws` path note (`~/src/node_modules/.bin/gws` → "on PATH after `npm install -g @googleworkspace/cli`").
  - `~/.claude/CLAUDE.md`: add a one-line `gws` entry to the tool list so it is visible globally. (This is a user-global file, edited directly — not synced by the repo.)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `npm_tools.py` already wraps install in try/except returning `NpmToolResult(action="failed", error=...)` — add/confirm a test asserting that a failed `@googleworkspace/cli` install produces a `failed` result (not a raised exception), mirroring the existing non-fatal contract.
- [ ] `verify.py::check_command` catches `subprocess.TimeoutExpired` and generic `Exception` — confirm the new `gws` check returns a `ToolCheck` (never raises) when the binary is absent. No new `except: pass` blocks introduced.

### Empty/Invalid Input Handling
- [ ] Skills are markdown, not code — no function inputs. N/A for the two SKILL.md files beyond markdown-lint.
- [ ] `verify.py` `gws` check: assert `available=False, error="Not found in PATH"` path when `shutil.which("gws")` is None (the pre-install state).

### Error State Rendering
- [ ] `/update` output: when `gws` is missing, the run must print a **warning** and continue (exit 0), not fail. Test asserts the verification result contains the `gws` unavailable check and the overall run is non-fatal.
- [ ] The error (missing `gws`) propagates to the user as a visible `/update` warning line, not swallowed.

## Test Impact

- [ ] `tests/unit/` (update module tests, if any cover `npm_tools.MANAGED_PACKAGES` length/contents) — UPDATE: adjust the expected package count/contents to include `@googleworkspace/cli`. Builder must `grep -rn "MANAGED_PACKAGES\|excalidraw-export" tests/` first.
- [ ] `tests/unit/` (update module tests covering `verify.check_system_tools`) — UPDATE: if a test asserts the exact tool list returned by `check_system_tools`, extend it to include `gws`. Builder must `grep -rn "check_system_tools" tests/` first.
- [ ] Hardlink sync tests covering `PROJECT_ONLY_SKILLS` or the retired list — UPDATE: if a test asserts `google-workspace` is project-only or tombstoned, update it to expect global sync. Builder must `grep -rn "PROJECT_ONLY_SKILLS\|google-workspace" tests/` first.
- [ ] Skill-validation tests (frontmatter schema, `user-invocable`) — UPDATE/ADD: ensure the new `/email` skill and the moved `google-workspace` skill pass whatever skill-frontmatter validator exists. Builder must `grep -rn "user-invocable\|skills-global\|SKILL.md" tests/` first.

If the above greps find no existing coverage, ADD a minimal unit test asserting (a) `@googleworkspace/cli` is in `MANAGED_PACKAGES`, and (b) a missing-`gws` verify check returns `available=False` without raising. No existing test deletions anticipated.

## Rabbit Holes

- **Wiring `gws` authentication into `/update`.** `gws auth setup`/`login` require an interactive Google OAuth flow and a Cloud project. Do NOT attempt to automate this in the update script. Install-and-verify-presence only; auth is an external human step.
- **Reverse-engineering / depending on the gato `gws` wrapper.** It is Nix/SOPS-coupled and only runs inside gato's dev shell. Tempting because it is already on this machine, but it is not a portable install path. Install the clean npm package instead.
- **Building a `valor-calendar`/`valor-drive` CLI to parallel `valor-email`.** Out of scope — the Workspace skill points at `gws` for those, no new Python CLIs.
- **Rewriting the entire `google-workspace` skill body.** The existing behavioral guidance (timezone, draft-first, previews) is good and stays. Only add the tool-selection preamble and flip frontmatter; do not relitigate the rest.
- **Normalizing MCP tool names in the skill.** The current skill uses shorthand (`gmail.search()`, `people.getMe()`) that does not match the actual `mcp__claude_ai_Gmail__*` tool names. Correcting every reference is a large edit; scope this plan to the tool-selection preamble (which uses correct names) and leave the legacy body shorthand as-is unless it is trivially in a touched line.

## Risks

### Risk 1: `@googleworkspace/cli` install path differs across machines (nvm vs system npm)
**Impact:** `gws` lands at `$(npm prefix -g)/bin/gws`, which may not be on PATH for non-nvm setups, so the verify check fails even after a successful install.
**Mitigation:** The verify check uses `shutil.which("gws")`, which respects the live PATH. If a machine's npm global bin is off PATH, that is a pre-existing machine-config issue surfaced (correctly) as a warning. Document the expected location in `CLAUDE.md`. Do not hardcode a path.

### Risk 2: Skill move breaks resolution mid-flight
**Impact:** Moving `google-workspace` from `.claude/skills/` to `.claude/skills-global/` while it is referenced as project-only could orphan it until the next `/update` hardlink sync runs.
**Mitigation:** The hardlink sync runs on every `/update`; the move and the `hardlinks.py` edit land together in one PR, and the sync recreates the global hardlink during the same `/update` invocation. Running `/update` on the skills machine (safe here) materializes the link in one pass — no intermediate orphaned state persists across machines.

### Risk 3: `gws` present but unauthenticated leads the agent to stall
**Impact:** Agent picks `gws gmail` because the binary exists, but every call fails with an auth error, and the agent does not fall through.
**Mitigation:** The `/email` and `google-workspace` skills explicitly instruct: "on tool absence **or auth failure**, fall through to the next tier." The decision tree is failure-aware, not just presence-aware.

## Race Conditions

No race conditions identified. All changes are synchronous, single-threaded: an npm install during `/update`, a `shutil.which` check, and static markdown skill files. The skill move is a one-shot git operation reconciled by the next idempotent hardlink sync; there is no concurrent writer to the skills directories.

## No-Gos (Out of Scope)

- [EXTERNAL] **`gws` OAuth authentication (`gws auth setup` / `gws auth login`).** Requires a human to complete a Google OAuth consent flow and provision/select a Google Cloud project. The agent cannot click through third-party consent UI. This plan installs and verifies *presence*; first-use auth is a documented human step.
- [EXTERNAL] **Editing `~/.claude/CLAUDE.md` on the bridge machine(s).** This plan edits the skills-machine copy; the bridge machine's user-global file is on a different host the agent cannot reach. Propagation there is a human/operator step on that machine.
Nothing else deferred — every relevant repo-side item (install manifest, verify check, both skills, repo `CLAUDE.md`) is in scope for this plan.

## Update System

This feature **is** a change to the update system — that is Part 1.

- **Update script changes (required):** add `("@googleworkspace/cli", None)` to `MANAGED_PACKAGES` in `scripts/update/npm_tools.py`; add a non-fatal `gws` check in `scripts/update/verify.py::check_system_tools`. Both ride the existing `/update` flow with no new orchestration.
- **Update skill changes (required):** `scripts/update/hardlinks.py` must stop treating `google-workspace` as project-only — remove it from `PROJECT_ONLY_SKILLS` and the retired-skills tombstone so the new `skills-global/` copy syncs. The new `/email` skill in `skills-global/` is picked up automatically by the existing sync.
- **New dependencies to propagate:** one npm global package (`@googleworkspace/cli`), installed automatically by the above on the next `/update` on each machine.
- **Migration for existing installs:** none beyond running `/update` (installs the package, syncs the moved/new skills). On the skills machine, `/update --full` is safe and materializes the changes immediately.

## Agent Integration

The agent reaches new functionality via a CLI on PATH (Bash tool) or a globally-resolvable skill — not via a new MCP server.

- **No new MCP server / `.mcp.json` change.** `gws` is a CLI the agent invokes through Bash; `valor-email` already exists. The Gmail MCP tools (`mcp__claude_ai_Gmail__*`) are already registered.
- **No bridge code change.** The bridge does not need to import anything; the skills are loaded by the agent at runtime.
- **CLI entry point:** `@googleworkspace/cli` provides the `gws` binary via npm global install — no `pyproject.toml [project.scripts]` entry (it is not a Python tool). `valor-email` is already declared.
- **Integration tests that verify the agent can invoke the new tools:**
  - Assert `/email` and `/google-workspace` skills resolve with `user-invocable: true` and live in `skills-global/`.
  - Assert `@googleworkspace/cli` is in `MANAGED_PACKAGES` (so it installs on every machine, making `gws` invokable via Bash).
  - A smoke test that, when `gws` is on PATH, `gws --version` exits 0 (skipped when absent — the auth-gated machine reality).

## Documentation

### Feature Documentation
- [ ] Create `docs/features/email-google-workspace-skills.md` describing the `/email` and upgraded `/google-workspace` skills, the tool hierarchy, and the `gws` install/auth story.
- [ ] Add an entry to `docs/features/README.md` index table.

### External Documentation Site
- [ ] N/A — repo has no external docs site for skills.

### Inline Documentation
- [ ] Correct the stale `gws` path in repo `CLAUDE.md` (`~/src/node_modules/.bin/gws` → "on PATH after `npm install -g @googleworkspace/cli`").
- [ ] Add a `gws` line to `~/.claude/CLAUDE.md` (user-global) so the tool is visible to agents in all repos.
- [ ] Comment in `npm_tools.py` `MANAGED_PACKAGES` noting `@googleworkspace/cli` provides the `gws` binary.

## Success Criteria

- [ ] `which gws` succeeds on the skills machine after `/update` runs (`@googleworkspace/cli` installed; binary on PATH).
- [ ] `/update` warns non-fatally if `gws` is missing, consistent with other optional CLI checks in `verify.py` (run exits 0).
- [ ] `.claude/skills-global/email/SKILL.md` exists, is `user-invocable: true`, and documents the ladder `valor-email` → `gws gmail` → Gmail MCP → BYOB.
- [ ] `google-workspace` skill is `user-invocable: true`, lives in `.claude/skills-global/`, and lists tools per service in priority order.
- [ ] `hardlinks.py` no longer marks `google-workspace` project-only; both skills appear under `~/.claude/skills/` after `/update`.
- [ ] An agent in a non-`ai` repo can invoke `/email` and `/google-workspace` and choose a non-BYOB tool.
- [ ] Repo `CLAUDE.md` and `~/.claude/CLAUDE.md` document `gws` with the correct path.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep -n "@googleworkspace/cli" scripts/update/npm_tools.py` confirms the manifest entry.

## Team Orchestration

### Team Members

- **Builder (update-prereq)**
  - Name: prereq-builder
  - Role: Add `@googleworkspace/cli` to `MANAGED_PACKAGES`, add the `gws` verify check, edit `hardlinks.py` to un-project-only `google-workspace`.
  - Agent Type: builder
  - Resume: true

- **Builder (skills)**
  - Name: skills-builder
  - Role: Author `/email` skill in `skills-global/`, move + upgrade `google-workspace` skill, fix doc paths.
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: upgrade-validator
  - Role: Verify install manifest, verify check non-fatality, skill frontmatter, global sync, doc edits.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create `docs/features/email-google-workspace-skills.md` and index entry.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Update-system prereq + verify + hardlinks
- **Task ID**: build-prereq
- **Depends On**: none
- **Validates**: `tests/unit/` update-module tests (grep first); manual `/update --verify`
- **Informed By**: Research finding 1 (package is `@googleworkspace/cli`, not `gws`)
- **Assigned To**: prereq-builder
- **Agent Type**: builder
- **Parallel**: true
- Append `("@googleworkspace/cli", None)` to `MANAGED_PACKAGES` in `scripts/update/npm_tools.py` with an explanatory comment.
- Add a non-fatal `gws` check to `scripts/update/verify.py::check_system_tools` (always-present `check_command("gws", "--version")`).
- In `scripts/update/hardlinks.py`, remove `"google-workspace"` from `PROJECT_ONLY_SKILLS` and remove the `("skills", "google-workspace")` tombstone entry.

### 2. Skills: /email (new) + google-workspace (move + upgrade) + doc paths
- **Task ID**: build-skills
- **Depends On**: none
- **Validates**: skill-frontmatter validator (grep first); `ls .claude/skills-global/email .claude/skills-global/google-workspace`
- **Informed By**: Research findings 2-4 (install path, auth degradation, gato wrapper non-dependency)
- **Assigned To**: skills-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/skills-global/email/SKILL.md` (`user-invocable: true`, four-tier ladder, fall-through-on-failure rule).
- `git mv .claude/skills/google-workspace .claude/skills-global/google-workspace`; flip `user-invocable: true`; prepend the per-service tool-selection preamble.
- Fix the stale `gws` path in repo `CLAUDE.md`; add the `gws` line to `~/.claude/CLAUDE.md`.

### 3. Validate all
- **Task ID**: validate-all
- **Depends On**: build-prereq, build-skills, document-feature
- **Assigned To**: upgrade-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `@googleworkspace/cli` in `MANAGED_PACKAGES`; confirm the `gws` verify check returns non-fatally when `gws` absent.
- Confirm both skills have `user-invocable: true` and live in `skills-global/`; confirm `hardlinks.py` no longer marks `google-workspace` project-only.
- Run `/update --verify` (safe on skills machine) and confirm a non-fatal `gws` warning when absent / a green check when present.
- Confirm doc edits. Report pass/fail.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-prereq, build-skills
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/email-google-workspace-skills.md`; add the index entry to `docs/features/README.md`.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Package in manifest | `grep -c "@googleworkspace/cli" scripts/update/npm_tools.py` | output > 0 |
| /email skill present | `test -f .claude/skills-global/email/SKILL.md && echo ok` | output contains ok |
| google-workspace moved | `test -f .claude/skills-global/google-workspace/SKILL.md && echo ok` | output contains ok |
| google-workspace not project-only | `grep -c '"google-workspace"' scripts/update/hardlinks.py` | exit code 1 |
| both skills user-invocable | `grep -l 'user-invocable: true' .claude/skills-global/email/SKILL.md .claude/skills-global/google-workspace/SKILL.md` | output contains both paths |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Pin or float `@googleworkspace/cli`?** This plan uses `None` (latest) to match the existing `excalidraw-export` convention and track Google's actively-developed CLI. If you'd prefer a pinned version for reproducibility across machines, name the pin (current latest is `0.22.5`).
2. **`gws` verify check: always-present vs. optional-style?** This plan makes it always-present and non-fatal (so the warning surfaces pre-install on every `/update`, per the issue). The `sentry-cli` precedent only adds its check when the tool is already present. Confirm you want the always-on warning rather than the optional-style silence-until-installed.
3. **MCP tool-name shorthand in the existing `google-workspace` body.** The legacy body uses `gmail.search()`-style shorthand that doesn't match the real `mcp__claude_ai_Gmail__*` names. This plan scopes the fix to the new tool-selection preamble and leaves the legacy body shorthand as-is (correcting all of it is a large, separable edit). OK to leave the legacy shorthand for a follow-up, or fold it in now?
