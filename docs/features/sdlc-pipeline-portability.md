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

- **Primary:** `sdlc-tool meta-set --key pr_number --value N` whitelists
  `pr_number` (coerced to a positive `int`; non-positive/non-numeric values
  exit 2). `_compute_meta` (`tools/sdlc_stage_query.py`) reads `_pr_number`
  from `stage_states` as a resolution source.
- **Fallback:** `_lookup_pr_number` falls back to a branch-head search
  (`gh pr list --head session/{slug} --state open`) when the issue-number
  search returns nothing. The slug is resolved from the PM session; the search
  uses the canonical SDLC branch shape `session/{slug}` (never a fabricated
  `session/sdlc-{n}` form this repo does not create).

Resolution order in `_compute_meta`: `session.pr_number` → `_pr_number` meta
key → `gh` lookup (issue-search then branch-head).

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
the tracking issue → create the authorization file `data/merge_authorized_{pr}`
the merge-guard hook requires → squash-merge → delete the auth file. It defers
repo-specific gate detail (shape classification, stale-review filter,
lockfile/full-suite gates) to `docs/sdlc/do-merge.md`.

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

`write_marker()` now returns `(result, exit_code)`. The `/do-build`,
`/do-pr-review`, and `/do-merge` SKILL.md files gained a substrate-probe step
(mirroring `/do-docs`) so a forked sub-skill announces "running in degraded
mode (state not persisted)" at the top of its run instead of silently lagging.

## Cross-Repo Smoke Test

The integration smoke test that would have caught D1/D2/D3 originally runs
`sdlc-tool next-skill` end-to-end in a temp non-`ai` git repo (no
`SDLC_TARGET_REPO` set) and asserts the plan resolves and the router routes
forward.

## Key Files

- `tools/_sdlc_utils.py` — `find_plan_path` (D1, D2), `_git_toplevel` helper.
- `agent/sdlc_router.py` — row-4b/4c predicates (D3), `compute_same_stage_count`
  reset (D5), `guard_g4_oscillation` docstring.
- `tools/sdlc_stage_query.py` — `_compute_meta` pr_number resolution + live
  snapshot (D4, D5), `_lookup_pr_number` branch-head fallback (D4).
- `tools/sdlc_meta_set.py` — `pr_number` whitelist + int coercion (D4).
- `tools/sdlc_dispatch.py` — `dispatch reset` subcommand (D5).
- `tools/sdlc_stage_marker.py` — tri-state degradation probe (D7).
- `.claude/skills-global/do-merge/SKILL.md` — portable merge gate (D6).
- `docs/sdlc/do-merge.md` — repo-specific merge-gate addenda.

## Race Conditions

All `stage_states` writes (`dispatch reset`, `pr_number` meta-set, stage-marker
writeback) route through `tools.stage_states_helpers.update_stage_states`, the
optimistic-retry safe writer, so a concurrent reset and marker writeback on the
same PM session never clobber each other's keys.
