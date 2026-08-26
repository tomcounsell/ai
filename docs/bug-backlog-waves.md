# Bug Backlog: Wave Playbook

Triage date: 2026-08-26. Scope: all issues carrying the `bug` label.

Starting set: 71 open. Closed at triage: 6 (#2970, #2825, #2810, #2806 fixed; #2824, #2808 duplicates).
Four more (#3004, #3006, #3016, #3017) were filed during the triage pass itself and are slotted in
below. Remaining: **69**, sequenced into 9 waves.

Every remaining issue was verified live — grepped against current `main` or checked for a merged
PR — not inferred from the issue text. No issue in the set was stale-by-architecture.

## Sequencing principle

Wave 0 comes first because it is the **signal-integrity wave**: several grep-based tests are red
for environmental reasons (stale off-pin `.pyc`, shadowed console scripts). Until those clear, a
green or red gate from any later wave cannot be trusted, and every SDLC lane pays the tax of
re-diagnosing the same environmental noise. Waves 1-8 are ordered by blast radius after that.

Waves 1 is a **batched hotfix** — one branch, mechanical fixes, disjoint files. Waves 2-8 are
**SDLC lanes**: each wave is one plan document covering a cluster that shares a code seam, so the
plan is written once and the critique round reasons about the seam as a whole rather than N times.

---

## Wave 0 — Toolchain & test-signal integrity (blocks all later gate readings)

| # | Issue | Mode |
|---|-------|------|
| 2807 | Overclaim guard greps whole worktree; stale `.pyc` keeps deleted string alive (canonical) | SDLC |
| 2809 | Anti-criterion test greps working tree; unregenerable 3.13 `.pyc` keeps node red | fold into 2807 |
| 2883 | 1143 off-pin `.pyc` files unswept; no doctor check, no `/update` purge | SDLC |
| 2780 | Stale `~/Library/Python/3.12/bin` shims shadow 63 venv executables | SDLC |
| 2749 | Gates conflate exit 127 (missing entry point) with a real non-zero verdict | SDLC |
| 2852 | Umbrella: main's unit suite to zero failures — **narrowed to 3 residual nodes** (#2846/#2822 closed) | hotfix |
| 3006 | 6 pre-existing failures in `tests/integration/test_stall_advisory_e2e.py` | fold under 2852 |
| 3016 | Nightly regression: `test_promise_gate_real_api.py::test_forward_deferral_blocks_real_api` | SDLC |
| 2854 | Nightly unit tests leak fixture ERROR lines into production `logs/bridge.log` | SDLC |
| 2799 | `tests/conftest.py` Redis client ignores `REDIS_PORT`; private-redis isolation hits prod 6379 | hotfix |
| 2707 | Steering tests collide across concurrent runs via hardcoded session ids | SDLC |
| 2770 | Upstream popoto: pytest11 plugin should honor env-pinned db | upstream PR |

**Order within the wave:** 2807+2809 and 2883 and 2780 first (they are one story — off-pin
artifacts corrupting grep and PATH). Then 2799 (one-line: pass host/port), then 2852's 3 nodes.
2749 last, since its audit needs a clean baseline to distinguish 127 from a real failure.

**Note on 2809 vs 2807:** same root cause, different failing node. Fix 2807's sweep-tracked-content
approach once and reconcile 2809 against it rather than opening a second lane. Its plan
(`docs/plans/overclaim-guard-greps-whole-worktree.md`, rev r3) already exists.

**Watch item:** #2770 is a change to a *different* checkout (`~/src/popoto`). PR #2683 landed a
local mitigation here; the upstream ask is still open and should not block Wave 0's close-out.

---

## Wave 1 — Batched hotfix sweep (one branch, one PR)

Mechanical, low-risk, disjoint files. These need no plan document.

| # | Issue | Shape |
|---|-------|-------|
| 2674 | `health_check.py` writes a float into `AgentSession.updated_at` (DatetimeField) | 3 call sites → `utc_now()` |
| 2678 | `scripts/update/run.py` never configures logging | one `basicConfig()` in `__main__` |
| 2727 | Deployed `site/` references deleted `bridge/session_router.py` (16 refs) | regenerate graph.js + runtime.html |
| 2752 | Four doc/comment accuracy nits severed from PR #2728 | 4 one-liners |
| 2750 | Two same-named `read_hook_input()` with divergent safety contracts | collapse to the hardened one |
| 2800 | pre-push hook falsely refuses up-to-date pushes; remedy arms break-glass | fix empty-stdin fallback + reword |
| 2778 | `validate_verification_section.py` not registered in `manifest.toml` | one manifest entry + regen |
| 2855 | Reflection registration lands in the clobbered config copy under `VALOR_LAUNCHD=1` | split write/read resolver |
| 2898 | `--verify` mutates `warn_state`, consuming the next cron run's emission | make verify read-only |

**Two carve-outs — do NOT batch these:**
- **#2896** (granite PTY residue, 2 code sites left after PR #2910 fixed 3 docs sites) is gated on a
  decision in **#1633**. Resolve that first; the fix itself is trivial.
- **#2837** (`PREMIUM_DIGIT_REACTIONS` is still `{}`) requires a human at the keyboard with the
  bridge stopped, to read document_ids. It cannot ship from an autonomous lane.

**Landing rule:** per repo convention a commit on `main` without a PR must declare issue
disposition (`Closes #N` / `Refs #N` / `No-issue:`). Batch these as one branch + PR instead — nine
`Closes` trailers in one squash is cleaner than nine hotfix commits, and gives one review surface.

---

## Wave 2 — SDLC router terminal-state & verdict integrity

Largest cluster and the one that actively wedges live lanes. All nine touch
`agent/sdlc_router.py` and the critique/verdict machinery, and several share the same predicates
(`_review_verdict_head_is_stale`, `stage_states`) — hence one plan, not nine.

| # | Issue |
|---|-------|
| 2894 | Router row 10 never terminates: `/do-merge` re-dispatches forever on an already-merged PR |
| 2851 | Mid-pipeline entry deadlock: empty stages map + plan doc makes REVIEW/merge unreachable |
| 2817 | `next-skill` has no terminal verdict; MERGE-completed pipeline with lost `pr_number` blocks NO_RULE |
| 2850 | CHANGES REQUESTED verdicts record no `head_sha` — PATCH↔REVIEW lap has no staleness anchor |
| 2895 | G6 fast-pathed a head-stale APPROVED verdict; shared staleness seam has a fail-open branch |
| 2885 | `critique_cycle_count` never increments — plan/critique loop has no automatic cap |
| 2832 | Critique run dirs lose `_roster.json`/`.plan_hash` mid-run, defeating the roster barrier |
| 2849 | `SDLC_ISSUE_NUMBER` goes stale in a long-lived dev session, pointing stages at the wrong issue |
| 2886 | do-plan-critique roster ran serially: a `context: fork` skill inside a subagent has no Agent tool |

**Priority within wave:** 2894 / 2851 / 2817 are the terminal-state trio — they cause infinite
re-dispatch and unreachable merges, i.e. wedged lanes burning tokens right now. 2850 + 2895 are the
verdict-staleness pair and should be fixed together (2895 is UNREPRODUCED — fix the fail-open branch
2850 exposes and see whether 2895 survives). 2885/2832/2849/2886 are the critique-loop set.

**#2895 caveat:** unreproduced. Do not let a lane claim it fixed without a demonstrated-red test, or
it will be re-filed. This is exactly the failure mode #2658 (Wave 4) exists to prevent.

---

## Wave 3 — Lane identity, lease scoping, and PR resolution

| # | Issue |
|---|-------|
| 2869 | Lane slug adoption not universal: a minted `sdlc-N` slug names a branch that doesn't exist (**absorbed #2824**) |
| 2813 | SDLC issue lock and session lookup are not repo-scoped — two repos sharing an issue number collide |
| 2820 | `resolve_project_key(cwd)` misses every projects.json path from a scratch worktree; 6 tests phantom-fail |
| 2811 | `_run_identities` anchor has a silent bootstrap hole; pre-#2803 and first-lapse lanes re-mint |
| 2868 | `_gh_pr_search_issue_ref` candidate ordering rests on unguaranteed `gh` ranking |
| 2762 | Lease-helper AST sweep walks `tree.body` only, missing try/if-wrapped and relative imports |
| 2777 | `/do-sdlc` step 3d.4 halts REVIEW_VERDICT_MISSING in repos declaring no verdict substrate |
| 3017 | `current_stage` can never return PATCH: `SDLC_STAGES` omits it, so slugged PATCH sessions resolve to main with no worktree |
| 2812 | **Investigation:** ledger stage history for #2675 went empty after AgentSession recreation |
| 2760 | **Investigation:** PR #2728 merged while `merge_predicate` should have refused, no override recorded |

**#3017 should lead this wave.** A slugged PATCH session that resolves to `main` with no worktree
means patch work lands in the shared primary checkout — the isolation failure that produces the
"whose dirty state is this?" confusion behind several other issues here. It is a one-line symptom
(`SDLC_STAGES` omits PATCH) with a large blast radius.

**Handle the two investigations separately.** #2812 and #2760 have no confirmed root cause — two and
three competing hypotheses respectively. Don't route them into a fix lane; route them into a
reproduction lane whose deliverable is a confirmed mechanism plus a red test. #2760 in particular is
a merge-gate bypass, which is the highest-severity item in the entire backlog: a gate that can be
bypassed silently is worse than no gate, because it is trusted.

---

## Wave 4 — Hooks, guards, and gate authoring discipline

| # | Issue |
|---|-------|
| 2779 | `validate_no_inline_timeout.py` rescans whole staged file — any file with a pre-existing literal is frozen |
| 2736 | raw-Redis validator: prose quoting an interpreter still blocks (residual of #2638) |
| 2715 | `/do-build` mandates the Task tool but nothing checks the session has it — 4 silent no-op builds |
| 2658 | Gates that cannot fire: require demonstrated-red for verification rows, guards, and skill self-checks |

**Do #2658 last and treat it as the wave's capstone.** It is the general form of the other three
(and of #2895, #2810, #2806): a guard nobody proved could fail. Its plan doc already exists at
`docs/plans/gates-that-cannot-fire.md`. Landing it after 2779/2736/2715 means those three become its
first three regression cases rather than hypotheticals.

---

## Wave 5 — Verification runner & plan-grammar convergence

| # | Issue |
|---|-------|
| 2901 | Verification runner: 120s bound, BRE alternation lost to cell escaping, narrow expectation vocabulary |
| 2870 | `validate_build.py` carries two more private plan-document grammars |
| 2791 | Runner false-FAILs every 'prints N' row (unrecognized form falls through to bare `return False`) |
| 2905 | Nightly-tests installer: removal path descoped from #2823 — reintroduce with the 4 prior failures as required reading |

2901 / 2870 / 2791 are all one story: **N private grammars for one plan-document format.** Converge
them on a single parser in one lane rather than patching each grammar. #2791 is sequenced behind
#2783 per its own comments — check that dependency before starting.

---

## Wave 6 — Docs auditor & drift

| # | Issue |
|---|-------|
| 2739 | docs_auditor is its own committer — put a review gate in front of every write (**umbrella**) |
| 2834 | docs_auditor can't distinguish a live reference from a deletion record |
| 2937 | skills-audit files one issue per rule: one skill regression fans out into N issues |

**#2739 has an open PR (#2887) already in flight** — land or close that before opening new work here.
2834 and 2937 are both "the auditor's output is untrustworthy in a specific direction" (false
issues, and issue spam respectively); they pair naturally with 2739's review gate.

---

## Wave 7 — Durability, data model, and scheduled recovery

Anchored by the **#2494 umbrella** (`docs/plans/durability-room-job-agentrun.md`), which catalogues
~9 silent-loss failure modes. Keep #2494 open as a tracking epic — it is not itself a fix task.

| # | Issue | Note |
|---|-------|------|
| 2494 | **Umbrella:** Room / Job / AgentSession — recovery keys on work owed, not process status | epic, stays open |
| 3003 | Twenty production call sites bypass the Popoto ORM with hand-built raw Redis clients | fresh, needs own plan |
| 2699 | `_run_guarded_repairs` has no wall-clock budget; runs Job full-hydration on worker startup | deferred by PR #2671 |
| 2639 | popoto QueryBuilder executes hydration pipeline twice — every `.filter()` costs 2x | perf |
| 2848 | Cursor/pipeline `Job.renormalize_last_active_scores` before Job population hits ~10k | low urgency (92 Jobs today) |
| 2698 | Activate `AgentSession Meta.ttl` 30-day expiry deliberately (never fired) | policy decision |
| 2862 | Expectations cannot record a blocked state; corrupt goal JSON fails open then gets overwritten | |
| 2857 | Recovery scanners persist raw `message.text` without `strip_private` | **blocked on open PR #2856** |
| 2691 | Reconciler per-chat scan is load-bearing for the wedge verdict but has no health monitoring | |
| 2677 | Schedule the sdlc-local ledger orphan reaper (nothing invokes `--kill-orphans`) | copy PR #2681 pattern |
| 2650 | Plan-doc writes in the shared main checkout have no single-writer protocol | git half shipped (PR #2669) |
| 2661 | Rotate production `REDIS_URL` onto a dedicated ACL user credential | **CLOSED NOT_PLANNED 2026-08-26, citing #3004** |
| 3004 | Delete the server-side Redis access-control layer — the stack must work safely from a connection string alone | resolves the #2661 contradiction by deletion |

**Finish-the-remainder subgroup:** 2678 (Wave 1), 2677, 2661, 2650, 2699 are all cases where a
sibling PR already shipped part of the original scope. Brief each lane with what already landed
(#2643, #2681, #2680, #2669, #2671 respectively) so it doesn't re-derive or re-do it.

**#2661 vs #3004 was a live contradiction, resolved.** #2661 asked to rotate production `REDIS_URL`
onto an ACL user credential; #3004, filed 2026-08-25, deletes the ACL layer entirely so the stack
works from a connection string alone. The operator decided #3004's direction on 2026-08-25:
server-side Redis access control is declined. #2661 was closed NOT_PLANNED on 2026-08-26 citing that
decision, and #3004 ships the deletion.

---

## Wave 8 — Deferred features and dependency unblocks

| # | Issue |
|---|-------|
| 3001 | Unblock dependency bumps (anthropic/pydantic-ai), restore the dead `worker_key` regression guard, stop duplicate nightly triage |
| 2652 | Bridge has no Telegram forum-topic awareness: topic identity, session keying, default outbound topic |
| 2732 | Reply-chain media renders as literal `[media]`, stranding attachments the agent could read |

**#3001 is partly done** — the anthropic pin half was resolved by the same revert that closed #2970.
The `worker_key` regression guard is confirmed **still dead**: 9 tests in
`TestWorkerKeyProperty`/`TestWorkerKeyTruthTable` fail on a live run. Re-scope the issue to that
remainder rather than treating it whole.

---

## Cross-cutting notes

**Blocked / not-yet-actionable** — don't route these into a lane until the blocker clears:
- #2857 → open PR #2856
- #2739 → open PR #2887
- #2896 → decision in #1633
- #2791 → sequenced behind #2783
- #2837, #2661 → require a human at the keyboard
- #2770 → lands in the `popoto` checkout, not this one
- #2661 → blocked on the #3004 decision (see Wave 7)

**Backlog refill rate.** Four new bug issues (#3004, #3006, #3016, #3017) were filed in the ~14
hours spanning this triage. At that rate the backlog regrows faster than a single sequential lane
clears it, which is the argument for running Waves 4-7 in parallel and for prioritizing Wave 0 and
Wave 6 (#2937's issue fan-out) — both reduce the filing rate rather than the backlog depth.

**Parallelism.** Waves 2 and 3 both touch `agent/sdlc_router.py` and the lease/identity helpers.
Running them concurrently invites the collision this backlog is full of. Run 2 → 3 sequentially, or
split strictly by file with an explicit boundary written into both plans. Waves 4, 5, 6, 7 are
mutually disjoint and can run in parallel with each other.

**The meta-pattern worth naming.** A large share of this backlog is not product bugs — it is the
SDLC apparatus mis-measuring itself: guards that grep bytecode, gates that can't fire, verdicts with
no staleness anchor, routers with no terminal state, auditors that file false issues. Waves 0, 2, 4
are all instances. Landing them shrinks the *rate* at which this backlog refills, which is worth
more than the individual fixes. Sequence accordingly.
