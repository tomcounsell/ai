# SDLC Pipeline Portability

The `/sdlc` pipeline was built assuming it runs in `~/src/ai`, with the full
Python substrate present and the issue number written as a bare `#N`. The
moment those assumptions broke — any other repo, a URL-only plan, an
out-of-band PR, or an absent orchestration substrate — failures were *silent*
and compounded into pipeline lock-ups that the G4 oscillation guard then
latched closed.

This feature closes seven generic robustness defects (issue #1535) so the
pipeline runs unattended in **any** repo. The through-line: a missing
assumption degrades to *correct* instead of *silently wrong*, the router never
routes a finished PR back to build, G4 self-clears on real state transitions
(with an operator escape hatch), the merge stage has a real deterministic
skill, and absent substrate produces a *loud, visible* degraded-mode marker
instead of silent lag.

## The Seven Fixes

### D1 — Portable plan resolution

`find_plan_path()` (`tools/_sdlc_utils.py`) resolves the `docs/plans` directory
in this order:

1. `SDLC_TARGET_REPO` env var (explicit override wins — preserves the
   cross-repo override semantics callers already rely on).
2. Else the cwd's git working-tree root (`git rev-parse --show-toplevel`), so
   the pipeline finds plans in whatever repo it is invoked from.
3. Else the `__file__`-relative `~/src/ai/docs/plans` fallback.

Each step falls through on failure (not a git repo, `git` missing) via the
`_git_toplevel()` helper, which never raises. With no env var set, the default
is now the cwd git root rather than a hard-coded `~/src/ai`.

### D2 — Tracking-URL plan match

The resolver matches the issue by a bounded regex
`(?:#|issues/){issue_number}(?![0-9])` instead of a bare substring. This finds
plans that reference the issue only by tracking URL
(`https://github.com/org/repo/issues/145`) and fixes a pre-existing
false-positive where `#1455` matched a lookup for issue `145`.

### D3 — Finished PRs never route back to build

The router's row-4b / row-4c concern-path predicates
(`agent/sdlc_router.py`) now return `False` once a PR exists
(`meta["pr_number"]`) **or** `BUILD == completed`. Downstream rows (review,
patch, docs, merge) own routing from that point — a completed+approved PR is
never re-proposed for `/do-build`. This is implemented at the predicate layer,
not by reordering `DISPATCH_RULES` (the SKILL.md parity test cross-checks row
ordering).

### D4 — Out-of-band PR recovery

`pr_number` is now both settable and recoverable:

- **Single writer (#2003 T1.7):** `sdlc-tool meta-set --key pr_number --value N`
  writes the `AgentSession.pr_number` FIELD (coerced to a positive `int`;
  non-positive/non-numeric values exit 2). `/do-build` invokes it at PR
  creation; the same command is the out-of-band operator recovery path.
- **Read-only recovery:** `_lookup_pr` falls back to a branch-head search
  (`gh pr list --head session/{slug} --state open`) when the issue-number
  search returns nothing. The slug is resolved from the PM session; the search
  uses the canonical SDLC branch shape `session/{slug}` (never a fabricated
  `session/sdlc-{n}` form this repo does not create).

Resolution order in `_compute_meta`: `session.pr_number` field → `gh` lookup
(validated issue-search then branch-head). The recovery rungs never write.

### D5 — G4 self-clears + operator escape hatch

`same_stage_dispatch_count` (G4) used to latch closed after a transient
mis-read even once the underlying cause was corrected. Two complementary paths
fix this:

- **Self-clearing:** `_compute_meta` builds the live stage snapshot and passes
  it into `compute_same_stage_count(...)`. When the live snapshot diverges from
  the last recorded dispatch snapshot, the impending dispatch is a genuinely
  new stage, so the count resets to `0`. The backward history walk is
  unchanged.
- **Escape hatch:** `sdlc-tool dispatch reset --issue-number N` clears
  `_sdlc_dispatches` for the genuinely-latched recorded-history case. The G4
  block reason string documents it.

### D6 — Portable `/do-merge` skill

`.claude/skills-global/do-merge/SKILL.md` is a portable skill that performs the
deterministic merge gate: verify PR state (OPEN / mergeable / CI-green /
`mergeStateStatus == CLEAN`) → verify REVIEW `APPROVED` → verify the body links
the tracking issue → authorize and squash-merge. In this repo, authorization is
the live merge predicate the merge-guard hook evaluates
(`tools/merge_predicate.py`, issue #2003) — the skill no longer creates an
authorization file on the happy path; a repo whose merge-guard hook still
gates on file existence gets that behavior instead, per the repo-context file.
It defers repo-specific gate detail (shape classification, stale-review
filter, lockfile/full-suite gates) to `docs/sdlc/do-merge.md`.

The skill auto-deploys: `scripts/update/hardlinks.py::_sync_skills` discovers
any `skills-global/*/SKILL.md` directory and hardlinks it to `~/.claude/skills/`
with no registration step.

### D7 — Loud degradation, quiet absence

`tools/sdlc_stage_marker.py` replaces the old binary present/absent check with
a tri-state probe (`probe_substrate()`):

- **ABSENT** — cannot import `models.agent_session` / Redis unreachable: emit a
  visible degraded marker (`{"status": "degraded", ...}`) and exit 0. The
  non-`ai`-repo case.
- **PRESENT_NO_SESSION** — substrate present but no PM session resolves: emit a
  degraded marker and exit 0 (**quiet** — the marker cannot tell a legitimate
  non-`ai` repo apart from a wiring bug, and a session-less local invocation
  must not be noisy).
- **PRESENT_WRITE_FAILED** — session resolved but the state-machine write
  rejects or raises: print a clear stderr diagnostic and exit non-zero. This is
  the **only** loud case. The idempotent already-completed path stays exit 0.

  **Superseded (issue #2012):** the quiet `PRESENT_NO_SESSION` no-op above was
  the deadlock's root cause — a takeover session had no way to tell "no
  session" apart from "not my session's session." `tools/sdlc_stage_marker.py`
  moved to an issue-keyed, lease-gated write; the ABSENT/Redis-unreachable case
  is still the only quiet exit-0 path, and every "no session/lease to fall
  back to" case (`LEASE_ABSENT`/`ISSUE_LOCKED`/`TARGET_REPO_MISSING`) is now
  loud. See [SDLC Issue-Keyed Stage Ledger](sdlc-issue-keyed-stage-ledger.md).

`write_marker()` now returns `(result, exit_code)`. The `/do-build`,
`/do-pr-review`, and `/do-merge` SKILL.md files gained a substrate-probe step
(mirroring `/do-docs`) so a forked sub-skill announces "running in degraded
mode (state not persisted)" at the top of its run instead of silently lagging.

### D8 — Cross-repo plan resolution + revision_applied-stripped hash (issue #1761)

Three complementary fixes close the PLAN↔CRITIQUE loop that occurred when running local `/do-sdlc` against a non-ai-repo issue (reproduced with cuttlefish issues #547, #550):

**Root cause:** `sdlc-tool` forces cwd to `~/src/ai` (correct and load-bearing). Local `/do-sdlc` never exported `SDLC_TARGET_REPO`, so `find_plan_path` resolved from `~/src/ai/docs/plans` — the wrong repo. `revision_applied: true` was never read; router row 4c was unreachable; the PLAN→CRITIQUE→PLAN loop ran forever.

**Fix 1 — SDLC_TARGET_REPO export:** `/do-sdlc` Step 2 now captures `git rev-parse --show-toplevel` in the supervision cwd (the target repo) and exports `SDLC_TARGET_REPO` for the lifetime of the supervision loop. `sdlc-tool` inherits it (bash `exec` propagates the env). Both the bridge/worker path (`agent/sdk_client.py:1590`) and the local `/do-sdlc` path now export the same env var shape (absolute filesystem path). See `sdlc-tool-resolver.md` for the `SDLC_TARGET_REPO` vs `SDLC_REPO` (GitHub slug) distinction.

**Fix 2 — `find_plan_path` hardening:** When the `__file__`-fallback resolution path is taken (SDLC_TARGET_REPO unset AND not in a git repo), a bare-`#N` textual fallback now returns `None` instead of a foreign plan. The `tracking:` match remains authoritative on all paths. This scopes the rejection strictly to the ai-repo `__file__` fallback branch, so a same-repo issue without a `tracking:` match still works (the git-toplevel and SDLC_TARGET_REPO paths keep the fallback).

**Fix 3 — `revision_applied`-stripped plan hash:** `compute_plan_body_hash` (new, in `tools/sdlc_verdict.py`) strips **only** the `revision_applied:` frontmatter key before hashing — all other frontmatter (`status:`, `type:`, `tracking:`, `last_comment_id:`) and the full body still contribute to the hash. G5's staleness input (`context["current_plan_hash"]` in `tools/sdlc_next_skill.py`) and the writer's `_compute_artifact_hash` both use `compute_plan_body_hash`, so a `/do-plan` revision write flipping `revision_applied: true` does NOT bust the G5 cache. A real body or other frontmatter edit still busts it. `compute_plan_hash` (full-bytes) is retained for callers that explicitly want the complete fingerprint.

*Notes-only re-stale:* the frontmatter-inclusive hash that previously re-busted G5 after every `/do-plan` revision write is gone. The notes-only re-stale cycle (PLAN→CRITIQUE→PLAN driven by the hash mismatch) is now closed. G5 fires on the first `next-skill` call after a revision write and routes straight to `/do-build`.

*Migration:* see "G5 transparent-rewrite migration" in `sdlc-tool-resolver.md` for how pre-#1761 stored `artifact_hash` values self-heal on the first router pass.

**Skill portability:** all bare `from tools.X` / `python -m tools.X` / `cd ~/src/ai` invocations in global SDLC skills are now anchored to `${AI_REPO_ROOT:-$HOME/src/ai}`. This prevents a target repo that ships its own `tools/` package from shadowing the ai-repo's canonical `tools/` when a skill runs from a cross-repo cwd.

## Cross-Repo Smoke Test

The integration smoke test that would have caught D1/D2/D3 originally runs
`sdlc-tool next-skill` end-to-end in a temp non-`ai` git repo (no
`SDLC_TARGET_REPO` set) and asserts the plan resolves and the router routes
forward.

## Key Files

- `tools/_sdlc_utils.py` — `find_plan_path` (D1, D2, D8), `_git_toplevel` helper.
- `agent/sdlc_router.py` — row-4b/4c predicates (D3), `compute_same_stage_count`
  reset (D5), `guard_g4_oscillation` docstring, G5 transparent-rewrite migration (D8).
- `tools/sdlc_verdict.py` — `compute_plan_hash` (full-bytes), `compute_plan_body_hash`
  (revision_applied-stripped, used by G5 — D8).
- `tools/sdlc_next_skill.py` — `current_plan_hash` context key uses `compute_plan_body_hash` (D8).
- `tools/sdlc_stage_query.py` — `_compute_meta` pr_number resolution + live
  snapshot (D4, D5), `_lookup_pr` branch-head fallback (D4).
- `tools/sdlc_meta_set.py` — `pr_number` whitelist + int coercion (D4).
- `tools/sdlc_dispatch.py` — `dispatch reset` subcommand (D5).
- `tools/sdlc_stage_marker.py` — tri-state degradation probe (D7).
- `.claude/skills-global/do-merge/SKILL.md` — portable merge gate (D6).
- `.claude/skills-global/do-sdlc/SKILL.md` — `SDLC_TARGET_REPO` export in Step 2 (D8).
- `.claude/skills-global/do-{build,docs,patch,plan,plan-critique,pr-review}/` — all anchored
  to `AI_REPO_ROOT` for cross-repo portability (D8).
- `docs/sdlc/do-merge.md` — repo-specific merge-gate addenda.

## Race Conditions

All `stage_states` writes (`dispatch reset`, `pr_number` meta-set, stage-marker
writeback) route through `tools.stage_states_helpers.update_stage_states`, the
optimistic-retry safe writer, so a concurrent reset and marker writeback on the
same PM session never clobber each other's keys.
