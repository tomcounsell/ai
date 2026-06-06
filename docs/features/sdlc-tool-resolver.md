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

Resolution order (unchanged): explicit `session_id` arg → `VALOR_SESSION_ID` env → `AGENT_SESSION_ID` env → `find_session_by_issue(issue_number)`.

**The `ensure` parameter (issue #1558):**

- **`ensure=False` (default)** — a pure, side-effect-free lookup. Returns the session or `None`. **No session is ever created.** Every *read* path uses this: `verdict get`, `stage-query`, `next-skill`, and `meta-set`/`stage-marker` when called by read-only code. This preserves the original pre-#1558 lookup behavior byte-for-byte.
- **`ensure=True`** — opt-in auto-create. When no existing PM session is found **and** there is issue context (`issue_number >= 1`) **or** a session-id env var is set, the resolver calls `tools.sdlc_session_ensure.ensure_session(issue_number)` to create (or dedup onto a live bridge session) a `sdlc-local-{N}` PM session, then re-resolves and returns it. A bare sessionless call with no issue context still returns `None` — no fabricated session. An ensure failure (e.g. `ProjectKeyResolutionError`) yields `None` rather than raising.

**Only the three *write* subcommands pass `ensure=True`:** `sdlc_meta_set.write_meta`, `sdlc_stage_marker.write_marker`, and `sdlc_verdict._cli_record`. This guarantees a state *write* always has a home regardless of how the pipeline is driven — a direct `sdlc-tool verdict record` in a clean (non-`/sdlc`) session now persists instead of silently no-op'ing. The opt-in is grep-able: `grep -rn 'ensure=True' tools/` surfaces exactly the three write sites plus the signature in `_sdlc_utils.py`.

This **supersedes the per-skill `session-ensure` requirement for non-`/sdlc` callers.** The explicit `sdlc-tool session-ensure` call in `/sdlc` Step 1.5 is now belt-and-suspenders (tagged `REDUNDANT-AFTER-#1558` in `SKILL.md`) — auto-ensure on the first write covers callers that never pass through `/sdlc`.

**Read-after-write within `find_session_by_issue`:** an auto-ensured `sdlc-local-{N}` session carries no `issue_url`/`message_text`, so the read paths match it by its **deterministic session id** (`sdlc-local-{issue_number}`) first, before the `issue_url`/`message_text` scans. This is what lets a sessionless `verdict record` be read back by a separate `verdict get`/`stage-query` process (verified end-to-end in `tests/integration/test_sdlc_sessionless_e2e.py`).

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

## See also

- [SDLC Router Oscillation Guard](sdlc-router-oscillation-guard.md) — the original Guard G1-G5 single-writer verdict design. This wrapper is the missing piece that made the verdict-write path work from any cwd.
- `docs/plans/sdlc-1175-tool-resolver.md` — original plan with critique findings applied.
