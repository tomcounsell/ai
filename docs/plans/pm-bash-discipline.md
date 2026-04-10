---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/881
last_comment_id:
---

# PM Bash Discipline — Read-Only Enforcement for PM Sessions

## Problem

On 2026-04-10 a PM session running `/sdlc` on issue #875 executed four `rm -rf` + `git clone` commands directly as Bash tool calls. The session took the "Recovery steps (must be done externally, not in this session)" output from a crashed dev-session at face value and attempted the recovery itself. The fourth attempt succeeded in re-cloning the repo, but a read-only PM session physically could not have run any of those commands.

The project's [`CLAUDE.md`](../../CLAUDE.md) documents PM sessions as *"Orchestrates work, PM persona, read-only"*, and the in-code comment at `agent/sdk_client.py:1965-1966` claims *"full permissions but hook-restricted. [...] Code writes blocked by pre_tool_use hook."* The first is a lie; the second is half-true.

**Current behavior:**
- PM sessions launch with `_permission_mode = "bypassPermissions"` (`agent/sdk_client.py:1962`).
- The SDK-level hook at `agent/hooks/pre_tool_use.py` blocks PM `Write` and `Edit` tool calls to paths outside `docs/` (verified in `tests/unit/test_pm_session_permissions.py`).
- **`Bash` tool calls are not checked for session type.** The hook inspects Bash commands only for sensitive-file write patterns (`.env`, `credentials`, etc.). Any other Bash command — including `rm -rf`, `git clone`, `git commit`, `git push`, `pip install`, `uv sync`, `git reset --hard` — runs unrestricted from a PM session.
- The shell script at `.claude/hooks/pre_tool_use.py` is purely observational (baseline capture + logging); it has no deny path and never fires for PM restrictions.

**Desired outcome:**
A PM session must not be able to mutate the filesystem, the git working tree, the venv, or remote state. Read-only orchestration (`git status`, `git log`, `gh issue view`, `tail logs/...`, `python -m tools.valor_session status`, `cat docs/plans/...`) remains fully available — that is the PM's job. Any mutating operation must be dispatched to a Dev session via the `Agent(subagent_type="dev-session", …)` pattern, never executed directly. The stale comment in `sdk_client.py` becomes true.

## Freshness Check

**Baseline commit:** `226fbc1d59e15eacd4ecaea09f5e3eeb4663fc53`
**Issue filed at:** `2026-04-10T08:31:33Z`
**Disposition:** Minor drift

The issue correctly identified the incident, the `bypassPermissions` setting, and the fact that the shell hook at `.claude/hooks/pre_tool_use.py` contains no PM logic. However, the issue's framing — *"`.claude/hooks/pre_tool_use.py` contains no PM-aware logic [...] the comment describes restriction that does not exist"* — is only half-correct. The real enforcement layer is a second hook that the issue did not reference.

**File:line references re-verified:**
- `agent/sdk_client.py:1962` — `_permission_mode = "bypassPermissions"` — still holds at line 1962.
- `agent/sdk_client.py:1964` — `if _session_type == SessionType.PM:` branch — still holds at line 1964.
- `agent/sdk_client.py:1965-1966` — stale comment "full permissions but hook-restricted [...] Code writes blocked by pre_tool_use hook" — still holds at lines 1965-1966. Half-true: Code *file* writes ARE blocked (via the SDK hook), but Bash commands that mutate code paths via `rm`, `git`, `pip`, etc. are NOT blocked.
- `.claude/hooks/pre_tool_use.py` — confirmed no PM logic, no deny path, only baseline capture + logging (standalone script invoked via `.claude/settings.json` PreToolUse hook).
- **Correction:** `agent/hooks/pre_tool_use.py` (a separate SDK-level hook registered via `claude_agent_sdk.HookMatcher` — see `agent/hooks/__init__.py:34`) DOES contain PM-aware logic. It defines `_is_pm_session()` at line 70 that reads `os.environ["SESSION_TYPE"]`, and blocks PM `Write`/`Edit` calls to non-`docs/` paths at lines 286-296. The gap is specifically that the `Bash` branch (lines 298-327) has no PM check — only sensitive-file pattern matching.

**Cited sibling issues/PRs re-checked:**
- [#880](https://github.com/tomcounsell/ai/issues/880) — worktree_manager crash (the first-order failure). Still open. Its fix is orthogonal to this plan; this plan addresses what a PM should be *allowed* to do in response to any crash, not the specific crash that triggered the incident.
- [#827](https://github.com/tomcounsell/ai/issues/827) — prior PM-role confusion (prompt-level restriction firing wrongly). Fixed and merged. Referenced only to contrast: #827 was a prompt restriction misfiring; this is a tool restriction that never fires at all. No overlap with the fix approach.

**Commits on main since issue was filed (touching referenced files):**
- None. `agent/hooks/pre_tool_use.py`, `agent/sdk_client.py`, `.claude/hooks/pre_tool_use.py`, and `config/personas/project-manager.md` are all unchanged since the issue was filed ~6 hours ago.

**Active plans in `docs/plans/` overlapping this area:** none.
- `session-type-pm-rename.md` — renames the `SessionType` enum; unrelated scope.
- `pm-skips-critique-and-review.md` — SDLC stage gating; unrelated scope.
- `pm-telegram-tool.md`, `pm-voice-refinement.md` — PM-adjacent but don't touch hooks or tool permissions.

**Notes:**
- The key infrastructure the fix depends on already exists: `SESSION_TYPE=pm` is injected into the child subprocess env at `agent/sdk_client.py:934`, and `agent/hooks/pre_tool_use.py` already reads it for Write/Edit enforcement. The fix is additive — extend the existing SDK hook's Bash branch, not build new infrastructure.
- The issue's recommendation to "lean Option 2 (allowlist)" is confirmed as the right call by this freshness check: the hook and env-var plumbing are in place and the test file `tests/unit/test_pm_session_permissions.py` already has the exact pytest pattern (monkeypatch SESSION_TYPE, call `pre_tool_use_hook` directly, assert `decision == "block"`). Adding Bash cases is mechanical.

## Prior Art

- **[Issue #827](https://github.com/tomcounsell/ai/issues/827)**: "PM sessions receive Teammate read-only restriction due to `is_dm` proxy" — fixed and merged as `d3084205 fix: use session_type instead of is_dm for Teammate restriction in build_context_prefix (#837)`. This was the *opposite* problem: a prompt-level restriction firing when it shouldn't. The lesson is that role enforcement must happen at the right layer (tool layer for tool access, prompt layer for prompt context). This plan fixes the opposite side of the same coin.
- **PR #837** merged the fix for #827. The approach there was purely in the system-prompt builder; no tool-layer enforcement was added or modified. So the tool layer has never been audited for PM Bash restrictions — this is the first such fix.
- **[Issue #491](https://github.com/tomcounsell/ai/issues/491)** (closed): ChatSession steering PR — surfaced in a keyword search but unrelated to hook enforcement.
- No prior merged PR has attempted to restrict PM Bash tool access. This is greenfield for the Bash-branch logic even though the Write/Edit-branch PM enforcement is established.

## Data Flow

End-to-end flow for a Bash tool call from a PM session today (the broken path):

1. **Entry point:** PM session receives a prompt and the model emits a `Bash` tool call with a command string.
2. **Claude Agent SDK:** forwards the tool call to all registered `PreToolUse` hooks (see `agent/hooks/__init__.py:34`).
3. **`agent/hooks/pre_tool_use.py::pre_tool_use_hook`**: the SDK hook runs. For `tool_name == "Bash"`, it iterates `SENSITIVE_PATHS` and checks for `> file`, `>> file`, `cp`, `mv`, `tee` patterns pointing at sensitive files. **No PM check is performed.** Returns `{}` (allow).
4. **`.claude/hooks/pre_tool_use.py`**: the shell-script hook runs via `.claude/settings.json` PreToolUse wiring. It captures a git baseline and logs the tool call. It does not return a deny decision. Ever.
5. **Bash tool executes the command.** In the incident: `rm -rf /Users/valorengels/src/ai && git clone ...` runs as the PM session's uid. The working tree is destroyed and re-cloned.
6. **Output:** command output returned to the PM session, which then proceeds to the next step in its reasoning loop.

After the fix, step 3 intercepts the Bash call for PM sessions: if the command does not match the read-only allowlist, the hook returns `{"decision": "block", "reason": "..."}` and the tool never runs. Steps 4-6 are unreached.

## Architectural Impact

- **New dependencies:** none. The fix reuses `os.environ["SESSION_TYPE"]` (already set by `sdk_client.py:934`) and the existing SDK hook's return-based block protocol (already used for Write/Edit).
- **Interface changes:** none visible to the PM persona or external callers. The hook signature, MCP tools, and `.claude/settings.json` are unchanged.
- **Coupling:** slightly tighter coupling between the PM session's allowed Bash surface and the hook file. This is acceptable — the allowlist is the durable contract.
- **Data ownership:** unchanged.
- **Reversibility:** high. The fix is localized to one function in one file plus a test file plus a persona-prompt snippet. A single revert restores the prior behavior.

## Design Decision — Chosen Enforcement Approach

The issue enumerates three options (classifier, allowlist, typed-tool decomposition). **This plan selects Option 2 — Command-Prefix Allowlist.** Rationale:

1. **Whitelist beats denylist for security.** A classifier (Option 1) must enumerate every mutating pattern; each gap becomes a bypass. An allowlist enumerates only the read-only surface, which is small and auditable. A command that looks read-only but pipes through `xargs rm` still wouldn't match the allowlist prefix (`git log` is allowed; `git log | xargs rm` starts with `git log` but contains additional verbs after a pipe — we require the *entire* command to be allowlisted, not just its prefix).

2. **The infrastructure already exists.** `SESSION_TYPE=pm` is injected into the subprocess env at `sdk_client.py:934`. The SDK hook at `agent/hooks/pre_tool_use.py` already reads it for Write/Edit enforcement. Extending the Bash branch is ~30 lines of code in one function.

3. **Option 3 (typed tools) is correct but too expensive for this bug fix.** Each typed tool would need to be designed, implemented, tested, and surfaced via an MCP server. The PM persona prompt would need rewriting to reference new tool names. That is a feature, not a fix. It can be tracked as a follow-up (suggested: new issue titled "Decompose PM Bash into typed orchestration tools") without blocking this incident fix.

4. **The allowlist is brittle-but-manageable.** The list only needs to grow when the PM genuinely needs a new orchestration verb, which is rare. Each addition is a trivial single-line PR with a clear rationale. Brittleness > letting `rm -rf` run.

The exact allowlist is specified in the Technical Approach section below.

## Appetite

**Size:** Small

**Team:** Solo dev (builder + validator pair).

**Interactions:**
- PM check-ins: 0 (design decision is already made in this plan)
- Review rounds: 1 (standard `/do-pr-review` cycle)

This is a localized bug fix: one hook function, one test file, one comment update, one persona-prompt snippet, one feature-doc note. The design is already committed to Option 2. No spikes, no new infrastructure, no cross-component coordination.

## Prerequisites

No prerequisites — the hook infrastructure, `SESSION_TYPE` env var injection, and the test pattern all already exist.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Existing SDK hook | `python -c "from agent.hooks.pre_tool_use import pre_tool_use_hook, _is_pm_session"` | Confirms the hook module is importable |
| Existing test file | `test -f tests/unit/test_pm_session_permissions.py` | Confirms the test pattern to extend |
| SESSION_TYPE env injection | `grep -n 'env\[.SESSION_TYPE.\]' agent/sdk_client.py` | Confirms PM env var is still wired |

## Solution

### Key Elements

- **PM Bash allowlist check** — a new pure helper `_is_pm_allowed_bash(command: str) -> bool` in `agent/hooks/pre_tool_use.py` that returns True iff the command matches one of the allowed read-only prefixes. Deliberately strict: the entire command (post-trim) must start with an allowed prefix, and the allowlist patterns forbid shell metacharacters that could smuggle mutations (`|`, `>`, `>>`, `&&`, `;`, `` ` ``, `$(`).
- **Bash branch denial path** — extend the existing `if tool_name == "Bash":` block in `pre_tool_use_hook` so that when `_is_pm_session()` is True AND `_is_pm_allowed_bash(command)` is False, the hook returns `{"decision": "block", "reason": "..."}`. The existing sensitive-file checks continue to run before the PM check (they catch non-PM Bash writes to `.env`, etc.).
- **Stale-comment repair** — rewrite `agent/sdk_client.py:1965-1966` to describe the real enforcement: "PM persona with hook-restricted tool access. `agent/hooks/pre_tool_use.py` blocks Write/Edit to paths outside `docs/` and restricts Bash commands to a read-only allowlist."
- **PM persona anomaly-response rule** — append a new "Anomaly Response" rule to `config/personas/project-manager.md` instructing the PM to hibernate and page a human when a child agent reports missing/corrupted working tree, `.git`, or `.venv`, rather than attempting auto-recovery.
- **Feature doc update** — add a short "Enforcement" section to `docs/features/pm-dev-session-architecture.md` that describes both the Write/Edit blocklist (already in place) and the new Bash allowlist, linking to `agent/hooks/pre_tool_use.py` as the ground truth.
- **Real hook-path test** — extend `tests/unit/test_pm_session_permissions.py` with a new `TestPMBashRestriction` class that invokes the real `pre_tool_use_hook` (no stub) with Bash tool input. Tests cover: each mutating command from the incident is blocked; each allowlisted read-only command from AC #3 is allowed; non-PM sessions are unrestricted; PM sessions still have sensitive-file checks running in front of the new allowlist.

### Flow

Running the fix in the live system:

PM session receives a prompt → model emits `Bash` tool call → SDK dispatches to `pre_tool_use_hook` → hook checks sensitive-file patterns (unchanged) → hook checks PM session + allowlist → if PM and not allowed: `{"decision": "block", "reason": "PM session Bash restricted to read-only commands. Spawn a dev-session for mutations."}` → SDK surfaces the block to the model → model adjusts (either picks an allowlisted command, spawns a dev-session, or hibernates per the anomaly-response rule).

### Technical Approach

**The allowlist.** The Bash command (after `.strip()`) must exactly match — or start with and be followed by a space — one of these prefixes. No other suffix restriction is imposed beyond the no-metacharacter rule described below.

```
# git (read-only verbs)
git status
git log
git diff
git show
git branch
git rev-parse
git ls-remote
git stash list
git config --get
git remote -v
git remote show
git rev-list
git describe
git shortlog

# gh CLI (view/list verbs only)
gh issue view
gh issue list
gh pr view
gh pr list
gh pr diff
gh pr checks
gh pr status
gh run view
gh run list
gh api  # read-only by the nature of typical PM use; see "No-Gos" for the tradeoff
gh repo view

# log/file reading
tail logs/
tail -n
tail -f logs/
cat docs/
cat config/personas/
cat CLAUDE.md
head docs/
head CLAUDE.md
ls
pwd
wc
file

# tools (read-only subcommands)
python -m tools.valor_session list
python -m tools.valor_session status
python -m tools.agent_session_scheduler status
python -m tools.agent_session_scheduler list
python -m tools.memory_search search
python -m tools.memory_search inspect
python -m tools.sdlc_stage_query
python -m tools.code_impact_finder

# pytest collect-only (no execution)
pytest --collect-only

# curl to localhost dashboard
curl -s localhost:8500/dashboard.json
curl localhost:8500/dashboard.json
```

**Metacharacter guard.** Even if a command starts with an allowlisted prefix, the entire command string is rejected if it contains any of: `|`, `>`, `>>`, `<`, `&&`, `||`, `;`, `` ` ``, `$(`, `$((`. This prevents `git log | xargs rm`, `git status > /tmp/x && rm -rf ...`, command substitution injection, and similar smuggling. The one intentional exception is that plain `&` at end-of-string (background) is also rejected — PM sessions have no legitimate reason to background processes.

**Why prefix matching and not full regex.** The model's Bash output is natural and consistent — the PM uses commands in their canonical form. A prefix check plus metacharacter rejection covers the realistic surface without the cost and bypass-risk of regex parsing. If a command is rejected by the allowlist, the block reason tells the model *which* allowlist prefix it's close to, so the model can correct (and humans auditing the allowlist can easily see what to add).

**Fallback for ambiguity.** If `SESSION_TYPE` is not set in the environment (non-SDK harness, local test), the check is skipped — the hook's current behavior is unchanged for non-PM sessions. This is consistent with how the Write/Edit PM check already works.

**Stale-comment repair at `agent/sdk_client.py:1965-1966`.** Current:
```python
# PM session: PM persona, full permissions but hook-restricted.
# Can write to docs/ and use gh CLI. Code writes blocked by pre_tool_use hook.
```
Replacement:
```python
# PM session: PM persona with hook-restricted tool access.
# agent/hooks/pre_tool_use.py enforces: (1) Write/Edit blocked to paths outside docs/,
# (2) Bash restricted to a read-only allowlist (git status/log/diff/show, gh issue/pr view/list,
# tail logs/, cat docs/, python -m tools.valor_session status, etc.).
# Any mutation must be dispatched to a dev-session subagent.
```

**PM persona anomaly-response rule.** Append this to `config/personas/project-manager.md` after the "Escalation Policy" section:
```markdown
## Anomaly Response — Hibernate, Do Not Self-Heal

When a child agent reports that the working tree is broken, `.git` is missing/corrupted,
`.venv` is missing, a required file has vanished, or the repository is in an inconsistent
state, you MUST:

1. Stop dispatching stages immediately.
2. Do NOT attempt to re-clone, reset, or otherwise "recover" the workspace yourself.
3. Surface the failure to the human with the child agent's error output verbatim.
4. Wait for human guidance. The human is the only authority that may authorize a
   destructive recovery step.

Rationale: your tool surface is read-only by design (see agent/hooks/pre_tool_use.py).
Even if you could run a recovery command, you SHOULD NOT — recovery is a human decision.
The 2026-04-10 incident that motivated issue #881 was a PM session treating a
"repo missing" error as recoverable and running `rm -rf && git clone` four times until
one attempt succeeded. That is not a valid recovery path.
```

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] `agent/hooks/pre_tool_use.py::pre_tool_use_hook` has no bare `except Exception: pass` blocks in scope. The only `try`/`except` is in `_start_pipeline_stage` (for Redis failures) and `_handle_skill_tool_start` (for the same), both of which log warnings. No test changes needed there.
- [x] The new `_is_pm_allowed_bash` helper will be a pure function with no exception handlers — it returns a boolean. If it raises for any reason (unexpected), the enclosing `pre_tool_use_hook` function is not wrapped in a try/except, so the SDK would surface the error. A test asserts the helper never raises for empty strings, whitespace, or None.

### Empty/Invalid Input Handling
- [ ] Test: empty Bash command string returns True from `_is_pm_allowed_bash` — no-op is allowed (the SDK would pass through a no-op Bash call harmlessly). OR test that empty string is blocked; pick one and document. **Decision:** block empty commands too — they serve no PM purpose and a bare empty string is a classifier-evasion vector.
- [ ] Test: whitespace-only command `"   "` is blocked for PM sessions.
- [ ] Test: None passed as command (shouldn't happen but defensive) — the helper handles it gracefully (returns False → command blocked).

### Error State Rendering
- [ ] When the hook returns `{"decision": "block", "reason": "..."}`, the SDK surfaces the `reason` string to the model as the tool result. Verify the reason string is informative enough for the model to correct course: it names the session type, says which command was blocked, and points at "spawn a dev-session subagent" as the resolution. Test asserts the reason string contains the command, "PM session", and "dev-session".

## Test Impact

- [ ] `tests/unit/test_pm_session_permissions.py::TestPMWriteRestriction` — NO CHANGE. The Write/Edit tests remain valid; this plan adds a sibling class, not modifies existing tests.
- [ ] `tests/unit/test_pm_session_permissions.py` (module-level) — UPDATE: add a new `TestPMBashRestriction` class alongside the existing `TestPMWriteRestriction`. Add at least 10 test cases:
  - `test_pm_blocked_from_rm_rf` — the incident command
  - `test_pm_blocked_from_git_clone`
  - `test_pm_blocked_from_git_commit`
  - `test_pm_blocked_from_git_push`
  - `test_pm_blocked_from_git_reset_hard`
  - `test_pm_blocked_from_git_checkout`
  - `test_pm_blocked_from_pip_install`
  - `test_pm_blocked_from_uv_sync`
  - `test_pm_blocked_from_rm_rf_venv`
  - `test_pm_blocked_from_metacharacter_smuggling` — `git log | xargs rm -rf .`
  - `test_pm_blocked_from_command_substitution` — `git status; rm -rf /tmp/x`
  - `test_pm_blocked_from_redirection` — `git log > /tmp/x && rm -rf ...`
  - `test_pm_allowed_git_status`
  - `test_pm_allowed_git_log`
  - `test_pm_allowed_git_diff`
  - `test_pm_allowed_gh_issue_view` — with issue number arg
  - `test_pm_allowed_gh_pr_list`
  - `test_pm_allowed_tail_logs`
  - `test_pm_allowed_cat_docs_plans`
  - `test_pm_allowed_valor_session_status`
  - `test_non_pm_session_bash_unrestricted` — `SESSION_TYPE=dev` can still run `rm -rf tmp/`
  - `test_pm_sensitive_file_check_still_runs` — `SESSION_TYPE=pm` attempting `echo x > .env` is blocked by the sensitive-file check (not the new allowlist, but the check order matters)
  - `test_pm_empty_command_blocked`
  - `test_pm_whitespace_only_blocked`
- [ ] `tests/unit/test_pre_tool_use_hook.py` — NO CHANGE. This file tests the standalone shell script at `.claude/hooks/pre_tool_use.py`, which is not modified by this plan.
- [ ] `tests/unit/test_pre_tool_use_start_stage.py` — NO CHANGE. Tests stage-extraction logic unrelated to tool access.

**Test authenticity requirement (AC #5):** All new test cases invoke the real `pre_tool_use_hook` function via `asyncio.run(pre_tool_use_hook(input_data, ...))`. No stubs, no mocking of the hook itself. The only mock is `mock_context` (per the existing pattern for `TestPMWriteRestriction`). Environment setup uses `monkeypatch.setenv("SESSION_TYPE", "pm")` exactly as the existing Write/Edit tests do.

## Rabbit Holes

- **Regex-based command parsing.** Tempting to write a "proper" parser for Bash commands so we can distinguish `git log | less` (pager, safe) from `git log | xargs rm` (mutation). DO NOT. The metacharacter rejection is strict enough and forces the PM into clean command forms. Any time spent on a parser is time that won't be on the actual fix.
- **Decomposing PM Bash into typed MCP tools.** Option 3 from the issue is the correct long-term architecture but is not this plan. File it as a follow-up issue after this lands. Do not conflate the two — that is how Small fixes become Large features.
- **"Making the stale comment true" by changing the SDK permission mode.** Tempting to set PM's `_permission_mode` to something other than `bypassPermissions`. DO NOT. The existing tests (`test_pm_session_not_using_plan_mode`, `test_default_permission_mode_is_bypass`) lock this in, and the write-to-`docs/` PM capability depends on `bypassPermissions` to work. The right layer for enforcement is the hook, not the SDK permission mode.
- **Adding Bash rules to `.claude/hooks/pre_tool_use.py`** (the shell script). DO NOT. That hook runs for all Claude Code sessions including the main Valor session that must NOT be restricted. All PM-aware logic lives in `agent/hooks/pre_tool_use.py` (the SDK hook), which only runs for sessions spawned via `ValorAgent` with `session_type="pm"`.
- **Updating the allowlist during the build.** Resist the urge to discover "oh I need `git fetch`" during the build and add it. The initial allowlist is committed in this plan. If the build or any downstream PM session actually needs a new command, add it in a follow-up PR with the rationale. Keeping the allowlist small and deliberate is the point.

## Risks

### Risk 1: The allowlist is missing a command the PM actually needs, breaking its workflow
**Impact:** A PM session hits a legitimate read-only command (e.g., `git fetch`, `git stash list`) that isn't in the allowlist, and the block prevents it from progressing. The session would either adapt (spawn a dev-session, which is correct but slower) or hibernate.
**Mitigation:** Before merge, manually enumerate the Bash commands currently used by the PM persona and the SDLC skill files (`grep -rn 'git \|gh \|python -m tools.' .claude/skills/sdlc/ .claude/skills/do-*/ config/personas/project-manager.md`) and cross-check each against the allowlist. Any command used by a committed skill must be on the list. The build task includes this audit as a checklist item.

### Risk 2: An allowlisted command can still be mutating with creative argumentation
**Impact:** `git show HEAD -- file.py > /tmp/x.py` is rejected by the metacharacter guard. But `git show HEAD:file.py` (no redirection, no mutation) is allowed. If a future allowlist entry introduces a subcommand that mutates under unusual flags (e.g., `gh api --method POST`), that would be a silent gap.
**Mitigation:** The initial allowlist is audited in the build task. For `gh api` specifically, the plan keeps it on the allowlist (PM does use it for status queries) but documents in the test comment that it's the one entry where the prefix check trusts the PM not to pass `--method POST/PATCH/DELETE/PUT`. A follow-up hardening can add a method-flag inspection if needed, but that is not in scope for this fix.

### Risk 3: Non-PM sessions accidentally start getting `SESSION_TYPE=pm` due to an upstream bug
**Impact:** A dev session suddenly becomes restricted and cannot commit or push, breaking the SDLC pipeline.
**Mitigation:** The `SESSION_TYPE` env var is only set in `agent/sdk_client.py:934` when `self.session_type` is truthy, and is set to the actual session type enum value. A dev session sets `SESSION_TYPE=dev`, not `pm`. The existing test `test_dev_session_type_can_write_anywhere` locks this in for Write/Edit; a new parallel test `test_non_pm_session_bash_unrestricted` locks it in for Bash. If this risk manifests, the tests would immediately fail.

### Risk 4: The PM reason-string is ignored by the model, and the session loops
**Impact:** The model sees the block, ignores the "spawn a dev-session" hint, and retries the same mutating command in a slightly different form until it gives up or blows its turn budget.
**Mitigation:** (a) The reason string explicitly names the allowed alternative ("spawn a dev-session subagent"). (b) The PM persona's Anomaly Response rule instructs it to hibernate on repeated blocks rather than fighting them. (c) The session steering loop and auto-continue cap (50) put a ceiling on damage. This risk is soft and is observed by monitoring post-deployment.

## Race Conditions

No race conditions identified — the hook is invoked synchronously within a single tool call, reads an environment variable (immutable for the subprocess lifetime), performs pure string matching, and returns. There is no shared state, no concurrent access, and no data prerequisite. The hook is stateless across invocations.

## No-Gos (Out of Scope)

- **Option 3 typed-tool decomposition.** Deferred to a follow-up issue (recommended title: "Decompose PM Bash into typed orchestration tools"). The allowlist lands first; typed tools can replace it later if the cost-benefit is ever justified.
- **Fixing the worktree_manager crash from issue #880.** That is tracked separately and is the first-order failure from the incident. This plan only addresses the second-order failure (PM executed recovery commands).
- **Changing the SDK permission mode for PM sessions.** `bypassPermissions` stays. Enforcement lives in the hook.
- **Auditing other session types for Bash restrictions.** Dev sessions and Teammate sessions are out of scope. Only the PM session is restricted by this plan.
- **Hardening `gh api` against `--method POST/PATCH/DELETE/PUT` flags.** Mentioned in Risk 2. If it becomes a real problem, it's a follow-up — not this fix.
- **Adding a hook-level audit log for blocked commands.** The hook already logs via the module-level logger, and the SDK surfaces the block reason back to the model. Structured audit logging is a nice-to-have, not a requirement.
- **Editing `.claude/hooks/pre_tool_use.py` (the shell hook).** It stays as-is. All enforcement is in `agent/hooks/pre_tool_use.py`.

## Update System

No update system changes required — this fix is purely internal to the Python codebase. No new dependencies, no new config files, no new launchd services. The `/update` skill and `scripts/remote-update.sh` do not need changes. Deployed machines pick up the fix via standard `git pull` on the next update cycle.

## Agent Integration

No agent integration required — this is a restriction on the existing Bash tool's PM-session behavior, not a new capability surfaced to the agent. The agent continues to use the same `Bash` tool name; the only change is that some command strings return a block decision instead of executing. No MCP server changes, no `.mcp.json` edits, no `mcp_servers/` additions. The bridge (`bridge/telegram_bridge.py`) is unaffected — it spawns PM sessions via the existing `ValorAgent` interface and the new hook behavior applies automatically.

## Test Impact

- [x] `tests/unit/test_pm_session_permissions.py` — UPDATE: add `TestPMBashRestriction` class with the ~25 test cases enumerated in the "Test Impact" section above. No existing tests in this file are modified.
- [x] `tests/unit/test_pre_tool_use_hook.py` — NO CHANGE. This file tests the shell script at `.claude/hooks/pre_tool_use.py`, which is not modified.
- [x] `tests/unit/test_pre_tool_use_start_stage.py` — NO CHANGE. Tests stage-extraction logic in the SDK hook, unrelated to the tool-access branch being modified.
- [x] Integration tests — NO CHANGE. No integration tests currently exercise the PM-session + Bash + hook deny path end-to-end. The unit test in `test_pm_session_permissions.py` invokes the real hook function directly (per AC #5), which is the canonical test path for this layer.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/pm-dev-session-architecture.md`: add an "Enforcement" section after the existing session-type table. Content: two short paragraphs describing (1) the Write/Edit-to-docs restriction (already in place), (2) the new Bash read-only allowlist restriction, and (3) the anomaly-response rule in the PM persona. Point readers at `agent/hooks/pre_tool_use.py` as the ground truth for the allowlist and `config/personas/project-manager.md` as the ground truth for the anomaly-response rule. This covers AC #8.
- [ ] No `docs/features/README.md` index change needed — `pm-dev-session-architecture.md` already has an entry.

### Inline Documentation
- [ ] Update the stale comment at `agent/sdk_client.py:1965-1966` per the exact replacement text in the Technical Approach section. This covers AC #4.
- [ ] Add a module-level docstring comment in `agent/hooks/pre_tool_use.py` briefly noting that the Bash branch now enforces a PM allowlist. Point readers at the test file for the authoritative list of allowed/blocked commands.
- [ ] Inline docstring for the new `_is_pm_allowed_bash` helper: describe inputs, return value, the metacharacter guard, and why allowlist over denylist.

### External Documentation Site
- Not applicable. This repo does not publish a Sphinx/MkDocs site.

## Success Criteria

- [ ] AC #1 — PM session Bash tool access is restricted. The SDK hook `pre_tool_use_hook` denies any Bash command from a PM session that is not in the allowlist (or that contains forbidden metacharacters). **Verification:** `grep -n '_is_pm_allowed_bash' agent/hooks/pre_tool_use.py` returns the helper + its call site.
- [ ] AC #2 — All incident commands blocked. Unit tests assert that `rm -rf ai`, `git clone https://github.com/tomcounsell/ai.git`, `git checkout main`, `git reset --hard`, `git push`, `git commit -m "..."`, `pip install foo`, `uv sync`, `rm -rf .venv` are all rejected from a PM session with `{"decision": "block", ...}`.
- [ ] AC #3 — All legitimate read-only commands still work. Unit tests assert that `git status`, `git log --oneline -10`, `git diff`, `git show HEAD`, `git branch`, `gh issue view 881`, `gh pr view 123`, `gh pr list`, `gh pr checks 123`, `tail logs/bridge.log`, `cat docs/plans/pm-bash-discipline.md`, `python -m tools.valor_session status --id X`, `python -m tools.agent_session_scheduler status` are all allowed (hook returns `{}` or no decision).
- [ ] AC #4 — Stale comment repaired. `agent/sdk_client.py:1965-1966` reflects the real enforcement mechanism. **Verification:** the comment text contains `"Bash restricted"` and references `agent/hooks/pre_tool_use.py`.
- [ ] AC #5 — Real hook-path test. The test cases invoke `asyncio.run(pre_tool_use_hook(...))` directly (not a stub, not a classifier helper in isolation). **Verification:** `grep -c 'pre_tool_use_hook(' tests/unit/test_pm_session_permissions.py` returns at least 25 after the fix (was 7 before).
- [ ] AC #6 — PM persona anomaly-response rule added. `config/personas/project-manager.md` contains a "Anomaly Response" section with the text specified in the Technical Approach section. **Verification:** `grep -n "Anomaly Response" config/personas/project-manager.md` returns one line.
- [ ] AC #7 — No regression in existing PM session tests. `pytest tests/unit/test_pm_session_permissions.py -v` shows all pre-existing tests (Write/Edit restrictions, env injection, permission mode) still pass. Full unit suite `pytest tests/unit/ -q` is green.
- [ ] AC #8 — Feature doc updated. `docs/features/pm-dev-session-architecture.md` has a new "Enforcement" section describing both the Write/Edit blocklist and the Bash allowlist, plus the anomaly-response rule. **Verification:** `grep -n "Enforcement" docs/features/pm-dev-session-architecture.md` returns one line.
- [ ] Tests pass (`/do-test`) — the full unit suite is green.
- [ ] Documentation updated (`/do-docs`) — the feature doc and inline comment changes land.
- [ ] Lint clean — `python -m ruff check .` and `python -m ruff format --check .` return exit code 0.
- [ ] Allowlist audit complete — the builder has grepped the PM persona prompt and SDLC skill files for Bash commands and confirmed every legitimate read-only command is on the allowlist (Risk 1 mitigation).

## Team Orchestration

### Team Members

- **Builder (pm-bash-hook)**
  - Name: pm-bash-hook-builder
  - Role: Extend `agent/hooks/pre_tool_use.py` with the PM Bash allowlist helper + branch update. Update the stale comment in `agent/sdk_client.py`. Add the anomaly-response rule to `config/personas/project-manager.md`. Update `docs/features/pm-dev-session-architecture.md`.
  - Agent Type: builder
  - Resume: true

- **Test Writer (pm-bash-tests)**
  - Name: pm-bash-test-writer
  - Role: Add `TestPMBashRestriction` class to `tests/unit/test_pm_session_permissions.py` with the ~25 test cases enumerated in Test Impact.
  - Agent Type: test-writer
  - Resume: true

- **Validator (pm-bash-validator)**
  - Name: pm-bash-validator
  - Role: Run the full unit suite. Grep the PM persona prompt and SDLC skill files for Bash commands and verify the allowlist audit. Confirm each acceptance criterion has a passing verification command.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Extend the SDK hook with the PM Bash allowlist
- **Task ID**: build-hook
- **Depends On**: none
- **Validates**: `tests/unit/test_pm_session_permissions.py::TestPMBashRestriction` (created in task 2)
- **Informed By**: Freshness Check (existing hook infrastructure), Design Decision (Option 2 allowlist)
- **Assigned To**: pm-bash-hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Add the `_is_pm_allowed_bash(command: str) -> bool` helper to `agent/hooks/pre_tool_use.py`, implementing the prefix allowlist and metacharacter guard from the Technical Approach section.
- Extend the `if tool_name == "Bash":` branch in `pre_tool_use_hook` so that after the sensitive-file checks, it runs the PM check: `if _is_pm_session() and not _is_pm_allowed_bash(command): return {"decision": "block", "reason": "..."}`.
- Write the block `reason` string to include the offending command (truncated to 200 chars), the words "PM session", and the phrase "spawn a dev-session subagent" so the model has a clear recovery path.
- Add a module-level docstring comment noting the PM Bash enforcement, and an inline docstring for `_is_pm_allowed_bash` with its contract.

### 2. Add the test cases
- **Task ID**: build-tests
- **Depends On**: none (test file can be drafted in parallel with hook — tests fail until hook lands)
- **Validates**: `pytest tests/unit/test_pm_session_permissions.py -v` shows all tests pass after both tasks merge
- **Informed By**: Test Impact section
- **Assigned To**: pm-bash-test-writer
- **Agent Type**: test-writer
- **Parallel**: true
- Add the `TestPMBashRestriction` class to `tests/unit/test_pm_session_permissions.py` following the exact pattern of the existing `TestPMWriteRestriction` class.
- Implement all ~25 test cases enumerated in Test Impact. Each test calls `asyncio.run(pre_tool_use_hook(input_data, tool_use_id, mock_context))` with Bash tool input.
- Add a `_make_bash_input(command)` helper method on the class for brevity.
- Do NOT stub `pre_tool_use_hook`; the real function must be called (AC #5).

### 3. Repair the stale comment in sdk_client.py
- **Task ID**: build-comment
- **Depends On**: none
- **Validates**: `grep -n "Bash restricted" agent/sdk_client.py` returns a line in the PM branch
- **Assigned To**: pm-bash-hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace the two-line comment at `agent/sdk_client.py:1965-1966` with the exact replacement text from the Technical Approach section.

### 4. Add the PM anomaly-response rule to the persona prompt
- **Task ID**: build-persona
- **Depends On**: none
- **Validates**: `grep -n "Anomaly Response" config/personas/project-manager.md` returns exactly one line
- **Assigned To**: pm-bash-hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Append the "Anomaly Response — Hibernate, Do Not Self-Heal" section (exact text from Technical Approach) to `config/personas/project-manager.md` after the "Escalation Policy" section.

### 5. Update the feature doc
- **Task ID**: build-feature-doc
- **Depends On**: build-hook, build-persona
- **Validates**: `grep -n "Enforcement" docs/features/pm-dev-session-architecture.md` returns one line; manual read confirms the new section covers both Write/Edit blocklist and Bash allowlist, plus the anomaly-response rule
- **Assigned To**: pm-bash-hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Add an "Enforcement" section to `docs/features/pm-dev-session-architecture.md` per the Documentation section.
- Include explicit file:line pointers to `agent/hooks/pre_tool_use.py` (for the hook) and `config/personas/project-manager.md` (for the anomaly rule).

### 6. Allowlist audit
- **Task ID**: audit-allowlist
- **Depends On**: build-hook
- **Validates**: Written audit report in the PR description
- **Assigned To**: pm-bash-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn 'git \|gh \|tail \|cat \|python -m tools.' .claude/skills/sdlc/ .claude/skills/do-build/ .claude/skills/do-plan-critique/ .claude/skills/do-test/ .claude/skills/do-docs/ .claude/skills/do-patch/ .claude/skills/do-pr-review/ config/personas/project-manager.md`.
- For each unique Bash command pattern found, verify it matches an allowlist prefix in `agent/hooks/pre_tool_use.py`.
- If any legitimate read-only command is missing, file a one-line finding and add it to the allowlist (and add a corresponding test).
- Attach the audit result to the PR description.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-hook, build-tests, build-comment, build-persona, build-feature-doc, audit-allowlist
- **Assigned To**: pm-bash-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_pm_session_permissions.py -v` — all tests pass.
- Run `pytest tests/unit/ -q` — full unit suite is green.
- Run `python -m ruff check . && python -m ruff format --check .` — exit code 0.
- Verify each acceptance criterion from the Success Criteria section with its specified verification command.
- Verify the allowlist audit is attached to the PR description.
- Report pass/fail for each AC.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Target test class passes | `pytest tests/unit/test_pm_session_permissions.py::TestPMBashRestriction -v` | exit code 0 |
| PM write tests still pass | `pytest tests/unit/test_pm_session_permissions.py::TestPMWriteRestriction -v` | exit code 0 |
| Full unit suite passes | `pytest tests/unit/ -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Helper exists | `grep -n '_is_pm_allowed_bash' agent/hooks/pre_tool_use.py` | output contains def |
| Stale comment repaired | `grep -n "Bash restricted" agent/sdk_client.py` | exit code 0 |
| Anomaly rule added | `grep -n "Anomaly Response" config/personas/project-manager.md` | exit code 0 |
| Feature doc updated | `grep -n "Enforcement" docs/features/pm-dev-session-architecture.md` | exit code 0 |
| Test invokes real hook | `grep -c 'pre_tool_use_hook(' tests/unit/test_pm_session_permissions.py` | output > 20 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None. The issue is fully specified, the design decision (Option 2) is committed in this plan with rationale, the freshness check resolved the only significant ambiguity (which hook is the enforcement layer), and no human input is required before critique.
