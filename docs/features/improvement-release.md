# Improvement Release

What happens after an evaluation says `accept`: the release lifecycle, the
rollback drill, exposure and its observation window, the promotion gate, the
recursive comparison of two research processes, and the claim report that says
which ladder levels the evidence supports.

Tracking issue: [#3218](https://github.com/tomcounsell/ai/issues/3218).
Companion to [Improvement Controller](improvement-controller.md) and
[Improvement Evaluation](improvement-evaluation.md).

## What exists

Two packages and one binary.

| Module | Owns |
|---|---|
| `tools/improvement_release/lifecycle.py` | The sole writer of `ImprovementRelease` state: `propose`, `approve`, `open_pr`, `expose`, `close_window`, `rollback`, `withdraw`, `due_windows` |
| `tools/improvement_release/drill.py` | The rollback drill, its worktree slot under the retention root, `sweep`, and the step executor the real rollback reuses |
| `tools/improvement_release/runner.py` | The injectable subprocess `Runner` (`SubprocessRunner`, `RecordingRunner`) and `run_step` |
| `tools/improvement_release/promotion.py` | `promotion_gate` and `promote_automatically`, which refuses every call |
| `tools/improvement_release/denylist.py` | `CANDIDATE_SURFACE_DENYLIST`, `normalize_surface`, `denied_surfaces`, `refuse_denied` |
| `tools/improvement_release/observation.py` | `measure`, `compare_windows`, the Wilson band, `falsifier` |
| `tools/improvement_release/evaluation_read.py` | `effect_of`, `interval_of`, `notes_of`, `budget_of`, `json_field`: the one reader of an evaluation's JSON-string fields |
| `tools/improvement_release/lineage.py` | `release_lineage`, the join behind the dashboard partial and `show` |
| `tools/improvement_release/cli.py` | `valor-improve-release` |
| `tools/improvement_recursion/process.py` | `ResearchProcessSpec` and `research_process_digest`, the one hashing routine for a research process |
| `tools/improvement_recursion/freshness.py` | `fresh_opportunities`, decided by record lookup |
| `tools/improvement_recursion/budget.py` | `BudgetCap`, `BudgetUse`, `LedgerBudgetReader`, `budgets_comparable` |
| `tools/improvement_recursion/arms.py` | The `ArmRunner` protocol, `ReplayArmRunner`, the registry, and `resolve_arm_runner` |
| `tools/improvement_recursion/compare.py` | `freeze`, `run`, `supersede_revision` |
| `tools/improvement_recursion/report.py` | `claim_report` and `render` |

`valor-improve-release` is a separate binary from lane 3's `valor-improve` on
purpose. Rollback and the drill are incident surfaces that must work when the
control journal is unreachable, so the binary imports nothing from
`tools.improvement`, `tools.improvement_ranking`, or `tools.improvement_plan_arm`
at load. The one place the two meet is `compare run --arm-runner`, which
imports the named module lazily at the moment the comparison runs.

Every JSON-shaped field on the release row (`surfaces`, `exposure`,
`rollback_plan`, `observation`, `rollback_drill`, `promotion_gate`, `outcome`)
is written with `json.dumps(..., sort_keys=True)` and read back through
`evaluation_read.json_field`. Popoto's plain `Field` hands a queried row back a
string, so a writer that assigned a dict directly would store its Python repr;
`json_field` parses both forms and answers `None` for anything else.

## The release lifecycle

`ImprovementRelease` has six states: `proposed`, `approved`, `observing`,
`accepted`, `rolled_back`, `withdrawn`. Every transition is a function in
`lifecycle.py` that checks its preconditions first and raises
`ReleaseRefused(code, detail)` from a closed vocabulary. A refusal writes
nothing, with one exception described under Rollback. `_transition` re-reads
the row immediately before `save()` and refuses `WRONG_STATE` when the state
moved under the caller; Popoto has no compare-and-set, so a sub-millisecond
interleave is detectable through the history rather than prevented.

| From | Event | To | Guard |
|---|---|---|---|
| (none) | `propose` | `proposed` | evaluation `complete` with verdict `accept`; charter digest is the pinned one; kind valid; surfaces non-empty, normalized, none denied; `base_revision` and `candidate_ref` resolved without conflict; rollback plan and observation plan valid |
| `proposed` | `drill` | `proposed` | writes `rollback_drill` and `drill_log`; no state change |
| `proposed` | `approve` | `approved` | drill passed after the last rollback-plan write; approver is a human name; no charter drift since proposal; gate answer recorded |
| `approved` | `open_pr` | `approved` | records `exposure.pr_number`; no state change |
| `approved` | `expose` | `observing` | PR merged; `exposed_at = mergedAt`; baseline rows inside the evidence TTL; baseline frozen; window end restamped |
| `observing` | `close_window` | `accepted` on `held`, otherwise stays `observing` | window elapsed, or `--force` with a reason |
| `observing`, `accepted` | `rollback` | `rolled_back` | revert pushed to `origin/<target>` and confirmed by `ls-remote` |
| `proposed`, `approved` | `withdraw` | `withdrawn` | reason recorded |

Every transition appends `{event, at, from, to, ...}` to `outcome.history`.
The list is bounded at `HISTORY_MAX = 50`; past that the oldest entry is
dropped and `outcome.history_truncated` is set once and never cleared, so a
reader knows the list is a tail. The bound exists because a
`rollback_push_refused` event carries up to 2 KB of stderr and `outcome` is a
plain field on an immortal row.

### Refusal codes

| Code | Raised by | Meaning |
|---|---|---|
| `EVALUATION_NOT_ACCEPT` | `propose` | The evaluation is missing, or its state and verdict are not `complete` and `accept` |
| `CHARTER_DRIFT` | `propose`, `approve` | The evaluation's (or release's) `charter_digest` differs from `ImprovementCharter.pinned(project_key).digest`. Charter §12: reassess pending actions under the new authority before further effects. The operator withdraws and re-proposes; the withdrawn row keeps the old digest |
| `INVALID_KIND` | `propose` | `kind` is outside `RELEASE_KINDS` (`core_workflow`, `evaluator`, `infrastructure`) |
| `EVALUATOR_RELEASE_NEEDS_CALIBRATION` | `propose` | An `evaluator` release without `--calibration-ref` and a non-empty `--argument` |
| `SURFACE_DENIED` | `propose` | No surfaces, a surface the denylist cannot normalize, a surface on the denylist, or a directory surface that encloses an entry (`docs`, `models`, `config`, `tools`) |
| `MANIFEST_LACKS_BASE_REVISION` | `propose` | The manifest carries no `base_revision` and `--base-revision` was not given |
| `BASE_REVISION_CONFLICT` | `propose` | The manifest's `base_revision` and `--base-revision` both exist and differ |
| `CANDIDATE_REF_CONFLICT` | `propose` | The manifest's `candidate_ref` (lane 5's manifests carry one) and `--candidate-ref` both exist and differ |
| `CANDIDATE_REF_UNRESOLVED` | `propose` | The ref is empty, option-shaped, or `git rev-parse --verify` fails |
| `INVALID_ROLLBACK_PLAN` | `propose` | The plan is not `{"kind": "git_revert", "verify": [...], "propagation": "/update"}` |
| `INVALID_OBSERVATION_PLAN` | `propose` | `window_days` or `baseline_window_days` below 1, or `metrics` not a non-empty subset of `OBSERVATION_METRICS` |
| `WINDOW_EXCEEDS_EVIDENCE_TTL` | `propose` | `baseline_window_days + window_days > 28` (`WINDOW_SUM_MAX_DAYS`, the 30-day evidence TTL less two days of slack) |
| `DRILL_REQUIRED` | `approve` | No drill record, an unreadable one, or a last result other than `pass` |
| `DRILL_STALE` | `approve` | The passing drill predates the rollback plan's write, or carries no `drilled_at` |
| `APPROVER_NOT_HUMAN` | `approve` | `--approved-by` is empty, whitespace, or one of `AGENT_IDENTITIES` (`valor`, `valor-engels`, `agent`, `system`, `claude`, compared case-insensitively) |
| `PR_CREATE_FAILED` | `open_pr` | `gh pr create` exited nonzero or printed no PR URL |
| `PR_NOT_MERGED` | `expose` | No PR recorded, `gh pr view` failed or printed no JSON, `state != "MERGED"`, or `mergedAt` unparsable |
| `MERGE_SHA_INVALID` | `expose` | `mergeCommit.oid` is not a 40-hex SHA (`mergeCommit` is an object, never a string) |
| `EVIDENCE_EXPIRED` | `expose` | `now - (mergedAt - baseline_window_days) > EVIDENCE_TTL_DAYS`: the baseline's oldest rows have expired. Also an `outcome.reason` at `close_window`; one name, one meaning, two surfaces |
| `WINDOW_OPEN` | `close_window` | Called before `observation_window_ends_at` without `--force` and a reason |
| `ROLLBACK_STEP_FAILED` | `rollback` | No `merge_sha` on the release, no reason, a worktree slot outside the retention root or inside a git checkout (`CHECKOUT_PATH` in the detail, the drill's rule), an option-shaped or malformed `--branch`, or a fetch, worktree add, revert, commit, or update-ref step exited nonzero. Once a step has run, the attempt and its transcript are written to `outcome.rollback_attempt` beside a `rollback_step_failed` history event |
| `ROLLBACK_PUSH_REFUSED` | `rollback` | The push exited nonzero, or `ls-remote` resolved the target to something other than the revert commit. Writes a history event naming the orphaned revert and its local ref, plus `outcome.rollback_attempt` with the transcript |
| `WRONG_STATE` | every transition | The release is not in a state the event accepts, on the first read or on the re-read before save |
| `NOT_FOUND` | every command | No release with that id under that project key |

The CLI prints a refusal as `{"refused": true, "code": ..., "detail": ...}` and
exits 2.

### Proposal

`propose` reads the evaluation and, through it, the experiment and its
manifest (re-hashed on load by the verifying store). The resolved
`base_revision` comes from the manifest or `--base-revision`; the resolved
`candidate_ref` from the manifest or `--candidate-ref`; the two conflict codes
are distinct so the operator reads a refusal about the ref that actually
disagrees. `git rev-parse --verify` on the resolved ref runs through the
runner. The row is written with `kind`, `surfaces`, `candidate_ref`,
`base_revision`, `exposure` (the plan: `{"unit": "fleet", "mechanism":
"merged PR via /update", "experiment_id", "calibration_ref", "argument"}`),
`rollback_plan`, `observation` (the plan), `charter_digest`, and an `outcome`
carrying `rollback_plan_written_at` and the first history event. Every
commitment the release makes is on the row before anything else can happen.

## The rollback drill

A `pass` establishes one thing: a full-range `git revert --no-commit
<base_revision>..<candidate_ref>` on the candidate branch, run inside a fresh
worktree, restores the tree to `base_revision` on every declared surface and
as a whole, and the plan's `verify` commands succeed on that restored tree.
The record says so in two lists. `exercised` names `range_checks`, `revert`,
`tree_restoration`, and `verify` (the last only when the plan declared a
verify command; otherwise `verify` moves to `not_exercised`). `not_exercised`
always names `fleet_update`, `production_traffic`, and
`merge_commit_revert`. The drill rehearses a range revert on the candidate
branch; the real `rollback()` runs `git revert -m 1 <merge_sha>` against
`origin/main` after a merge commit, or a plain revert of a squash-merged
commit. The two operations differ, and the drill still stands as evidence for
the real rollback because they share this package's step executor
(`runner.run_step`), the same restoration check (`drill.assert_restored`), and
the same declared surfaces. The dashboard renders a drilled release as
"drilled (worktree)" for the same reason.

`drill.run(release)` executes in this order, inside a worktree at
`<retention root>/drills/<release id>/<timestamp>/` created with `git worktree
add --detach`. Any other path is refused before anything is created
(`DrillRefused("CHECKOUT_PATH")`): the slot must sit under the retention root
and outside every git checkout. When the repository has a `.venv`, the whole
directory is symlinked into the worktree (a linked worktree carries none of
its own), so a `verify` command such as `scripts/pytest-clean.sh` finds an
interpreter there; the link is removed with the worktree and never followed. A release past `proposed` is refused
`NOT_PROPOSED`; an option-shaped ref, an invalid surface, a rollback plan that
is not a JSON object, and a missing repository are refused `BAD_REF`,
`BAD_SURFACE`, `BAD_PLAN`, and `NO_REPO`.

1. Resolve both SHAs and record `git diff --stat base..candidate`.
2. Three pre-revert range checks, each a recorded `fail` that stops the drill
   before any revert runs:
   - `BASE_NOT_ANCESTOR`: `git merge-base --is-ancestor <base> <candidate>` is
     nonzero. A candidate that rebased onto or merged newer `main` has a base
     that is no longer in its history, and `<base>..<candidate>` would then
     include unrelated commits; the drill refuses to revert what the release
     did not introduce.
   - `MERGE_COMMITS_IN_RANGE`: `git rev-list --merges <base>..<candidate>` is
     non-empty, listed under `merges`. A range revert aborts on a merge commit,
     and reverting each with `-m 1` rehearses a different operation from the
     one the plan declares; the operator re-proposes from a linear branch.
   - `UNDECLARED_SURFACE_CHANGED`: a path in `git diff --name-only <base>
     <candidate>` is neither a declared surface nor under a declared directory,
     listed under `paths`. This is the check that makes the declared surfaces
     a claim the drill can falsify.
   - `DENIED_SURFACE_CHANGED`: a changed path is on the candidate denylist,
     listed under `paths`. The denylist already refuses a declared directory
     that encloses an entry at proposal; this check reads the paths the
     candidate actually changed, so a charter edit under any declared surface
     stops here before a revert is rehearsed.

   The path lists behind these checks (and the revert's unmerged paths, and
   the restoration's differing paths) are read from the command's full
   output. The step record keeps a 2 KB tail of each stream, and git sorts
   paths, so a check that read the tail of a long listing would miss exactly
   the `.githooks/` and `config/` entries that sort first.
3. `git revert --no-commit <base>..<candidate>`; a conflict is `fail` with
   `reason: revert_conflict` and the unmerged paths.
4. Restoration: `git diff --quiet <base> -- <surface>` per surface, then `git
   diff --quiet <base>` over the whole tree. Any nonzero exit is `fail` with
   `reason: residue` and the differing paths. After a clean revert of a linear
   range the tree equals the base by construction, so this is the
   sequence-completed invariant: it catches a revert that stopped partway, a
   hook that rewrote a file, or a step executor bug.
5. Each `verify` command, `shlex.split` and run without a shell in the
   worktree, capped by `TIMEOUTS.improvement_drill_verify_seconds` (default
   300, see [Config Timeout Catalog](config-timeout-catalog.md)). A timeout is
   `fail` with `reason: verify_timeout`; a nonzero exit is `verify_failed`.
6. Restoration again, so a verify command that wrote a file is caught.
7. `finally`: `git worktree remove --force` and `git worktree prune`, then a
   direct delete of any slot git left behind.

The record written to `rollback_drill` is `{drilled_at, base_revision,
candidate_ref, worktree, diff_stat, steps: [...], restored, result, reason,
paths, exercised, not_exercised, seconds}`; each step carries `name`, `argv`,
`returncode`, `seconds`, `timed_out`, and the last 2 KB of stdout and stderr.
`drill_log` is the full transcript on the verifying artifact store, re-hashed
on every load; a corrupted transcript raises `ArtifactIntegrityError` through
`read_drill_log` instead of loading as something else.

`drill --sweep` removes drill slots older than a day (`SWEEP_AGE_SECONDS`),
aged by the timestamp in their name so a slot from a hard-killed drill is
swept even when the filesystem later touched it. A slot the checkout guard
refuses is logged and left in place; the sweep continues to the next one. The
drill's own `finally` handles every exception path; only a SIGKILL leaves
residue.

## Approval

`approve` requires a drill whose `result` is `pass` and whose `drilled_at` is
not before `outcome.rollback_plan_written_at`, a human `--approved-by`, and the
same pinned charter digest the release was proposed under. It records
`promotion_gate(project_key).as_dict()` on the row, so the record shows what
the gate answered when a human approved, stamps `approved_by` and
`approved_at`, and writes a provisional `observation_window_ends_at =
approved_at + window_days`. The provisional value is restamped at exposure.

## Opening the PR

`open_pr` runs `gh pr create --head <candidate_ref> --base main --body-file
<tmp>` through the runner and records `exposure.pr_number`, `pr_url`, and
`pr_base`. The body carries the evaluation's verdict, primary endpoint, effect,
confidence interval, and correction, the contract and charter digests, the
rollback plan, the drill record's result and both lists, and the observation
plan. Merging is the pipeline's and a human's; nothing in this package calls
`gh pr merge`.

## Exposure and the observation window

Exposure is anchored on the merge, never on the call. `expose` resolves the PR
through `gh pr view <n> --json mergeCommit,mergedAt,state`, refuses
`PR_NOT_MERGED` unless `state == "MERGED"`, and sets `exposed_at` to
`mergedAt`. `expose` is operator-invoked, so any delay between the merge and
the call would otherwise put post-merge rows inside the baseline and start the
window after the change was live.

The baseline is `observation.measure(project_key, merged_at - baseline_days,
merged_at)`, the half-open span `[merged_at - baseline_days, merged_at)`, so
no post-merge row can land in it. It is frozen onto `outcome.baseline` and
never recomputed. Before measuring, `expose` refuses `EVIDENCE_EXPIRED` when
`now - (merged_at - baseline_days) > EVIDENCE_TTL_DAYS`: the baseline's oldest
rows have already expired, and a late call would freeze an undercount as the
baseline. The remedy is a shorter `baseline_window_days` on a fresh proposal.

`expose` writes `exposure.merge_sha` and `exposure.merged_at`, restamps
`observation_window_ends_at = merged_at + window_days`, and appends two
history events: `exposed` with `merged_at`, `expose_called_at`, and
`merge_sha`, so a late call is visible on the record, and `window_restamped`
with `from` (the approve-time value) and `to`.

### Measurement

`OBSERVATION_METRICS` are `corrections_total`, `corrections_architectural`,
`coverage_ticks`, and `architectural_correction_rate`. `measure` reads
`ImprovementEvidence.recent(project_key, limit=READ_LIMIT)` (the read the
dashboard performs; `READ_LIMIT = 1000`, newest first), filters by
`created_at`, counts corrections by kind and classification, counts coverage
rows by the `coverage:` prefix on `source_ref`, and computes the rate as
architectural corrections per coverage tick, `None` on a zero denominator. Raw
counts and denominators travel beside every rate.

A capped, newest-first read can drop a busy span's oldest rows without any
error. `measure` reports `truncated: True` when the read returned `limit` rows
and the oldest of them is still inside the window, and a truncated span is
never scored: `compare_windows` returns `undetermined` with
`reason: EVIDENCE_TRUNCATED`, because an undercounted baseline would read as
`regressed`. A read that raised is `unavailable: True` and
`reason: EVIDENCE_UNAVAILABLE`.

Evidence rows expire after 30 days. `EVIDENCE_TTL_DAYS = 30` in
`observation.py` is pinned equal to `ImprovementEvidence._meta.ttl` by a test.

### Closing the window

`close_window` refuses `WINDOW_OPEN` before `observation_window_ends_at`
unless `--force` with a reason, recorded under `outcome.forced`. It writes
`closed_at`, `window_shortfall_days = max(0, window_days - (closed_at -
exposed_at).days)` (zero on an on-time close, positive only under `--force`),
and the `falsifier`.

When `now - exposed_at > EVIDENCE_TTL_DAYS`, the window's early rows are gone;
the outcome is `undetermined` with `reason: EVIDENCE_EXPIRED` and no window is
measured, because a count over a gap would report the gap as a number.
Otherwise the window is measured over `[exposed_at, min(now, ends_at))` and
scored by `compare_windows`:

- `deltas` carries both sides with raw counts, `days`, per-day normalizations,
  `rate_delta`, and `coverage_per_day_ratio`.
- `baseline_band` is a Wilson score interval at 95% over the baseline's own
  counts, treating the rate as the proportion of coverage ticks that produced
  an architectural correction; `upper` is the noise band. A ratio above 1 is
  clamped to 1, which makes every rate `held` against it, the honest answer
  when the proportion model has no noise estimate.
- `detection_declined` is true when the window's coverage ticks per day fall
  below `DETECTION_DECLINE_RATIO = 0.8` of the baseline's. Charter §11: fewer
  detected corrections with less detection is no improvement, so this is
  `undetermined` with `reason: DETECTION_DECLINED`, never `held`.
- A zero denominator on either side is `undetermined` with
  `reason: ZERO_DENOMINATOR`; the partial renders "no denominator" rather than
  0%.
- Otherwise `held` when the window rate is at or under the band's upper bound,
  `regressed` when it rises past it.

`claim_level_2_supported` is true only on `held` with `window_shortfall_days
== 0` and `effect_of(evaluation, primary_endpoint) > 0`, where the primary
endpoint is read from the experiment's frozen protocol. The falsifier written
to `outcome` is the observation that would overturn the claim: the
architectural correction rate over a later window of the same length exceeds
the baseline band with coverage at or above baseline.

On `held` the release transitions to `accepted` with
`rollback_recommended: False`. Every other verdict leaves the release
`observing`, appends a `window_closed` history event, and sets
`rollback_recommended: True`. `close_window` never performs a rollback; a
human runs `rollback`. `close-window --due` lists observing releases whose
window has ended, oldest end first, for an operator or a future controller
tick to act on.

## The promotion gate

`promotion.promotion_gate(project_key)` answers `PromotionGate(automated,
unmet, detail)`, and `automated` is `False` in this build. Two preconditions,
reported in order:

| Precondition | The event that satisfies it | How the gate decides today |
|---|---|---|
| `credential_separation` | A separate process identity for evaluation, with candidate execution unable to read evaluator secrets or production credentials, attested by a record | Unmet by construction. No module can observe that property of the execution environment, no attestation record exists, and a worktree is not a security boundary. A constant `False` |
| `charter_names_reversible_surfaces` | Tom amends `docs/improvement-charter.md` (charter §12: Tom is its only author) with a section naming the reversible surfaces | Reads the pinned charter's `text` through the verifying store and looks for a depth-one-to-three heading matching `reversible surfaces`, case-insensitive. Charter v2 has none |

Every answer comes from the module's constants and the pinned charter row. No
`config.settings` field, environment key, or file flag reads into the gate; a
Verification row in the lane 6 plan asserts no promotion flag exists in
`config/settings.py` or `.env.example`. `promote_automatically(release_id)`
raises `PromotionDisabled` naming every unmet precondition and never writes;
there is no promotion implementation behind it. It exists so the refusal is a
code path with a test rather than a sentence in a docstring. A synthetic
charter with a "Reversible surfaces" heading clears precondition 2 alone, and
the gate still refuses on precondition 1.

## The denylist

`CANDIDATE_SURFACE_DENYLIST` names the surfaces a candidate never changes:

| Entry | Why |
|---|---|
| `docs/improvement-charter.md` | The charter; Tom is its only author |
| `models/improvement_charter.py` | The charter's loader and owner check |
| `config/identity.json` | The structured identity file |
| `tools/improvement_release/` | This package; the loop cannot rewrite its own release gate |
| `.env`, `.env.example` | Secrets and their declarations |
| `.githooks/` | The hooks that enforce the pipeline |

Every surface goes through `posixpath.normpath` before comparison, and a
surface that is absolute, escapes the repo, carries a glob character, or names
the repo root is refused outright (`InvalidSurface`, a subclass of
`SurfaceDenied`), since a denylist that
`./docs/../docs/improvement-charter.md` walks around is not a denylist. An
entry ending in `/` denies the directory and everything under it, and a
surface that encloses any entry (`docs`, `models`, `config`, `tools`) is
denied for the same reason: the drill treats a declared directory as covering
everything beneath it, so `--surfaces docs` would carry the charter through.
The drill also checks the paths a candidate actually changed against the
list (`DENIED_SURFACE_CHANGED`). Identity and persona files beyond
`config/identity.json` live in the private vault outside the repo, which a
repo-relative surface cannot name.

The denylist is one of three checks, and it catches only what it names. It
does not refuse a candidate that edits `models/__init__.py` to import a
different charter module, or one that changes `load_from_file`'s owner check
from a file outside the list. The loader's owner refusal
(`ImprovementCharter.load_from_file` returns `None` for any owner other than
Tom) and the pinned-digest gate at proposal and approval both fire regardless
of surface names; `models/improvement_charter.py` is on the list precisely
because the loader is the second guard.

## Rollback

`rollback` is the one deliberately pipeline-exempt path in this lane. It is an
incident surface, and a revert that waits on critique and review is a
rollback that arrives after the damage. Before any step runs, the worktree
slot `<retention root>/drills/<release id>/<timestamp>` goes through the
drill's `refuse_checkout_path` (a slot outside the retention root or inside a
git checkout is `ROLLBACK_STEP_FAILED` with `CHECKOUT_PATH` in the detail),
and a `--branch` is refused when it starts with `-` or fails `git
check-ref-format --branch`, so `--branch=--prune` never reaches the fetch.
With `target = branch or "main"`:

1. `git fetch origin <target>` first. A local `main` behind `origin/main` is
   the ordinary state of a machine during an incident; a revert committed on
   that stale parent is rejected non-fast-forward on every push, and every
   re-run would refuse with the same stderr until the operator guessed the
   cause. Under `--branch <name>` for a branch the remote does not have, the
   fetch fails and the worktree comes from `origin/main` instead.
2. `git worktree add --detach <slot> origin/<parent>` under the retention
   root, the same slot shape the drill uses, with the repository's `.venv`
   symlinked in so the shared `.githooks/pre-push` runs under the repo's
   interpreter at push time. `parent_sha = git rev-parse origin/<parent>` is
   recorded.
3. `git revert --no-commit -m 1 <merge_sha>` when the merge commit has two
   parents, plain `git revert --no-commit <merge_sha>` when the PR was
   squash-merged; then `git commit -m "Roll back improvement release <id>:
   <reason> (Refs #3218)"`, so `.githooks/commit-msg` accepts it. `git
   update-ref refs/improvement-rollback/<release id> <revert sha>` in the
   repository then keeps the revert reachable after the worktree is removed;
   without it the commit would be unreferenced and gone at the next prune.
4. `assert_restored` against `base_revision` on the declared surfaces, recorded
   under `outcome.rollback.verification` with a note that it is informational:
   `main` has moved since `base_revision`, so a difference is expected.
5. `git push origin HEAD:refs/heads/<target>`, fully qualified because a
   detached worktree's `HEAD:<name>` is refused as "not a full refname" when
   the remote has no such branch yet, which is exactly the `--branch
   <new-name>` case.
6. The release transitions to `rolled_back` only when the push returned 0 and
   `git ls-remote origin refs/heads/<target>` resolves to the revert commit,
   checked against the same ref the push targeted. A refused push (branch
   protection, `.githooks/pre-push`, a head that moved after the fetch) or a
   remote head that differs appends `{"event": "rollback_push_refused",
   stderr, revert_sha, parent_sha, target, detail}` to `outcome.history`,
   leaves the state unchanged, and raises `ROLLBACK_PUSH_REFUSED`. The revert
   commit is reported, and stays reachable as
   `refs/improvement-rollback/<release id>` in the local repository for as
   long as that ref exists, so the operator can push it by hand (`git push
   origin refs/improvement-rollback/<id>:refs/heads/<target>`) or re-run with
   `--branch`.
7. `finally`: the worktree is removed.

Every step's transcript is persisted, bounded to the last
`ROLLBACK_TRANSCRIPT_BYTES` (16 KB): on success under
`outcome.rollback.transcript`; on `ROLLBACK_STEP_FAILED` after a step has run
and on `ROLLBACK_PUSH_REFUSED` under `outcome.rollback_attempt` as `{at,
code, detail, target, steps, transcript}`, beside a `rollback_step_failed` or
`rollback_push_refused` history event. Each new attempt overwrites
`rollback_attempt`; the history keeps one event per attempt.

On success `outcome.rollback` carries `reason`, `merge_sha`,
`merge_commit_parents`, `revert_sha`, `parent_sha`, `pushed_to`,
`worktree_ref`, `rollback_ref`, `verification`, `steps`, `transcript`,
`rolled_back_at`, and `propagation: "requires /update on fleet machines"`,
naming the step the rollback did not perform. A revert pushed to `main` reaches other machines
through the ordinary `/update`. Under `--branch`, `propagation` also names the
PR the operator must open, the record carries `pr_command`, and the CLI prints
it; that is the pipeline-shaped alternative for a rollback that is not urgent.

## The recursive comparison

Claim level 3 says a changed research process produces greater validated gain
per comparable budget on fresh opportunities. The comparison is the
measurement behind that sentence.

### Research process digest

`ResearchProcessSpec` names one research process: `selection_rule`,
`investigation_budget_split` (keyed by `INVESTIGATION_KINDS`; an empty split
is a legitimate "no split declared", and a non-empty one must sum to 1 within
`[0.99, 1.01]`), `revision_cadence_seconds`, `planner_prompt_digest`,
`skill_digest`, and `extra`. `research_process_digest(spec)` is
`"sha256:<hex>"` of exactly `json.dumps(asdict(spec), sort_keys=True,
separators=(",", ":")).encode("utf-8")`, so key order never changes it. That
byte form is a contract with lane 5, which stores the same bytes on every
`ImprovementModelRevision` as `research_process_spec` and sets
`research_process_digest` only by importing this function; there is one
hashing routine, this one. A spec naming an unknown investigation kind or
summing off 1 raises `ValueError` rather than digesting.

### Freshness

Both arms must run on opportunities neither process has already worked, or
the arm that saw them first inherits a head start. `fresh_opportunities`
decides by record lookup: the `ImprovementCase` exists and is in
`OPEN_CASE_STATES`, no `ImprovementExperiment` or `ImprovementInvestigation`
cites it as `case_id`, and no prior comparison experiment lists it in its
manifest's `opportunity_ids`. It returns `(fresh, excluded)` with a reason per
excluded id from `NOT_FOUND`, `NOT_OPEN`, `HAS_EXPERIMENT`,
`HAS_INVESTIGATION`, `IN_PRIOR_COMPARISON`. Nothing is inferred from titles or
timing.

### The frozen contract

`compare.freeze(arm_a, arm_b, opportunity_ids, budget_cap, ...)` refuses
`NO_OPPORTUNITIES`, `OPPORTUNITY_NOT_FRESH` (naming every excluded id and its
reason), `INCUMBENT_PROCESS_UNKNOWN` (`arm_a` omitted and no `current`
revision carries a digest), and `REVISION_CONFLICT` before writing anything.
It builds the protocol (`arms: {a, b}`, `opportunity_ids`,
`opportunity_set_digest` over the sorted id list, `budget_cap` in four fields,
`primary_endpoint: validated_gain`, `minimum_worthwhile_effect`,
`stopping_rule: finite batch`, `evaluator_version`), freezes it through lane
4's `freeze_protocol`, and writes an `ImprovementExperiment(state="frozen",
candidate_surfaces=["research_process"])` whose manifest cites `protocol_ref`,
the ids, the set digest, and both arm digests. The contract digest is lane
4's `compute_contract_digest` over the saved row, so lane 4's `load_protocol`
and digest verification work on it unchanged.

### The two arm seams

`ArmRunner` is a `Protocol` with one method, `run(process_digest,
opportunity_ids, budget_cap, arm_run_id) -> ArmResult`. `ArmResult.gains` maps
opportunity id to validated gain, with `None` meaning the arm rejected the
opportunity or reached no conclusion, scored as 0; `ArmResult.budget_use` is
the arm's own report. `compare run` resolves a runner in two ways:

- `--arm-runner <module>:<attr>` (for example
  `tools.improvement_plan_arm:PlannerArmRunner`) is resolved with
  `importlib.import_module` then `getattr(module, attr)()` at the moment the
  comparison runs. An `ImportError`, `AttributeError`, or malformed spec is
  `ArmRunnerAbsent("ARM_RUNNER_ABSENT", detail)` and the CLI exits 2.
- With the flag omitted, `get_arm_runner()` returns the runner registered
  in-process by `register_arm_runner(...)`, and raises `ArmRunnerAbsent`
  otherwise. This path serves a `compare` mounted inside a process that
  registered its runner, such as lane 3's `valor-improve`.

`ReplayArmRunner` returns gains and budget use from a fixture keyed by process
digest and admits its unit-3 spend through lane 7's `admit()` under the
`arm:<arm_run_id>:` resource prefix, so `LedgerBudgetReader` is exercised on
the same path a production runner takes. The production `ArmRunner` is lane
5's planner tick; `tools.improvement_recursion.arms` is the only module of
this lane it imports, taking `ArmResult`, `BudgetUse`, `register_arm_runner`,
and `get_arm_runner`.

### Budget accounting

`BudgetCap` and `BudgetUse` each carry four fields, numbered as the parent
plan's Gap D numbers the budget units: `subscription_turns` (unit 1, the
subscription lane slot; a concurrency budget, accounted as the arm's reported
turn count rather than money), `unit2_usd` (unit 2, daily paid inference),
`unit3_usd` (unit 3, weekly infrastructure), and `wall_seconds` (elapsed
time). A `None` use means unknown, never zero. Dollars come from
records, never from the arm's own report: `LedgerBudgetReader.unit3_usd`
sums `settled_usd` (else `amount_usd`) over `InfrastructureReservation` rows
in `reserved` or `settled` whose `resource` starts with `arm:<arm_run_id>:`,
and answers `None` when zero rows match, so an arm that admitted nothing
through the ledger is unknown rather than free. Turns and wall seconds come
from the `ArmResult`, since no record outside the arm carries them.

`unit2_usd` is unmetered until lane 3 lands its paid-inference meter;
`LedgerBudgetReader.unit2_usd` answers `None`, so every comparison today
carries `BUDGET_UNKNOWN:unit2` and no level-3 claim can be made. Charter §8:
uncertain or missing metering is never zero cost.

`budgets_comparable(a, b, cap, tolerance=0.10)` returns `(ok, reasons)`,
checking each unit in field order:

| Reason | Condition |
|---|---|
| `CAP_UNKNOWN:<unit>` | The cap for that unit is `None`; never ok |
| `BUDGET_UNKNOWN:<unit>` | Either arm's use is `None` |
| `BUDGET_EXCEEDED:<arm>:<unit>` | A known use exceeds a known cap |
| `BUDGET_MISMATCH:<unit>` | The two known uses differ by more than `tolerance * cap` |

A unit whose cap is explicitly `0` with `0` use on both sides is ok: a unit
not budgeted is not a mismatch.

### The run

`compare.run(experiment_id)` refuses `NOT_FOUND`, `WRONG_STATE` (not
`frozen`), `CONTRACT_DIGEST_MISMATCH`, `ARMS_IDENTICAL`, and
`REVISION_CONFLICT` before anything runs, then resolves the runner. A missing
pinned charter is `infra_failure` with `CHARTER_NOT_PINNED` before either arm
runs. Arm order is randomized with a seeded RNG and recorded as
`arm_assignment_digest`; both arms run with the same opportunity ids and the
same cap, and any exception from an arm or the accounting read is
`infra_failure` with the experiment moved to `aborted`, never a result.

Statistics are lane 4's: paired deltas `gain_b - gain_a` per opportunity,
clustered by the case's `priority_area` through `evaluate_family` (which
wraps `clustered_bootstrap_ci`), Holm-adjusted. `accept` needs the interval's
lower bound above `minimum_worthwhile_effect` and comparable budgets;
`reject` needs the upper bound below zero and comparable budgets; anything
else, including any budget refusal, is `inconclusive`.

One `ImprovementEvaluation` is written with
`evaluator_version="recursive-comparison/1"`, `blinded=False` (the arms are
processes, and the field is honest rather than decorative),
`trials=len(opportunities)`, `effect=json.dumps({"validated_gain": mean},
sort_keys=True)`, `confidence_interval=json.dumps({"validated_gain": {lower,
upper, n, raw_p_value, adjusted_p_value}}, sort_keys=True)`,
`correction="holm"`, and `notes` as a newline-joined string whose lines carry
the rationale, the arm digests, the run order, the assignment digest, one
`budget=<json>` line with the cap, both arms' use in every unit,
`comparable`, and `reasons`, and one line per refusal reason. These are lane
4's string shapes keyed by endpoint, so `evaluation_read` parses both
evaluator versions on one path and the budget accounting is shown whether or
not the verdict is a claim.

On `accept`, `_write_revision` creates the new `ImprovementModelRevision`
(`research_process_digest` of arm B, `prediction`, `supersedes_id` of the
current revision) and then moves the previous revision to `superseded`, in
that order. A crash between the two writes leaves two `current` rows, never
zero; `run` and the claim report detect more than one `current` revision as
`REVISION_CONFLICT`, and `revision supersede --revision <id> --reason <text>`
is the operator's remedy.

## The claim report

`report.claim_report(project_key)` returns `levels[1..3]`, each `{name,
supported, evidence, confidence_interval, correction, falsifier, why_not}`:

| Level | Supported when | `why_not` otherwise |
|---|---|---|
| 1, a complete research cycle | An experiment has a complete evaluation and its case has left the open states | "no complete cycle recorded" |
| 2, a released change held its measured effect through its observation window | An `accepted` release has `outcome.claim_level_2_supported == True`; the interval is `interval_of(evaluation, primary_endpoint)` | "no accepted release recorded", or the list of accepted releases with each one's reason |
| 3, a changed research process produced greater validated gain per comparable budget on fresh opportunities | A `recursive-comparison/` evaluation has verdict `accept` and `budget_of(evaluation).comparable` is true | "no comparison recorded", or the newest comparison's refusal reasons (`BUDGET_UNKNOWN:unit2`, ...) and verdict |

Each level is computed inside its own guard: a read failure logs a warning and
yields `supported=False` with `why_not="could not be determined: <reason>"`
while the other two levels still answer. Evidence names ids, never totals, and
no count of experiments, patches, or releases appears anywhere in the report
or its rendering. `render(report)` prints it as text (`report --render`).

## The dashboard

`ui/data/improvement.py::get_release_lineage(project_key)` is the fifth
getter, beside `get_coverage`, `get_goals`, `get_intervention_burden`, and
`get_provisional_assumptions`; a test pins that list as an exact list. It
delegates to `lineage.release_lineage`, which joins each release to its
evaluation (verdict, and `effect` and `confidence_interval` for the
protocol's primary endpoint through `evaluation_read`), experiment
(hypothesis, contract digest), and case (title, priority area), newest first,
with `drill: {result, drilled_at}`, `window: {exposed_at, ends_at,
days_remaining}`, and `outcome: {verdict, reason, claim_level_2_supported,
rollback_recommended}`. The result carries `promotion_gate: {automated,
unmet}`, `unavailable`, and `no_releases_yet`.

The partial `ui/templates/improvement/releases.html` at
`/_partials/improvement/releases/` renders one row per release and the gate as
a sentence ("Automated promotion: disabled; unmet: ...") on every load. A
raising lineage read renders "release lineage unavailable" and still renders
the gate sentence; with no releases it says "no release proposed yet; the
first arrives from an accepted evaluation". A release in `observing` with a
`regressed` outcome renders "window closed: regressed, rollback recommended",
never "accepted". There is no number of releases, experiments, or merged
patches anywhere on it.

## `valor-improve-release` reference

Every subcommand takes `--project-key` (default `valor`), prints exactly one
JSON object on stdout, and exits 0 on success, 2 on a refusal
(`ReleaseRefused`, `DrillRefused`, `ComparisonRefused`, `ArmRunnerAbsent`, or
an invalid argument such as an unreadable plan file, reported as
`INVALID_ARGUMENT`), and 1 on an unexpected error with the traceback on
stderr. `--help` on the binary and on every subcommand is authoritative.

| Subcommand | Arguments | Does |
|---|---|---|
| `propose` | `--evaluation`, `--kind`, `--candidate-ref`, `--surfaces ...`, `--rollback-plan <json file>`, `--observation <json file>`, `--base-revision`, `--calibration-ref`, `--argument`, `--repo` | Writes a `proposed` release from an `accept` verdict |
| `drill` | `--release`, `--root`, `--repo` | Runs the rollback drill; returns the release with its `rollback_drill` |
| `drill --sweep` | `--root`, `--repo` | Removes drill worktrees older than a day; returns `swept` |
| `approve` | `--release`, `--approved-by <human>` | `proposed` to `approved` |
| `open-pr` | `--release`, `--base`, `--title`, `--repo` | `gh pr create` from the candidate ref; records the PR |
| `expose` | `--release`, `--repo` | Records the merged PR; anchors the window on `mergedAt`; freezes the baseline |
| `close-window` | `--release`, `--force`, `--reason` | Scores the window; `accepted` on `held` |
| `close-window --due` | | Lists observing releases past their window end |
| `rollback` | `--release`, `--reason`, `--root`, `--repo` | Reverts the merge on freshly fetched `origin/main` and pushes it |
| `rollback --branch <name>` | | Pushes the revert to `<name>` instead and prints the `gh pr create` command as `pr_command` |
| `withdraw` | `--release`, `--reason` | `proposed` or `approved` to `withdrawn` |
| `show` | `--release` | The row, its drill record, and the drill log verified on load |
| `gate` | | The promotion gate's answer and both preconditions' detail |
| `compare fresh` | `--candidates ...` | Splits case ids into `fresh` and `excluded` with reasons |
| `compare freeze` | `--arm-a`, `--arm-b`, `--opportunities ...`, `--budget <json file>`, `--minimum-worthwhile-effect` | Freezes a comparison contract; each arm is a `sha256:` digest or a process spec JSON file, and `--arm-a` defaults to the current revision |
| `compare run` | `--experiment`, `--rng-seed` | Runs a frozen comparison with the registered arm runner |
| `compare run --arm-runner <module>:<attr>` | | Resolves the named `ArmRunner` by lazy import at run time |
| `revision supersede` | `--revision`, `--reason` | Moves one `current` model revision to `superseded` by hand (the `REVISION_CONFLICT` remedy) |
| `report` | `--render` | The three-level claim report as JSON, or as text with `--render` |

`propose`, `drill`, `open-pr`, `expose`, and `rollback` accept `--runner-log
<path>`, which appends one JSON line per subprocess call so a test or an
operator can inspect every `git` and `gh` invocation. Every subcommand runs on
the wall clock; the `now=` keyword the library functions take exists for their
unit tests and has no command-line spelling, so nothing on the binary can
close a window early without `--force` or sidestep `EVIDENCE_EXPIRED` and
`DRILL_STALE`. Every `git` and `gh` call outside the drill's verify step is
bounded by `TIMEOUTS.git_subprocess_s`, so a hung remote becomes a recorded
nonzero step instead of a CLI that never returns.

Rollback and observation plan files:

```json
{"kind": "git_revert", "verify": ["scripts/pytest-clean.sh tests/unit/test_x.py -q"], "propagation": "/update"}
```

A verify command runs inside the drill worktree, which carries a `.venv`
symlink to the repository's venv when the repository has one; that is what
lets `scripts/pytest-clean.sh` (which refuses a linked worktree without a
usable venv) run there. On a checkout without a `.venv`, declare a command
that needs none.

```json
{"window_days": 14, "baseline_window_days": 14, "metrics": ["corrections_total", "corrections_architectural", "coverage_ticks", "architectural_correction_rate"]}
```

Budget cap file for `compare freeze`:

```json
{"unit2_usd": 5.0, "unit3_usd": 5.0, "subscription_turns": 40, "wall_seconds": 7200}
```

The operator path, end to end: `propose` → `drill` → `approve` → `open-pr` →
the PR merges through the ordinary pipeline → `expose` → the window elapses →
`close-window` → `accepted`, or `rollback` → `rolled_back`. The lineage
partial and `report` then say whether level 2 is supported. The comparison
path: `compare fresh` → `compare freeze` → `compare run --arm-runner ...` →
an evaluation with verdict and budget accounting → on `accept`, a new model
revision → `report` says whether level 3 is supported.

## See also

- [Improvement Controller](improvement-controller.md): records, authorities, evidence collection, dashboard
- [Improvement Evaluation](improvement-evaluation.md): the harness that produces the `accept` verdict a release starts from
- [Config Timeout Catalog](config-timeout-catalog.md): `improvement_drill_verify_seconds`
- [`docs/plans/improvement-controller-lane-6-promotion-rollback-and-recursion.md`](../plans/improvement-controller-lane-6-promotion-rollback-and-recursion.md): the lane 6 plan with the mutation proofs and verification rows
- [Capability matrix](../plans/critiques/recursive-self-improvement-capability-matrix.md): what is implemented versus deployed, measured, and known to help
