---
status: docs_complete
type: bug
appetite: Small
owner: Valor
created: 2026-04-10
tracking: https://github.com/tomcounsell/ai/issues/881
last_comment_id:
revision_applied: true
revision_date: 2026-04-10
revision_critique_commit: 73d6ed07
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
gh repo view
# NOTE: `gh api` is DELIBERATELY NOT on the allowlist (resolves Concern C1).
# `gh api ... --method POST/PATCH/PUT/DELETE` is a silent mutation vector that
# would pass a naive prefix check. All read-only GitHub data the PM needs is
# available via the `gh issue view`, `gh pr view`, `gh pr list`, `gh run view`,
# and `gh repo view` verbs above. If a future PM use case genuinely needs `gh
# api`, add it as a gated entry with a method-flag denylist, not as a bare prefix.

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

**`git -C <path>` normalization (resolves Blocker B2).** The SDLC skills use `git -C "$REPO" <verb>` and `git -C $TARGET_REPO/.worktrees/{slug} <verb>` as the canonical cross-repo form (verified at 11+ call sites in `.claude/skills/sdlc/SKILL.md:81`, `.claude/skills/do-build/SKILL.md:70,77,88,126,189,190,297,411`, `.claude/skills/do-build/PR_AND_CLEANUP.md:49`, `.claude/skills/do-test/SKILL.md:62,72`, `.claude/skills/do-patch/SKILL.md:49`). Since the PM operates cross-repo via `SDLC_TARGET_REPO`, this is the dominant call form — not an edge case. A naive prefix check on `git status` would reject `git -C "/some/path" status`.

**Fix:** In `_is_pm_allowed_bash`, after the metacharacter guard runs but before the prefix check, apply a normalization step that strips a leading `git -C <token>` where `<token>` is a double-quoted string, single-quoted string, or a single unquoted word:

```python
import re
_GIT_DASH_C_PATTERN = re.compile(r'^git -C (?:"[^"]*"|\'[^\']*\'|\S+)\s+')
# After metacharacter guard, before prefix matching:
command = _GIT_DASH_C_PATTERN.sub('git ', command, count=1)
```

**Critical ordering.** The metacharacter guard MUST run BEFORE the normalization step. Otherwise an attacker could smuggle via the path argument: `git -C "$(rm -rf /)" status` would normalize to `git status` (allowed) but contains `$(` which must trigger the metacharacter guard first. The guard short-circuits this case before normalization ever runs.

**After normalization, the prefix check runs against the normalized command.** A command like `git -C "/x" commit -m "y"` normalizes to `git commit -m "y"`, which is still rejected (commit is not allowlisted). A command like `git -C $REPO status` normalizes to `git status`, which is allowed.

**Tests for normalization:** `test_pm_allowed_git_dash_c_status` (asserts `git -C "/some/path" status` and `git -C $REPO status` are both allowed); `test_git_dash_c_does_not_bypass_mutation` (asserts `git -C "/x" commit -m "y"` is still blocked); `test_git_dash_c_with_metacharacter_injection_blocked` (asserts `git -C "$(rm -rf /)" status` is blocked by the metacharacter guard, NOT allowed after normalization).

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
**Decision (unambiguous):** `_is_pm_allowed_bash` returns `False` for empty, whitespace-only, and `None` inputs. All three are blocked for PM sessions. Empty commands serve no legitimate PM purpose and a bare empty string is a classifier-evasion vector.

- [x] Test `test_pm_empty_command_blocked`: `_is_pm_allowed_bash("")` returns `False`; calling `pre_tool_use_hook` with a PM session and `tool_input={"command": ""}` returns `{"decision": "block", ...}`.
- [x] Test `test_pm_whitespace_only_blocked`: `_is_pm_allowed_bash("   ")` returns `False`; PM session with whitespace command is blocked.
- [x] Test `test_pm_none_command_handled_gracefully`: `_is_pm_allowed_bash(None)` returns `False` (defensive — the upstream `tool_input.get("command", "")` already defaults to `""`, so `None` is upstream-impossible, but the helper must not raise).
- [x] Test `test_pm_missing_command_key_blocked`: `asyncio.run(pre_tool_use_hook({"tool_name": "Bash", "tool_input": {}}, "tu", mock_context))["decision"] == "block"` for a PM session — a missing `command` key defaults to `""` which must be blocked.

The helper signature begins with `if not command or not command.strip(): return False`. This single guard handles all three invalid inputs.

### Error State Rendering
- [x] When the hook returns `{"decision": "block", "reason": "..."}`, the SDK surfaces the `reason` string to the model as the tool result. Verify the reason string is informative enough for the model to correct course: it names the session type, says which command was blocked, and points at "spawn a dev-session subagent" as the resolution. Test asserts the reason string contains the command, "PM session", and "dev-session".

## Test Impact

- [x] `tests/unit/test_pm_session_permissions.py::TestPMWriteRestriction` — NO CHANGE. The Write/Edit tests remain valid; this plan adds a sibling class, not modifies existing tests.
- [x] `tests/unit/test_pm_session_permissions.py` (module-level) — UPDATE: add a new `TestPMBashRestriction` class alongside the existing `TestPMWriteRestriction`. Add at least 10 test cases:
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
  - `test_pm_allowed_git_dash_c_status` — `git -C "/some/path" status` and `git -C $REPO status` are both allowed after `git -C <token>` normalization (resolves B2)
  - `test_pm_allowed_git_dash_c_log` — `git -C "$REPO" log --oneline -10` is allowed after normalization
  - `test_git_dash_c_does_not_bypass_mutation` — `git -C "/x" commit -m "y"` normalizes to `git commit -m "y"` and is still blocked (commit is not allowlisted)
  - `test_git_dash_c_with_metacharacter_injection_blocked` — `git -C "$(rm -rf /)" status` is blocked by the metacharacter guard BEFORE normalization runs (resolves B2 security ordering)
  - `test_pm_blocked_gh_api_get` — `gh api repos/x/y/issues/1` is blocked because `gh api` is not allowlisted (resolves C1)
  - `test_pm_blocked_gh_api_post` — `gh api repos/x/y/issues/1/comments --method POST --field body=z` is blocked
  - `test_pm_none_command_handled_gracefully` — `_is_pm_allowed_bash(None)` returns False without raising
  - `test_pm_missing_command_key_blocked` — PM session with `tool_input={}` (no command key) is blocked via the `.get("command", "")` default
  - `test_non_pm_session_bash_unrestricted` — `SESSION_TYPE=dev` can still run `rm -rf tmp/`
  - `test_pm_sensitive_file_check_runs_before_pm_allowlist` — `SESSION_TYPE=pm` attempting `echo x > .env` is blocked, AND the `reason` string contains `"sensitive"` (case-insensitive) AND does NOT contain `"PM session"`. This pins the ordering contract: the sensitive-file check must fire BEFORE the PM allowlist, so a sensitive-file violation surfaces as a "sensitive file" error, not a "PM command not allowlisted" error. If this test fails, either the hook ordering is wrong or the reason strings must be updated to match. **Note:** it may be impossible to construct a command that passes the allowlist but fails the sensitive-file check (all sensitive-file patterns require metachars like `>` which the metacharacter guard blocks first). If the test proves vacuous — i.e., `echo x > .env` is blocked by the metacharacter guard before either sensitive-file or PM-allowlist layers see it — delete this test and replace with a code comment at the hook site documenting the intended check order.
  - `test_pm_empty_command_blocked`
  - `test_pm_whitespace_only_blocked`
- [x] `tests/unit/test_pre_tool_use_hook.py` — NO CHANGE. This file tests the standalone shell script at `.claude/hooks/pre_tool_use.py`, which is not modified by this plan.
- [x] `tests/unit/test_pre_tool_use_start_stage.py` — NO CHANGE. Tests stage-extraction logic unrelated to tool access.

**Test authenticity requirement (AC #5):** All new test cases invoke the real `pre_tool_use_hook` function via `asyncio.run(pre_tool_use_hook(input_data, ...))`. No stubs, no mocking of the hook itself. The only mock is `mock_context` (per the existing pattern for `TestPMWriteRestriction`). Environment setup uses `monkeypatch.setenv("SESSION_TYPE", "pm")` exactly as the existing Write/Edit tests do.

## Rabbit Holes

- **Regex-based command parsing.** Tempting to write a "proper" parser for Bash commands so we can distinguish `git log | less` (pager, safe) from `git log | xargs rm` (mutation). DO NOT. The metacharacter rejection is strict enough and forces the PM into clean command forms. Any time spent on a parser is time that won't be on the actual fix.
- **Decomposing PM Bash into typed MCP tools.** Option 3 from the issue is the correct long-term architecture but is not this plan. File it as a follow-up issue after this lands. Do not conflate the two — that is how Small fixes become Large features.
- **"Making the stale comment true" by changing the SDK permission mode.** Tempting to set PM's `_permission_mode` to something other than `bypassPermissions`. DO NOT. The existing tests (`test_pm_session_not_using_plan_mode`, `test_default_permission_mode_is_bypass`) lock this in, and the write-to-`docs/` PM capability depends on `bypassPermissions` to work. The right layer for enforcement is the hook, not the SDK permission mode.
- **Adding Bash rules to `.claude/hooks/pre_tool_use.py`** (the shell script). DO NOT. That hook runs for all Claude Code sessions including the main Valor session that must NOT be restricted. All PM-aware logic lives in `agent/hooks/pre_tool_use.py` (the SDK hook), which only runs for sessions spawned via `ValorAgent` with `session_type="pm"`.
- **Updating the allowlist during the build.** Resist the urge to discover "oh I need `git fetch`" during the build and add it. The initial allowlist is committed in this plan. If the build or any downstream PM session actually needs a new command, add it in a follow-up PR with the rationale. Keeping the allowlist small and deliberate is the point.

## Risks

### Risk 1: The allowlist breaks the PM's own `/sdlc` orchestration loop on first deploy
**Impact:** **This is a deployment-blocking risk, not a routine audit item.** The critique of 2026-04-10 verified that `.claude/skills/sdlc/SKILL.md` uses `$(...)`, `|`, `||`, `>`, and `git -C` patterns at lines 51, 55, 81, 104, and 107 (`STAGE_STATES=$(python -m tools.sdlc_stage_query ...)`, `git -C "$REPO" branch -a | grep session/`, `gh pr diff {pr_number} --name-only | grep -c '^docs/' || echo "0"`, `PLAN_PATH=$(grep -rl ... | head -1)`). Every one of these is rejected by the metacharacter guard AND the prefix check. The moment this hook ships, the PM session is unable to advance past Step 2.0 of `/sdlc`. The same applies to 11+ `git -C` sites across `do-build/SKILL.md`, `do-test/SKILL.md`, `do-patch/SKILL.md`, and `do-build/PR_AND_CLEANUP.md`.
**Mitigation:** The `git -C` case is resolved by the normalization step in the Technical Approach section (see "git -C <path> normalization"). The `$(...)`, `|`, `||`, `>` cases in `.claude/skills/sdlc/SKILL.md` require a refactor of the skill file itself — the hook cannot accept those patterns without re-opening the denylist/allowlist question. **Task 0 (Audit & Refactor) is a prerequisite to Task 1 (build-hook).** Task 0 must: (a) grep `.claude/skills/**/*.md` for `\$\(`, `\|`, `&&`, `\|\|`, `>`, `git -C`; (b) refactor `.claude/skills/sdlc/SKILL.md` lines 51, 55, 81, 104, 107 to be metacharacter-free (single-command-per-line, outputs captured via subsequent separate commands, or encapsulated in `python -m scripts.xxx` entry points); (c) refactor `gh api` call sites at `.claude/skills/do-build/SKILL.md:112` and `.claude/skills/do-patch/SKILL.md:52-53` to use typed `gh` verbs; (d) publish a finalized allowlist that Task 1 implements verbatim. No hook code is written until Task 0 lands.

### Risk 2: An allowlisted command can still be mutating with creative argumentation
**Impact:** `git show HEAD -- file.py > /tmp/x.py` is rejected by the metacharacter guard. But `git show HEAD:file.py` (no redirection, no mutation) is allowed. If a future allowlist entry introduces a subcommand that mutates under unusual flags (e.g., `gh api --method POST`), that would be a silent gap.
**Mitigation:** `gh api` is DELIBERATELY excluded from the initial allowlist (see the allowlist block above and the comment within it) to close the most obvious creative-argumentation vector. The PM uses `gh issue view`, `gh pr view`, `gh pr list`, `gh run view`, and `gh repo view` — these cover all known orchestration data needs. If the allowlist audit (Task 0) finds a legitimate `gh api` call site in a PM-reachable skill file, it must be replaced with the equivalent typed verb (`gh issue view ... --json comments`, etc.) or the skill refactored, NOT added back to the allowlist as a bare prefix. See Audit Task 0 for the specific `gh api` call sites that must be checked and refactored: `.claude/skills/do-build/SKILL.md:112` (`LATEST_COMMENT_ID=$(gh api repos/${REPO}/issues/${ISSUE_NUM}/comments ...)`) and `.claude/skills/do-patch/SKILL.md:52-53`.

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

## Documentation

### Feature Documentation
- [x] Update `docs/features/pm-dev-session-architecture.md`: add an "Enforcement" section after the existing session-type table. Content: two short paragraphs describing (1) the Write/Edit-to-docs restriction (already in place), (2) the new Bash read-only allowlist restriction, and (3) the anomaly-response rule in the PM persona. Point readers at `agent/hooks/pre_tool_use.py` as the ground truth for the allowlist and `config/personas/project-manager.md` as the ground truth for the anomaly-response rule. This covers AC #8.
- [x] No `docs/features/README.md` index change needed — `pm-dev-session-architecture.md` already has an entry.

### Inline Documentation
- [x] Update the stale comment at `agent/sdk_client.py:1965-1966` per the exact replacement text in the Technical Approach section. This covers AC #4.
- [x] Add a module-level docstring comment in `agent/hooks/pre_tool_use.py` briefly noting that the Bash branch now enforces a PM allowlist. Point readers at the test file for the authoritative list of allowed/blocked commands.
- [x] Inline docstring for the new `_is_pm_allowed_bash` helper: describe inputs, return value, the metacharacter guard, and why allowlist over denylist.

### External Documentation Site
- Not applicable. This repo does not publish a Sphinx/MkDocs site.

## Success Criteria

- [x] AC #1 — PM session Bash tool access is restricted. The SDK hook `pre_tool_use_hook` denies any Bash command from a PM session that is not in the allowlist (or that contains forbidden metacharacters). **Verification:** `grep -n '_is_pm_allowed_bash' agent/hooks/pre_tool_use.py` returns the helper + its call site.
- [x] AC #2 — All incident commands blocked. Unit tests assert that `rm -rf ai`, `git clone https://github.com/tomcounsell/ai.git`, `git checkout main`, `git reset --hard`, `git push`, `git commit -m "..."`, `pip install foo`, `uv sync`, `rm -rf .venv` are all rejected from a PM session with `{"decision": "block", ...}`.
- [x] AC #3 — All legitimate read-only commands still work. Unit tests assert that `git status`, `git log --oneline -10`, `git diff`, `git show HEAD`, `git branch`, `gh issue view 881`, `gh pr view 123`, `gh pr list`, `gh pr checks 123`, `tail logs/bridge.log`, `cat docs/plans/pm-bash-discipline.md`, `python -m tools.valor_session status --id X`, `python -m tools.agent_session_scheduler status` are all allowed (hook returns `{}` or no decision).
- [x] AC #4 — Stale comment repaired. `agent/sdk_client.py:1965-1966` reflects the real enforcement mechanism. **Verification:** the comment text contains `"Bash restricted"` and references `agent/hooks/pre_tool_use.py`.
- [x] AC #5 — Real hook-path test. The test cases invoke `asyncio.run(pre_tool_use_hook(...))` directly (not a stub, not a classifier helper in isolation). **Verification:** `grep -c 'pre_tool_use_hook(' tests/unit/test_pm_session_permissions.py` returns at least 25 after the fix (was 7 before).
- [x] AC #6 — PM persona anomaly-response rule added. `config/personas/project-manager.md` contains a "Anomaly Response" section with the text specified in the Technical Approach section. **Verification:** `grep -n "Anomaly Response" config/personas/project-manager.md` returns one line.
- [x] AC #7 — No regression in existing PM session tests. `pytest tests/unit/test_pm_session_permissions.py -v` shows all pre-existing tests (Write/Edit restrictions, env injection, permission mode) still pass. Full unit suite `pytest tests/unit/ -q` is green.
- [x] AC #8 — Feature doc updated. `docs/features/pm-dev-session-architecture.md` has a new "Enforcement" section describing both the Write/Edit blocklist and the Bash allowlist, plus the anomaly-response rule. **Verification:** `grep -n "Enforcement" docs/features/pm-dev-session-architecture.md` returns one line.
- [x] Tests pass (`/do-test`) — the full unit suite is green.
- [x] Documentation updated (`/do-docs`) — the feature doc and inline comment changes land.
- [x] Lint clean — `python -m ruff check .` and `python -m ruff format --check .` return exit code 0.
- [x] Allowlist audit complete (Task 0) — the validator has grepped `.claude/skills/**/*.md` and `config/personas/project-manager.md` for metacharacter patterns and `git -C`, refactored the PM-reachable skill blocks to be metacharacter-free, and published the finalized allowlist. The audit report is attached to the PR description. Task 0 output is what Task 1 implements verbatim.
- [x] Post-hook audit recheck (Task 6) — the shipped hook's allowlist matches Task 0's finalized allowlist, and no new regressions were introduced by Task 1.

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

### 0. Audit & refactor PM-reachable bash commands in skill files (PREREQUISITE — resolves B1)
- **Task ID**: audit-and-refactor
- **Depends On**: none
- **Validates**: Written audit report committed to the PR description; `grep -rnE '\$\(|\|\||&&|\| |> ' .claude/skills/sdlc/SKILL.md .claude/skills/do-build/ .claude/skills/do-test/ .claude/skills/do-patch/ .claude/skills/do-pr-review/ config/personas/project-manager.md` returns zero matches in PM-reachable code paths, OR every remaining match is explicitly documented in the audit as "dev-session reachable only, not PM".
- **Informed By**: Critique B1, Risk 1
- **Assigned To**: pm-bash-validator
- **Agent Type**: validator
- **Parallel**: false
- **Step 0a.** Grep `.claude/skills/**/*.md` and `config/personas/project-manager.md` for `\$\(`, `\|`, `&&`, `\|\|`, `> `, `git -C`, `gh api`. Classify each hit as: (i) PM-reachable (the PM session will execute this block as part of `/sdlc` or a skill it invokes), or (ii) dev-session-only (the command runs inside a dev session spawned via Agent, where the PM Bash restriction does NOT apply).
- **Step 0b.** For every PM-reachable match, refactor the skill file to remove the metacharacter:
  - `.claude/skills/sdlc/SKILL.md:51` (`STAGE_STATES=$(python -m tools.sdlc_stage_query ...)`) → rewrite as a direct `python -m tools.sdlc_stage_query --session-id "$VALOR_SESSION_ID"` call with the output captured by the LLM's tool-result parsing, not by shell substitution.
  - `.claude/skills/sdlc/SKILL.md:55` (same pattern with `AGENT_SESSION_ID`) → same treatment.
  - `.claude/skills/sdlc/SKILL.md:81` (`git -C "$REPO" branch -a | grep session/`) → rewrite as `git branch -a` (run in the correct cwd) or two separate commands where the filter is done by the LLM.
  - `.claude/skills/sdlc/SKILL.md:104` (`gh pr diff {pr_number} --name-only | grep -c '^docs/' || echo "0"`) → rewrite as `gh pr diff {pr_number} --name-only` with the docs-count filter done by the LLM.
  - `.claude/skills/sdlc/SKILL.md:107` (`PLAN_PATH=$(grep -rl "#{issue_number}" "$REPO/docs/plans/" 2>/dev/null | head -1)`) → rewrite as `grep -rl "#{issue_number}" docs/plans/` with the first-match selection done by the LLM.
  - `.claude/skills/do-build/SKILL.md:112` (`LATEST_COMMENT_ID=$(gh api repos/${REPO}/issues/${ISSUE_NUM}/comments --jq '.[-1].id // empty' 2>/dev/null)`) → rewrite as `gh issue view ${ISSUE_NUM} --json comments` with the last-comment-id extraction done by the LLM, OR keep as-is if this block runs only inside the do-build dev session (classify in step 0a).
  - `.claude/skills/do-patch/SKILL.md:52-53` (`gh api` calls) → same treatment.
- **Step 0c.** For every `git -C` hit, verify it is accommodated by the normalization step in the Technical Approach. The normalization makes `git -C <token> <verb>` equivalent to `git <verb>` for allowlist purposes, so the skill files can keep their existing `git -C` idiom — no skill refactor required for `git -C` specifically.
- **Step 0d.** Publish the finalized allowlist as the audit output. The finalized allowlist is what Task 1 implements verbatim.
- **Step 0e.** Commit the skill refactors as a single commit titled `Refactor SDLC skill bash blocks to be metacharacter-free (prep for #881)`. This commit lands BEFORE Task 1 so the hook can be enabled against a clean skill tree.
- **Exit criterion:** A PM session running `/sdlc` end-to-end with the proposed hook enabled would not hit a metacharacter block on the refactored skill files. Verified by grep + manual trace of the `/sdlc` Step 2.0 flow.

### 1. Extend the SDK hook with the PM Bash allowlist
- **Task ID**: build-hook
- **Depends On**: audit-and-refactor
- **Validates**: `tests/unit/test_pm_session_permissions.py::TestPMBashRestriction` (created in task 2)
- **Informed By**: Task 0 output (finalized allowlist), Freshness Check (existing hook infrastructure), Design Decision (Option 2 allowlist)
- **Assigned To**: pm-bash-hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Add the `_is_pm_allowed_bash(command: str) -> bool` helper to `agent/hooks/pre_tool_use.py`, implementing the prefix allowlist (from Task 0 output) and metacharacter guard from the Technical Approach section.
- **Implement the `git -C <token>` normalization step** per the Technical Approach section. The metacharacter guard MUST run BEFORE normalization to prevent `git -C "$(rm -rf /)" status`-style injection.
- Extend the `if tool_name == "Bash":` branch in `pre_tool_use_hook` so that after the sensitive-file checks, it runs the PM check: `if _is_pm_session() and not _is_pm_allowed_bash(command): return {"decision": "block", "reason": "..."}`.
- Write the block `reason` string to include the offending command (truncated to 200 chars), the words "PM session", and the phrase "spawn a dev-session subagent" so the model has a clear recovery path.
- Add a module-level docstring comment noting the PM Bash enforcement, and an inline docstring for `_is_pm_allowed_bash` with its contract (including the `git -C` normalization behavior and the metacharacter guard ordering).

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

### 6. Post-hook audit recheck (sanity-verify Task 0 against the shipped hook)
- **Task ID**: recheck-audit
- **Depends On**: build-hook
- **Validates**: Written recheck report in the PR description confirming that every skill-file command identified in Task 0 still matches the shipped allowlist and that no new regressions were introduced by the hook code
- **Assigned To**: pm-bash-validator
- **Agent Type**: validator
- **Parallel**: false
- This is a sanity recheck of the Task 0 finalized allowlist against the actually-implemented hook. It should be a no-op if Task 0 was done properly.
- Run `grep -rn 'git \|gh \|tail \|cat \|python -m tools.' .claude/skills/sdlc/ .claude/skills/do-build/ .claude/skills/do-plan-critique/ .claude/skills/do-test/ .claude/skills/do-docs/ .claude/skills/do-patch/ .claude/skills/do-pr-review/ config/personas/project-manager.md`.
- For each unique Bash command pattern found, verify it matches an allowlist prefix in the shipped `agent/hooks/pre_tool_use.py`.
- If any legitimate read-only command is missing (unexpected after Task 0), file a finding and STOP — this means Task 0 was incomplete and must be re-run, not that Task 1 should be patched.
- Attach the recheck report to the PR description.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: build-hook, build-tests, build-comment, build-persona, build-feature-doc, recheck-audit
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

**Critique run:** 2026-04-10 (re-run — prior findings were reported but not persisted)
**Critics:** Structural (automated) + content review against verified source files
**Findings:** 7 total (2 blockers, 4 concerns, 1 nit)
**Verdict:** NEEDS REVISION

### Blockers

#### B1. Allowlist does not admit commands used by the PM's own /sdlc skill — the PM will be blocked from orchestrating
- **Severity:** BLOCKER
- **Critics:** Operator, Adversary
- **Location:** Solution → Technical Approach → The allowlist; conflicts with `.claude/skills/sdlc/SKILL.md:51,55,81,104,107`
- **Finding:** The `/sdlc` skill (the PM's primary orchestration loop) uses `STAGE_STATES=$(python -m tools.sdlc_stage_query ...)` at line 51, `git -C "$REPO" branch -a | grep session/` at line 81, `gh pr diff {pr_number} --name-only | grep -c '^docs/' || echo "0"` at line 104, and `PLAN_PATH=$(grep -rl ... | head -1)` at line 107. Every one of these will be rejected by the metacharacter guard (`|`, `$(`, `||`, `>`) AND by the prefix check (`git -C` is not an allowlisted prefix — only `git status`, `git log`, etc. are). The moment this hook ships, the PM session will be unable to advance past Step 2.0 of its own SDLC assessment loop. The Risk 1 mitigation ("manually enumerate... grep the skill files") catches this only if the auditor knows to expand `|` and `$(...)` patterns, but the plan presents it as a routine checklist item — not a "the core orchestration loop will break" warning.
- **Suggestion:** Before landing, either (a) rewrite the affected `/sdlc` SKILL.md patterns to be shell-free (one-command-per-line, no pipes or substitutions) AND add `git -C` as a permitted prefix variant OR as a pre-strip normalization (strip `-C <path>` before matching), OR (b) expand the allowlist to accept the specific compound patterns the skill uses. Option (a) is cleaner and keeps the allowlist small. The plan must explicitly list every `.claude/skills/sdlc/SKILL.md` bash block that must be rewritten, and the audit task (task 6) must be done BEFORE task 1 lands, not after.
- **Implementation Note:** Add a new Task 0 (prerequisite to Task 1) titled "Audit and refactor /sdlc skill bash blocks": grep `.claude/skills/sdlc/SKILL.md`, `.claude/skills/do-build/SKILL.md`, `.claude/skills/do-patch/SKILL.md`, `.claude/skills/do-pr-review/sub-skills/post-review.md` for `\$\(`, `\|`, `&&`, `\|\|`, `>`, `git -C`. For each match, rewrite to either (i) a single pipe-free command with outputs captured via subsequent separate commands, or (ii) a shell script under `scripts/` that is invoked via a single whitelisted `python -m scripts.xxx` entry. Specifically, line 81 becomes `git branch -a` (without `-C`, since PM's cwd is already the target repo — if not, the PM must `cd` first, which itself needs to be allowlisted); line 104 becomes `gh pr diff {pr_number} --name-only` with the docs-count done in Python or elided; line 107 becomes `grep -rl "#{issue_number}" docs/plans/` with the `| head -1` removed (Python script picks the first match). If refactoring `/sdlc` is out of scope, the allowlist MUST add: `python -m tools.sdlc_stage_query`, pattern-permit for plain `grep -rl ... docs/plans/` (but this re-opens the denylist-vs-allowlist question since `grep` is not currently allowlisted), and a documented `git -C <path>` prefix normalization. The plan currently does neither — it will break the orchestration loop on first deploy.

#### B2. `git -C <path>` is the canonical form used throughout SDLC skills and is not accommodated by prefix matching
- **Severity:** BLOCKER
- **Critics:** Archaeologist, Operator
- **Location:** Solution → Technical Approach → The allowlist
- **Finding:** Seven SDLC skill files use `git -C "$REPO" ...` or `git -C $TARGET_REPO/.worktrees/{slug} ...` (verified: `.claude/skills/sdlc/SKILL.md:81`, `.claude/skills/do-build/SKILL.md:70,77,88,126,189,190,297,411`, `.claude/skills/do-build/PR_AND_CLEANUP.md:49`, `.claude/skills/do-test/SKILL.md:62,72`, `.claude/skills/do-patch/SKILL.md:49`). The allowlist specifies prefixes like `git status`, `git log`, etc., but a command starting with `git -C "/some/path" status` does NOT match any of those prefixes — the literal first-word-and-second-word check fails because `-C` is the second word, not `status`. This is separate from the metacharacter issue in B1: even a single `git -C "$REPO" status` (no pipes) would be rejected. Since the PM operates cross-repo via `SDLC_TARGET_REPO`, this is not an edge case — it is the dominant call form.
- **Suggestion:** Before the prefix check, normalize the command by stripping a leading `git -C <token>` (where `<token>` is an unquoted word OR a double/single-quoted string). After stripping, the remaining string is checked against the allowlist as if it started with `git`. Or: explicitly list every `git -C ... <verb>` pattern in the allowlist (brittle — 14 combinations × 10+ repos × 10+ worktrees).
- **Implementation Note:** In `_is_pm_allowed_bash`, after `.strip()` and before prefix matching, apply: `if command.startswith("git -C "): command = re.sub(r'^git -C (?:"[^"]*"|\'[^\']*\'|\S+) ', 'git ', command)`. Then the existing prefix check runs against the normalized command. Add a test `test_pm_allowed_git_dash_c_status` asserting `git -C "/some/path" status` and `git -C $REPO status` are both allowed, and a test `test_git_dash_c_does_not_bypass_mutation` asserting `git -C "/x" commit -m "y"` is still blocked (because after normalization it becomes `git commit`, which is not allowlisted). The metacharacter guard MUST run BEFORE the normalization to prevent shell-injection via the path argument (e.g., `git -C "$(rm -rf /)" status` — the `$(` triggers the guard first).

### Concerns

#### C1. `gh api` on the allowlist with no method check is a silent mutation vector
- **Severity:** CONCERN
- **Critics:** Skeptic, Adversary
- **Location:** Technical Approach → The allowlist (`gh api`); Risks → Risk 2
- **Finding:** The plan allowlists bare `gh api` with the comment "read-only by the nature of typical PM use" and defers hardening to a follow-up. But `gh api repos/owner/repo/issues/N/comments --method POST --field body="..."` is a perfectly valid `gh api` invocation that starts with the allowlisted prefix and contains no forbidden metacharacters. The plan acknowledges this in Risk 2 and keeps it anyway. The incident that motivated this plan was a PM session doing something it *shouldn't* have been doing but *could*. Leaving a documented bypass in place on day one is the exact failure mode we are fixing.
- **Suggestion:** Either (a) remove `gh api` from the initial allowlist and require the PM to use `gh issue view`, `gh pr view`, `gh pr list`, `gh run view`, `gh run list` for all orchestration data (which covers nearly all real PM use cases), OR (b) gate `gh api` with a per-command flag check: reject any `gh api` command containing `--method POST`, `--method PATCH`, `--method PUT`, `--method DELETE`, `-X POST`, `-X PATCH`, `-X PUT`, `-X DELETE`.
- **Implementation Note:** Option (a) is simpler and is the recommended path. If `gh api` is removed from the allowlist, audit `.claude/skills/do-build/SKILL.md:112` (`LATEST_COMMENT_ID=$(gh api repos/${REPO}/issues/${ISSUE_NUM}/comments --jq '.[-1].id // empty' 2>/dev/null)`) and `.claude/skills/do-patch/SKILL.md:52,53` — if any of those paths run inside a PM context (vs. inside a dev session), they must be replaced with `gh issue view ... --json comments`. If Option (b) is chosen, add the method check as a distinct function `_gh_api_is_read_only(command: str) -> bool` and call it from `_is_pm_allowed_bash` when the command starts with `gh api `. Add tests: `test_pm_blocked_gh_api_post`, `test_pm_blocked_gh_api_patch`, `test_pm_blocked_gh_api_dash_X_delete`, `test_pm_allowed_gh_api_get_default`.

#### C2. Check ordering claim is tested ambiguously — PM allowlist would also block `echo x > .env`, making the "sensitive first" assertion unverifiable
- **Severity:** CONCERN
- **Critics:** Skeptic
- **Location:** Test Impact → `test_pm_sensitive_file_check_still_runs`; Solution → Key Elements ("The existing sensitive-file checks continue to run before the PM check")
- **Finding:** The plan wants to assert that the sensitive-file check runs *before* the PM allowlist for PM sessions, so that the error message says "sensitive file" rather than "PM command not allowlisted". But `echo x > .env` is blocked by the PM allowlist regardless (echo is not allowlisted, `>` is a forbidden metacharacter) AND by the sensitive-file check. The test cannot distinguish which layer blocked it from just the `decision == "block"` result — it must inspect the `reason` string. The plan says the sensitive check reason contains "sensitive" but the PM reason contains "PM session". A test that only asserts `decision == "block"` proves nothing about ordering.
- **Suggestion:** Make the ordering test explicit: assert that the `reason` string contains `"sensitive"` (and NOT `"PM session"`) when a PM session attempts `echo x > .env`. This pins the ordering contract. Additionally, pick a command that is *allowlisted* but writes to a sensitive file — e.g., `cat .env` is a read but `tee .env < foo` has `<` (metachar blocked). It may be impossible to construct a command that is allowed by the allowlist but blocked by the sensitive check, in which case the ordering test is vacuous and should be dropped in favor of a comment documenting the check order.
- **Implementation Note:** In the `TestPMBashRestriction` class, change `test_pm_sensitive_file_check_still_runs` to: assert `result["decision"] == "block"` AND `"sensitive" in result["reason"].lower()` AND `"PM session" not in result["reason"]`. This proves the sensitive check fired first. If this assertion fails (because the PM allowlist fires first and returns a PM-branded reason), then the ordering in `pre_tool_use_hook` is wrong OR the test is wrong and must be deleted. Either outcome is informative.

#### C3. Empty-string handling is specified twice with conflicting rationale
- **Severity:** CONCERN
- **Critics:** Operator
- **Location:** Failure Path Test Strategy → Empty/Invalid Input Handling (lines 254-256)
- **Finding:** Line 254 says: "Test: empty Bash command string returns True from `_is_pm_allowed_bash` — no-op is allowed ... OR test that empty string is blocked; pick one and document. **Decision:** block empty commands too". The plan picks "block" in the decision line but the opening sentence still contains the rejected alternative as a claim. A builder reading this quickly could implement the wrong branch. Compounding this, the helper is documented as "returns True iff the command matches one of the allowed read-only prefixes" — an empty string matches no prefix, so `_is_pm_allowed_bash("")` naturally returns False (blocked), which is consistent with the "block" decision. But the test in line 254 is worded as "returns True from `_is_pm_allowed_bash`" for the empty-string case, contradicting the decision.
- **Suggestion:** Rewrite lines 254-256 to unambiguously say: `_is_pm_allowed_bash("")` returns False (blocked); `_is_pm_allowed_bash("   ")` returns False (blocked); `_is_pm_allowed_bash(None)` returns False (blocked, via isinstance check or truthy guard). The test names are `test_pm_empty_command_blocked`, `test_pm_whitespace_only_blocked`, and add `test_pm_none_command_handled_gracefully`.
- **Implementation Note:** Update the Failure Path Test Strategy section to remove the "OR" phrasing. The helper signature should start with `if not command or not command.strip(): return False`. The test input for `None` is tricky because `pre_tool_use_hook` receives `tool_input.get("command", "")` at line 300 — the `.get("command", "")` already defaults to empty string, so None is upstream-impossible. The `None` test for `_is_pm_allowed_bash` is defensive only. Add: `assert _is_pm_allowed_bash(None) is False` AND separately `assert asyncio.run(pre_tool_use_hook({"tool_name": "Bash", "tool_input": {}}, "tu", mock_context))["decision"] == "block"` for a PM session, since a missing `command` key defaults to `""` which must be blocked.

#### C4. Audit task (task 6) runs AFTER build-hook, but its findings can invalidate the hook — ordering is wrong
- **Severity:** CONCERN
- **Critics:** Operator, Simplifier
- **Location:** Step by Step Tasks → Task 6 (audit-allowlist); Depends On: build-hook
- **Finding:** Task 6 depends on `build-hook` being complete, and its job is to find commands the PM genuinely needs but which aren't on the allowlist. If it finds one (almost certain given Blocker B1), task 1 must be redone. This is rework-by-construction. The audit should be a prerequisite to task 1, not a downstream validation of it. The same applies to the `/sdlc` skill refactoring required by Blocker B1.
- **Suggestion:** Renumber tasks so the audit runs first as Task 0 with no dependencies, and its output (either "allowlist matches reality" or "the following commands must be added: X, Y, Z") feeds Task 1. Task 6's role becomes a sanity recheck after the hook lands, not the primary audit.
- **Implementation Note:** New Task 0: "Audit PM-reachable bash commands in skill files and persona prompt. Output: the final allowlist (which Task 1 then implements verbatim) and any required `/sdlc` refactors. No code changes yet — just the audit + allowlist finalization." Task 1's `Depends On` changes from `none` to `audit-allowlist`. Task 6 is deleted or renamed to "verify-audit" and is a no-op if Task 0 was done properly. This is cleaner than the current plan, which has the audit as a post-facto check on a hook that may already be wrong.

### Nits

#### N1. Duplicate `## Test Impact` section
- **Severity:** NIT
- **Critics:** Structural
- **Location:** lines 261 and 342
- **Finding:** The plan contains two `## Test Impact` sections — one after "Error State Rendering" (line 261) with the detailed ~25-case enumeration, and a second near the doc-related sections (line 342) with `[x]`-checked items that restate the first section in a condensed form. This passes the structural validator (at least one exists and is non-empty) but is confusing for readers and invites the two lists to drift apart over time.
- **Suggestion:** Delete the second `## Test Impact` block (lines 342-347) or merge its content into the first. Keep the detailed enumeration as the single source of truth.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections present | PASS | Documentation, Update System, Agent Integration, Test Impact all present |
| Duplicate section | FAIL | `## Test Impact` appears twice (lines 261 and 342) — see N1 |
| Task numbering | PASS | Tasks 1-7 contiguous, no gaps |
| Dependencies valid | PASS | All `Depends On` references point to valid task IDs; no circular deps |
| File paths exist | PASS | All referenced files exist: `agent/hooks/pre_tool_use.py`, `agent/sdk_client.py`, `tests/unit/test_pm_session_permissions.py`, `config/personas/project-manager.md`, `docs/features/pm-dev-session-architecture.md`, `.claude/hooks/pre_tool_use.py`, `agent/hooks/__init__.py` |
| Line references verified | PASS | `agent/sdk_client.py:1962` = `_permission_mode = "bypassPermissions"` confirmed; `:1964` = `if _session_type == SessionType.PM:` confirmed; `:1965-1966` = stale comment confirmed; `:934` = `env["SESSION_TYPE"] = self.session_type` confirmed; `agent/hooks/pre_tool_use.py:70` = `_is_pm_session()` confirmed; Bash branch at `:299-326` confirmed (plan said 298-327, off by ~1 line — acceptable) |
| Prerequisites met | PASS | `_is_pm_session` importable; `test_pm_session_permissions.py` exists; `env["SESSION_TYPE"]` injection present at `sdk_client.py:934` |
| Cross-references | PASS | Each Success Criterion maps to at least one task; No-Gos and Rabbit Holes do not overlap with planned tasks |
| Baseline commit | PASS | `226fbc1d` resolves on main |
| Issue state | PASS | #881 is OPEN as of critique time |

### Verdict

**NEEDS REVISION** — 2 blockers must be resolved before build:

1. **Blocker B1** — The allowlist does not admit commands used by the PM's own `/sdlc` skill. On first deploy, the PM session will be unable to run Step 2.0 of its orchestration loop. Either the skill must be refactored to be metacharacter-free AND drop `git -C`, or the allowlist must expand (with documented tradeoffs). The plan currently does neither.
2. **Blocker B2** — `git -C <path>` is the canonical cross-repo form used by at least 11 sites across SDLC skill files. The prefix matching rejects it. A normalization step (strip `git -C <token>`) is required, OR every `git -C` site in the skills must be refactored away.

Concerns C1-C4 are non-blocking but should be addressed in the revision pass. NIT N1 is cosmetic.

**Recommended path forward:**
- Add a new **Task 0 — Audit and Refactor** (prerequisite to Task 1) that: (a) grep-audits `.claude/skills/**/*.md` for `\$\(`, `\|`, `&&`, `\|\|`, `>`, `git -C` patterns; (b) produces the finalized allowlist; (c) refactors the skill files where needed; (d) publishes a written audit report that the builder implements verbatim in Task 1.
- Decide on `gh api` disposition (remove from allowlist OR add method-flag guard) and update the plan to match.
- Add `git -C` prefix normalization to the helper design in the Technical Approach section.
- Delete or merge the duplicate `## Test Impact` section.
- Rewrite the empty-string test spec to be unambiguous.
- Rewrite the sensitive-file-ordering test to assert `"sensitive" in reason and "PM session" not in reason`.

Once the revision pass lands, re-run critique to confirm the blockers are resolved before proceeding to `/do-build`.

### Revision Pass Applied — 2026-04-10

All 2 blockers, 4 concerns, and 1 nit from critique commit `73d6ed07` have been addressed in-plan:

- **B1 (allowlist breaks /sdlc loop):** Added **Task 0 — Audit & refactor PM-reachable bash commands in skill files** as a prerequisite to Task 1. Task 0 explicitly lists the affected `.claude/skills/sdlc/SKILL.md` lines (51, 55, 81, 104, 107) and the refactor for each. Risk 1 upgraded to a deployment-blocking risk with the audit as the mitigation. Task 6 retitled to "Post-hook audit recheck" (a sanity verification, not the primary audit).
- **B2 (git -C not accommodated):** Added a "`git -C <path>` normalization" subsection to Technical Approach. The `_is_pm_allowed_bash` helper now strips a leading `git -C <token>` before prefix matching, with the metacharacter guard running BEFORE normalization to prevent `git -C "$(rm -rf /)" status`-style injection. Four new tests added: `test_pm_allowed_git_dash_c_status`, `test_pm_allowed_git_dash_c_log`, `test_git_dash_c_does_not_bypass_mutation`, `test_git_dash_c_with_metacharacter_injection_blocked`.
- **C1 (`gh api` silent mutation vector):** `gh api` DELIBERATELY removed from the initial allowlist. The allowlist block now includes an inline comment explaining why. Risk 2 rewritten to match. Task 0 includes refactoring the two known `gh api` PM-reachable sites (`.claude/skills/do-build/SKILL.md:112` and `.claude/skills/do-patch/SKILL.md:52-53`) to use typed `gh` verbs. Two new tests added: `test_pm_blocked_gh_api_get`, `test_pm_blocked_gh_api_post`.
- **C2 (ambiguous sensitive-file-ordering test):** `test_pm_sensitive_file_check_still_runs` renamed to `test_pm_sensitive_file_check_runs_before_pm_allowlist` and now asserts `"sensitive" in reason.lower() and "PM session" not in reason`. The test spec explicitly flags the vacuous-case escape hatch: if `echo x > .env` is blocked by the metacharacter guard before either sensitive-file or PM-allowlist layers see it, the test is deleted in favor of a code comment.
- **C3 (empty-string spec contradiction):** Failure Path Test Strategy rewritten to unambiguously state: `_is_pm_allowed_bash` returns `False` for empty, whitespace-only, and `None` inputs. Four tests enumerated: `test_pm_empty_command_blocked`, `test_pm_whitespace_only_blocked`, `test_pm_none_command_handled_gracefully`, `test_pm_missing_command_key_blocked`. The helper signature begins with `if not command or not command.strip(): return False`.
- **C4 (audit task ordering is wrong):** Task 0 (audit) now precedes Task 1 (build-hook). Task 1's `Depends On` updated from `none` to `audit-and-refactor`. Task 6 (formerly `audit-allowlist`) renamed to `recheck-audit` with clarified role as a sanity verification that Task 0 was thorough.
- **N1 (duplicate `## Test Impact` section):** Deleted the second `## Test Impact` block. Single source of truth retained at the original location.

Frontmatter updated: `revision_applied: true` set so the next SDLC dispatch routes to Row 4c (`/do-build`) rather than looping back to `/do-plan`. Per critique-verdict protocol, however, the revision introduces a non-trivial architectural change (Task 0 as a prerequisite + `git -C` normalization semantics), so a re-critique is RECOMMENDED before build to verify the blockers are actually resolved and no new issues were introduced. The SDLC router will make that call based on the stage_states transition.

---

## Open Questions

None. The issue is fully specified, the design decision (Option 2) is committed in this plan with rationale, the freshness check resolved the only significant ambiguity (which hook is the enforcement layer), and no human input is required before critique.
