# Resilience Simplification Program — Three Tiers (DRAFT)

```yaml
status: draft
created: 2026-07-10
tracking: none yet — draft pending scope review against open issues
```

> **DRAFT.** Synthesized from a review of the 21 bug-labeled issues closed 2026-07-08 → 2026-07-10
> (#1915 #1916 #1917 #1922 #1932 #1933 #1938 #1942 #1944 #1950 #1954 #1955 #1958 #1959 #1960
> #1961 #1962 #1963 #1964 #1971 #1980) and their ~26 merged PRs, plus three code deep-dives into
> the touched subsystems. Each tier item names the bugs it would have prevented. This is a
> program plan; each tier item ships as its own issue/plan/PR through the normal SDLC pipeline.

## Problem

The last two days closed 21 bugs concentrated in four clusters: session liveness/recovery
misclassification, SDLC pipeline distributed-state defects, stale-snapshot decay, and silent
fail-open degradation. Two of the bugs were caused by fixes for two others in the same window
(#1954's lock caused #1971; #1916 was the stage machine rejecting a legitimate recorder).
The failure-handling layer has grown by accretion: each incident adds a guard that reads yet
another partial signal. Five meta-patterns recur:

1. **Truth inferred from duplicated partial signals** — 3 pid fields for one subprocess
   (#1938); 4 forked "has progress" predicates (#1962, #1917); 12 pipeline state surfaces
   that can disagree (#1932, #1944).
2. **Destructive actions without confirmed preconditions** — worktree deleted under a live
   process (#1938), canned message substituted over a valid reply (#1980).
3. **Guards added at symptom sites instead of strengthening invariants** — router rows
   8b→8c→8d all patch "verdict not atomic with stage completion"; the "PR exists → never
   re-plan" invariant is re-guarded in 9+ predicates; 4 advisory staleness triggers on one
   baseline.
4. **Invariants enforced by prompt convention, not structure** — `dispatch record`,
   `SDLC_HOLDER_TOKEN` re-export, stage self-marking, "run children synchronously"
   (#1971, #1915, #1944).
5. **No shared degradation contract** — three dialects coexist: fail-closed-to-safe-default
   (`pr_shape_classify`), loud-log-silent-return (`impact_finder`, baseline staleness),
   silent-by-design (`_baseline_post_merge_update`). Only the first is written down (#1950,
   #1959).

Goal: fewer, stronger invariants owned in one place, with destructive actions gated on
confirmed facts — so future fixes land as structure, not scar tissue.

## Tier 1 — Small, high-leverage, near-zero risk (≤1–2 days each)

- [ ] **T1.1 `ExitReason` StrEnum** in `agent/session_runner/router.py`: every member declares
      `is_clean` / `wrapup_eligible` / `is_anomaly`; the three frozensets
      (`CLEAN_EXIT_REASONS` etc.) become derivations, so an unclassified reason cannot be
      added. Role-driver failure slugs become `TurnFailure(reason, detail)` so
      `headless_thinking_corruption: {traceback}` stops being an identity string. String
      values unchanged for telemetry continuity. *Prevents: #1922 class.*
- [ ] **T1.2 `SessionEvidence` helpers** in `agent/session_runner/liveness.py`:
      `has_started(entry)` (sticky triple: `turn_count`/`log_path`/`claude_session_uuid` +
      `sdk_ever_output`) and `has_recent_progress(entry, *, window, now)`. All four forked
      predicates (`session_health._has_progress`, `_never_started_past_grace`,
      `session_stall_classifier._has_demonstrable_progress`,
      `crash_signature._has_demonstrable_progress`) call these. Presence-vs-freshness becomes
      an explicit parameter, not a forked function. **Scope addition from open-issue review:**
      evidence must be *attempt-scoped* — #1979 showed a sticky `response_delivered_at`
      from a prior attempt force-finalizing a resumed session mid-run. **#1979 SHIPPED
      (PR #2006, merged 2026-07-10 07:41): the delivery guard is now epoch-scoped by
      timestamp comparison (`response_delivered_at >= started_at`), no new field.** T1.2
      generalizes that landed pattern: extend the same run-epoch anchor to the other
      sticky per-run signals so the *class* can't recur on the next field, rather than
      introducing a parallel counter concept. Absorb `test_delivery_guard_resume_epoch.py`
      unchanged; do not refactor the just-landed guard. Fix the 3 pre-existing `test_session_heartbeat_progress.py`
      failures (open #1983) as part of this item — they pin the exact predicates being
      unified. *Prevents: #1962, #1917, the #1979 class.*
- [ ] **T1.3 `ArtifactEnvelope`** in `scripts/_baseline_common.py`: writers stamp
      `{generated_at, commit, generated_by, runs, degraded, max_age_days,
      max_commit_distance}`; one shared `staleness(envelope)` used by both
      `baseline_gate.format_staleness_warning` and the refresh-check reflection (deleting the
      reflection's divergent reimplementation). `/do-merge` invokes the gate with
      `--strict-freshness`: stale/degraded ⇒ refuse to gate and demand refresh, instead of
      warn-and-mis-block. `refresh_test_baseline.py` stamps `degraded: true` instead of
      writing an unmarked degraded file. *Prevents: #1933, #1965; fixes the currently-live
      `runs: 1` baseline.*
- [ ] **T1.4 Degraded-result metadata in impact finder**: `find_affected` returns
      `(results, meta)` with mandatory `degraded: bool`, `reason`, `rerank_failures`,
      `candidates` set on every silent branch (no key, empty index, embed failure, both
      fallback paths, partial failure). Callers must branch on it. *Prevents: #1950;
      replaces its bespoke `failure_count` tuple plumbing.*
- [ ] **T1.5 Repo-wide silent-failure lint**: ruff S110/S112 or an AST validator under
      `.claude/hooks/validators/` (pattern: `validate_no_raw_redis_delete.py`), with an
      explicit allowlist file for by-design-silent sites (memory ops). Delete
      `TestNoSilentPassRemaining`'s 7-function string scan; keep the behavioral caplog tests.
      ~87 `except Exception: pass` sites need triage into fix vs allowlist. *Prevents: #1959
      and every future instance at write time.*
- [ ] **T1.6 Import-error fast-expiry + API contract test**: (a) `baseline_gate.py` never
      allowlists `import_error` baseline entries older than a short window (~3 days /
      30 commits via T1.3's envelope) — an import error on main is always rot, never flake;
      (b) one contract-test module snapshots the public API surface tests depend on
      (`inspect.signature` of `AgentSession.create_eng` and peers) so a rename fails one
      designated test with a named message. *Prevents: #1958's 18-test pile-up, #1933's
      `_build_draft_prompt` rot.*
- [ ] **T1.7 Single-writer `pr_number` + delete dead `branch_exists`**: `/do-build` records
      the PR number at creation; the only fallback is `gh pr list --head session/{slug}`
      (live refs); delete the `--search` rung entirely. Open #1987 upgrades this from
      hardening to bugfix: `--search "#N"` is fuzzy text search and **false-matched an
      unrelated PR** (#1984 returned for issue #1950), routing `next-skill` toward review of
      the wrong PR; the #1950 pipeline is currently blocked on it. If any text-search
      fallback survives, it must validate a word-boundary `Closes/Fixes #{N}` in the PR body
      before being trusted. Fix or delete `sdlc_next_skill.py:136`'s `branch_exists`, which
      checks branch shape `session/sdlc-{N}` that this repo never creates (signal permanently
      False). *Prevents: #1915's duplicate-PR contribution, #1987; removes a latent row-5
      misroute. Closes: #1987.*
- [ ] **T1.8 Definition-site constant invariants**: module-level `_assert_distinct()` at
      import in `bridge/response.py` for reaction-emoji distinctness and
      `VALIDATED ∩ INVALID = ∅`, sharing one helper with the existing test for
      lazily-resolved constants. *Prevents: #1961 in every environment, not just where tests
      run.*

## Tier 2 — Structural, medium effort (~2–5 days each)

- [ ] **T2.1 Pipeline `run_id`**: mint one logical run identity at `ensure_session`
      (create-or-adopt), stored on the AgentSession (`active_run_id`). Issue lock payload,
      dispatch records, and worktree lease key off it; `sdlc-tool` subprocesses resolve it
      from the session record they already fetch. Deletes `_process_holder_token()`
      per-process uuid, the `SDLC_HOLDER_TOKEN` env seam, the `data/.sdlc_run/` file, and the
      three re-export prose blocks in the skill bodies. Worker and local supervisor become
      the same code path; a second supervisor on the same issue becomes detectable (live
      foreign `active_run_id`). *Prevents: #1971 outright; hardens #1954/#1915.*
- [ ] **T2.2 Merge gate as single terminal enforcement point**: the merge-guard hook
      evaluates the real predicate (PR OPEN/MERGEABLE/CLEAN + CI green + SHA-fresh APPROVED
      verdict + DOCS marker completed-or-legitimately-skipped + issue link) instead of
      checking `data/merge_authorized_{PR}` file existence. Router terminal rows (G6, 10,
      10b) collapse to "PR exists, nothing else derivable → dispatch /do-merge"; delete row
      10b's stage-states-unavailable weakening. *Prevents: #1944 by construction for every
      router-bypassing path (forks, raw `gh pr merge`).*
- [ ] **T2.3 One subprocess lease**: collapse `claude_pid` / `pm_pid` / `harness_pid` to
      `live_pgid` + `pid_generation`, written only at the runner's spawn/exit seam
      (`_on_turn_spawn` / `_clear_claude_pid`); legacy conversational path routes through the
      same helper. `pm_pid`/`harness_pid` become dashboard projections. Readers
      (`_confirm_subprocess_dead`, `_sweep_dead_worker_sessions`, `find_by_claude_pid`) read
      the one field; the generation rejects stale reads (PID-reuse guard for free). Keep
      `claude_pid` as a read-through alias for one release. *Prevents: #1938's writer/reader
      field mismatch becoming possible again.*
- [ ] **T2.4 `HarnessResult` struct**: `get_response_via_harness` returns
      `HarnessResult(text, result_event_fired, returncode, claude_uuid, stderr_snippet,
      invocation)` instead of a bare string plus `on_exit_status`/`on_init` side channels and
      module dicts. Role-driver's `exit_statuses[-1]` reconstruction and
      `_get_prior_session_uuid` side-channel read disappear; the #1980 retry gate becomes a
      plain field test. Keep callbacks as optional taps during transition.
      *Prevents: #1980's classification half; the residual #1916-class inference.*

## Tier 3 — Deep fixes (1–2 weeks each; after Tiers 1–2 shrink the surface)

- [ ] **T3.1 Route on artifact state, not dispatch trajectory**: critique verdicts pinned to
      the plan-body hash; review verdicts pinned to the PR head SHA (stored in the verdict
      record). "A valid verdict for this artifact exists" replaces `last_dispatched_skill`
      matching and both wall-clock staleness comparators. Deletes recovery rows
      2b/2c/8b/8c/8d and closes the documented stale-review-after-patch hole
      (`.claude/skills/sdlc/SKILL.md` "Known gap") — a crashed review simply *is* "no valid
      verdict for this SHA". Dispatch history remains solely for G4 oscillation counting.
      **Design constraint from open-issue review (#1760 conflict):** a naive hash makes the
      known PLAN↔CRITIQUE non-convergence loop *worse* — #1760 documents that the current
      frontmatter-inclusive hash re-stales a clean verdict on every notes-only revision pass
      (revision writes `revision_applied: true` + embeds nit notes → hash busts → verdict
      stale → re-critique → forever). The verdict-pinning hash must cover **normative plan
      content only** (exclude frontmatter and the appended critique-notes section), and
      `revision_applied: true` after a `READY TO BUILD (with concerns)` verdict must count as
      convergence toward BUILD, never as re-staling. Open #1871 (G5 fast-path dispatches
      /do-build while `plan_revising=true`) is the same predicate family — absorb it here.
      *Prevents: all four #1932 gaps and the category. Resolves: #1760, #1871. Synergy:
      #1267 (outcome verification — same "trust ground truth, not claims" principle).*
- [ ] **T3.2 One kill service** (`agent/process_reaper.py`):
      `reap_group(pgid, *, mode, timeout) -> KillOutcome(confirmed_dead, signal_sent)` in
      sync and async-offloaded variants; `_confirm_subprocess_dead`, `_reap_turn_group`,
      `_kill_turn`, and the orphan-reap SIGTERM+staged-SIGKILL delegate to it. Enforced by
      signature: every destructive follow-up (worktree delete, requeue, finalize-failed,
      canned-message substitution) takes a `KillOutcome`, never a re-derived bool; the
      `runner_reap_failed` marker becomes the standard persistence of
      `confirmed_dead=False`. Preserve the runner's uninterruptible-sync property as a
      distinct mode; land call-site-by-call-site. *Prevents: both halves of #1938
      structurally.*
- [ ] **T3.3 Ownership lease for the orphan net**: `owner_worker_id` + lease renewal
      piggybacked on the existing 60s heartbeat save. "Orphaned running row" = lease expired
      — one cross-process fact — replacing the #944 inference from process-local dicts
      (`worker_alive && in_scope_handle is None && !_has_progress`) plus the #1962 guard
      stack. `_has_progress` demotes to a hang detector for owned sessions. Run log-only
      alongside the existing branch for one release before switching the recovery trigger.
      **Alignment from open-issue review:** this is the same lease-TTL architecture #1815
      (resilience workstream, CRITICAL) prescribes for slot reclamation — implement one lease
      primitive shared by both, not two. Honor #1868's finding when doing so: lease reads
      must distinguish "not found" (reclaim) from "transient Redis error" (do nothing) —
      fail-open vs fail-closed is an explicit per-decision choice, not an accident. #1312
      (bridge enqueues work with no live worker) becomes trivially detectable once worker
      leases exist. *Prevents: #1962 directly; the shared-worker_key ambiguity. Advances:
      #1815, #1868, #1312.*
- [ ] **T3.4 Event-sourced stage log**: stage statuses become a pure fold over an append-only
      event list `{stage, status, at, run_id, source}`; `derive_from_durable_signals` becomes
      another input to the same fold. Marker writes are never rejected — out-of-order
      observations are recorded and flagged in the derivation, not bounced with exit 1.
      Replaces `start_stage` ordering enforcement + `backfill_predecessors` machinery, the
      `_save()` preserve-unowned-keys merge, and most optimistic-retry plumbing. The
      substrate half-exists (`stage_states` is already a view over `session_events`).
      **Scope addition from open-issue review:** design this to satisfy #1629 (durable
      per-project SDLC state that survives Redis TTL/restarts) rather than letting #1629 add
      a 13th state surface — the append-only event log, exported past TTL (session-archive
      pattern already exists in `data/session_archive.db`), *is* the durable artifact.
      One design, one owner. *Prevents: #1916 class; the 8d "crash can leave either marker"
      ambiguity. Resolves: #1629.*

## Live defects found during this review (fix now, independent of tiers)

- [ ] `data/main_test_baseline.json` is currently degraded (`runs: 1`, below
      `MIN_USABLE_RUNS_FOR_FLAKY_DETECTION=2`) with no downstream-visible marker; provenance
      reads `generated_by: "python --merge"` (argv-join drops the script name).
- [ ] The baseline-refresh reflection (`test_baseline_refresh_check`) is not registered in
      this machine's `reflections.yaml` — the #1933 anti-recurrence mechanism has its own
      deployment gap.
- [ ] `flaky` baseline entries never decay (`apply_decay` only ages `real`), so a
      flaky-turned-real test is permanently allowlisted.
- [ ] `sdlc_next_skill.py:136` `branch_exists` checks `session/sdlc-{N}`, a branch shape the
      repo never creates — signal permanently False (folded into T1.7).

## Open-Issue Cross-Review (2026-07-10)

All ~38 open issues were reviewed against this plan. Beyond the per-item scope additions
already folded in above (#1979/#1983 → T1.2; #1987 → T1.7; #1760/#1871/#1267 → T3.1;
#1815/#1868/#1312 → T3.3; #1629 → T3.4), three program-level relationships need explicit
sequencing, plus one candidate scope extension.

**Supersession rule (owner direction, 2026-07-10):** we know substantially more now than
when most of these issues were filed — line references are dead post-teardown, suspected
mechanisms have been confirmed or refuted, and several "solution sketches" predate the
patterns this plan names. When an item here absorbs an open issue, the *plan's* framing
wins: the implementing issue/plan restates current understanding and links the old issue as
history, rather than inheriting its body as spec. Close absorbed issues with a pointer here
instead of leaving them open as drifting duplicates.

### Sibling programs — coordinate, don't duplicate

- **#1926 (post-teardown scar-tissue removal)** is this plan's philosophical sibling with an
  owner-directed *deletion-first* posture: cut machinery aggressively, keep happy path +
  Sentry, log removed defenses in a ledger, re-add targeted fixes only when Sentry proves a
  class recurs. This plan is *consolidation-first*. They compose in one order only:
  **run #1926's pruning pass over the liveness/watchdog fleet before investing in T3.2/T3.3**
  — no point unifying five kill paths if two are deleted, or building a lease under a
  watchdog that's being retired. T1.1/T1.2 are safe either way (they shrink exactly the
  code #1926 would prune, and make the survivors legible). #1926's "collapse liveness to one
  model: subprocess alive + turn timeout + last-turn age" is compatible with T1.2+T3.3 and
  should be treated as their acceptance shape. #1855 (delete the `stall_recovery_enabled`
  flag — one always-on recovery path) belongs to the same pruning pass.
- **#1818 (resilience-hardening tracking: #1814/#1815/#1816/#1817)** shares this plan's
  diagnosis ("failures made invisible to the recovery we already have"; "Python-level CAS
  instead of Redis-atomic primitives"). Division of labor: #1818 owns substrate durability
  (Redis AOF, loop isolation, dead-man's-switch); this plan owns signal/state consolidation
  above it. The lease primitive is the one shared component (T3.3 ↔ #1815). #1818's "do
  now" AOF item is a prerequisite worth doing before any Tier 3 work — leases and event logs
  on a store that silently loses an hour of state inherit that fragility.
- **#1927 (AgentSession schema diet) + #1925 (remove claude_code_sdk)**: T2.3's pid-field
  collapse **is** a schema-diet line item — land it inside #1927's field-by-field audit and
  single migration, not as a competing migration on the same model. #1925's removal of the
  SDK path deletes the "legacy conversational path" T2.3 would otherwise have to rewire, and
  T2.4's `HarnessResult` should be shaped as part of #1925's single-harness consolidation.
  Suggested order: #1925 → (#1927 + T2.3) → T2.4.

### Candidate scope extension (owner decision)

- **Delivery-path consolidation (#1370 + #1802)**: the #1955 fix (drafter flags local file
  paths) is itself scar tissue over the structural gap #1802 names — the PM has no
  file-capable send path, so sessions *can only* emit dead local paths for binary artifacts.
  #1370 documents three send paths with divergent filters. This is a fifth cluster with the
  same shape as the other four (duplicated partial surfaces, convention-patched). Currently
  excluded by the No-Gos (bridge I/O). Recommend: keep excluded from this program, but when
  #1802 is planned, apply this program's rule — one send surface, filters declared once —
  rather than adding a fourth path.

### Adjacent, low-touch

- **#1992** (enable `FEATURES__CRASH_AUTORESUME_ENABLED=1` on the worker machine) —
  operational toggle completing #1917; independent of this plan, do anytime.
- **#1968** (centralize magic timeout/TTL literals in settings) — T1.2 eliminates the worst
  offenders (three divergent freshness windows for one heartbeat field become explicit
  parameters); do #1968 after T1.2 so it centralizes survivors, not soon-to-die literals.
- **#1897** (test-isolation flake) — same test-rot family as T1.6's targets; unaffected by
  this plan but worth batching with T1.6's contract-test work.

### Conflicts checked, none found with

#1951 (Groq transcription), #1923 (drop ollama), #1920 (video watch path), #1886
(tool-budget default), #1883 (skills audit), #1834 (Sentry env gating — complements T1.5's
loud-failure posture), #1829/#1931/#1249/#728 (memory system), #1819 (Firecrawl), #1813
(1Password), #1799 (DOCS-skip — T2.2 explicitly preserves its semantics), #1721 (lossless
checkpoint resume — complementary to attempt-scoping in T1.2; resume handles are *what* to
resume, attempt_id is *which run* signals belong to), #1630 (prompt-injection inspection),
#1541 (per-project fan-out — will lean on T2.1's run identity if built after it), #1338,
#1336, #1031.

## Packaging into SDLC Issues

Can Tiers 1 and 2 ship as one SDLC issue? Mechanically yes — the pipeline doesn't cap
scope — but a full T1+T2 mega-issue recreates hazards this very review documented, and
T2.3/T2.4 are excluded regardless (they ride #1927/#1925 to avoid racing migrations on
`AgentSession` and refactoring a harness path #1925 deletes).

**Recommended: two issues, split by file-overlap boundary rather than by tier.** The tier
grouping is an effort/risk taxonomy; the shipping boundary should be "which items touch the
same files":

- **Issue A — pipeline substrate: ownership + single merge enforcement.**
  T1.7 (+#1987, live-blocked — makes this the urgent one), T2.1 (run_id), T2.2 (merge-gate
  enforcement). One coherent story ("pipeline state gets one owner and one gate"), one
  surface (`tools/sdlc_*`, `models/session_lifecycle.py`, `agent/sdlc_router.py`, sdlc/merge
  skill bodies, merge-guard hook). Combining these avoids three sequential rebases over the
  same files. ~1 week.
- **Issue B — signals, gates, and loud-failure hygiene sweep.**
  T1.1, T1.2 (after #1979 merges), T1.3, T1.4, T1.5, T1.6, T1.8, plus the four live defects
  above. Heterogeneous files but zero overlap with Issue A, so the two pipelines can run
  concurrently without lane conflicts. Items are individually small and additive; one serial
  builder in one worktree. ~1 week.

**Why not one combined T1+T2 issue:** a ~2-week single PR concentrates review and makes the
full-suite merge gate all-or-nothing over 12 subsystem changes (one flake blocks
everything, regressions are hard to bisect). And the alternative failure mode is equally
documented: the 2026-07-05 six-issue parallel batch needed manual recovery on 4 of 6
pipelines (#1915), so "twelve tiny issues" is *worse*, not better. Two medium issues with
disjoint file sets is the optimum between those two observed failure modes. If forced to
one issue, run builders strictly serially in one worktree and expect the review/gate stage
to be the bottleneck.

Per the supersession rule, each issue is written fresh from this plan (current knowledge),
lists which open issues it absorbs (#1987 into A; #1983 into B), and closes them on merge.

## Success Criteria

- Each shipped tier item **deletes at least as many guards/predicates/fields as it adds** —
  measured in the PR description (e.g. T1.2: 4 progress predicates → 1 shared pair; T2.3:
  3 pid fields → 1; T3.1: 5 recovery rows deleted).
- The regression tests pinning the original 21 bugs (#1962's fresh-heartbeat test, #1938's
  reap tests, #1980's result-preservation test, #1971's shared-token test, etc.) all still
  pass after each item lands — the refactors preserve the fixed behavior while removing the
  scar tissue.
- Tier 1 complete ⇒ an unclassified exit reason, an unstamped decaying artifact, a silent
  `except: pass`, and a duplicate constant each fail at write/import/lint time rather than
  in production.
- Tier 2 complete ⇒ no prompt-convention re-export of ownership tokens remains in any skill
  body; the merge-guard hook enforces the merge predicate itself; exactly one persisted pid
  surface exists.
- Tier 3 complete ⇒ router recovery rows 2b/2c/8b/8c/8d are deleted; a marker write is never
  rejected; "orphaned" is derived from lease expiry alone; every destructive follow-up
  requires a `KillOutcome`.

## No-Gos

- No rewrite of `_apply_recovery_transition` as the single recovery transition, the
  `_should_kill_no_progress` reprieve gate, `derive_sdk_ever_output`, the
  CAS/StatusConflictError discipline, or the runner's synchronous reap ordering — these are
  the parts that already work; proposals route *through* them.
- No new guard rows, step-asides, or staleness triggers as part of this program — the point
  is deletion and consolidation. A tier item that ends with more predicates than it started
  with has failed its own test.
- No big-bang cutover on Tier 3 items: T3.3 runs log-only for a release; T3.2 lands
  call-site-by-call-site; T3.4 only after T3.1/T2.2 shrink the consumer surface.
- No changes to the bridge's Telegram I/O path — this program is worker/pipeline/gate scoped.

## Update System

- T1.3 / T1.6 touch `scripts/baseline_gate.py` and the refresh reflection — the reflection
  registration gap must be fixed via the update path (`scripts/update/reflection_register.py`)
  so all machines get the freshness check, not just ones with a manual vault edit.
- T1.5's validator (if implemented as a hook) ships via the existing `.claude/hooks/`
  propagation; if as ruff config, `pyproject.toml` propagates with the repo.
- T2.1 removes the `SDLC_HOLDER_TOKEN` env seam — `/do-sdlc` and `/sdlc` skill bodies (both
  synced-global and project-only) must be updated in the same PR; add `RENAMED_REMOVALS`
  entries only if skill files move.
- T2.3 requires a one-release migration alias (`claude_pid` read-through) — call out in the
  deploy notes so mixed-version machines during rollout don't lose kill coverage.
- Other items are repo-internal; no update-script changes required beyond normal deploy.

## Agent Integration

- No new CLI entry points in `pyproject.toml [project.scripts]` are required; all tier items
  modify existing surfaces (`sdlc-tool`, the gate scripts, the runner stack) already reachable
  by the agent.
- T2.2 changes the merge-guard hook contract: skills and agents that today create
  `data/merge_authorized_{PR}` sentinel files must migrate to the predicate-evaluating hook.
  The `/do-merge` skill body is updated in the same PR.
- T1.4 changes `find_affected`'s return shape — its callers (`/do-docs` impact finder,
  doc-impact wrappers) are agent-invoked; migrate call sites in the same PR with a thin
  list-subclass shim if needed.

## Failure Path Test Strategy

- T1.1: a test that iterates the enum and asserts every member has an explicit
  classification; adding an unclassified member fails.
- T1.3: gate refuses to emit a verdict when the envelope is stale/degraded under
  `--strict-freshness`; degraded write path stamps `degraded: true` (assert on artifact).
- T2.1: two subprocesses sharing a run resolve the same ownership; a foreign live run blocks
  with a distinct reason; a dead run's lock expires.
- T2.3: kill machinery with a stale `pid_generation` refuses to act; `_sweep_dead_worker_sessions`
  finds the pgid on the single field.
- T3.2: property-style test that no destructive follow-up path is reachable without a
  `KillOutcome(confirmed_dead=True)` (signature-enforced; verify via type checks in tests).
- T3.3: fresh-lease session is never recovered regardless of turn_count/log_path absence
  (regression pin for #1962).

## Test Impact

- [ ] `tests/unit/test_sdlc_router*.py`, `tests/unit/test_sdlc_next_skill.py`,
      `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE (T1.7, T2.2) / REPLACE (T3.1: rows
      2b/2c/8b/8c/8d tests become artifact-hash verdict-validity tests).
- [ ] `tests/integration/test_silent_failures.py::TestNoSilentPassRemaining` — DELETE
      (T1.5 replaces with lint/validator); keep behavioral caplog classes.
- [ ] `tests/unit/test_do_merge_baseline.py`, `tests/unit/test_refresh_test_baseline.py`,
      `tests/unit/reflections/test_test_baseline_refresh_check.py` — UPDATE for the envelope
      (T1.3, T1.6).
- [ ] `tests/unit/test_doc_impact_finder.py` — UPDATE for `(results, meta)` shape (T1.4).
- [ ] `tests/unit/session_runner/test_runner_*.py`, `tests/unit/test_session_executor_*.py`,
      `tests/unit/test_session_health_*.py`, `tests/unit/test_never_started_recovery.py` —
      UPDATE (T1.1, T1.2, T2.3, T2.4); the #1962/#1938 regression tests must keep passing
      unmodified where possible (they pin the behavior the refactor preserves).
- [ ] `tests/unit/test_session_lifecycle.py`, `tests/unit/test_sdlc_*` lock tests — UPDATE
      (T2.1 replaces holder-token tests with run-id tests).
- [ ] `tests/integration/test_reply_delivery.py::TestReactionEmojiSelection` — UPDATE to
      share the helper with the import-time assert (T1.8).

## Rabbit Holes

- Rewriting the router as a general workflow engine. T3.1 only changes what predicates read;
  the rule-table shape stays.
- Solving distributed locking in general. T2.1 is one logical id + the existing advisory
  lock semantics (fail-open on Redis errors stays as-is).
- Chasing all ~87 `except Exception: pass` sites to zero in T1.5 — triage to
  allowlist-with-comment is an acceptable terminal state for by-design-silent paths.
- Unifying the telemetry timeline with `session_events` — out of scope; T3.4 only touches
  stage-status derivation.
- Touching PTY-era code paths or resurrecting granite container abstractions — dead code
  stays dead.

## Documentation

- [ ] Create `docs/features/resilience-simplification.md` — program overview, tier status
      table, links to per-item issues/PRs (created with the first shipped item).
- [ ] Update `docs/features/session-recovery-mechanisms.md` (T1.2, T2.3, T3.2, T3.3),
      `docs/features/headless-session-runner.md` (T1.1, T2.4),
      `docs/features/sdlc-issue-ownership-lock.md` (T2.1),
      `docs/features/merge-gate-baseline.md` (T1.3, T1.6),
      `docs/features/sdlc-pipeline-state.md` + `docs/features/pipeline-state-machine.md`
      (T3.1, T3.4), `docs/features/semantic-doc-impact-finder.md` (T1.4).
- [ ] Add entries to `docs/features/README.md` index for any new feature docs.
