# Session Isolation: Task Lists + Git Worktrees

## Overview

Session isolation prevents task lists and filesystem state from bleeding between concurrent or back-to-back coding sessions. It uses a two-tier model:

- **Tier 1 (automatic, thread-scoped):** Every session gets an isolated task list automatically, with zero configuration. Scoped by Telegram thread ID or local session ID. Ephemeral and disposable.
- **Tier 2 (named, slug-scoped):** When `/do-plan {slug}` is invoked, the session graduates to a durable, named task list keyed by the slug. The same slug ties together the task list, branch, worktree, plan doc, and GitHub issue.

This approach ensures ad-hoc conversations never pollute each other's tasks, while planned work items get persistent, resumable task isolation that survives session restarts.

## Technical Implementation

### Task List ID Injection

The bridge injects `CLAUDE_CODE_TASK_LIST_ID` into the environment when spawning Claude Code via the harness:

- **Tier 1**: `CLAUDE_CODE_TASK_LIST_ID=thread-{chat_id}-{root_message_id}` for Telegram sessions, or `session-{session_id}` for local Claude Code sessions.
- **Tier 2**: `CLAUDE_CODE_TASK_LIST_ID={slug}` when a work item slug is assigned via `/do-plan`.

The env var is set on the `_harness_env` dict built in `agent/session_executor.py` (see [Harness Abstraction § Session Environment Injection](harness-abstraction.md#session-environment-injection-issue-1148)). The prior `ValorAgent._create_options()` / `get_agent_response_sdk()` injection site was deleted in #2000 along with the rest of the dead SDK path.

**Important distinction:** `CLAUDE_CODE_TASK_LIST_ID` scopes **sub-agent Task storage** (`~/.claude/tasks/{id}/`) used by `TaskCreate`/`TaskList`/`TaskUpdate` when spawned via the `Task` tool. It does **not** affect `TodoWrite`, which is scoped by Claude Code's internal session ID automatically. In practice, this means:

- Sub-agent tasks (spawned during `/do-build`) are isolated by the env var
- In-session todos (TodoWrite) are isolated by session ID -- no env var needed
- For cross-session persistence, use `--session-id` with a deterministic ID derived from the thread

### Model Fields

- `AgentSession.slug` -- Redis model field storing the active slug for a session. Set when `/do-plan {slug}` runs.
- **Looking up an AgentSession by id** -- Use `AgentSession.get_by_id(agent_session_id)` for any raw-string lookup. Popoto's `AgentSession.query.get()` requires a key kwarg (`db_key=` / `redis_key=`) and raises `AttributeError` on bare strings, which historically got swallowed by silent `except` blocks (issue #765). The `get_by_id` helper handles None/empty/whitespace input, logs warnings on backend failures, and is the canonical entry point for CLI args, parent references, and Redis hash fields.
- `AgentSession.project_config` -- DictField carrying the full project dict from `projects.json`. Populated at enqueue time so downstream code (queue worker, SDK client, formatting) can read project properties without re-deriving from config files or parallel registries. See [Eng Session Architecture](eng-session-architecture.md#project-config-propagation) for the propagation flow.
- `Job.slug` -- Propagated from the session to each session for task list routing.
- `Job.task_list_id` -- The computed task list ID (either slug or thread-derived).

### Tier Transition

Tier 1 tasks do not migrate to tier 2. They are scratch work from investigation and exploration. When `/do-plan` runs, it creates a clean slate for the named task list. The plan document captures what matters.

### Git Worktrees for Filesystem Isolation

Each tier 2 work item gets its own git worktree for filesystem isolation:

- Worktrees live under `.worktrees/{slug}/` (added to `.gitignore`)
- Branch convention: `session/{slug}`
- Created at `/do-build` time via `agent/worktree_manager.py`
- `settings.local.json` is copied into the worktree's `.claude/` directory (since it's not tracked by git)
- On completion: changes are merged back, worktree is removed
- Stale worktree references are automatically detected and cleaned up by `create_worktree()`
- `get_or_create_worktree()` is the preferred idempotent entry point: it returns an existing worktree path or creates a new one, making session resumption seamless

The worktree manager provides six operations: `get_or_create_worktree()`, `create_worktree()`, `remove_worktree()`, `list_worktrees()`, `prune_worktrees()`, and `cleanup_after_merge()`.

### Worktree Enforcement for Child Eng Sessions (Issue #887)

Child eng sessions with a slug are **required** to run inside a worktree. Three enforcement layers prevent contamination of the main checkout:

1. **Worktree provisioning failure escalation** (`agent/agent_session_queue.py`): When `get_or_create_worktree()` fails for a slugged eng session, the error is escalated to a `RuntimeError` instead of falling back to the main checkout. Slugless and teammate sessions retain the original fallback-to-main-checkout behavior.

2. **Main-checkout protection guard** (`agent/agent_session_queue.py`): A secondary guard runs after worktree resolution. If a slugged eng session resolves to a `working_dir` that does not contain `.worktrees`, the session is rejected with a `RuntimeError`. This catches cases where worktree provisioning was silently skipped or the path was overridden.

3. **Engineer persona instruction** (`config/personas/engineer.md`): The engineer persona prompt explicitly instructs the agent to allocate a non-overlapping worktree (`.worktrees/{slug}/`) and pass it as the working directory when fanning out to child eng sessions via `valor-session create --role eng`. This is the first line of defense -- the infrastructure guards are the safety net.

**Why this was added:** The 2026-04-10 incident (issue [#887](https://github.com/tomcounsell/ai/issues/887)) demonstrated that `valor-session create` without a prior `/do-plan` step bypassed worktree provisioning entirely, causing child sessions to run git operations in the main checkout. This contaminated concurrent human and agent work.

### Synthetic Slugs for Slugless Eng Sessions (Issue #1272)

The #887 main-checkout protection guard short-circuits when `slug is None`:

```python
if _stype == "eng" and slug and WORKTREES_DIR not in str(working_dir):
    raise RuntimeError(...)
```

That left a residual hole — an eng session created without a slug (a future debug harness, a test fixture, or any code path that bypasses the CLI's `--slug` requirement) would skip worktree provisioning AND skip the guard, landing in the main checkout.

Issue [#1272](https://github.com/tomcounsell/ai/issues/1272) closes that hole with two surgical additions:

1. **CLI symmetry guard** (`tools/valor_session.py::cmd_create`): `valor-session create --role eng` requires `--slug` or `issue #N` in the message. Slugless invocations exit 1 with a stderr error referencing #1272. The message format includes the literal substring `dev sessions must be created with --slug` for grep-ability.

2. **Synthetic-slug synthesis** (`agent/session_executor.py`): If a slugless eng session somehow reaches the executor (a future programmatic spawn site that bypasses the CLI), the executor synthesizes a slug `dev-{agent_session_id[:8]}` and provisions a worktree the same way slugged sessions do today. The synthesis emits a stable `[synthetic-slug]` log marker so operators can grep post-deploy:

   ```
   [synthetic-slug] Allocated synthetic slug dev-abcd1234 for slugless dev session abcd1234-... (issue #1272)
   ```

3. **Pre-synthesis precondition**: An executor-guard precondition raises (and finalizes the session as failed) if `agent_session_id is None` for a slugless eng session — without an aid, the synthesis line `dev-{aid[:8]}` would crash with `TypeError`.

4. **Synthetic-slug cleanup hook**: Synthetic-slug worktrees may not have a corresponding PR (the eng session may complete without ever opening one), and `prune_worktrees()` only runs `git worktree prune` (removes references, not directories). The session-completion `finally` block in `_execute_agent_session` calls `cleanup_after_merge(repo_root, slug)` directly when the slug matches the regex `^dev-[0-9a-f]{8}$`. The regex is exact — it must NOT match a real human-chosen slug like `dev-improvements` or `dev-1272`. Cleanup failures are logged at WARNING and do NOT propagate as session failures.

   **Unmerged-branch guard (issue #1646):** `cleanup_after_merge` now verifies the merged
   precondition before deleting the branch, using the squash-safe `merged_via_tree` oracle
   (`git merge-tree --write-tree`). If the branch has unmerged commits, it is preserved
   (`skipped_unmerged=True`) and a `[unmerged-branch-guard]` warning is logged. The
   worktree directory is also preserved so the work remains easy to find and resume. This
   replaces the prior unconditional `git branch -D` that caused silent data loss. See
   `docs/features/headless-session-runner.md` for the current execution substrate.

The synthetic-slug regex `^dev-[0-9a-f]{8}$` is the safety guarantee: it matches only the synthesis output, never a real human slug.

### Git-Layer Enforcement for Manual Operations (Issue #1288)

The three #887 layers above all live on the worker / agent code path. Nothing prevented a `git checkout session/X && git commit ...` from the main checkout (or any other working tree) — git was happy, the commit landed, the PR merged, and the only signal of the violation was that `.worktrees/X/` never existed. Issue [#1288](https://github.com/tomcounsell/ai/issues/1288) closes that surface with a pre-commit hook.

**Where it lives:** `.githooks/pre-commit` Phase 0.5 (runs before lint, lockfile, and secret-scan phases so a misplaced commit fails fast).

**What it guards:** if the current branch matches `session/*` and `git rev-parse --show-toplevel` does not end with `.worktrees/{slug}/`, the guard self-detects whether the owning worktree exists before deciding. If `.worktrees/{slug}/` **exists** on disk, the commit is blocked with an actionable error pointing at the right path — the operator must commit from inside the worktree. If `.worktrees/{slug}/` does **not** exist (the operator is working a session branch deliberately in the main checkout, with no isolated worktree to contaminate), the commit is **allowed** with an informational stderr note (option (a), decided in [#1620](https://github.com/tomcounsell/ai/issues/1620)). Detached HEAD (rebase, cherry-pick, bisect) is a no-op. Non-`session/*` branches are unaffected. An explicit empty-slug guard handles the pathological `session/` branch name (unreachable through normal git but defensive).

**Override:** `git commit --no-verify` bypasses the hook. This is intentional — the caller takes responsibility, matching the project's broader stance on `--no-verify` for WIP commits. If `--no-verify` becomes a dominant bypass vector, the layered fix is a Claude Code `PreToolUse` Bash validator; not built today.

**Install path:** the hook runs only when `git config core.hooksPath` is set to `.githooks`. This is configured by the `/update` skill (`scripts/update/git.py`), not by `/setup`. Machines that have only run `/setup` will silently skip the hook until `/update` runs (or until the operator runs `git config core.hooksPath .githooks` manually).

**Relationship to #887 and #1272:** sibling enforcement on a different surface. #887 covers the worker-side AgentSession executor; #1272 closes the slugless residual hole; #1288 covers the git-side path (`git commit` from the wrong CWD on a session branch). Together they make the invariant — "`session/{slug}` work never contaminates an *existing* `.worktrees/{slug}/`" — a closed system across worker, CLI, and git surfaces. The option-(a) refinement (#1620) preserves that invariant: the guard only relaxes when no worktree exists, so an existing isolated worktree is never bypassed.

### Branch verification on worktree reuse (#1377)

A worktree handed between SDLC stages may still be checked out to the previous stage's branch (e.g. BUILD leaves `.worktrees/{slug}/` on `session/{slug}`; a follow-up MERGE eng session expects `main`). The executor calls `verify_worktree_branch` after the #887 main-checkout guard and before launching the Claude Code subprocess. Clean worktrees are auto-checked-out to the expected branch with an INFO `[worktree-branch-recovery]` log; dirty worktrees raise `WorktreeBranchMismatchError` so the session fails loudly with `last_error` populated instead of hanging silently. See `docs/features/worktree-manager.md` for the full behavior table and rationale.

### Early Worktree Provisioning via `--slug`

The `valor-session create` CLI command accepts a `--slug` flag that provisions a worktree at session creation time, before the session is enqueued:

```bash
python -m tools.valor_session create --role eng --slug my-feature --message "Build the feature"
```

When `--slug` is provided:
- The slug is validated via `_validate_slug()` (rejects empty strings, path traversal, unsafe characters)
- `get_or_create_worktree()` provisions the worktree at `.worktrees/{slug}/`
- The session's `working_dir` is set to the worktree path
- The slug is stored on the `AgentSession` model

This ensures worktree isolation is established at the earliest possible point, closing the gap where sessions created outside of `/do-plan` would skip isolation.

### SDLC Classification & `issue_url` Derivation (Issue #2140)

SDLC pipelines can be launched two ways: via the Telegram bridge (which classifies the message before enqueue) or via the CLI — `python -m tools.valor_session create`. Historically the CLI path never set `classification_type`, so CLI-created SDLC sessions silently degraded: the enqueue-time `stage_states` init (`agent/agent_session_queue.py`, gated on `classification_type == ClassificationType.SDLC`) was skipped, the dashboard rendered `current_stage: None, stages: []` for the entire run, and the output router's auto-continue rule (`agent/output_router.py`, `session_type == "eng" and classification_type == "sdlc"` → `nudge_continue`) fell through to `deliver` — so on a bridge machine the pipeline could pause as if awaiting a human.

`cmd_create` now derives SDLC metadata from the same message it already uses to auto-derive the slug, via `_derive_sdlc_metadata(message, project_config)`. Precedence:

1. A full GitHub **issue URL** in the message → `("sdlc", <that URL>)` (wins outright; preserves the URL's own repo).
2. Else a bare **`issue #N`** reference (the existing `_ISSUE_REF_RE`) → `("sdlc", https://github.com/{org}/{repo}/issues/N)`, building the URL from the resolved project's `github.org`/`github.repo` config. If that config is absent, classification is still set but `issue_url` is `None`.
3. Else a bare **`pr #N` / `pull request #N`** reference → `("sdlc", None)`.
4. Else → `(None, None)` (conversational/teammate messages leave metadata unset).

The derived `classification_type` and `issue_url` are threaded through `_push_agent_session` (and, for signature symmetry, the public `enqueue_agent_session` wrapper) onto the `AgentSession`. CLI-created SDLC sessions then behave identically to bridge-classified ones: `stage_states` is initialized at enqueue (dashboard shows stage progression from the start), `issue_url` links the session to its issue and ledger-side stage state, and the router auto-continues turn-end status updates.

**Design note (ledger ↔ session-store divergence):** the issue-keyed `PipelineLedger` (#2012) and the session-keyed `stage_states` are kept independent. The fix sets classification at **creation time** (honoring the content-blind output router, #1058) rather than adding a runtime ledger→`stage_states` sync or making the dashboard read the ledger — the divergence in this bug existed only because `classification_type` was unset, so restoring parity with the bridge path is the minimal root-cause fix.

### Stale Worktree Recovery

When a session crashes or times out, it may leave a stale worktree that blocks future builds for the same slug. The `create_worktree()` function handles this automatically by detecting and cleaning up stale worktrees before creation. Three recovery cases are handled:

1. **Worktree directory exists and is valid**: Returns the existing path as a no-op.
2. **Worktree directory is gone but git still tracks it**: Runs `git worktree prune` to clean the stale reference, then creates a fresh worktree.
3. **Branch is locked by a worktree at a different path**: Force-removes the stale worktree via `git worktree remove --force`, then creates at the expected path. Falls back to `shutil.rmtree` + prune if force-remove fails.

Detection uses `git worktree list --porcelain` to find branches already associated with a worktree (via the `_find_worktree_for_branch()` helper). All recovery actions are logged with warnings so operators can see what was cleaned up.

This makes the SDLC pipeline resilient to stale worktree state -- no manual `git worktree remove --force` is needed.

See GitHub issue [#237](https://github.com/tomcounsell/ai/issues/237) for the original bug report.

### Path-Containment Invariant

`_cleanup_stale_worktree()` enforces a strict path-containment invariant: it will only operate on paths strictly under `repo_root / .worktrees/`. Any other input -- including `repo_root` itself -- raises `RuntimeError` before any filesystem operation runs. The `shutil.rmtree` fallback does not pass `ignore_errors=True`, so partial-destruction failures surface as real exceptions instead of being silently swallowed. Both the guard and the fallback fire `logger.critical` before acting, giving the crash tracker and log audits a correlation point.

This guard was added in response to the 2026-04-10 incident (issue [#880](https://github.com/tomcounsell/ai/issues/880)), where a session branch got checked out in the main working tree and the cleanup helper was handed the main repo path; the `shutil.rmtree(..., ignore_errors=True)` fallback then recursively deleted the main repository. The guard now refuses bogus paths loudly and the fallback no longer hides errors.

### CLI-level Project Scope Resolution (Issue #1158)

When a session is created via `valor-session create` (e.g. a PM session spawning a child SDLC session), the CLI enforces an **immutable project → repo pairing**: a project name determines its repo, and no input may decouple them.

**Governing principle**:

> A project and a repo should not be provided separately. The local machine's configuration sets the pairing and that pairing cannot be broken.

**Resolution precedence for `cmd_create`**:

1. `--project-key <key>` — explicit flag wins over everything else.
2. `--parent <id>` — if the parent `AgentSession` is resolvable, its `project_key` is inherited. `working_dir` is **never** inherited — it is always re-derived from the (inherited) `project_key`.
3. `resolve_project_key(os.getcwd())` — matches the cwd against each project's `working_directory` in `projects.json`. Raises `ProjectKeyResolutionError` on no match; the CLI prints the message (cwd, available keys, suggested `--project-key`) to stderr and exits non-zero.

**Removed surfaces**:

- There is no working-directory override flag on `valor-session create`. Callers who need a different repo pass a different `--project-key`.
- `resolve_project_key` no longer silently returns `"valor"` on unmatched cwd. It raises `ProjectKeyResolutionError`. Callers relying on the old fallback must catch the exception explicitly or supply a key.
- `load_config()` failures raise `ProjectsConfigUnavailableError` (distinct from `ProjectKeyResolutionError`) so operators can distinguish a missing config from an unknown key.

**Helper**:

- `_resolve_project_working_directory(project_key)` — loads `projects.json` once and returns `(repo_root: Path, project_dict: dict)`. The dict is passed through to `AgentSession.project_config` so CLI-created sessions carry the same per-project payload as bridge-created sessions (PR #685).

**Where the rule is enforced**:

- `tools/valor_session.py::cmd_create` — the primary CLI surface.
- `tools/sdlc_session_ensure.py::ensure_session` — derives `working_dir` from `projects.json` (not `os.getcwd()`). On resolution failure the function returns `{}` (idempotent no-op), never a mis-scoped session.
- `agent/reflection_scheduler.py` — catches the new typed errors explicitly; falls back to `PROJECT_KEY` env var as a last-resort for local nightly reflections only.

**Where it is NOT enforced (by design)**:

- `_push_agent_session(..., working_dir=...)` — internal primitive; its `working_dir` kwarg is computed by the CLI/scheduler/bridge, not supplied by users.
- `bridge/telegram_bridge.py` — already correct (derives `working_directory` from `projects.json`).
- `tools/agent_session_scheduler.py` — already follows the rule; was the reference model for the fix.

### Post-Merge Worktree Cleanup

When a PR is merged via `gh pr merge --squash --delete-branch`, the remote branch is deleted but local branch deletion fails if a git worktree still references it. The `cleanup_after_merge()` function handles this:

1. Removes the worktree at `.worktrees/{slug}/` if it still exists
2. Prunes stale git worktree references
3. Deletes the local `session/{slug}` branch if it still exists

A CLI script is available for manual or automated use:

```bash
python scripts/post_merge_cleanup.py {slug}
```

The function returns a status dict with `worktree_removed`, `branch_deleted`, `already_clean`, and `errors` fields. It is safe to call in any state -- if everything is already cleaned up, it is a no-op.

### Worktree Busy Guard (Issue #1357)

`remove_worktree()` enforces a runtime invariant: **a worktree at `.worktrees/{slug}/` is removable only if no non-terminal `AgentSession` references it as `working_dir`**. The guard is implemented by `worktree_busy_check(repo_root, slug)`, which scans `AgentSession.query.all()` for live sessions and matches their `working_dir` against the target worktree path using segment-aware containment (so `.worktrees/sdlc-1218` matches `.worktrees/sdlc-1218/subdir` but not `.worktrees/sdlc-1218-other`).

When the guard fires:

- `remove_worktree(repo_root, slug)` returns `("blocked", session_id)` instead of `True`/`False`.
- `cleanup_after_merge(repo_root, slug)` surfaces the block as `result["blocked_by_session"]` and adds `f"blocked: worktree in use by session_id=..."` to `result["errors"]`.
- `python scripts/post_merge_cleanup.py {slug}` prints the offending session id to stderr and exits **2** (distinct from exit 1 for generic errors). See [`docs/sdlc/do-merge.md`](../sdlc/do-merge.md#busy-guard-issue-1357) for the operator workflow.

**Why this guard exists.** Investigation #1246 documented a 10.9-hour PM-session wedge: a sibling `/do-merge` ran `post_merge_cleanup.py` while the PM session's SDK subprocess was still running with cwd inside the same worktree. macOS does not signal subprocesses about deleted cwd directories — `getcwd(3)` returns ENOENT, the harness hangs in `await proc.communicate()` forever, and the AgentSession row stays at `status=running` until a manual kill. The busy guard prevents that delete from happening.

**Override.** `remove_worktree(repo_root, slug, force=True)` removes the worktree despite a live session, with a WARNING log (`force-removing worktree .worktrees/{slug} despite live session_id=...`). Use only when the session has already been verified dead but its row hasn't flipped yet. The WARNING is grep-able for audit.

**Complementary watchdog.** `BackgroundTask._watchdog` (`agent/messenger.py`) checks `os.path.isdir(working_dir)` on each heartbeat tick. If the directory has vanished — by manual `rm -rf`, OS cleanup, or any path that bypassed the busy guard — the watchdog cancels the work task within one tick, logs `cwd_vanished session_id=...`, and increments the `{project_key}:session-health:cwd_vanished` Redis counter (falls back to bare `session-health:cwd_vanished` when the project_key is unknown). The existing `CancelledError` handler in `_run_work` stays silent unless the cancel-reason is the terminal `no_resume` (see [Reason-Aware Interrupt Messaging](pm-final-delivery.md#reason-aware-interrupt-messaging-and-failure-notification-issue-1877-silent-resume-inversion)); an auto-resuming interruption sends nothing, and only a genuinely terminal stop sends `INTERRUPT_NO_RESUME`.

**Layering.** The guard prevents the bad delete (source); the watchdog catches it if it happens anyway via a different path (sink). The two layers are independent and reverting either does not break the other.

### Uncommitted-Work Preservation & Destructive-Git Guard (Issue #2137)

Session worktrees are force-removed on session exit (`git worktree remove --force`). The unmerged-branch guard (#1646) protects only *committed* work; before #2137, staged, unstaged, and untracked edits in a dirty worktree were discarded with no backstop. A production incident destroyed six uncommitted files this way (the reflog showed `reset: moving to HEAD`). Two complementary layers close the gap.

**1. Auto-WIP-commit before teardown.** `preserve_uncommitted_worktree_changes(repo_root, slug, worktree_dir)` (`agent/worktree_manager.py`) runs *before* every force-remove. It is called from `remove_worktree()` and directly from `_cleanup_stale_worktree()` (which force-removes without going through `remove_worktree`). Mechanism:

1. `git -C <worktree> status --porcelain` — if clean, no-op (`{"preserved": False, "was_clean": True}`).
2. `git -C <worktree> add -A` — captures untracked + tracked edits.
3. `git -C <worktree> commit --no-verify --no-gpg-sign -m "WIP: auto-preserved before teardown [slug] [ISO-ts]"` — `--no-verify` avoids pre-commit hooks hanging teardown; `--no-gpg-sign` avoids signing prompts.
4. `git -C <repo_root> update-ref refs/session-wip/{slug} <sha>` — writes to the **common** ref store (not the per-worktree one), so the ref survives both worktree removal and the unmerged-branch-guard branch deletion.

The recovery pointer is logged at WARNING with a greppable tag: `[worktree-wip-preserved] slug=… ref=refs/session-wip/… sha=…`.

**Why a WIP commit + named ref, not `git stash`.** A plain `git stash` inside a worktree writes to the *per-worktree* `refs/stash`, which is destroyed with the worktree — useless as a teardown backstop.

**Non-blocking contract.** Preservation must never block or hang teardown. Any subprocess failure, timeout, or exception is caught, logged at ERROR with `[worktree-wip-preserve-failed]`, and returned in the result dict (`{"preserved": False, "errors": [...]}`) — the force-remove still proceeds.

**Recovery procedure.** The preserved work lives at `refs/session-wip/{slug}` and as a WIP commit on `session/{slug}`:

```bash
git checkout refs/session-wip/{slug}      # inspect the preserved tree
git cherry-pick refs/session-wip/{slug}   # or replay onto another branch
git reset --soft HEAD~1                    # on a resumed session: unstage the WIP to restore the dirty tree
```

**GC policy.** `refs/session-wip/*` refs are reclaimed **manually** — they are cheap pointers to dangling commits. There is no automated GC/TTL daemon in this feature (out of scope; a scheduled ref-GC reflection is a separate follow-up). Remove a stale ref with `git update-ref -d refs/session-wip/{slug}`.

**2. Destructive-git PreToolUse guard.** `.claude/hooks/validators/validate_no_destructive_git_in_worktree.py` blocks an agent from destroying a dirty worktree in-session (before the teardown backstop can fire). It blocks `git reset --hard`, `git clean -f[dx]`, `git checkout -- .` / `git checkout .`, `git restore .`, and bare `git stash` / `git stash push` (no pathspec) **only when** the cwd resolves inside a `.worktrees/` path **and** the tree is dirty. It mirrors `validate_no_uv_sync_in_worktree.py`: a pure `find_violation(command, cwd, is_dirty)` core, command-position (not substring) detection, `cd … &&` chain resolution, and fail-open on any parse/git error.

- **Clean-tree resets are allowed** — a `git reset --hard` on a clean tree loses nothing.
- **Override token.** Append `# allow-destructive-git` anywhere in the command to deliberately run a destructive command (greppable, mirrors existing hook-override conventions).
- Registered in `.claude/settings.json` under the `PreToolUse` `Bash` matcher.

**Layering.** The guard stops in-session destruction (source); the auto-WIP-commit catches whatever survives to teardown (sink). Independent — reverting either leaves the other functional.

## Key Experiment Findings

Experiments validated the approach before implementation:

1. **Worktree + SDK compatibility**: The Claude Code SDK v2.1.38 works in bare worktree directories with no modifications. Even with `.claude/` completely absent, the SDK no longer crashes. `settings.local.json` is copied for convenience (local settings), not for crash prevention.
2. **`CLAUDE_CODE_TASK_LIST_ID` scoping**: The env var scopes sub-agent Task storage (`~/.claude/tasks/{id}/`) but does **not** affect TodoWrite, which is always scoped by session ID. See `docs/features/task-list-isolation.md` for detailed findings.
3. **Thread ID uniqueness**: Using `chat_id` + `root_message_id` provides per-conversation isolation within group chats, not just per-chat isolation.

## Relevant Files

| File | Purpose |
|------|---------|
| `agent/worktree_manager.py` | Git worktree create/remove/list/prune/cleanup operations; `preserve_uncommitted_worktree_changes()` auto-WIP-commit backstop (#2137) |
| `.claude/hooks/validators/validate_no_destructive_git_in_worktree.py` | PreToolUse guard blocking destructive git commands in a dirty worktree (#2137) |
| `scripts/post_merge_cleanup.py` | CLI script for post-merge worktree and branch cleanup |
| `agent/hooks/session_registry.py` | Maps Claude Code UUIDs to bridge session IDs for hook-side resolution |
| `agent/sdk_client.py` | Injects `CLAUDE_CODE_TASK_LIST_ID` into SDK environment; registers/unregisters sessions in the hook registry |
| `agent/agent_session_queue.py` | Computes task list ID in `_execute_agent_session()` and passes to SDK; worktree enforcement guards for slugged eng sessions |
| `tools/valor_session.py` | CLI for session management; `--slug` flag provisions worktree at creation time |
| `config/personas/engineer.md` | Engineer prompt with worktree CWD instruction for child eng sessions spawned via `valor-session create --role eng` |
| `models/agent_session.py` | `AgentSession` model with `slug` field |
| `docs/features/task-list-isolation.md` | Experiment results for CLAUDE_CODE_TASK_LIST_ID behavior |
| `docs/features/worktree-sdk-compatibility.md` | Experiment results for SDK + worktree compatibility |

## Completion Signal

Sessions transition to the **Complete** state when work is finished. Two mechanisms signal completion:

1. **Automatic** -- `mark_work_done()` is called in `agent/agent_session_queue.py` when a session finishes successfully. This is the primary completion mechanism.
2. **Human signal** -- The thumbs-up emoji reaction (👍) in the Telegram group chat serves as a visual acknowledgment between humans that work is done.

Note: **Telethon cannot receive emoji reaction events** for user accounts (Telegram API limitation). The 👍 reaction is purely a human-to-human signal -- it does not trigger any programmatic state change. No reaction handler is needed in the bridge.

## Session Continuation Gate

When spawning a Claude Code subprocess, `_create_options()` in `agent/sdk_client.py` decides whether to set `continue_conversation=True`. Previously, this was set for any non-None `session_id`, which could cause fresh sessions to reuse stale Claude Code session files on disk -- leaking context between unrelated conversations (see issue #232).

Now, `_has_prior_session(session_id)` queries the AgentSession Redis model to check if a prior session ran for this session_id with a status of `completed`, `running`, `active`, or `dormant`. Only when a prior session exists is `continue_conversation` (and `resume`) set to True. This prevents cross-contamination between concurrent DM and group conversations while preserving reply-thread continuation (which reuses the original session_id and thus has a prior AgentSession record).

The check fails safe: if Redis is unavailable, `_has_prior_session()` returns False (don't continue), ensuring fresh sessions never accidentally inherit stale context.

### Claude Code UUID Mapping (Issue #374)

The session continuation gate was extended to fix three compounding bugs that caused the Observer to prematurely deliver output on continuation sessions:

1. **Session identity mapping**: `AgentSession` now has a `claude_session_uuid` field that stores the Claude Code transcript UUID (from `ResultMessage.session_id`). The `resume` parameter in `_create_options()` uses this stored UUID instead of the Telegram session ID. This prevents Claude Code from falling back to the most recent unrelated session file on disk. The function `_get_prior_session_uuid()` replaces the boolean `_has_prior_session()` check with a UUID lookup, and `_store_claude_session_uuid()` persists the mapping after each query.

2. **Watchdog count scoping**: The health check hook (`agent/health_check.py`) uses the session registry (see below) for tool count tracking instead of Claude Code's internal session ID. A `reset_session_count()` function is called at the start of each SDK query to clear stale counts from prior runs. This prevents continuation sessions from inheriting inflated tool counts that trigger premature health check kills.

3. **Deterministic record selection**: When re-reading `AgentSession` records (in both `agent_session_queue.py` and `bridge/observer.py`), the code now filters by active statuses (`running`, `active`, `pending`) first, then falls back to all records, sorted by `created_at` descending. This ensures the newest relevant record is always selected when duplicates exist. Additionally, `_push_agent_session()` marks old completed records as `superseded` to prevent ambiguity.

The `claude_session_uuid` field is included in `_AGENT_SESSION_FIELDS` so it is preserved across the delete-and-recreate pattern used by `_enqueue_continuation()`.

### Hook Session Registry (Issue #597)

Hooks fired by the Claude Agent SDK execute in the **parent bridge process**, not inside the Claude Code subprocess. The `VALOR_SESSION_ID` env var (injected into the subprocess at `sdk_client.py`) is invisible to hooks because they run in a different process context. This caused all hook-side session lookups to fall back to Claude Code's internal UUID, breaking activity logging, Redis session tracking, heartbeat enrichment, and Dev session registration.

The fix is a **module-level registry** (`agent/hooks/session_registry.py`) that maps Claude Code UUIDs to bridge session IDs within the parent process. The registry uses a two-phase registration pattern:

1. **Pre-registration**: `SDKAgentClient.query()` calls `register_pending(bridge_session_id)` before starting the SDK query. At this point the Claude Code UUID is not yet known.
2. **Promotion**: The first hook callback calls `complete_registration(claude_uuid)` (or `resolve()` which auto-promotes) using the UUID from `input_data["session_id"]`. This promotes the pending entry to a full UUID-keyed mapping.
3. **Lookup**: All subsequent hook calls use `resolve(claude_uuid)` to look up the bridge session ID. This replaces the previous `os.environ.get("VALOR_SESSION_ID")` calls.
4. **Cleanup**: `SDKAgentClient.query()` calls `unregister(claude_uuid)` in its `finally` block.

The registry also tracks per-session tool activity (tool count and last 3 tool names) via `record_tool_use()` and `get_activity()`. The bridge watchdog (`BackgroundTask._watchdog()` in `agent/messenger.py`) reads this data to enrich heartbeat logs with tool-level progress (e.g., `"running 120s, tools=15, last=Bash"`).

**Thread safety**: The bridge is single-threaded asyncio, so dict operations on distinct keys are safe without locking. A TTL-based sweep (`cleanup_stale()`) removes entries older than 30 minutes as a safety net for entries not cleaned up due to uncaught exceptions.

**Hook call sites using the registry**:
- `agent/health_check.py` -- watchdog tool count tracking
- `agent/hooks/pre_tool_use.py` -- pipeline stage start on in-session SDLC Skill invocation (e.g. a `/sdlc` stage), tracked within the running eng session rather than at child spawn

(Historical: `agent/hooks/subagent_stop.py` previously used the registry for completion tracking and a two-lookup pattern for the child AgentSession. The hook was stripped in the Phase 5 harness migration and then deleted in issue #1024. Child-session completion is no longer driven by a stop hook: when a child eng session finalizes, `complete_transcript()` runs `_finalize_parent_sync()` (`agent/session_completion.py`) synchronously, which re-enqueues the waiting parent. SDLC stage tracking now happens in-session via the `pre_tool_use` / `post_tool_use` hooks on Skill invocations, not at child spawn.)

Note: The `VALOR_SESSION_ID` env var injection in `sdk_client.py` is retained for code running inside the Claude Code subprocess (shell scripts, Python tools via Bash). The registry is only for parent-process hook resolution.

## History Truncation Warning

Session history is capped at `HISTORY_MAX_ENTRIES` (currently 20) entries via `AgentSession.append_event()`. When a session exceeds this cap, the oldest entries are silently dropped to stay within the limit. A `WARNING`-level log message is emitted each time truncation occurs, including the original length and number of entries lost:

```
WARNING Session abc123 history truncated from 25 to 20, 5 oldest entries lost
```

This is particularly relevant for long-running SDLC sessions that may accumulate many lifecycle events. The warning enables operators to diagnose issues where early history (e.g., initial classification or stage transitions) is no longer available, without needing to reproduce the session.

## Auto-Continue and Session Scope

The auto-continue system uses session re-enqueue rather than steering queue injection. When a status update triggers auto-continue, a new session is enqueued through the normal session queue with the same session context:

- `session_id` -- preserves thread identity
- `slug` -- preserves slug-scoped task list binding
- `task_list_id` -- preserves the CLAUDE_CODE_TASK_LIST_ID value

This ensures auto-continued work remains within the correct isolation scope. The previous approach (steering queue injection) could bypass session scoping if the agent process had already exited.

See [Reaction Semantics](reaction-semantics.md) for details on the re-enqueue design and the race condition it fixes.

## Semantic Session Routing

In addition to mechanical routing (reply-to message ID), sessions can be matched semantically. When the message drafter produces structured output, it extracts `context_summary` and `expectations` fields that describe what a session is working on and what it needs from the human. Unthreaded messages are then evaluated against sessions with expectations, and high-confidence matches are routed based on session status:

- **Active/running sessions**: The message is pushed to the session's steering queue (`push_steering_message`). The user gets an ack ("Noted — I'll incorporate this on my next checkpoint.") and the Observer picks it up at its next stop. No competing session is created.
- **Dormant sessions**: The session is resumed using the matched session_id (existing behavior).

This complements the isolation model: sessions remain isolated, but messages can find their way to the correct session even without explicit reply-to threading. See [Semantic Session Routing](semantic-session-routing.md) for full details.

## See Also

- [Semantic Session Routing](semantic-session-routing.md) -- Semantic matching of unthreaded messages to sessions with expectations
- [Scale Session Queue (Popoto + Worktrees)](scale-agent-session-queue-with-popoto-and-worktrees.md) -- The parallel execution foundation that this feature enables
- [Session Watchdog](session-watchdog.md) -- Active session monitoring that works alongside isolation
- [Bridge Workflow Gaps](bridge-workflow-gaps.md) -- Auto-continue, output classification, session logs
- GitHub Issue [#62](https://github.com/tomcounsell/ai/issues/62) -- Tracking issue with experiment details
