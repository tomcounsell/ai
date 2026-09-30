---
status: docs_complete
type: feature
appetite: Small
owner: Valor
created: 2026-05-04
tracking: https://github.com/tomcounsell/ai/issues/1272
last_comment_id:
---

# Guard Against Parallel-Session Main-Checkout Contamination

## Problem

Issue [#887](https://github.com/tomcounsell/ai/issues/887) closed the most direct version of this failure: dev sessions created via `valor-session create` without a prior `/do-plan` step bypassed worktree provisioning, causing them to run `git checkout session/{slug}` inside the main checkout. The fix added three enforcement layers (worktree provisioning failure escalation, main-checkout protection guard, PM persona prompt instruction) plus a `--slug` flag on `valor-session create`.

**Residual contamination surface (this plan addresses):** the #887 guards depend on `AgentSession.slug` being set. The current code path in `agent/session_executor.py:766` reads:

```python
if _stype == "dev" and slug and WORKTREES_DIR not in str(working_dir):
    raise RuntimeError(...)
```

A dev session **without** a slug bypasses the guard by design — the `slug and` clause short-circuits. This is the residual hole. Examples where slugless dev sessions could appear:

1. A future debugging harness that spawns a dev session without invoking `/do-plan` first
2. A test fixture that creates a real `AgentSession(session_type="dev")` without a slug
3. A new CLI entry point that forgets to require `--slug` (analogous to the `valor-session create --role pm` slug-required check that already exists at `tools/valor_session.py:361-377` for PM but is **not** enforced for `--role dev`)
4. A human collision: a programmatic write happens while a human is in the middle of an edit in the main checkout
5. A reflection job (memory-dedup, autoexperiment) or cron job that mutates the repo while a dev session is active

**Current behavior:**
The #887 guards correctly reject slugged dev sessions that resolve to the main checkout. They do not reject slugless dev sessions, do not coordinate with concurrent humans, and do not coordinate with concurrent reflection/cron writers.

**Desired outcome:**
A small, fail-open invariant that closes the slugless-dev hole without introducing any state that can go stale. The user's hard constraint is that any guard must be fail-open: a buggy implementation must not be able to wedge the system. "Do nothing programmatic" (Alternative D) is a legitimate output of this plan if no other alternative passes the no-side-effects bar.

## Freshness Check

**Baseline commit:** `5055b527c9fbe7710d7bb5dbe9a44132565e9fa6`
**Issue filed at:** 2026-05-04T09:19:46Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/agent_session_queue.py:2704-2721` (cited in #887 plan, referenced here as recon source) — **drifted**: worktree-provisioning logic moved to `agent/session_executor.py:709-758`. The three #887 enforcement layers are now at:
  - Layer 1 (escalation): `agent/session_executor.py:740-753`
  - Layer 2 (main-checkout guard): `agent/session_executor.py:762-777`
  - Layer 3 (PM persona prompt): `config/personas/project-manager.md` (still present)
- `tools/valor_session.py` `--slug` flag — **still holds** at `tools/valor_session.py:420-426` (worktree provisioning at session-create time)
- `tools/valor_session.py` PM-slug-required guard — **still holds** at lines 360-377 (PM without slug is rejected with exit 1; **dev without slug is currently NOT rejected**, which is the residual hole this plan addresses)
- `agent/worktree_manager.py:206-260` `_cleanup_stale_worktree` path-containment guard (#880) — still holds (verified)
- `scripts/update/mcp_memory.py:107-156` — `fcntl.flock(LOCK_NB)` with retry/backoff for `~/.claude.json` writes — **prior art for fail-open locking in this repo** (was not flagged in the issue's "no existing lock infrastructure" recon; technically applies to a config file, not the checkout)

**Cited sibling issues/PRs re-checked:**
- #887 — closed 2026-04-10. Three enforcement layers shipped and verified active.
- #880 — closed 2026-04-10. Path-containment guard in `_cleanup_stale_worktree` shipped and verified active.
- #875 — original repo wipe, referenced only as background.

**Commits on main since issue was filed (touching referenced files):**
- None on `agent/session_executor.py`, `tools/valor_session.py`, or `agent/worktree_manager.py` since 2026-05-04T09:19:46Z. Verified via `git log --oneline --since=...`.

**Active plans in `docs/plans/` overlapping this area:** None. `session-isolation-bypass.md` (the #887 plan) is the only adjacent plan and it is `status: Ready` (shipped).

**Notes:** The #887 recon line numbers are stale (~1000 lines of drift from a refactor that split `_execute_agent_session` into `agent/session_executor.py`). The semantic claims are intact. Update file:line references in Solution to point at the current locations.

## Prior Art

- **Issue #887**: *Session isolation bypass: PM sessions created via valor-session create operate in main checkout instead of a worktree* — landed three enforcement layers + `--slug` flag. Status: closed/shipped. **Direct precedent — this plan extends it.**
- **Issue #880**: *worktree_manager._cleanup_stale_worktree can shutil.rmtree the main repo* — landed path-containment guard. **Key precedent: invariants enforced at the operation site (not via global state) are the safer pattern in this codebase.** This plan follows the same shape.
- **Issue #875**: original catastrophic repo wipe. Background only.
- **PR #831 (closed 2026-04-08)**: *worker_key computed property routes pm/dev/teammate sessions by actual isolation level* — establishes that session_type already drives isolation routing. Strengthens Alternative A's case that the dev/non-dev split is the natural axis.
- **`scripts/update/mcp_memory.py:107-156`**: existing fail-open `fcntl.flock(LOCK_NB)` pattern in this repo with retry/backoff (50ms / 200ms / 800ms) and read-only fail-back. **Counter-evidence to the issue's "no existing lock infrastructure" recon claim.** This is a working fail-open lock pattern that already lives here, on a config file with similar concurrency shape (multiple processes mutating a shared resource).

## Research

No external research needed — this is purely about internal isolation invariants. No new libraries, APIs, or ecosystem patterns are involved. The fazm reference cited in the issue is background, not a candidate library.

## Architectural Impact

- **New dependencies**: None for the recommended path. Alternative A reuses `get_or_create_worktree()` (already imported in `agent/session_executor.py`).
- **Interface changes**: `agent/session_executor.py::_execute_agent_session()` gains one new branch in the worktree-resolution block. `tools/valor_session.py::cmd_create()` gains slug-required behavior for `--role dev` (parallels the existing PM check).
- **Coupling**: No change. Already-coupled modules (`session_executor.py` → `worktree_manager.py`) are the only ones touched.
- **Data ownership**: No change — `AgentSession.slug` and `AgentSession.working_dir` remain authoritative. Synthetic dev slugs go through the same field.
- **Reversibility**: Fully reversible — both changes are additive guards that can be removed by deleting one branch each.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

The work is two surgical edits (one in `session_executor.py`, one in `valor_session.py`) plus tests and documentation. No new infrastructure.

## Prerequisites

No prerequisites — this work has no external dependencies. #887 already shipped.

## Solution

### Comparative Analysis of Alternatives

The issue asks for an enumeration with reasoning, not a pre-chosen design. Each row is evaluated against the no-side-effects constraint (fail-open, no stale-block failure mode, no new operator burden).

| Alt | Approach | New state introduced | Stale-block possible? | Operator burden | Closes residual hole? | No-side-effects rating |
|-----|----------|---------------------|-----------------------|-----------------|----------------------|------------------------|
| **A** | Mandate worktree for every dev session (synthesize slug if missing) | Worktree dirs (already exist) | No (no global state) | None added | **Yes** for slugless dev | **PASS** |
| **B** | Filesystem directory lock + PID file + max-wait | `/tmp/valor-checkout-*.lock` dir + pid file | Yes (lock dir survives crash; PID detection is best-effort across users/containers) | Manual `rm -rf` if PID detection fails | Partially | **FAIL** — fazm itself flags this risk |
| **B'** | Redis SET NX EX 60 lock | Redis key with TTL | TTL bounds it but Redlock-style clock-skew issues exist | None | Partially | **MARGINAL** — TTL fixes stale-block but adds Redis dependency for a problem that doesn't need it |
| **C** | Optimistic concurrency detector (read HEAD + dirty set, re-check before write) | None (read-only) | No (no state) | None | Detects but doesn't prevent (catches the *second* writer) | **PASS but coverage gap** |
| **D** | Do nothing programmatic; document harder | None | N/A | None | No | **PASS** (by construction) but accepts residual risk |
| **E** | Wrapper command (`valor-checkout-exclusive`) for opt-in serialization | Redis key (same as B') under wrapper | TTL bounds it | None for legacy callers; explicit for wrapper users | Partially (only operations that opt in) | **MARGINAL** — same concerns as B', plus opt-in coverage gap |

### Cross-Cutting Questions (from issue's Solution Sketch)

The issue requires the plan to address all six. Answers below:

1. **Interaction with launchd worker auto-respawn (`KeepAlive=true`):** Alternative A has no global state to recover; relaunch is a no-op. Alternatives B/B'/E require the relaunched worker to either time out (B) or wait for TTL (B', E). **A wins.**
2. **Interaction with the existing `_cleanup_stale_worktree` guard (#880):** Alternative A simply provisions a worktree under `.worktrees/` — fully compatible. Alternatives B/B'/E acquire locks separate from worktrees; their cleanup paths must never touch worktree paths. **A wins.**
3. **Multi-machine ownership:** Out of scope per `projects.json` single-machine ownership (`docs/features/single-machine-ownership.md`). Same answer for all alternatives.
4. **Existing prior art in the repo:** **The issue's recon was incomplete.** `scripts/update/mcp_memory.py:107-156` does have a fail-open `fcntl.flock(LOCK_NB)` pattern in production. However, that pattern protects a config file (`~/.claude.json`), not the entire checkout — adapting it to lock the full repo would expand a narrow tested pattern into a broad untested one. Alternative A avoids touching this question entirely.
5. **Unit-of-locking granularity:** Alternative A locks on **slug** (worktree directory). Alternative E proposes operation-scoped. Alternatives B/B' lock on **repo-root**. Repo-root is the broadest surface and the most likely to wedge things; slug is the narrowest and matches the existing isolation contract.
6. **Failure-mode audit (adversarial scenarios):** see "Adversarial Failure-Mode Audit" subsection below for Alternative A. (Not needed for D since D introduces nothing.)

### Recommendation: **Alternative A** (mandate worktree for every dev session) + **bonus: PM-style slug-required check for `--role dev`**

**Rationale:**

- It is the **only alternative** that closes the residual hole (slugless dev sessions) without introducing any new state that can fail.
- It is **structurally identical** to the existing #887 enforcement — adding one branch to the same code path, not a new system.
- It **reuses already-tested infrastructure** (`get_or_create_worktree()` is idempotent and well-covered by `tests/unit/test_worktree_manager.py`).
- It **respects the "prevention over cleanup" feedback rule** from `feedback_prevention_over_cleanup.md` — a guard at the creation site, not a cleanup utility.
- The **PM analogue already exists**: `tools/valor_session.py:360-377` rejects PM-without-slug. Extending the same check to `--role dev` is a one-line symmetry fix that prevents future entry points from creating slugless dev sessions in the first place.
- The **cross-cutting questions favor A** in 4 of 6 cases (1, 2, 4, 5).

**Why not Alternative D (do nothing):** D leaves the slugless-dev hole and depends on agent behavior compliance for human/cron contention. The residual risk includes a nonzero chance of repeating the 2026-04-10 incident through a different code path. Given that A is single-digit lines of code with zero new state, D's "introduces nothing" advantage is not meaningfully different from A's "introduces only a synthetic slug + a worktree we already know how to manage."

**Why not Alternatives B / B' / E (locks):** All three either admit stale-block (B), depend on Redis for a problem that doesn't need a distributed primitive (B'), or punt the coverage problem to opt-in callers (E). The user's hard constraint ("a stale block is crippling") and the principle of preferring operation-site invariants over global state both rule these out.

**Why not Alternative C (optimistic detector):** C catches the second writer but doesn't prevent the first. For the worker→worker case, A prevents the contention entirely by sending each writer to its own worktree. For the human→worker case, neither A nor C fully solves it — but A reduces the contention surface to *only* slugless dev sessions, which after this plan will not exist.

### Key Elements

- **Element 1 — Worktree synthesis for slugless dev sessions:** In `agent/session_executor.py`, when `session_type == "dev"` and `slug is None`, synthesize a slug as `dev-{agent_session_id[:8]}` and provision a worktree the same way slugged sessions do today. The synthetic worktree is deleted on session completion (already-existing cleanup_after_merge path handles slugged worktrees; ephemeral synthetics need a small extension OR a periodic prune that the existing `prune_worktrees()` already provides).
- **Element 2 — CLI symmetry guard:** In `tools/valor_session.py::cmd_create()`, extend the existing PM-slug-required check (lines 360-377) to also reject `--role dev` without `--slug`. This prevents future code paths from creating slugless dev sessions in the first place. Slug auto-derivation from `issue #N` already works for PM and applies here unchanged.

### Flow

**Slugged dev session (today, unchanged):**
`valor-session create --role dev --slug X` → worktree provisioned at create time → worker picks up → `_execute_agent_session()` confirms worktree → agent runs in worktree.

**Slugless dev session (new path, was the hole):**
`valor-session create --role dev` → CLI rejects with exit 1, message: "dev sessions must include `--slug` or `issue #N` so a worktree can be provisioned." Fail-loud at the CLI layer.

**Slugless dev session through some future programmatic path that bypasses the CLI:**
Worker picks up session with `slug=None`, `session_type="dev"` → `_execute_agent_session()` synthesizes slug `dev-{aid[:8]}` → calls `get_or_create_worktree(repo_root, synthetic_slug)` → resolves to `.worktrees/dev-abcd1234/` → existing #887 main-checkout guard now passes (working_dir contains `.worktrees`).

### Technical Approach

1. **In `agent/session_executor.py` at the worktree resolution block (~line 709):**
   - Before the existing `if slug:` branch, add: `if not slug and session_type == "dev": slug = f"dev-{agent_session_id[:8]}"`
   - The synthetic slug then flows through `resolve_branch_for_stage(slug, stage=None)` and `get_or_create_worktree()` unchanged. The `_validate_slug()` check passes (`dev-` prefix + 8 hex chars matches the existing regex).
   - The existing #887 main-checkout guard at line 762-777 is unchanged but no longer has a slugless dev case to miss.

2. **In `tools/valor_session.py::cmd_create()` (~line 361):**
   - Change `if not slug and role == "pm":` to `if not slug and role in ("pm", "dev"):` (both error message and auto-derivation apply identically — both PMs and slugless devs need a slug for worktree provisioning, the message just needs minor wording adjustment to mention dev).

3. **In `agent/worktree_manager.py` (no change required):**
   - The synthetic-slug worktrees use the same path layout (`.worktrees/dev-XXXXXXXX/`), so existing `prune_worktrees()` and `cleanup_after_merge()` already handle them. No new code path needed.

4. **Synthetic-slug cleanup (verify, don't add code):** Synthetic-slug worktrees may not have a corresponding PR (the dev session may complete without ever opening one). The `prune_worktrees()` function already handles this case — it drops worktrees whose branches are gone or merged. For belt-and-suspenders, verify in a unit test that calling `cleanup_after_merge()` on a `dev-XXXXXXXX` slug doesn't error.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The existing `except Exception as e: ... if _stype == "dev": raise` block at `agent/session_executor.py:739-753` is unchanged. Synthetic-slug failures route through the same path and fail loudly. Test: simulate `get_or_create_worktree()` raising for a synthetic slug; assert `RuntimeError` propagates.
- [ ] No new `except Exception` blocks introduced.

### Empty/Invalid Input Handling
- [ ] `agent_session_id` is None: synthetic slug becomes `dev-None`. Should be guarded — raise rather than create a worktree named `dev-None`. Add a precondition: if `agent_session_id` is missing, raise.
- [ ] `agent_session_id` is shorter than 8 chars: synthetic slug uses what's available. The existing `_validate_slug()` regex permits anything matching `[A-Za-z0-9._-]+`, so `dev-XX` would pass. Acceptable.
- [ ] CLI `--role dev` with no slug and no `issue #N`: must exit 1 with a clear message.
- [ ] CLI `--role dev` with `--slug ""`: already rejected by `_validate_slug()`; verify error surfaces.

### Error State Rendering
- [ ] When the synthesis path fails (e.g., disk full creating worktree), the existing `[branch-mapping] FATAL` log fires with the synthetic slug visible. Verify in a test that the log line includes `slug=dev-...`.
- [ ] CLI rejection of `--role dev` without slug must exit 1 (not 0) and write the message to stderr (not stdout).

## Test Impact

- [ ] `tests/unit/test_session_isolation_bypass.py` (created in #887 plan) — UPDATE: add three test cases:
  - `test_dev_session_no_slug_synthesizes_worktree` — assert slugless dev session gets `dev-{aid[:8]}` slug and worktree
  - `test_dev_session_no_slug_no_agent_session_id_raises` — assert missing aid raises RuntimeError
  - `test_synthetic_slug_worktree_pruneable` — assert `prune_worktrees()` includes `dev-XXXXXXXX` worktrees
- [ ] `tests/unit/test_valor_session.py` (or wherever CLI tests live) — UPDATE: add `test_create_dev_role_requires_slug` paralleling the existing PM check
- [ ] `tests/unit/test_worktree_manager.py` — no changes needed; existing slug validation tests cover the synthetic format
- [ ] No DELETE or REPLACE dispositions — all changes are additive

## Rabbit Holes

- **Trying to detect human-vs-agent collisions in the main checkout.** Alternative C-style detection is intriguing but doesn't prevent the first writer. The plan deliberately stays out of human-collision territory; that is a separate problem requiring a Stop-hook (Alternative D's residual mechanism), which is out of scope here.
- **Adding a Redis advisory lock to the dev-session execution.** Alternative B' was rejected for good reasons (stale-block via clock skew, adding Redis dependency for a problem that doesn't need a distributed primitive). Resist the temptation to "also add a lock just in case" — it expands the failure surface for marginal correctness gain.
- **Refactoring `_execute_agent_session()` to extract the worktree-resolution block.** Tempting because the block is getting long, but a refactor expands review scope and adds risk. Save it for a separate chore PR.
- **Migrating the slugless PM check to a shared helper.** The PM check at lines 360-377 and the new dev check could be deduplicated. Don't — readability of the two distinct error messages is worth the duplication. Two short branches are clearer than a parameterized helper.
- **Ephemeral worktree garbage collection on a timer.** `prune_worktrees()` already handles this; don't add a second mechanism.

## Risks

### Risk 1: Synthetic slug collisions
**Impact:** Two dev sessions with `agent_session_id` prefixes that happen to collide in the first 8 chars would race for the same worktree.
**Mitigation:** `agent_session_id` is a UUID4; the probability of collision in the first 8 hex chars is ~1 in 4 billion. `get_or_create_worktree()` is idempotent — even if collision occurred, both sessions would land in the same worktree, which is no worse than a slug collision today. Acceptable.

### Risk 2: Synthetic worktree never gets cleaned up
**Impact:** `.worktrees/dev-XXXXXXXX/` accumulates if the dev session never opens a PR (and thus never triggers `cleanup_after_merge`).
**Mitigation:** `prune_worktrees()` runs on a schedule (verify) and removes worktrees whose branches are gone. If the synthetic branch isn't pushed (likely the case for ephemeral dev sessions), local-only branches with no remote tracking are pruneable. **Open question — needs verification:** does `prune_worktrees()` actually trigger automatically? If not, we need to wire it into the session-completion path. Spike-1 covers this.

### Risk 3: CLI breakage for downstream callers
**Impact:** Any script that calls `valor-session create --role dev` without `--slug` today will start exiting 1.
**Mitigation:** Search the repo and the `scripts/` directory for `valor-session create --role dev` invocations without `--slug`. Surface findings in the plan; update or remove them as part of this work.

## Race Conditions

### Race 1: Concurrent synthetic-slug worktree creation for the same slug
**Location:** `agent/worktree_manager.py::create_worktree()`
**Trigger:** Two dev sessions with the same `agent_session_id[:8]` prefix start simultaneously (probability ~10⁻⁹).
**Data prerequisite:** The worktree directory must not exist for `git worktree add` to succeed.
**State prerequisite:** Git's worktree list must reflect filesystem state.
**Mitigation:** `get_or_create_worktree()` checks `worktree_dir.exists()` first. The `git worktree add` command itself is atomic at the filesystem level. Two concurrent calls result in one success and one early-return.

### Race 2: Synthetic slug computed before AgentSession is fully persisted
**Location:** `agent/session_executor.py` synthetic-slug block
**Trigger:** Worker picks up an AgentSession before its `agent_session_id` field is persisted.
**Data prerequisite:** `agent_session_id` must be set on the model.
**State prerequisite:** Popoto save must have completed before the worker reads.
**Mitigation:** AgentSession is enqueued only after save completes (Popoto convention). Add a precondition check: if `getattr(session, 'agent_session_id', None)` is falsy, raise — same shape as the existing executor-guard at `agent/session_executor.py:641-693`.

## Adversarial Failure-Mode Audit (required for non-D alternative)

The issue requires this audit since this plan picks Alternative A (not D). Each scenario asks: "Can a buggy implementation of this plan leave the system *worse off* than no plan at all?"

| Adversarial scenario | Outcome under this plan | Worse than today? |
|----------------------|-------------------------|-------------------|
| `get_or_create_worktree()` raises an unhandled exception for synthetic slug | Existing `if _stype == "dev": raise RuntimeError` block fires — session fails loudly. Same behavior as today for slugged sessions. | No — same fail-loud shape |
| Worker crashes mid-creation of synthetic worktree | Filesystem may have a partial `.worktrees/dev-XX/` dir. `prune_worktrees()` or `_cleanup_stale_worktree()` (#880-protected) cleans it up. | No — same cleanup paths |
| Two workers race to create the same synthetic slug | One wins, the other gets the existing worktree (idempotent). | No — race already handled |
| `agent_session_id` field gets set to a string that breaks `_validate_slug()` (e.g., contains `/`) | Synthetic slug = `dev-{garbage}`. `_validate_slug()` raises. Session fails loudly. | No — fail-loud, no contamination |
| Disk fills up creating synthetic worktree | `git worktree add` fails. `_execute_agent_session()` catches, sees `_stype == "dev"`, raises. Session fails loudly. | No — fail-loud |
| Synthetic worktree directory gets `chmod -R 000`'d by a malicious actor | `git worktree add` fails. Session fails loudly. | No — fail-loud |
| The synthetic-slug branch already exists on remote with conflicting history | `git worktree add` fails or creates the branch in the wrong state. Existing `_cleanup_stale_worktree()` guard fires. | No — existing guard catches |
| The CLI dev-slug check incorrectly rejects a valid invocation | Caller sees exit 1 + clear error message. Caller adds `--slug` and retries. | No — fail-loud at CLI |

**Conclusion:** No adversarial scenario produces a worse-than-today outcome. The plan satisfies the no-side-effects bar.

## No-Gos (Out of Scope)

- **Fail-closed locking** — explicitly forbidden by the user's hard constraint. No design that blocks on uncertain state.
- **Operator manual-cleanup steps** — no instructions like "if you see a stale lock, run `rm -rf /tmp/foo`" anywhere in CLAUDE.md, plans, or runbooks.
- **Multi-machine coordination** — out of scope per `projects.json` single-machine ownership.
- **Stop-hook for human-in-main-checkout warnings** — Alternative D's residual mechanism. Worth a separate plan if the residual risk after this plan still feels too high.
- **Optimistic concurrency detector (Alternative C)** — covered by this plan's worktree-routing approach for worker→worker, not solving worker→human (a Stop-hook problem).
- **Redis SET NX EX 60 lock (Alternative B')** — adds a Redis dependency for a problem that doesn't need a distributed primitive.
- **Filesystem directory locks (Alternative B)** — fazm itself flags the stale-block risk; user constraint rules it out.
- **Wrapper command `valor-checkout-exclusive` (Alternative E)** — opt-in coverage means it doesn't close the residual hole; deferred.
- **Refactoring `_execute_agent_session()` for line-count reasons** — out of scope (separate chore).

## Update System

No update system changes required — this is purely internal session-execution logic that runs on each machine independently. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is an internal change to the session executor and CLI. No new MCP servers, tool wrappers, or bridge modifications. The only externally-visible change is that `valor-session create --role dev` (without `--slug` or `issue #N`) now exits 1 instead of starting a session in the main checkout.

## Documentation

- [ ] Update `docs/features/session-isolation.md` § "Worktree Enforcement for Dev Sessions (Issue #887)" — add a subsection for the slugless-dev path: "Synthetic Slugs for Slugless Dev Sessions (Issue #1272)" describing the `dev-{aid[:8]}` synthesis and CLI rejection of slugless `--role dev`.
- [ ] Update `tools/valor_session.py` docstring on `cmd_create()` — note that both PM and dev roles require a slug.
- [ ] Inline code comments on the synthetic-slug block in `agent/session_executor.py` referencing issue #1272 and explaining the design rationale (Alternative A from this plan).
- [ ] Add a row to the `docs/features/README.md` index linking the updated session-isolation entry to issue #1272 (only if the index references #887; otherwise no index change needed).

## Success Criteria

- [ ] A dev session with `slug=None` started via the worker (bypassing the CLI) is routed to a synthesized worktree under `.worktrees/dev-XXXXXXXX/` rather than the main checkout
- [ ] `valor-session create --role dev` (without `--slug` and without `issue #N` in the message) exits 1 with a clear error message
- [ ] `valor-session create --role dev --slug X` continues to work unchanged
- [ ] `valor-session create --role dev --message "Fix issue #42"` (auto-derived slug) continues to work unchanged
- [ ] The existing #887 main-checkout protection guard at `agent/session_executor.py:762-777` no longer has a code path where a dev session can reach it with `slug=None`
- [ ] `prune_worktrees()` cleans up `.worktrees/dev-XXXXXXXX/` directories whose branches are gone
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] No regressions in `tests/unit/test_session_isolation_bypass.py`
- [ ] No regressions in `tests/unit/test_worktree_manager.py`

## Team Orchestration

### Team Members

- **Builder (synthesis)**
  - Name: synthesis-builder
  - Role: Implement synthetic slug for slugless dev sessions in session_executor + CLI symmetry guard in valor_session
  - Agent Type: builder
  - Resume: true

- **Validator (synthesis-check)**
  - Name: synthesis-validator
  - Role: Verify the residual hole is closed and #887 guards still pass
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using core tier: builder + validator pair. Small appetite, no specialists needed.

## Step by Step Tasks

### 1. Verify prune_worktrees runs on a schedule (spike-style task)
- **Task ID**: spike-prune-schedule
- **Depends On**: none
- **Validates**: manual code-read
- **Assigned To**: synthesis-builder
- **Agent Type**: builder
- **Parallel**: true
- Read `agent/worktree_manager.py::prune_worktrees()` and grep for callers
- If it runs on a schedule (cron, launchd, reflection), note the cadence in the plan
- If it does NOT run automatically, add a one-line task: trigger `prune_worktrees()` from the dev-session completion path (already-instrumented hook)
- Update Risk 2 mitigation with the finding

### 2. Audit downstream callers of valor-session create --role dev
- **Task ID**: spike-cli-callers
- **Depends On**: none
- **Validates**: manual code-read
- **Assigned To**: synthesis-builder
- **Agent Type**: builder
- **Parallel**: true
- `grep -rn 'valor-session create' --include='*.sh' --include='*.py' --include='*.md' .`
- Identify any caller that uses `--role dev` without `--slug`
- For each: either update to add `--slug X` or note as "ad-hoc human use, will see clear error"

### 3. Implement synthetic-slug branch in session_executor
- **Task ID**: build-synthetic-slug
- **Depends On**: spike-prune-schedule, spike-cli-callers
- **Validates**: tests/unit/test_session_isolation_bypass.py (extend)
- **Assigned To**: synthesis-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/session_executor.py`, immediately before the existing `if slug:` branch (~line 709):
  - Add: precondition check that `agent_session_id` is set; raise if missing
  - Add: `if not slug and session_type == "dev": slug = f"dev-{agent_session_id[:8]}"`
  - Add inline comment referencing issue #1272 and Alternative A rationale
- Extend `tests/unit/test_session_isolation_bypass.py` with three new test cases:
  - `test_dev_session_no_slug_synthesizes_worktree`
  - `test_dev_session_no_slug_no_agent_session_id_raises`
  - `test_synthetic_slug_worktree_pruneable`

### 4. Implement CLI symmetry guard for --role dev
- **Task ID**: build-cli-dev-slug
- **Depends On**: spike-cli-callers
- **Validates**: tests/unit/test_valor_session.py (or equivalent)
- **Assigned To**: synthesis-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/valor_session.py::cmd_create()` lines 360-377, change `if not slug and role == "pm":` to `if not slug and role in ("pm", "dev"):`
- Update the error message to mention both roles: "PM and dev sessions must be created with --slug ..."
- Add a unit test `test_create_dev_role_requires_slug` paralleling the existing PM check

### 5. Update documentation
- **Task ID**: document-feature
- **Depends On**: build-synthetic-slug, build-cli-dev-slug
- **Assigned To**: synthesis-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/session-isolation.md` with the new subsection
- Update `tools/valor_session.py::cmd_create()` docstring
- Verify no other docs need surgical updates

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-synthetic-slug, build-cli-dev-slug, document-feature
- **Assigned To**: synthesis-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_session_isolation_bypass.py -v`
- Run `pytest tests/unit/test_worktree_manager.py -v` (regression)
- Run `pytest tests/unit/ -k "valor_session" -v` (CLI test)
- Run `python -m ruff format --check .`
- Verify CLI: `python -m tools.valor_session create --role dev --message "no slug here"` exits 1
- Verify CLI: `python -m tools.valor_session create --role dev --slug test-1272 --help` does not exit 1
- Generate validation report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Synthesis tests pass | `pytest tests/unit/test_session_isolation_bypass.py -v` | exit code 0 |
| Worktree regression clean | `pytest tests/unit/test_worktree_manager.py -v` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| CLI rejects slugless dev | `python -m tools.valor_session create --role dev --message "test"` | exit code 1 |
| CLI accepts slugged dev | `python -m tools.valor_session create --role dev --slug test --message "test" 2>&1 \| head -1` | output does not contain "Error" |
| #887 guard still active | `grep -n 'worktree-guard.*Dev session' agent/session_executor.py` | exit code 0 |
| Synthetic slug pattern in code | `grep -n 'dev-.*agent_session_id' agent/session_executor.py` | exit code 0 |

## Critique Results

War-room critique 2026-05-04. Verdict: **READY TO BUILD (with concerns)**. 4 CONCERNs, 3 NITs, 0 BLOCKERs.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic | `prune_worktrees()` (worktree_manager.py:535-543) only runs `git worktree prune` (removes stale references, not directories). No scheduled cleanup of orphaned `.worktrees/dev-XXXXXXXX/` exists. Risk 2 mitigation is mistaken. | Drop the spike-prune-schedule task and replace with an explicit cleanup hook: in `agent/session_executor.py` session-completion path (the existing `finally` block around line 619), if the session's slug starts with `dev-` (i.e., a synthesized slug) AND the session reached a terminal state, call `cleanup_after_merge(repo_root, slug)` directly rather than waiting for a PR merge. | Synthesized slugs are identifiable by the literal prefix `dev-` followed by 8 hex chars (`re.match(r'^dev-[0-9a-f]{8}$', slug)`). The existing `cleanup_after_merge()` is idempotent and safe to call without a PR; it removes the worktree dir + local branch. Place the call inside `try/except Exception as e: logger.warning(...)` so cleanup failures do NOT propagate as session failures. |
| CONCERN | Operator | No log/metric distinguishes "synthetic slug allocated" from regular slugged dev sessions. Operators cannot validate the hypothesis post-deploy. | Add `logger.info` at the synthesis site with structured fields. Add a `[synthetic-slug]` log marker so reflection scans can count incidents. | At the synthesis line in `agent/session_executor.py`, emit: `logger.info(f"[synthetic-slug] Allocated synthetic slug {slug} for slugless dev session {agent_session_id} (issue #1272)")`. The `[synthetic-slug]` literal token is the grep handle for daily/weekly reflections — it MUST be a stable prefix, not interpolated, so log scans can count occurrences without false positives. |
| CONCERN | Adversary | The plan's precondition "raise if `agent_session_id` is None" needs to fire BEFORE the synthesis line. The existing executor-guard at `agent/session_executor.py:641-693` checks `working_dir` and `session_id` but NOT `agent_session_id`. A future spawn site producing `agent_session_id=None` would crash with `TypeError: 'NoneType' object is not subscriptable` at `agent_session_id[:8]`. | Extend the existing executor-guard at `session_executor.py:641-693` to also check `session_type == "dev" and agent_session_id is None and slug is None`. Fail loudly via `finalize_session(session, "failed", reason=...)` like the other guards in that block. | The guard signature: `if _stype == "dev" and getattr(session, "agent_session_id", None) is None and getattr(session, "slug", None) is None:`. The conjunction `and slug is None` matters — a slugged dev session with no agent_session_id is an upstream bug that should fail in a different way; only the slugless+aid-less combo enters the synthesis path. The error message must say `"slugless dev session requires agent_session_id for synthetic slug derivation (issue #1272)"`. |
| CONCERN | Adversary | spike-cli-callers (Task 2) audit only covers `.sh`, `.py`, `.md`. It misses test fixtures (`conftest.py`), launchd `.plist` files, and operator runbooks in `~/Desktop/Valor/`. Risk 3 (CLI breakage) coverage is incomplete. | Expand the audit grep in Task 2 to: `grep -rn 'valor-session create.*--role dev' --include='*.sh' --include='*.py' --include='*.md' --include='*.plist' --include='*.toml' --include='*.yaml' --include='*.yml' . ~/Desktop/Valor/ 2>/dev/null` | Note the trailing `2>/dev/null` — `~/Desktop/Valor/` may not be readable on machines without iCloud sync, and that's expected; the script must not fail when the path doesn't exist. The plan's CLI rejection is at `tools/valor_session.py:361`, so the regex must be flexible: callers may write `valor-session create --role dev` or `python -m tools.valor_session create --role dev` — `valor-session create.*--role dev` covers both via the `.*`. |
| CONCERN | Consistency Auditor | Solution claims synthetic worktrees flow through "existing `prune_worktrees()` and `cleanup_after_merge()`." Risk 2 acknowledges "synthetic worktree never gets cleaned up if dev session never opens a PR." These contradict. | Resolved by the Skeptic's Implementation Note above (explicit cleanup hook on session completion). Update Solution Element 1 to remove the claim that existing cleanup paths already handle synthetic worktrees, and reference the new explicit hook instead. | The Solution wording change: replace "existing `prune_worktrees()` and `cleanup_after_merge()` already handle them" with "explicit `cleanup_after_merge(repo_root, slug)` call in the session-completion finally block, gated on slug matching `^dev-[0-9a-f]{8}$`." |
| NIT | Archaeologist | Prior Art's `scripts/update/mcp_memory.py` reference is informational, not load-bearing. | Keep as-is. | (NITs exempt from Implementation Notes.) |
| NIT | Simplifier | Task 1 (spike-prune-schedule) leaves a build-time gap that the critique already resolved. | Drop the spike entirely; replace with the explicit cleanup hook task per Skeptic's Implementation Note. | (NITs exempt from Implementation Notes.) |
| NIT | User | Success Criteria don't validate the CLI error message content, only the exit code. | Add a criterion: "CLI rejection message includes the substring `dev sessions must be created with --slug` for grep-ability." | (NITs exempt from Implementation Notes.) |

---

## Open Questions

1. **Does `prune_worktrees()` run automatically?** Risk 2 hinges on this. spike-prune-schedule (Task 1) resolves it. If it does not run automatically, the plan needs a small addition: trigger it on dev-session completion or via the existing reflection schedule. This is a finding, not a blocker — the fix is one line in the completion path.

2. **Are there any existing callers of `valor-session create --role dev` without `--slug`?** spike-cli-callers (Task 2) resolves it. If there are, this plan's Risk 3 needs to be addressed by updating those callers. Likely candidates: test fixtures, debug scripts, ad-hoc human invocations from operator runbooks. None of these are in the bridge's hot path.
