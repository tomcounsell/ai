# SDLC Tool Resolver

**Status:** Shipped (issue #1175)

## Summary

`sdlc-tool` is a small bash wrapper that turns every `python -m tools.sdlc_*` invocation in the SDLC skills, hooks, and personas into a cwd-independent call. It resolves the `ai/` repo via `AI_REPO_ROOT`, dispatches into the right `tools.sdlc_*` module via `uv run --directory`, and passes the underlying exit code through unchanged.

It exists because `python -m tools.X` resolves `tools/` against the current working directory. When `/sdlc` runs from a target-repo cwd that ships its own `tools/` package (cuttlefish, popoto, etc.), Python's module resolution finds the wrong `tools/` first and the SDLC dispatch fails silently. Guard G5 ("unchanged-critique cache hit") never fires because verdicts never get recorded, and the war-room critics produce diverging non-deterministic verdicts on every re-run.

## How it works

The wrapper lives at `scripts/sdlc-tool` and is hardlinked into `~/.local/bin/sdlc-tool` by the update system. Skill markdown calls `sdlc-tool <subcommand>` instead of `python -m tools.sdlc_<subcommand>`.

```
local /sdlc invocation in target-repo cwd
   |
   v
skill markdown calls `sdlc-tool verdict record ...`
   |
   v
wrapper resolves AI_REPO_ROOT (default: $HOME/src/ai)
   |
   v
uv run --directory $AI_REPO_ROOT python -m tools.sdlc_verdict record ...
   |
   v
verdict written to AgentSession.stage_states._verdicts[CRITIQUE]
   |
   v
next /sdlc reads it via `sdlc-tool stage-query` -> Guard G5 fires
```

## AI_REPO_ROOT resolution

The wrapper resolves the repo path in this order:

1. `AI_REPO_ROOT` environment variable (explicit override)
2. `$HOME/src/ai` (default — correct on every Valor machine today)

If neither resolves to a directory containing `tools/`, the wrapper exits 2 with a clear stderr message naming the resolved path. There is no probing of multiple locations — predictability beats heroics.

## Subcommands

The wrapper has a hard-coded allowlist of subcommands. Unknown subcommands exit 2 with usage rather than letting Python report an opaque `ModuleNotFoundError`:

| Subcommand | Underlying module | Exit policy |
|------------|-------------------|-------------|
| `verdict` | `tools.sdlc_verdict` | **Loud** — exits 1 on failure |
| `dispatch` | `tools.sdlc_dispatch` | **Loud** — exits 1 on failure |
| `next-skill` | `tools.sdlc_next_skill` | **Loud** — exits 1 on error; 0 on dispatch or block |
| `stage-marker` | `tools.sdlc_stage_marker` | Best-effort — always exits 0 |
| `stage-query` | `tools.sdlc_stage_query` | Graceful — returns `unavailable` marker |
| `session-ensure` | `tools.sdlc_session_ensure` | Best-effort — always exits 0 |

Adding a new subcommand: append to `ALLOWED_SUBCOMMANDS` in `scripts/sdlc-tool`. The kebab-case name maps to `tools.sdlc_<snake_case>` automatically.

## Session resolution and write-path auto-ensure (`find_session(..., ensure=True)`)

All `sdlc-tool` subcommands store/read pipeline state on a **PM `AgentSession`'s `stage_states`** in Redis — not in the plan file or git. They resolve that session through the shared resolver `find_session(session_id=None, issue_number=None, ensure=False)` in `tools/_sdlc_utils.py`.

### Forked-skill issue-number passing — skill arg/env layer (issue #1731)

The recorder-layer precedence (#1671/#1672, see below) only helps when a real `--issue-number N`
value reaches `find_session`. A separate, earlier failure mode (#1731) prevented the value from
ever being produced inside forked CRITIQUE/REVIEW skills:

- `do-plan-critique/SKILL.md` assigned `ISSUE_NUM` in the Plan Resolution block but all downstream
  recorder calls referenced `$ISSUE_NUMBER` (a different, never-assigned variable).
- `do-pr-review/SKILL.md` set `$SDLC_ISSUE_NUMBER` from env and `$PR_NUMBER` in its
  context-resolution block, but every recorder call referenced `$ISSUE_NUMBER` (never assigned).

The consequence: `--issue-number $ISSUE_NUMBER` collapsed to `--issue-number ` (empty token),
which argparse `type=int` rejected with exit code 2 ("expected one argument"). On stage-marker
calls (then guarded with `2>/dev/null || true`) this silently no-op'd — the marker stayed
`in_progress`. On the verdict-record call (no `|| true`) it errored — nothing was recorded.
Either way the router saw no matching verdict and returned `Blocked('no matching dispatch rule')`.
If a stale non-empty `ISSUE_NUMBER` was inherited from a prior context (the "latched onto #1724"
symptom), it silently diverted the write to the wrong issue's session.

**The #1731 fix (applied to the skill markdown layer):**

1. `do-plan-critique`: Plan Resolution now unconditionally assigns `ISSUE_NUMBER` (clobbers
   any inherited value, never `${ISSUE_NUMBER:-…}`). Numeric `$ARGUMENTS` → `ISSUE_NUMBER`.
   Plan-path `$ARGUMENTS` → extract from plan frontmatter `tracking:` field.
2. `do-pr-review`: Context-resolution now unconditionally assigns `ISSUE_NUMBER` by extracting
   `Closes #N`/`Fixes #N`/`Resolves #N` from the PR body (PRIMARY — always runs first), then
   `tracking: .../issues/N` from the PR body (secondary fallback), with `$SDLC_ISSUE_NUMBER`
   as a last-resort validated hint only (never authoritative). **`$ARGUMENTS` is the PR number
   in this skill, not the issue number — it is never used as `ISSUE_NUMBER`.**
3. Both skills: **positive-integer assertion** `[[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] || { ... exit 1 }`
   added after every resolution path and before any recorder call. An unresolvable issue
   number now fails loudly rather than silently diverting.
4. Both skills: `2>/dev/null || true` swallow stripped from all stage-marker calls so failures
   surface as visible non-zero exits in the subagent report.
5. Both skills: every `--issue-number` flag now passes `"$ISSUE_NUMBER"` (quoted) so an empty
   value produces a clear argparse error rather than a confusing token-drop.
6. `do-sdlc` §3c: confirmed args-only hand-off (no `export SDLC_ISSUE_NUMBER`) — the skill
   re-parses the number from `$ARGUMENTS` directly.

**The two layers are distinct and complementary:**

| Layer | PR / Issue | What it fixes |
|-------|-----------|---------------|
| Recorder session resolution | #1671/#1672 (PR #1673) | Given a real `--issue-number N`, the write lands on the issue-scoped session, not the env-var session |
| Skill arg/env passing | #1731 | Ensures the `--issue-number N` value is actually produced inside the forked skill (so the recorder layer has something to work with) |

Neither layer can compensate for the other. Together they close the full divert path.

### Session resolution precedence (issue #1671/#1672)

Resolution order in `find_session`:

1. **Explicit `session_id` argument** — highest precedence. A caller passing a concrete id means it; it overrides everything below, including issue-based resolution.
2. **Issue-based lookup** via `find_session_by_issue(issue_number)`, attempted when `issue_number >= 1`. This runs **before** env-var resolution so that a write issued with an explicit `--issue-number N` lands on the *same* session the router reads for that issue.
3. **Env-var session** (`VALOR_SESSION_ID` / `AGENT_SESSION_ID`) — a **last-resort fallback**, consulted only when there is no explicit `session_id` and no issue-based match. Preserves the bridge case byte-for-byte: a write with no `--issue-number` resolves the env-var session exactly as before.
4. **Auto-ensure** (writes only) — see below.

**Why issue-number beats env-var (#1671/#1672):** Before this fix, the *read* path resolved by issue number while the *write* path resolved by an inherited env-var session first. A forked CRITIQUE/REVIEW subagent (spawned by `/do-sdlc`) that inherited a parent's `VALOR_SESSION_ID` wrote its verdict/marker/dispatch entry to the *parent's* session, while the router reading `--issue-number N` saw an empty verdict and looped on guard G3. Both reads and writes now consult `find_session_by_issue` first for an explicit issue number, so they **converge** on one session.

**`find_session_by_issue` ordering (concern C2):** the `issue_url`-ownership pass runs **first** — a live bridge PM session that owns the issue via its `issue_url` wins over a stale deterministic `sdlc-local-{N}` record. The deterministic-id pass is the fallback for the sessionless-local case it was built for (#1558), reached only when no PM session owns the issue via `issue_url`.

**`ensure_session` reconciliation (concern C1):** the env-var short-circuit in `tools/sdlc_session_ensure.py` is **not** a blind reorder. When the env var resolves to a live PM session, it is kept **only when that session owns the requested issue** (its `issue_url` ends in `/issues/N`) — the legitimate bridge dedup case (#1147), a true no-op. When the env session is live but does *not* own the issue, the resolver consults `find_session_by_issue(N)` and prefers an existing issue-scoped session, falling through to create only if none exists. No duplicate is ever created for the bridge case.

**The `ensure` parameter (issue #1558):**

- **`ensure=False` (default)** — a pure, side-effect-free lookup. Returns the session or `None`. **No session is ever created.** Every *read* path uses this: `verdict get`, `stage-query`, `next-skill`, and `meta-set`/`stage-marker` when called by read-only code. This preserves the original pre-#1558 lookup behavior byte-for-byte.
- **`ensure=True`** — opt-in auto-create. When no existing PM session is found **and** there is issue context (`issue_number >= 1`) **or** a session-id env var is set, the resolver calls `tools.sdlc_session_ensure.ensure_session(issue_number)` to create (or dedup onto a live bridge session) a `sdlc-local-{N}` PM session, then re-resolves and returns it. A bare sessionless call with no issue context still returns `None` — no fabricated session. An ensure failure (e.g. `ProjectKeyResolutionError`) yields `None` rather than raising.

**Four *write* paths pass `ensure=True`:** `sdlc_meta_set.write_meta`, `sdlc_stage_marker.write_marker`, `sdlc_verdict._cli_record`, and `sdlc_dispatch._cli_record`. The dispatch `record` path joined the other three in #1671 — #1671 explicitly named "dispatch-history entries" as a skew symptom, and without `ensure=True` a cold-start `dispatch record --issue-number N` would env-resolve to a divergent inherited session or silently no-op. The dispatch `get`/`reset` paths stay non-ensuring (`get` is read-only; `reset` must not fabricate a session). This guarantees a state *write* always has a home regardless of how the pipeline is driven — a direct `sdlc-tool verdict record` or `sdlc-tool dispatch record` in a clean (non-`/sdlc`) session now persists instead of silently no-op'ing. The opt-in is grep-able: `grep -rn 'ensure=True' tools/` surfaces exactly the four write sites plus the signature in `_sdlc_utils.py`.

This **supersedes the per-skill `session-ensure` requirement for non-`/sdlc` callers.** The explicit `sdlc-tool session-ensure` call in `/sdlc` Step 1.5 is now belt-and-suspenders (tagged `REDUNDANT-AFTER-#1558` in `SKILL.md`) — auto-ensure on the first write covers callers that never pass through `/sdlc`.

**Read-after-write within `find_session_by_issue`:** an auto-ensured `sdlc-local-{N}` session carries no `issue_url` (so the `issue_url`-ownership pass always misses it — see C2 above), but does carry an issue-anchored `message_text` set by Fix A (#1741: `"Run the full SDLC pipeline for issue #N..."`). The read paths match it by its **deterministic session id** (`sdlc-local-{issue_number}`) — but only after the `issue_url`-ownership pass misses (C2, #1671). When a live bridge PM session owns the issue via `issue_url`, that session wins; the deterministic-id match is the fallback for the sessionless-local case. This is what lets a sessionless `verdict record` be read back by a separate `verdict get`/`stage-query` process (verified end-to-end in `tests/integration/test_sdlc_sessionless_e2e.py`) while a live bridge session is never shadowed by a stale local record.

`tools/sdlc_stage_query.py` is **read-only and intentionally left unchanged** — it keeps its own `_find_session_by_id`/`_find_session_by_issue` helpers (which delegate to the shared `find_session_by_issue`), returns correct `_default_meta()` defaults when no session exists, and finds the session once a write has ensured it. Its `_parse_revision_applied`/`_compute_meta` frontmatter reads were already correct; they fire as soon as a session exists.

## Loud-vs-silent policy

The two load-bearing recorders (`verdict`, `dispatch`) exit 1 when their inner CLI handler raises. This is the core fix for issue #1175 — without these exit codes, removing `|| true` from skill markdown is cosmetic.

**The contract:**

- `tools.sdlc_verdict.main()` and `tools.sdlc_dispatch.main()` catch internal exceptions, print `{}` to stdout (so existing JSON parsers don't break), log the error to stderr, and `sys.exit(1)`.
- Skill markdown calling `sdlc-tool verdict record ...` and `sdlc-tool dispatch record ...` does **not** wrap these calls in `2>/dev/null || true` — failures must surface to the operator.
- The other three modules (`stage_marker`, `stage_query`, `session_ensure`) keep `sys.exit(0)` unconditionally; their callers in skill markdown still use `2>/dev/null || true` because they are best-effort.

The split is enforced by:

1. The wrapper passes the exit code through unchanged.
2. The parity sweep test (`tests/unit/test_sdlc_tool_wrapper.py::TestSkillMarkdownParity`) fails CI if any `python -m tools.sdlc_*` reference reappears in the include set, or if any `sdlc-tool verdict|dispatch` invocation gets silenced with `2>/dev/null || true`.

## What lives where

| Path | Role |
|------|------|
| `scripts/sdlc-tool` | The wrapper itself (bash, `set -euo pipefail`). |
| `scripts/update/hardlinks.py` | Hardlinks `scripts/sdlc-tool` to `~/.local/bin/sdlc-tool` via `USER_BIN_SCRIPTS` table. |
| `scripts/update/verify.py` | `check_sdlc_tool()` is the green-light gate before bridge restart. |
| `scripts/update/run.py` | Step 4.7 runs the verify check; on failure, suppresses bridge restart. |
| `tools/sdlc_verdict.py`, `tools/sdlc_dispatch.py` | Underlying load-bearing modules. `main()` exits 1 on caught exception. |
| `agent/hooks/pre_tool_use.py` | `PM_BASH_ALLOWED_PREFIXES` includes `sdlc-tool {verdict,dispatch,stage-marker,stage-query,session-ensure}`. |
| `tests/unit/test_sdlc_tool_wrapper.py` | Wrapper shell semantics, foreign-cwd dispatch, loud-exit, parity sweep. |
| `tests/unit/test_update_hardlinks.py` | Verifies the hardlink propagation works. |

## Why bash, not Python

Tempting to make the wrapper a Python script that does its own argparse and routes to `tools.sdlc_*` directly. That just moves the cwd problem from "where is `tools/`" to "where is the `sdlc-tool` Python script's interpreter and its sys.path." Bash plus `uv run --directory` is the correct level of abstraction: bash is universally available, `uv run --directory` is already a hard dependency of the update system, and the cold-start overhead is the same one operators already pay for every `python -m tools.X` call.

## Failure modes and recovery

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `sdlc-tool: command not found` | Wrapper not on PATH or `~/.local/bin` not on `$PATH`. | Re-run `/update`. Verify `~/.local/bin` is on `$PATH`. |
| `sdlc-tool: AI_REPO_ROOT does not exist: ...` | `AI_REPO_ROOT` env override points at a stale path, or `~/src/ai` is missing. | Unset the env var or fix the repo location. |
| `sdlc-tool: AI_REPO_ROOT does not contain a tools/ directory` | Repo present but corrupt / wrong directory. | Verify `git -C "$AI_REPO_ROOT" status`. |
| `sdlc_verdict: CLI record failed: ...` (exit 1) | Redis unreachable, AgentSession not found, or a model-shape change. | This is the **intended loud failure**. Read the stderr message; the underlying tools module raised. |
| `sdlc-tool stage-query` returns `{"stages": {}, "_meta": ...}` | No AgentSession exists for the issue yet (e.g. `--issue-number 0` smoke). | Expected; that's the documented "no session" payload. |

## Cross-repo invocation

The bridge sets `cwd = target project's worktree` when spawning a PM session. The wrapper does **not** depend on cwd — it `cd`s into `$AI_REPO_ROOT` itself. The fix covers the bridge case at zero extra cost.

## Risks and limits

- **`uv run --directory` cold-start overhead.** Each invocation pays 50-200ms warm / 200-500ms cold for `uv` env resolution. Skills shell out 5-10× per `/sdlc` invocation, so worst case adds ~1-2s per `/sdlc` round. Acceptable; operators already paid this for `python -m tools.X` calls.
- **`uv` itself is a hard dependency.** If `uv` is missing on a remote, `sdlc-tool` fails. Same impact as the prior `python -m tools.X` call. The update system's verify step already gates on `uv`.
- **Loud verdict failures show up as session-log noise.** This is the intended design — failures must be visible. If transient Redis blips become a real annoyance, add a single-retry policy inside `tools.sdlc_verdict.record()` itself, not in the wrapper.

## PR-state repo resolution (`_resolve_target_repo`)

### Problem

`stage-query` fetches PR merge state from GitHub via `gh pr view`. When the PM session runs from a target-repo worktree (e.g. `popoto`, `cuttlefish`), `gh` without a `--repo` flag interrogates that repo's PR list — not the `ai` repo where the SDLC plan and session live. The PR number derived from the plan doc is meaningless against the wrong repo, so `mergeStateStatus` comes back wrong (or `gh` exits non-zero).

### Solution: `_resolve_target_repo()` in `tools/_sdlc_utils.py`

`_resolve_target_repo()` returns an `owner/name` slug (e.g. `tomcounsell/ai`) or `None`. It is called **exactly once per `_compute_meta` invocation** in `tools/sdlc_stage_query.py`; the resolved slug is threaded as the `repo=` keyword argument into both `_fetch_pr_merge_state` and `_gh_pr_list` (via `_lookup_pr_number`). Neither callee calls `_resolve_target_repo` itself — resolution happens once at the top of `_compute_meta`.

### Resolution ladder

| Rung | Source | Type | How it is used |
|------|--------|------|----------------|
| 0 | `GH_REPO` env var | `owner/name` slug | Returned directly — zero subprocess cost. Injected by the bridge for bridge-spawned sessions. Passed as `gh --repo GH_REPO ...`. |
| 1 | `SDLC_TARGET_REPO` env var | **Filesystem path** | Used as the `cwd` for `gh repo view --json nameWithOwner -q .nameWithOwner`. The slug comes from `gh` stdout. **Never passed to `gh --repo`.** |
| 2 | `_git_toplevel()` | Filesystem path | Used as `cwd` for the same `gh repo view` command. Resolves to the git root of whatever directory `sdlc_stage_query` was invoked from. |
| 3 | — | — | Returns `None`. Graceful degradation — callers omit `--repo` entirely and `gh` uses its own cwd resolution. |

**Critical distinction:** `SDLC_TARGET_REPO` is a **filesystem path** pointing to the target repo's checkout, not an `owner/name` slug. It is used as the working directory for `gh repo view` so that `gh` interrogates the correct repo. It is **never** passed to `gh --repo`. Only `GH_REPO` (a slug) is passed to `--repo`.

### Meta propagation

The resolved slug is stored in `_compute_meta`'s return dict under the `_resolved_target_repo` key:

```python
"_resolved_target_repo": resolved_repo,   # owner/name or None
```

This value flows into `sdlc_router.decide_next_dispatch()` via the `meta` dict it receives. The router uses it in the distinguishable Blocked message described below.

### Blocked reason when merge state is unresolvable

When the router cannot find a dispatch rule and a PR is known (`pr_number` is set) but its merge state is `None` or `"UNKNOWN"`, the router emits a **distinguishable Blocked reason** that names the PR number, its state, and the resolved repo:

```
Blocked: PR #42 merge state 'UNKNOWN' — could not resolve mergeability
         (target repo: tomcounsell/ai; check GH_REPO / SDLC_TARGET_REPO env)
```

This replaces the generic `"no matching dispatch rule"` message and tells the operator exactly which env var to check. The check is in `agent/sdlc_router.py` at the end of the `decide_next_dispatch()` function — it fires only when `pr_merge_state` is `None` or `"UNKNOWN"`, not for real GitHub states like `DIRTY` or `BLOCKED` (those route normally).

### Environment variable reference

| Variable | Value type | Purpose |
|----------|-----------|---------|
| `GH_REPO` | `owner/name` slug | Directly identifies the GitHub repo. Injected automatically by the bridge for all bridge-spawned sessions. Takes precedence over everything else. |
| `SDLC_TARGET_REPO` | Filesystem path | Points to a local checkout of the target repo. Used as `cwd` for `gh repo view` to derive the slug. Useful when running `sdlc-tool` locally in a cross-repo context without a bridge. |

### Failure modes

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `Blocked: PR #N merge state 'UNKNOWN'` | `GH_REPO` not set; `SDLC_TARGET_REPO` points at a non-git dir; `gh` not authenticated | Set `GH_REPO=owner/name` for the target repo, or ensure `SDLC_TARGET_REPO` points at a valid git checkout with `gh` auth |
| `Blocked: PR #N merge state 'UNKNOWN' (target repo: <none — using cwd>)` | No env var set and `_git_toplevel()` returned `None` (invoked outside a git repo) | Run from inside a git repo, or set `GH_REPO` / `SDLC_TARGET_REPO` |
| `_resolve_target_repo: gh repo view failed` (in logs) | `gh` exited non-zero in the given cwd — either not a git repo or not authenticated | Check `gh auth status`; verify cwd is a valid git repo |

## See also

- [SDLC Router Oscillation Guard](sdlc-router-oscillation-guard.md) — the original Guard G1-G5 single-writer verdict design. This wrapper is the missing piece that made the verdict-write path work from any cwd.
- `docs/plans/sdlc-1175-tool-resolver.md` — original plan with critique findings applied.
- `docs/plans/sdlc-1642.md` — plan for the PR-state repo resolution ladder (issue #1642).
