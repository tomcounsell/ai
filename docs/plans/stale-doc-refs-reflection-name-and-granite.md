---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2853, https://github.com/tomcounsell/ai/issues/2839
last_comment_id: 5337658489
companion_last_comment_id: 5324617995
revision_applied: true
revision_applied_at: 2026-08-19T06:34:53Z
---

# Stale doc/prose references: wrong reflection name, deleted granite package

Two-issue lane. Anchor **#2853** (wrong reflection name in prose), companion **#2839**
(doc points at a deleted package path). Both are prose-only corrections to
already-correct systems. The implementation PR body carries **`Closes #2853`** and
**`Closes #2839`**.

**Why `tracking:` names both issues.** `find_plan_path` resolves a plan on exactly one
rung: a `^tracking:` frontmatter line naming the issue. An earlier `companion_tracking:`
key had no consumer anywhere in the repo, so `find_plan_path(2839)` returned `None` and
any stage dispatched under #2839 would hit a hard `CRITIQUE_PLAN_UNRESOLVABLE` refusal.
Both URLs now sit on the single `tracking:` line, which was verified against all four
consumers: `find_plan_path` resolves 2853 **and** 2839 (and still correctly rejects the
boundary cases 285 and 28539); `yaml.safe_load` reads one string-valued key;
`test_plan_docs.py`'s `_TRACKING_RE` invariant matches; and `extract_tracking_issue` +
`merged_branch_cleanup`'s first-match regex resolve the migration anchor to **2853**, the
issue whose closure should archive this plan.

## Problem

Two independent classes of stale prose, both discovered by audit rather than by
breakage. Neither changes runtime behavior; both mislead the next reader (human or
agent) into a wrong mental model of a live subsystem.

**Current behavior:**

*#2853 — wrong reflection name.* Four comment/docstring sites name the popoto index
cleanup reflection `popoto-index-cleanup`. No reflection by that name is registered.
The registered name is `redis-index-cleanup` (`config/reflections.yaml:53`). A reader
who greps `popoto-index-cleanup` in `config/reflections.yaml` finds nothing and
concludes the cleanup is unscheduled — which is exactly the wrong conclusion that
produced issue #2853's own false premise.

*#2839 — deleted target.* `docs/features/standardized-enums.md:19` describes
`SessionType.GRANITE` as labelling "Direct invocations of the standalone
`valor-granite-loop` CLI (`tools/granite_interactive_tui_poc/cli.py`)". That package
does not exist, no successor exists (`tools/granite_loop/` was the #1643 rename target
and it is gone too), and `pyproject.toml [project.scripts]` has no `valor-granite-loop`
entry. The same cell also claims "Bridge sessions that run through the granite PTY
container are typed `ENG`" — the PTY container was deleted with plan #1924.

**Desired outcome:**

- Every live prose site naming the cleanup reflection uses `redis-index-cleanup`.
- The daily-cadence prose **stays** everywhere it appears — it is correct and is
  asserted by a tracked test.
- `standardized-enums.md`'s `SessionType.GRANITE` row says what `config/enums.py`'s
  own `SessionType` docstring says: a historical value whose producer was deleted,
  retained so pre-cutover Redis records keep hydrating.
- Closure is proved by repo-wide token sweeps, not by an enumerated site list.

## Freshness Check

**Baseline commit:** `f491306c5`
**Issues filed at:** #2853 — 2026-08-18T03:03:37Z; #2839 — 2026-08-17T08:08:36Z
**Disposition:** Minor drift — the code is unchanged, but #2853's stated premise was
already false at filing time and its site inventory was incomplete. Corrected below and
recorded as a `## Recon Summary` on both issues.

**File:line references re-verified:**

| Reference | Issue's claim | Verified state |
|---|---|---|
| `worker/__main__.py:743-749` | Sole production caller of `run_cleanup()`, once per worker start | **Holds.** |
| `config/reflections.yaml` | `redis-index-cleanup` calls `agent.agent_session_queue.cleanup_corrupted_agent_sessions` | **FALSE.** Lines 53-62 carry `execution_type: function`, `callable: "scripts.popoto_index_cleanup.run_cleanup"`, `every: 86400s`, `enabled: true`. |
| `~/Desktop/Valor/reflections.yaml` (resolved ahead of the checkout copy by `agent/reflection_scheduler.py:183`) | not checked by the issue | **Byte-identical to `config/reflections.yaml`** (`diff` clean). Both register `run_cleanup` daily. |
| `tests/integration/test_reflections_redis.py:199,204` | not cited | Asserts `callable == "scripts.popoto_index_cleanup.run_cleanup"` and `parse_every_duration(entry["every"]) == 86400`. The daily cadence is **asserted by a tracked test against the machine-local `config/reflections.yaml`, which is byte-identical to the vault copy** — the test file is tracked, the YAML it reads (line 193) is not (`.gitignore:8`), so the vault parity is what actually carries the guarantee. |
| `scripts/popoto_index_cleanup.py:4-5` | stale | **Accurate.** Leave alone. |
| `scripts/popoto_index_cleanup.py:306-307` | stale | **Stale — wrong name only.** Cadence claim correct. The sentence spans both lines: 306 ends "``run_cleanup`` runs on", 307 carries "the daily ``popoto-index-cleanup`` reflection plus once per worker start." |
| `docs/features/popoto-index-hygiene.md:35, 54, 61, 63` | stale | **Accurate.** All name `redis-index-cleanup`. Leave alone. |
| `bridge/email_bridge.py:1087` | not in the issue's list | **Stale — wrong name**, and `nightly` on the same line. |
| `models/ghost_reconcile.py:23-24` | not in the issue's list | **Stale — wrong name**, `nightly` on 23; the `once/24h` parenthetical on 24 is correct. |
| `models/ghost_reconcile.py:36` | not in the issue's list, **and not in the critique's** | **Stale — `nightly` only** ("the same one the nightly reflection calls"). Found during revision by measuring a `nightly` anti-criterion across the four named files: `grep -rc nightly` returned `ghost_reconcile.py:2`, not `:1`. Carries no `popoto-index-cleanup` token, so **neither normative sweep can reach it** — it is exactly the residue class this lane exists to close. Added to Task 1. |
| `models/dedup.py:69-70` | not in the issue's list | **Stale — wrong name** on 70, `nightly` on 69. A builder editing only line 70 leaves `nightly` behind. |
| `docs/features/standardized-enums.md:19` | points at `tools/granite_interactive_tui_poc/cli.py` | **Holds.** `ls -d tools/granite*` → no match; no `valor-granite-loop` in `pyproject.toml`. |
| `config/enums.py:20-25` | not cited | Correct historical framing; the model wording to copy. Its `"Removal is #1927's scope"` pointer is itself stale — see Task 3. |
| `docs/features/bridge-worker-architecture.md:564` | not cited | Already correct ("the standalone CLI ... no longer exists"). No change. |
| `models/agent_session.py:4` and `:102` | raised in #2839 comment `5324617995` | Already correct: `"granite" persists on historical records only`. Third in-repo wording precedent. No change. |

**Cited sibling issues/PRs re-checked:**
- PR **#2833** — MERGED 2026-08-18T02:57:37Z. Fixed only its own PR-introduced "daily"
  claims and deferred the pre-existing prose here. Its diff touched none of the four
  stale sites.
- Issue **#2636** — CLOSED 2026-08-18T02:57:38Z by #2833. Landscape unchanged for this work.
- Issue **#1927** (AgentSession schema diet) — **CLOSED** without removing
  `SessionType.GRANITE`. This is why the new doc prose must not inherit `enums.py`'s
  `"Removal is #1927's scope"` pointer.
- Plan **#1924** (granite PTY teardown) — completed; `docs/plans/completed/granite-pty-teardown.md`
  confirms `tools/granite_loop/` and the `valor-granite-loop` entry point were deleted.

**Commits on main since the issues were filed (touching referenced files):**
`git log --since=<createdAt> -- scripts/popoto_index_cleanup.py docs/features/popoto-index-hygiene.md config/reflections.yaml bridge/email_bridge.py models/ghost_reconcile.py models/dedup.py docs/features/standardized-enums.md config/enums.py`
returns **no commits**. Nothing has moved.

**Active plans in `docs/plans/` overlapping this area:** none. `move-bridge-utc-to-utils.md`
is the only in-flight plan touching `bridge/`, and it touches `bridge/telegram_bridge.py`
time handling, not `email_bridge.py:1087`'s comment block.

**Notes:** `granite` alone is **not** a dead token — it also names the live local
ollama classifier model (`tools/classifier.py`, `agent.llm.run_typed_local`). Sweeps
must key on the deleted-package tokens (`granite_interactive_tui_poc`, `granite_loop`,
`valor-granite-loop`), never on bare `granite`.

## Prior Art

- **PR #2833** — "Bound `Job.recent_for_room` via a direct bounded reverse-range read
  (#2636)". During its review rounds, judges audited the popoto cleanup cadence and
  produced the (incorrect) finding that seeded #2853. It fixed its own introduced
  claims and explicitly deferred the pre-existing prose to #2853. **Relevant:** this
  plan is that deferral, but on the corrected premise.
- **PR #2671** — "Register Job in the guarded index-repair sweep (#2640)". Established
  the `_GUARDED_ELSEWHERE` two-part contract documented at
  `docs/features/popoto-index-hygiene.md:48-58`. Not modified here.
- **Plan #1643** (`docs/plans/completed/sdlc-1643.md`) — renamed
  `tools/granite_interactive_tui_poc/` → `tools/granite_loop/`. **Directly relevant:**
  it invented the token-driven sweep gate this plan reuses, precisely because its own
  line-enumerated task list missed a stray site twice. Its closure grep excluded
  `docs/plans/completed/` — the same exclusion applies here.
- **Plan #1924** (`docs/plans/completed/granite-pty-teardown.md`) — deleted the PTY
  substrate and `tools/granite_loop/`. It noted `valor-granite-loop` in `pyproject.toml`
  was "already dangling" and removed it. `standardized-enums.md:19` is the residue that
  teardown missed.
- No prior attempt exists to fix either stale-prose site. This is the first pass.

## Research

No relevant external findings — proceeding with codebase context. This is a
prose-only correction to internal documentation with no external libraries, APIs, or
ecosystem patterns involved. Phase 0.7 WebSearch was skipped for that reason.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2833 (#2636) | Corrected PR-introduced cadence prose; deferred pre-existing prose to #2853 | Its closure criterion was a **diff-scoped** grep (`git diff main...HEAD -U0 \| grep '^+' \| grep -i daily`). A diff-scoped gate can never see pre-existing text, so the residue survived by construction. Worse, the reviewers' cadence finding was itself wrong — a grep that stopped six lines short of `callable:` in `reflections.yaml`. |
| Plan #1924 (PR #1930) | Deleted `tools/granite_loop/`, `agent/granite_container/`, the `valor-granite-loop` entry point | Swept source and config, not `docs/features/`. `standardized-enums.md:19` was never in its inventory. |
| Plan #1643 | Renamed the granite package; ran a token-driven sweep to exit 1 | Its sweep **excluded `docs/plans/completed/`** and ran before #1924 deleted the package, so `standardized-enums.md:19` was correct at the time and legitimately untouched. |

**Root cause pattern:** every prior gate was scoped narrower than the claim it was
meant to certify — diff-scoped instead of tree-scoped (#2833), source-scoped instead
of docs-inclusive (#1924). This plan's Verification rows are **repo-wide token sweeps**
so closure cannot outrun the claim.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** none. No function signature, enum member, config key, or
  Redis schema changes. `SessionType.GRANITE` is untouched.
- **Coupling:** unchanged.
- **Data ownership:** unchanged.
- **Reversibility:** trivially reversible — every edit is a comment, docstring, or
  markdown table cell.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (both premises re-verified at plan time; nothing needs a human call)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. Every edit is text in files
already tracked in the checkout.

## Solution

### Key Elements

- **Reflection-name correction (#2853)**: four sites that say `popoto-index-cleanup`
  say `redis-index-cleanup`, and a fifth (`models/ghost_reconcile.py:36`) that says
  only `nightly` says `daily`. The daily/24h cadence language in each is preserved
  verbatim — it is correct.
- **Granite row rewrite (#2839)**: the `SessionType.GRANITE` table row in
  `standardized-enums.md` is restated as a historical value, mirroring the
  `SessionType` docstring in `config/enums.py`, with no file path and no PTY-container
  claim. The section's lead-in (line 13) is brought into the same shape, since it
  currently presents `granite` as a peer live discriminator eight lines above the row
  being corrected.
- **Stale-pointer replacement in the authority**: `config/enums.py`'s docstring points
  removal at #1927, which closed without removing GRANITE. The pointer is **replaced**,
  not deleted — the live state ("the schema-diet issue closed without acting, so a
  fresh decision is needed") is what the next reader of the enum actually needs, and
  deleting it would move that knowledge into a plan doc both closure sweeps exclude.
- **A durable guard**: `tests/unit/test_stale_reference_sweep.py` asserts three things
  stay clean — the two token sweeps, plus a `nightly` assertion scoped to the four named
  files. The plan's own Verification rows are archived out of reach at merge
  (`docs/plans/` is excluded by both sweeps); a tracked test is the only form of this
  gate that survives. This is the direct answer to the root-cause pattern below — every
  prior gate died with the artifact that carried it. The `nightly` assertion exists
  because the `nightly` residue class (the one this plan discovered during its own
  revision, at `models/ghost_reconcile.py:36`) otherwise had **no** durable gate: its
  only guard was a Verification row, and Verification rows are archived at merge.
- **Enumerate from git, not from the filesystem**: both sweeps and the guard walk
  `git ls-files`, not the working tree. An untracked file is not repo content, and this
  machine routinely has untracked `.md` files carrying these exact tokens — this repo's
  own `/do-plan-critique` writes a verbatim `SOURCE_FILES.md` bundle plus per-critic
  `.result.md` files into gitignored `.critique-runs/` (`.gitignore:474`), and a critique
  of *this* plan bundles the very token-bearing files it names. Measured on a fully fixed
  tree with one such artifact present: the filesystem-walk form returned 1 hit / exit 0 on
  **both** sweeps (false red) while the `git ls-files` form returned 0 / exit 1. That is a
  race decided by which lanes are mid-critique, so the filesystem form would redden
  `tests/unit/` for unrelated lanes on a coin flip and get diagnosed as flake and muted.
  **Trade-off, accepted:** `git ls-files` does not see a brand-new untracked file. That is
  correct behavior here — an untracked file is not repo content, and any real fix to these
  tokens lands in a tracked file.
- **Token-driven closure**: two repo-wide greps over tracked content, each excluding
  `docs/plans/`, are the gate. Named sites are illustrative; the greps are normative.
  The granite sweep keys on the **package path** tokens (`granite_interactive_tui_poc`,
  `tools/granite_loop`), never on the command name `valor-granite-loop` — that name is
  legitimately mentioned in past tense by `config/enums.py:21` and
  `docs/features/bridge-worker-architecture.md:564`, and by the replacement doc row
  itself. Sweeping on the command name is unsatisfiable by construction and would
  pressure a builder into deleting correct prose.

### Flow

Reader greps `popoto-index-cleanup` → **finds nothing outside plan archives** → greps
`redis-index-cleanup` → finds `config/reflections.yaml:53` **and** every prose site
that references it → mental model matches the config.

Reader opens `standardized-enums.md`, sees `SessionType.GRANITE` → **historical value,
producer deleted, retained for record hydration** → does not go looking for a CLI that
does not exist.

### Technical Approach

Corrected file:line references (re-verified at `f491306c5`; see Freshness Check):

**#2853 — replace `popoto-index-cleanup` with `redis-index-cleanup`, keep the cadence claim:**

Line spans, not token lines: each row names every line the edit touches, because
`nightly` and the stale token frequently sit on *different* lines and a builder editing
only the token line leaves `nightly` behind.

| Site (span) | Current | Note |
|---|---|---|
| `scripts/popoto_index_cleanup.py:306-307` | 306: "``run_cleanup`` runs on" / 307: "the daily ``popoto-index-cleanup`` reflection plus once per worker start." | Keep "daily" and "plus once per worker start"; swap the name only. No `nightly` here. |
| `bridge/email_bridge.py:1087` | "waiting for the nightly popoto-index-cleanup sweep" | Both tokens on one line. Swap the name. Also `nightly` → `daily`: the reflection is `every: 86400s` (a rolling 24h interval), not clock-pinned to night. |
| `models/ghost_reconcile.py:23-24` | 23: "Left alone it is only cleaned by the nightly ``popoto-index-cleanup``" / 24: "reflection (``scripts/popoto_index_cleanup.py``, once/24h)" | Both tokens on 23. Swap the name; `nightly` → `daily`. Line 24 is correct — the module path is real and `once/24h` is right. Keep it. |
| `models/ghost_reconcile.py:36` | "(``Model.clean_indexes()``, the same one the nightly reflection calls)." | **`nightly` → `daily` only.** No stale name token here, which is why no sweep keyed on `popoto-index-cleanup` can find it. Same docstring as line 23; leaving it says "nightly" two paragraphs after the fix says "daily". |
| `models/dedup.py:69-70` | 69: "index instead of waiting up to 24h for the nightly" / 70: "popoto-index-cleanup reflection." | `nightly` on 69, token on 70 — **both lines must be edited**. Keep "waiting up to 24h for". |

Explicitly **not** touched (verified accurate): `scripts/popoto_index_cleanup.py:4-5`,
`docs/features/popoto-index-hygiene.md:35, 54, 61, 63`.

**#2839 — rewrite `docs/features/standardized-enums.md:19`:**

The replacement row states, in the doc's own register: `SessionType.GRANITE` is a
historical value; its sole producer (the standalone `valor-granite-loop` CLI) was
deleted with the PTY substrate (plan #1924); nothing creates new sessions with it; it
is retained so pre-cutover Redis records carrying `session_type="granite"` keep
hydrating and rendering. No file path (none exists). No PTY-container sentence (the
container is gone). Three in-repo precedents already say exactly this, and the new row
must contradict none of them: `config/enums.py:20-25`,
`docs/features/bridge-worker-architecture.md:564`, and `models/agent_session.py:4, 102`
(`"granite" persists on historical records only`).

**Also line 13's lead-in.** It currently reads "Discriminator for AgentSession: eng,
teammate, or granite." An earlier draft of this plan justified leaving it alone as
"matching `config/enums.py`" — that justification is false and is corrected here.
`config/enums.py:18` reads "Discriminator for AgentSession: eng or teammate." and
footnotes GRANITE below; `models/agent_session.py:4` and `:102` use the same
two-member lead-in plus a historical footnote. All three cited precedents agree, and
line 13 is the single site that departs, presenting `granite` as a peer live
discriminator eight lines above the row being corrected. Bring it into the precedents'
shape: **"Discriminator for AgentSession: eng or teammate (`granite` is a historical
value; see the row below)."** Do not simply delete `granite` from line 13 — the table
below still carries a `SessionType.GRANITE` row, and a two-member lead-in over a
three-row table reads as a doc bug.

**Adjacent, in scope: `config/enums.py:24`** — "Removal is #1927's (schema diet)
scope." #1927 is CLOSED and did not remove GRANITE. **Replace the sentence, do not
delete it.** The knowledge that removal is unowned and needs a fresh decision is
precisely what the next reader of the enum needs; deleting it relocates that knowledge
into this plan doc, which both closure sweeps exclude and which is archived at merge.
The replacement:

> Removing the member would be a schema change, not a prose change; the schema-diet
> issue that owned removal closed without acting, so it needs a fresh decision.

The replacement must not contain the string `#1927` (the Verification row expects zero
matches), and the `#1924` reference two lines above it is correct and must survive — an
anti-criterion row guards it, because a careless issue-number sweep takes both out
together.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] No exception handlers in scope. Every edit is a comment, docstring, or markdown
      table cell. No `try`/`except` block is added, removed, or modified — the diff
      touches zero executable statements.

### Empty/Invalid Input Handling
- [x] Not applicable. No function is created or modified; no input path changes.

### Error State Rendering
- [x] Not applicable. No user-visible output path changes. The one user-visible
      artifact touched is a documentation table.

## Test Impact

- [ ] `tests/integration/test_reflections_redis.py::TestReflectionConfig::test_reflection_entry_structure` — **NO CHANGE, but it is the guard.** It already asserts `callable == "scripts.popoto_index_cleanup.run_cleanup"`, `every` parses to 86400, and `enabled is True`. It must stay green, and it is the reason the daily-cadence prose is preserved rather than deleted. Any build that "fixes" the cadence claim will contradict this test.
- [ ] `tests/unit/test_session_executor_runner_dispatch.py::test_executor_module_has_no_granite_imports` — **NO CHANGE.** Scoped to `inspect.getsource(agent.session_executor)`; it never reads docs or `config/enums.py`, so the granite row rewrite cannot affect it.

- [ ] `tests/unit/test_stale_reference_sweep.py` — **ADD.** The durable guard (see Task 4): three assertions over `git ls-files` enumeration (name token, granite package paths, and `nightly` scoped to the four named files). This is the one executable file the lane creates; every production edit remains comment/docstring/markdown.

No other existing tests are affected — no test in `tests/` greps for `popoto-index-cleanup`, for `standardized-enums.md`, or for the `SessionType` docstring text (verified: `grep -rln "popoto-index-cleanup\|standardized-enums" tests/` returns nothing). The diff modifies no production executable line, so no behavioral test can observe it.

## Rabbit Holes

- **Rewriting the cadence story.** The issue's original acceptance criterion asks for
  the daily claims to be swept out. They are correct. Deleting them puts the docs at
  odds with a green tracked test. Fix the *name*, keep the *cadence*.
- **Bare `granite` sweeps.** `granite` is a live token (the local ollama classifier
  model). Only the deleted-package tokens are dead. A `grep -i granite` returns
  hundreds of legitimate hits and will drown the build.
- **The wider PTY-teardown prose residue.** `tools/valor_session.py:867` and
  `tests/unit/test_valor_session_cli.py:257` still say "The granite PTY container
  **owns** the PM/Dev split" in the present tense. That is a real defect, but it belongs
  to the #1924 teardown family, not to either issue in this lane, and it sits in a
  stopgap comment block whose surrounding logic needs its own read. Out of scope here —
  see No-Gos.
- **Removing `SessionType.GRANITE`.** Tempting once the doc calls it historical. It is
  load-bearing for pre-cutover Redis record hydration and removing it is a schema
  change, not a prose change.
- **`docs/plans/` archives.** `docs/plans/done/popoto-redis-hygiene.md` and
  `docs/plans/completed/*` are full of both dead tokens. They are the historical record
  of what was planned at the time. Do not edit them; exclude them from every sweep.

## Risks

### Risk 1: The build "corrects" the daily-cadence prose out of existence
**Impact:** Docs contradict `tests/integration/test_reflections_redis.py`, and the next
reader concludes the sweep only runs on worker start — reintroducing the exact false
premise this lane exists to correct.
**Mitigation:** Verification row **"Cadence sentence survives verbatim"** asserts
`grep -c 'plus once per worker start' scripts/popoto_index_cleanup.py` > 0. That phrase
occurs exactly once, inside the sentence at risk, so the row is sensitive to the failure
it guards.

The row it replaces (`grep -c 'daily' ... ` / `output > 2`) could not fire and is
deleted. `daily` occurs four times in that file — lines 5, 31, 305 and 307 — and only
307 is the cadence claim. Deleting lines 306-307 outright leaves three unrelated
occurrences, `3 > 2` is still True, and the gate reported PASS on the exact failure it
existed to catch. **Measured:** old row 4 → 3 under the mutation (still PASS); new row
1 → 0 under the same mutation (correctly FAIL).

### Risk 2: A stray token site is missed because the task list enumerates lines
**Impact:** Closure grep can't reach exit 1, or worse, passes because it was scoped to
`scripts/` and `docs/features/` as the issue's criterion proposed — leaving `bridge/`
and `models/` stale. This is exactly how #1643 burned two review cycles.
**Mitigation:** Task 4 is a self-completing token-driven sweep: run the two greps over
the whole worktree, resolve every remaining hit by meaning, re-run until both return
exit 1. The named-line tables are illustrative, not normative.

### Risk 3: `nightly` → `daily` reads as scope creep in review
**Impact:** A reviewer blocks on an unrequested wording change.
**Mitigation:** It is on the same three lines already being edited for the name, and it
is an accuracy fix: `every: 86400s` is a rolling 24h interval with no clock pinning.
The rationale is stated per-site in the Technical Approach table so a reviewer sees the
justification without asking.

## Race Conditions

No race conditions identified. Every change is a static text edit to a comment,
docstring, or markdown file. Nothing is read at runtime, nothing is concurrent, and no
shared mutable state is touched.

## No-Gos (Out of Scope)

- `[EXTERNAL]` Correcting the present-tense "The granite PTY container owns the PM/Dev
  split" prose at `tools/valor_session.py:867` and `tests/unit/test_valor_session_cli.py:257`.
  These are #1924 teardown residue in a stopgap-gate comment block whose surrounding
  refusal logic (`models/child_session_gate.py`) needs a human call on whether the
  stopgap itself still applies now that the container is gone. That judgement is not
  the agent's to make inside a prose lane; it needs its own issue filed by a human who
  can decide the gate's fate.
- `[SEPARATE-SLUG #1927]` Removing the `SessionType.GRANITE` enum member. Schema change,
  not a prose change; #1927 is the schema-diet slug that owns it (now closed without
  acting, so a fresh decision is needed before any removal — this plan takes none).

Everything else relevant is in scope for this plan.

## Update System

No update system changes required. No new dependency, config file, secret, entry point,
or migration is introduced. `/update` propagates these files by `git pull` alone. There
is no `reflections.yaml` change, so `env_sync.py::sync_reflections_yaml` is unaffected.

## Agent Integration

No agent integration required. No CLI entry point is added to
`pyproject.toml [project.scripts]`, and the bridge imports nothing new. The changes are
comments, a docstring, and a markdown table — the agent already reaches them through
the ordinary Read/Grep path.

## Documentation

- [ ] Update `docs/features/standardized-enums.md` — rewrite the `SessionType.GRANITE`
      table row (line 19) to the historical framing; remove the dead
      `tools/granite_interactive_tui_poc/cli.py` path and the granite-PTY-container
      sentence. **This edit is the #2839 fix itself**, not a follow-on.
- [ ] No change to `docs/features/popoto-index-hygiene.md` — verified accurate at
      lines 35, 54, 61, 63; it already names `redis-index-cleanup`. Recording the
      explicit no-change decision so a later reader does not re-open it.
- [ ] No change to `docs/features/README.md` — no feature doc is added or renamed, so
      the index table is unaffected.
- [ ] Update `docs/features/standardized-enums.md` line 13 — the `SessionType` section
      lead-in, brought into the two-member-plus-footnote shape the three in-repo
      precedents already use.
- [ ] Inline: `scripts/popoto_index_cleanup.py:307` docstring, `bridge/email_bridge.py:1087`
      comment, `models/ghost_reconcile.py:23` and `:36` docstring, `models/dedup.py:69-70`
      comment, `config/enums.py` `SessionType` docstring.
- [ ] No new feature doc for `tests/unit/test_stale_reference_sweep.py` — it is a
      consistency guard, and its own module docstring carries the rationale, matching how
      `test_skill_agent_tool_consistency.py` and `test_update_persona_drift.py` are
      documented.

## Success Criteria

- [ ] No tracked file outside `docs/plans/` names `popoto-index-cleanup` as a registered
      reflection.
- [ ] `scripts/popoto_index_cleanup.py`, `docs/features/popoto-index-hygiene.md`, and
      the three comment sites still describe the sweep as running daily / once per 24h.
- [ ] No live site describes the sweep as `nightly`. It is `every: 86400s`, a rolling
      24h interval with no clock pinning.
- [ ] `docs/features/standardized-enums.md`'s section lead-in (line 13) names two live
      discriminators and footnotes `granite`, matching `config/enums.py:18` and
      `models/agent_session.py:4`.
- [ ] `config/enums.py`'s `SessionType` docstring still says *something* about removal —
      that it is a schema change and needs a fresh decision — rather than falling silent.
- [ ] `tests/unit/test_stale_reference_sweep.py` exists, carries all three assertions
      (name token, granite package paths, scoped `nightly`), is green, and has been shown
      red twice: once by reverting `models/dedup.py` lines 69-70, and once by reverting
      `models/ghost_reconcile.py:36` alone.
- [ ] No tracked file outside `docs/plans/` references the deleted package path
      `granite_interactive_tui_poc` or `tools/granite_loop`. The command name
      `valor-granite-loop` still appears, correctly, in past tense.
- [ ] `docs/features/standardized-enums.md`'s `SessionType.GRANITE` row describes a
      historical value with a deleted producer, consistent with `config/enums.py`.
- [ ] `config/enums.py` no longer points GRANITE removal at the closed #1927.
- [ ] The diff contains no **production** executable-line change — every edit outside
      `tests/unit/test_stale_reference_sweep.py` is a comment, docstring, or markdown.
- [ ] Tests pass (`/do-test`), including
      `tests/integration/test_reflections_redis.py`.
- [ ] Documentation updated (`/do-docs`).
- [ ] PR body carries both `Closes #2853` and `Closes #2839`.
- [ ] No xfail conversions apply — no expected-failure marker relates to either issue
      (`grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/` surfaces nothing touching
      popoto index cleanup or granite enums).

## Team Orchestration

Small, single-file-class, prose-only work. One builder, one validator.

### Team Members

- **Builder (prose)**
  - Name: `prose-builder`
  - Role: Apply all six text edits and run the token sweeps to exit 1
  - Agent Type: builder
  - Resume: true

- **Validator (sweep)**
  - Name: `sweep-validator`
  - Role: Independently re-run every Verification row and confirm the diff touches no
    executable line
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Correct the reflection name and cadence wording at all five sites (#2853)
- **Task ID**: build-reflection-name
- **Depends On**: none
- **Validates**: `tests/integration/test_reflections_redis.py`
- **Assigned To**: prose-builder
- **Agent Type**: builder
- **Parallel**: true
- In `scripts/popoto_index_cleanup.py:307`, change ``popoto-index-cleanup`` to
  ``redis-index-cleanup``. **Keep** "the daily" and "plus once per worker start" — both
  are correct, and "plus once per worker start" is what the cadence anti-criterion
  asserts.
- In `bridge/email_bridge.py:1087`, change "the nightly popoto-index-cleanup sweep" to
  "the daily redis-index-cleanup sweep".
- In `models/ghost_reconcile.py:23`, change "the nightly ``popoto-index-cleanup``
  reflection" to "the daily ``redis-index-cleanup`` reflection". **Keep** line 24's
  ``scripts/popoto_index_cleanup.py``, once/24h parenthetical — the module path is real
  and the interval is correct.
- In `models/ghost_reconcile.py:36`, change "the same one the nightly reflection calls"
  to "the same one the daily reflection calls". This line carries **no** stale name
  token, so neither sweep in Task 5 can find it; it is listed here because it is the
  only way it gets fixed, and the `nightly` anti-criterion row is what proves it did.
- In `models/dedup.py`, edit **both** lines 69 and 70: "waiting up to 24h for the
  nightly" / "popoto-index-cleanup reflection" becomes "waiting up to 24h for the
  daily" / "redis-index-cleanup reflection". Editing only line 70 leaves `nightly`
  stranded on 69.
- Do **not** touch `scripts/popoto_index_cleanup.py:4-5` or
  `docs/features/popoto-index-hygiene.md` — verified accurate.
- When done, `grep -rc 'nightly'` across all four files must read `:0` for every file.
  On main it reads `0;1;2;1`.

### 2. Rewrite the SessionType.GRANITE doc row (#2839)
- **Task ID**: build-granite-row
- **Depends On**: none
- **Assigned To**: prose-builder
- **Agent Type**: builder
- **Parallel**: true
- In `docs/features/standardized-enums.md`, replace the Usage cell of the
  `SessionType.GRANITE` row (line 19) with the historical framing: a historical value;
  its sole producer, the standalone `valor-granite-loop` CLI, was deleted with the PTY
  substrate (plan #1924); nothing creates new sessions with it; retained so pre-cutover
  Redis records carrying `session_type="granite"` keep hydrating and rendering.
- Remove the `tools/granite_interactive_tui_poc/cli.py` path entirely — do not repoint
  it, no successor file exists.
- Remove the sentence "Bridge sessions that run through the granite PTY container are
  typed `ENG`." The container is gone.
- Do **not** carry over `config/enums.py`'s "Removal is #1927's scope" pointer — #1927
  is closed and did not remove the member.
- Open the replacement cell with the word "Historical" so the framing is the first thing
  a reader sees. The Verification row lowercases the line before matching, so either
  case passes; the earlier case-sensitive form would have failed on correct prose.
- **Also edit line 13.** Replace "Discriminator for AgentSession: eng, teammate, or
  granite." with "Discriminator for AgentSession: eng or teammate (`granite` is a
  historical value; see the row below)." This matches `config/enums.py:18` and
  `models/agent_session.py:4`. Do not delete `granite` from line 13 outright — the
  table below still has a `SessionType.GRANITE` row.

### 3. Replace the closed-issue removal pointer in the enum docstring
- **Task ID**: build-enums-pointer
- **Depends On**: none
- **Assigned To**: prose-builder
- **Agent Type**: builder
- **Parallel**: true
- In `config/enums.py`, **replace** the `SessionType` docstring sentence "Removal is
  #1927's (schema diet) scope." with: "Removing the member would be a schema change, not
  a prose change; the schema-diet issue that owned removal closed without acting, so it
  needs a fresh decision." Deleting it outright would leave the docstring silent on
  removal and push that knowledge into this plan doc, which both sweeps exclude.
- Keep the phrase "fresh decision" on a **single line** — `grep` is line-based and the
  Verification row matches that phrase. A wrap between "fresh" and "decision" makes the
  row red on a correct edit. (Measured: the first draft of this replacement wrapped
  exactly there and the row read 0.)
- The replacement must not contain `#1927`. The `#1924` reference earlier in the same
  docstring is correct — leave it. An anti-criterion row guards it.
- Change nothing else in the docstring and no enum member. `GRANITE = "granite"` stays.

### 4. Add the durable token-sweep guard
- **Task ID**: build-guard-test
- **Depends On**: build-reflection-name, build-granite-row
- **Assigned To**: prose-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_stale_reference_sweep.py`. This is the lane's answer to its own
  root-cause finding: every prior gate (#1643, #1924, #2833) died with the artifact that
  carried it, and this plan's Verification rows are archived into `docs/plans/` at merge,
  which both sweeps exclude. Repo precedent for a tracked consistency test:
  `tests/unit/test_skill_agent_tool_consistency.py`,
  `test_default_project_key_consistency.py`, `test_update_persona_drift.py`.
- **Three** assertions. Two run over the shared enumeration: no in-scope file contains
  `popoto-index-cleanup`; no in-scope file contains `granite_interactive_tui_poc` or
  `tools/granite_loop`. Assert on those package-path tokens only — **never bare
  `granite`**, which is the live ollama model name in `tools/classifier.py`.
- **Third assertion — the `nightly` residue class**, scoped to a literal four-file tuple
  resolved from the repo root:
  `_NIGHTLY_SCOPED = ("scripts/popoto_index_cleanup.py", "bridge/email_bridge.py", "models/ghost_reconcile.py", "models/dedup.py")`,
  asserting `"nightly" not in line` per file with `path:line` output. This is the only
  durable gate on the class, and it is what makes Task 6's mutation check satisfiable for
  `models/ghost_reconcile.py:36` — that site carries none of the other two tokens, so
  without this assertion reverting it leaves the guard green and the mutation proves
  nothing. **Scope it; do not go repo-wide.** A bare repo-wide `nightly` assertion is
  unsatisfiable: measured **134** legitimate tracked hits outside `docs/plans/`, led by
  `docs/features/nightly-regression-tests.md` (25), `docs/features/nightly-alert-triage.md`
  (21), `scripts/nightly_regression_tests.py` (16) and `scripts/update/service.py` (10).
  Baseline across the four scoped files on main is `0;1;2;1`, so the assertion is red
  before the fix and green after — a real signal, not a tautology.
- **Enumerate from `git ls-files`, not the filesystem.** Resolve the root as
  `Path(__file__).resolve().parents[2]`, then
  `subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True).stdout.split("\0")`.
  Filter on suffix in `{.py, .md, .yaml, .yml, .toml}`, still excluding the relative dir
  `docs/plans` and the test file itself. **No `os.walk`, no `dirnames[:]` prune list, no
  `rglob`.** Untracked files carrying these tokens are routine on this machine —
  `/do-plan-critique` writes a verbatim `SOURCE_FILES.md` and per-critic `.result.md`
  files into gitignored `.critique-runs/` — and a filesystem walk turns them into failures
  for unrelated lanes. Measured on a fully fixed tree with one such artifact on disk: the
  `os.walk` form reported `.critique-runs/.../SOURCE_FILES.md` as a hit for **both** token
  assertions, while the `git ls-files` form reported none. The prune list existed to avoid
  the 16 GB `.worktrees` traversal; `git ls-files` never sees those paths at all, so the
  performance rationale survives and the prune list drops entirely. **Measured: 3 tests
  green in 0.04 s.**
- **Known trade-off, state it in the test's module docstring:** `git ls-files` does not
  see a brand-new untracked file. That is the intended behavior — an untracked file is not
  repo content, and any genuine occurrence of these tokens lands in a tracked file.
- The test file must exclude **itself** — it necessarily contains all the literal
  tokens. Skip by `path.resolve() == Path(__file__).resolve()`, and build each token by
  string concatenation so a future careless sweep of the repo does not read this file's
  own assertions as violations.
- Failure output must name `path:line` per hit, not just a count. The prototype's output
  was what surfaced `models/ghost_reconcile.py:36` during plan revision, and it is what
  Task 6's mutation check reads.

### 5. Token-driven sweep to closure
- **Task ID**: build-sweep
- **Depends On**: build-reflection-name, build-granite-row, build-enums-pointer, build-guard-test
- **Assigned To**: prose-builder
- **Agent Type**: builder
- **Parallel**: false
- Run both Verification greps below over the repo's **tracked** content. Do **not** rely
  on the named-line lists in tasks 1-3.
- Run them **exactly as written in the Verification table** — `git ls-files -z ... |
  xargs -0 grep ... | grep -v 'docs/plans'`. Do not substitute a `grep -r` over the
  working tree: that form walks untracked `.critique-runs/` artifacts that carry these
  exact tokens and cannot reach exit 1 while any such directory exists. Measured on a
  fully fixed tree with one artifact present: `grep -r` returned 1 hit / exit 0 on both
  sweeps, the `git ls-files` form returned 0 / exit 1. The `git ls-files` form also drops
  the `--exclude-dir` flags entirely (git never lists `.worktrees/`, `.venv/`, or
  `node_modules/`) and is faster than the old flagged form: measured **0.67 s** and
  **0.80 s** in the main checkout, against 0.95 s and 1.4 s.
- Keep the trailing `grep -v 'docs/plans'` leg. BSD `grep` matches `--exclude-dir` on a
  directory *basename*, so it cannot express the path-scoped exclusion, and that leg is
  load-bearing — it filters real archive hits (measured 26 in `sdlc-1643.md` alone).
- The final `grep -v` sets the pipeline's exit code, so the row stays correct even when
  `xargs` splits the file list across several `grep` invocations.
- For every remaining hit, resolve it by meaning: reflection name → `redis-index-cleanup`;
  deleted package path → historical framing with no path. If a hit is inside
  `docs/plans/`, it is out of scope by exclusion, not by editing.
- Sweep the granite side on the **package path** tokens only
  (`granite_interactive_tui_poc`, `tools/granite_loop`). Do **not** sweep on
  `valor-granite-loop`: that command name is correctly named in past tense by
  `config/enums.py:21`, by `docs/features/bridge-worker-architecture.md:564`, and by the
  replacement row from Task 2. A sweep on it cannot reach exit 1 without deleting
  accurate prose.
- Re-run until both greps return exit 1.
- Confirm the diff touches no **production** executable line: every `+`/`-` line outside
  `tests/unit/test_stale_reference_sweep.py` is inside a comment, a docstring, or a
  markdown file.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-sweep
- **Assigned To**: sweep-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run every row of the Verification table independently, **from the main checkout as
  well as the lane worktree** — the two sweep rows are enumeration-sensitive, and a
  regression to a working-tree walk would show up as a difference between those two
  locations.
- Confirm `tests/integration/test_reflections_redis.py` and
  `tests/unit/test_stale_reference_sweep.py` are both green.
- Confirm the daily-cadence prose survived (it is an easy casualty of a careless sweep).
- **Mutation-check the guard before trusting it, at `models/dedup.py` specifically.**
  Revert both lines 69 and 70 (`daily` → `nightly`, `redis-index-cleanup` →
  `popoto-index-cleanup`), confirm the test goes red naming those `path:line`s, restore.
  `models/dedup.py` is the named site because it is the one file carrying **both** guarded
  token classes, so a single revert exercises two of the three assertions. Do not
  substitute "one of the five sites": `models/ghost_reconcile.py:36` carries neither token
  class, and before the `nightly` assertion was added, reverting it left the guard green
  and proved nothing. **Measured during revision:** all 3 green → reverting `dedup.py`
  reddens `test_no_stale_reflection_name` **and** `test_no_nightly_cadence_at_scoped_sites`
  (2 failed, 1 passed) → restore returns 3 green.
- **Also mutation-check the third assertion on its own**, at `models/ghost_reconcile.py:36`:
  revert `daily` → `nightly` there and confirm `test_no_nightly_cadence_at_scoped_sites`
  goes red naming `models/ghost_reconcile.py:36` while the two token assertions stay
  green. **Measured during revision: 1 failed, 2 passed** — which is precisely the proof
  that the old two-assertion guard could not have caught this site.
- Confirm the guard does **not** redden on an untracked artifact: create a throwaway
  `.critique-runs/tmp/SOURCE_FILES.md` containing the tokens, re-run the guard, confirm
  still green, delete it. Measured during revision: green with the artifact present under
  `git ls-files`; the `os.walk` form reported it as a hit for both token assertions.
- **Confirm the PR body carries both closing keywords** — `Closes #2853` and
  `Closes #2839`. This lane closes its companion issue only through the PR body; nothing
  else in the diff references #2839.
- Report pass/fail per row.

## Verification

Every row below was mutation-checked: the failure the row exists to catch was applied,
the row was confirmed red, and the mutation reverted. Measured before/after values are in
the last column. A row measured only in its green state proves nothing, which is exactly
how the previous "Daily cadence preserved" row shipped broken.

**Round-2 note on how these were measured.** Round 1 carried this same preamble and still
shipped two rows that could not fire — a claim of mutation-checking is not itself a
mutation check. The round-2 revision therefore built a scratch git repository containing
copies of the token-bearing files, reproduced the real repo's state exactly (the same four
`popoto-index-cleanup` hits, the same single granite hit, the same `0;1;2;1` `nightly`
baseline, `config/reflections.yaml` gitignored and untracked, `.critique-runs/` gitignored),
applied each row's failure there, and recorded the reading. Live source files were never
modified. Two structural rules came out of it and both are now respected above:

- **Any row shaped `git diff ... -- config/reflections.yaml` is dead on arrival.**
  `.gitignore:8` is the literal line `config/reflections.yaml` and the file is untracked
  (`git ls-files --error-unmatch` exits 1), so `git diff` reports nothing about it no
  matter what changes. The same holds for `config/projects.json`. Guard those files by
  **content**, never by diff.
- **Any row that walks the working tree instead of `git ls-files` is a coin flip.**
  Untracked token-bearing artifacts appear and vanish under `.critique-runs/` as other
  lanes run critiques.

| Check | Command | Expected | Mutation measurement |
|-------|---------|----------|----------------------|
| No stale reflection name | `git ls-files -z '*.py' '*.md' '*.yaml' '*.toml' \| xargs -0 grep -n 'popoto-index-cleanup' \| grep -v 'docs/plans'` | exit code 1 | 4 hits / exit 0 on main → 0 hits / exit 1 on the fixed tree → back to 1 hit / exit 0 when `models/dedup.py:70`'s rename alone is reverted. **Immunity measured:** with an untracked `.critique-runs/.../SOURCE_FILES.md` carrying the token on a fully fixed tree, this form returns 0 / exit 1 while the old `grep -r` form returned 1 hit / exit 0. Runtime 0.67 s in the main checkout (old flagged form: 0.95 s). |
| No deleted granite package path | `git ls-files -z '*.py' '*.md' '*.yaml' '*.toml' \| xargs -0 grep -nE 'granite_interactive_tui_poc\|tools/granite_loop' \| grep -v 'docs/plans'` | exit code 1 | 1 hit / exit 0 on main → 0 / exit 1 fixed → back to 1 hit / exit 0 when the deleted path is reintroduced into the replacement row. **Immunity measured:** same untracked-artifact test, 0 / exit 1 here against 1 hit / exit 0 for the old `grep -r` form. Runtime 0.80 s (old flagged form: 1.4 s). |
| Cadence sentence survives verbatim (anti-criterion) | `grep -c 'plus once per worker start' scripts/popoto_index_cleanup.py` | output > 0 | **Replaces the broken blocker row.** 1 on main → **0** after deleting lines 306-307. The old `grep -c 'daily'` / `output > 2` row read 4 → **3** under the same mutation and still PASSED. |
| Per-file rename coverage, all four files | `grep -rl 'redis-index-cleanup' scripts/popoto_index_cleanup.py bridge/email_bridge.py models/ghost_reconcile.py models/dedup.py \| wc -l` | output > 3 | 0 on main → 4 fixed → **1** when the sentence is deleted at three sites and renamed at one. The old `grep -rn ...` / `exit code 0` row PASSED under that same mutation, as did the stale-token row. `-l` not `-c`: `-c` emits `path:N` per file even at zero, so `wc -l` would print 4 unconditionally. |
| No `nightly` left at the four files (anti-criterion) | `grep -rc 'nightly' scripts/popoto_index_cleanup.py bridge/email_bridge.py models/ghost_reconcile.py models/dedup.py` | match count == 0 | `0;1;2;1` on main → all `:0` fixed → back to `ghost_reconcile.py:1` when that file's line 36 alone is reverted. **This row is what found `models/ghost_reconcile.py:36`** — a fifth stale site carrying no name token, invisible to both sweeps. It is now mirrored by the guard's third assertion (Task 4), so the class keeps a gate after this plan is archived; this row alone would not survive merge. |
| Section lead-in no longer lists granite as a peer | `grep -c 'eng, teammate, or granite' docs/features/standardized-enums.md` | match count == 0 | 1 on main (row red) → 0 fixed. |
| Granite row states historical framing | `grep -n 'SessionType.GRANITE' docs/features/standardized-enums.md \| tr 'A-Z' 'a-z'` | output contains historical | 0 on main → 1 fixed. The `tr` leg is required: without it the row is case-sensitive and read **0** against a correct replacement opening "Historical value." |
| Removal knowledge retained, not deleted | `grep -c 'fresh decision' config/enums.py` | output > 0 | 0 on main → 1 fixed. Read **0** on a first draft that wrapped the phrase across two lines; `grep` is line-based, so the replacement must keep "fresh decision" unbroken. |
| Closed-issue pointer dropped | `grep -c "#1927" config/enums.py` | match count == 0 | 1 on main → 0 fixed. |
| `#1924` reference survives (anti-criterion) | `grep -c '#1924' config/enums.py` | output > 0 | 1 on main and fixed → **0** when an issue-number sweep strips both references together. |
| Accurate past-tense CLI mentions survive (anti-criterion) | `grep -l 'valor-granite-loop' config/enums.py docs/features/bridge-worker-architecture.md \| wc -l` | output > 1 | 2 on main and fixed → 1 when either file's past-tense mention is deleted. |
| GRANITE member not removed (anti-criterion) | `grep -c 'GRANITE = "granite"' config/enums.py` | output > 0 | 1 on main and fixed → **0** when the member is removed. |
| Durable guard green | `scripts/pytest-clean.sh tests/unit/test_stale_reference_sweep.py -q` | exit code 0 | `git ls-files` prototype measured across all three assertions: **3 passed in 0.04 s** on the fixed tree → **2 failed, 1 passed** after reverting `models/dedup.py` lines 69-70 (name + `nightly`) → **1 failed, 2 passed** after reverting only `models/ghost_reconcile.py:36`, with the failure naming `models/ghost_reconcile.py:36` → 3 passed again on restore. Still 3 passed with an untracked `.critique-runs/.../SOURCE_FILES.md` carrying the tokens on disk, where the `os.walk` form reported it as a hit for both token assertions. |
| Reflection registration intact (anti-criterion) | `grep -c 'callable: "scripts.popoto_index_cleanup.run_cleanup"' config/reflections.yaml` | output > 0 | 1 on main and fixed → **0** when the callable is repointed at `agent.agent_session_queue.cleanup_corrupted_agent_sessions`. **This replaces a git-diff row that could not fire:** `config/reflections.yaml` is gitignored (`.gitignore:8`) and untracked, and under the same mutation `git diff main...HEAD --stat -- config/reflections.yaml \| wc -l` read **0 → 0**, passing on the exact failure it guarded. Content assertion, so git tracking is irrelevant. The `every`/`enabled` fields stay covered by the "Reflections config contract green" pytest row. |
| Hygiene doc untouched (anti-criterion) | `git diff main...HEAD --stat -- docs/features/popoto-index-hygiene.md \| wc -l` | match count == 0 | `0` untouched → **2** on a one-line edit (`git diff --stat` on a tracked file emits the per-file line plus the `1 file changed` summary line, so `wc -l` is 2, not 1). `match count == 0` is exact for `wc -l` output — `"       0"` strips to `"0"` — where `output contains 0` would also pass on 10 or 20. This file is tracked, so the row can fire. Subsumes the deleted "Hygiene doc cadence preserved" row. |
| Reflection scheduler untouched (anti-criterion) | `git diff main...HEAD --stat -- agent/reflection_scheduler.py agent/reflection_schedule.py \| wc -l` | match count == 0 | `0` untouched → **2** on a one-line edit to either file (same `--stat` shape as the row above). Both files are tracked, so the row can fire. |
| PR body closes both issues | `gh pr view --json body -q .body \| grep -c 'Closes #2839'` | output > 0 | 1 on a body carrying both keywords → **0** when the companion `Closes #2839` is omitted and only `Closes #2853` is present. Two-issue lane: #2839 closes through the PR body alone, so this is the one criterion whose omission silently leaves an issue open. Run from the lane worktree once the PR exists; the `gh` leg cannot be exercised before then, so the measurement above is of the `grep` leg against both body shapes. |
| Reflections config contract green | `scripts/pytest-clean.sh tests/integration/test_reflections_redis.py -q` | exit code 0 | Asserts `callable` and `every == 86400`; red if the registration or cadence is touched. |
| Lint clean | `python -m ruff check .` | exit code 0 | -- |
| Format clean | `python -m ruff format --check .` | exit code 0 | -- |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Verification row "Reflection registration untouched (anti-criterion)" cannot fire. `config/reflections.yaml` is gitignored at `.gitignore:8` and untracked (`git ls-files --error-unmatch` exits 1), so `git diff main...HEAD --stat -- config/reflections.yaml \| wc -l` reports 0 no matter what the builder does to the file. Mutation-measured: appended a comment line, then `git diff HEAD --stat ... \| wc -l` -> 0 and `git diff main...HEAD --stat ... \| wc -l` -> 0. The row PASSES on the exact failure it guards. Same defect class as the round-1 BLOCKER, in a row the revision added, under a preamble asserting every row was mutation-checked. | **Applied.** Row replaced with the content assertion `grep -c 'callable: "scripts.popoto_index_cleanup.run_cleanup"' config/reflections.yaml` / `output > 0`, retitled "Reflection registration intact". **Mutation-measured in a scratch repo reproducing the untracked state** (`git rm --cached` + `.gitignore` entry, so `git ls-files --error-unmatch` exits 1 as in the real repo): repointing the callable read old row **0 -> 0** (dead; passes on the exact failure it guards) and new row **1 -> 0** (fires). The structural rule -- any row shaped `git diff ... -- config/reflections.yaml` or `config/projects.json` is dead on arrival, so guard those by content -- is now written into the Verification preamble so a later revision cannot re-add one. The other two git-diff rows were confirmed able to fire: both targets are tracked, measured `0 -> 2` on a one-line edit. | Replace the git-diff row with a content assertion that does not depend on git tracking: `grep -c 'callable: "scripts.popoto_index_cleanup.run_cleanup"' config/reflections.yaml` / `output > 0`, leaning on the existing "Reflections config contract green" pytest row for the cadence and `enabled` fields. `.gitignore:8` is the literal line `config/reflections.yaml`; the checkout copy is machine-local and regenerated by `scripts/update/reflections_yaml.py`, so ANY row shaped `git diff ... -- config/reflections.yaml` is dead on arrival (same for `config/projects.json`). The other two git-diff rows are fine -- `docs/features/popoto-index-hygiene.md` and `agent/reflection_scheduler.py` are both tracked. |
| BLOCKER | Risk & Robustness, Scope & Value | Both normative sweeps and the Task 4 guard walk the working tree rather than the repo's tracked content, so any untracked `.md`/`.py`/`.yaml` file carrying the tokens turns them red. This repo's own `/do-plan-critique` writes exactly such files: per-critic results land at `.critique-runs/<run>/<critic>.result.md`, `.critique-runs/` is gitignored (`.gitignore:474`), and the addendum PRESERVES that directory on the incomplete path. Measured with `/usr/bin/grep` via `/bin/sh` (the path `run_checks` takes through `subprocess.run(shell=True)`): with one such artifact present, sweep 1 returned 5 hits / exit 0 instead of 4 and sweep 2 returned 2 hits / exit 0 instead of 1; a Python prototype of the Task 4 walk with the plan's exact prune set went 5 -> 6 hits. The Task 5 builder cannot reach exit 1 while such a directory exists, and after merge `tests/unit/test_stale_reference_sweep.py` goes red for unrelated lanes whenever a concurrent critique run dir is on disk. | **Applied.** Both sweep rows now read `git ls-files -z '*.py' '*.md' '*.yaml' '*.toml' \| xargs -0 grep -n <token> \| grep -v 'docs/plans'`, and Task 4 replaces `os.walk` + prune with `git ls-files -z` filtered on suffix, still excluding `docs/plans` and the test file itself. The prune list and the `--exclude-dir` flags are dropped entirely -- git never lists `.worktrees/`, `.venv/`, or `node_modules/`. **Mutation-measured in a scratch repo reproducing the real baselines** (same 4 name-token hits, same 1 granite hit, `.critique-runs/` gitignored per `.gitignore:474`): on a fully fixed tree with one untracked `SOURCE_FILES.md` carrying the tokens, the old `grep -r` form returned **1 hit / exit 0 on both sweeps** (false red) and the `os.walk` guard prototype reported that artifact as a hit for **both** token assertions, while the `git ls-files` form returned **0 / exit 1** and the guard stayed **3 passed**. Red-state checks confirm the new rows still fire on real regressions: reverting `models/dedup.py:70` -> 1 hit / exit 0; reintroducing the granite path -> 1 hit / exit 0. The new form is also faster in the main checkout: **0.67 s / 0.80 s** against 0.95 s / 1.4 s. The `grep -v 'docs/plans'` leg is kept for the BSD-basename reason (measured 26 archive hits in `sdlc-1643.md` alone). Trade-off stated in Task 4 and the Solution: `git ls-files` does not see brand-new untracked files, which is correct -- an untracked file is not repo content. | Enumerate candidates from git, not the filesystem. In Task 4 replace the `os.walk` + `dirnames[:]` prune with `subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True).stdout.split("\0")`, filter on suffix in {`.py`, `.md`, `.yaml`, `.yml`, `.toml`}, still excluding the relative dir `docs/plans` and the test file itself. This also removes the 16 GB `.worktrees` traversal the prune list existed to solve, so the performance rationale survives and the prune list can be dropped entirely (none of those dirs are tracked). State the known trade-off in the task: `git ls-files` does not see a brand-new untracked file, which is correct here -- an untracked file is not repo content. For the two sweep rows the equivalent is `git ls-files -z '*.py' '*.md' '*.yaml' '*.toml' \| xargs -0 grep -n <token> \| grep -v 'docs/plans'`; keep the `grep -v 'docs/plans'` leg for the BSD-basename reason the plan already documents. **Bundled source widens this past critic prose:** the critique harness writes a verbatim `SOURCE_FILES.md` into the same run dir (measured: 84K / 26 file sections in `.critique-runs/2660-*`, 74K / 13 sections in `.critique-runs/2817-*`), and this plan names exactly the token-bearing files, so a critique of THIS plan bundles `popoto_index_cleanup.py:307`, `email_bridge.py:1087`, `ghost_reconcile.py:23`, `dedup.py:70` and `standardized-enums.md:19` verbatim into an untracked `.md`. A critic can choose grounding quotes that avoid the tokens; it cannot choose what the driver bundled. Scope note so the builder does not over-chase: the `--include='*.md'` sweeps and the guard's suffix filter do NOT match `*.result.md.tmp`, so only `SOURCE_FILES.md` and completed `.result.md` files are in range -- and no artifact currently on disk carries any of the three tokens (measured 0 hits with `/usr/bin/grep -rn` over `.critique-runs/`), so this is a live mechanism, not a currently-red tree. **It is a race, not a fixed state:** sampled `.critique-runs/` three times across ~20 minutes on this machine and saw six distinct run dirs, five of them transient (`2739`, `2748`, `2817`, `2823`, `2845` came and went, `2739` twice under different timestamps; only the crashed `2660` persisted). Whether the tree is red is therefore decided by which other lanes are mid-critique when CI runs, and a guard that reddens `tests/unit/` for unrelated lanes on a coin flip gets diagnosed as flake and muted -- worse than one that is reliably red. `git ls-files -z` removes the race rather than shrinking the window. Magnitude hedge: bundle presence and size vary by driver path and by how far a run got (26 file sections in `2660`, 13 in a `2817-*` dir, `_roster.json` + `.plan_hash` only in others), so treat the bundle as a real but non-uniform amplifier, not a constant. |
| CONCERN | History & Consistency | Task 6's guard mutation-check is unsatisfiable for one of the five sites, and the `nightly` residue class ends up with no durable gate. Task 6 says "revert one of the five prose sites, confirm the test goes red naming that path:line", but the guard asserts only the name token and the two granite package-path tokens; `models/ghost_reconcile.py:36` carries none of them (verified: the repo-wide name-token sweep returns `ghost_reconcile.py:23`, never `:36`), so reverting that site leaves the guard green and the mutation check proves nothing -- a green row reaching no code, the exact defect round 1 caught, reproduced inside the plan's own validation instruction. Second-order: the only gate on the `nightly` class is the Verification row `grep -rc 'nightly' <four files>`, which is archived into `docs/plans/` at merge, where both sweeps and the guard exclude it. The one residue class this plan discovered during its own revision therefore ends with no durable gate -- the root-cause pattern repeating inside the task written to break it. | **Applied.** Task 4 gains a third assertion, `test_no_nightly_cadence_at_scoped_sites`, over the literal tuple `("scripts/popoto_index_cleanup.py", "bridge/email_bridge.py", "models/ghost_reconcile.py", "models/dedup.py")`, asserting `"nightly" not in line` per file with `path:line` output. Task 6 now names `models/dedup.py` explicitly as the mutation site (the one file carrying both guarded token classes) and adds a second, separate mutation at `models/ghost_reconcile.py:36`. **Mutation-measured:** guard green at **3 passed / 0.04 s**; reverting `models/dedup.py` lines 69-70 -> **2 failed, 1 passed**; reverting `models/ghost_reconcile.py:36` alone -> **1 failed, 2 passed**, the failure naming `models/ghost_reconcile.py:36` while both token assertions stayed green -- the direct proof that the old two-assertion guard could not have caught this site. Scope confirmed necessary: a repo-wide bare `nightly` assertion is unsatisfiable at **134** legitimate tracked hits outside `docs/plans/` (measured; led by `nightly-regression-tests.md` at 25 and `nightly-alert-triage.md` at 21). The class now has a gate that survives this plan's archival. | Add a third assertion to the guard test, scoped to the four named files, asserting zero occurrences of `nightly`; and name `models/dedup.py` explicitly as the mutation site in Task 6 instead of "one of the five". A repo-wide bare `nightly` assertion is unsatisfiable -- measured 171 live hits, legitimately from `scripts/nightly_regression_tests.py` plus its test and from memory-dedup prose. Scope it to a literal tuple resolved from the repo root: `_NIGHTLY_SCOPED = ("scripts/popoto_index_cleanup.py", "bridge/email_bridge.py", "models/ghost_reconcile.py", "models/dedup.py")`, asserting `"nightly" not in text` per file with `path:line` output. Baseline on main is `0;1;2;1` across those four files, so the assertion is red before the fix and green after -- a real mutation signal, not a tautology. |
| NIT | Risk & Robustness | Two accuracy slips in the three `git diff main...HEAD --stat ... \\| wc -l` rows, under a preamble claiming every row was mutation-measured. (a) The measurement column says "0 untouched -> 1 once a line changes"; measured on a tracked file, `git diff --stat` emits the file line plus the summary line, so `wc -l` is 2, not 1. (b) The expectation `output contains 0` is a substring test, so a `wc -l` value of 10 or 20 would also pass; `match count == 0` is exact for `wc -l` output (`"       0"` strips to `"0"`) and is already supported by `agent/verification_parser.py::evaluate_expectation`. | **Applied.** Both surviving git-diff rows switched to `match count == 0`, measurement columns corrected to 2. **Measured:** `git diff HEAD --stat -- docs/features/popoto-index-hygiene.md \| wc -l` read `0` untouched and `2` after a one-line append, the raw output being the per-file line plus the `1 file changed, 1 insertion(+)` summary. `evaluate_expectation`'s `match count == 0` branch was read at `agent/verification_parser.py:198-207` and confirmed to accept the `"       0"` shape while gating on non-empty stdout. The third git-diff row (`config/reflections.yaml`) was removed outright by the first BLOCKER, so only two remain. | Correct the measurement column to 2 and switch the two surviving git-diff rows to `match count == 0`. |
| NIT | History & Consistency | The `SessionType` section lead-in is at `docs/features/standardized-enums.md:13`, not line 11 (line 11 is the `### SessionType` header; `grep -n 'eng, teammate, or granite'` returns 13). The plan says "line 11" in five places. The GRANITE row at line 19 is correct, so the file has not shifted -- the number is simply wrong. Same class as the round-1 span nit, whose fix re-verified the four #2853 spans without re-verifying the one #2839 span it introduced. | **Applied.** Corrected in all 12 occurrences (the critique said five; `grep -c` found 12). Re-verified at the source: `grep -n 'eng, teammate, or granite' docs/features/standardized-enums.md` -> `13`; line 11 is the `### SessionType` header, line 12 blank. The GRANITE row is confirmed still at line 19. | Change every "line 11" to "line 13". Low risk either way: the plan quotes the sentence verbatim and the gate greps the phrase, not the line number. |
| NIT | Scope & Value | The Success Criterion "PR body carries both `Closes #2853` and `Closes #2839`" maps to no task in `## Step by Step Tasks` and to no row in `## Verification`. Every other criterion has at least one. For a two-issue lane whose companion closes only via the PR body, this is the one criterion whose omission silently leaves an issue open. | **Applied.** Both remedies taken. Verification row "PR body closes both issues" added -- `gh pr view --json body -q .body \| grep -c 'Closes #2839'` / `output > 0` -- and Task 6 now instructs the validator to confirm both closing keywords. **Mutation-measured on the `grep` leg:** a body carrying both keywords reads **1** (passes); a body carrying only `Closes #2853` reads **0** (fails). The `gh` leg cannot be exercised before the PR exists; `gh pr view --json body -q .body` was confirmed against merged PR #2833 to emit the body text the grep expects. | Add a Verification row keyed on the PR body -- `gh pr view --json body -q .body \\| grep -c 'Closes #2839'` / `output > 0` -- or state explicitly in Task 6 that the validator checks the PR body. |
| NIT | Scope & Value | The Verification row titled "Per-file rename coverage, all five sites" takes four file arguments and expects `output > 3`, i.e. all four FILES. The fifth site (`models/ghost_reconcile.py:36`) is a second site inside a file already in the list and is covered by the `nightly` row, not this one. | **Applied.** Retitled to "Per-file rename coverage, all four files". Command and expectation unchanged -- `output > 3` over four file arguments was already correct; only the title overstated it. | Retitle to "Per-file rename coverage, all four files" so the row's name matches what it measures. |
| NIT | History & Consistency | The Freshness Check row for `tests/integration/test_reflections_redis.py:199,204` calls the daily cadence "a **committed contract**". The assertion is committed, but the artifact it asserts against is not: line 193 of that test opens `config/reflections.yaml`, which is gitignored at `.gitignore:8` and untracked. The plan's central justification for preserving the "daily" prose is therefore a tracked test reading a machine-local file -- weaker than the phrase implies (the vault copy being byte-identical is what actually carries it). | **Applied.** Reworded to "asserted by a tracked test against the machine-local `config/reflections.yaml`, which is byte-identical to the vault copy", with the tracked/untracked split spelled out so the next reader is not surprised to find the YAML absent from `git ls-files`. Re-verified: `tests/integration/test_reflections_redis.py:192-193` opens `config/reflections.yaml`, `.gitignore:8` is that exact path, and `diff ~/Desktop/Valor/reflections.yaml config/reflections.yaml` is clean. | Reword to "asserted by a tracked test against the machine-local `config/reflections.yaml`, which is byte-identical to the vault copy" so the next reader is not surprised to find the YAML absent from `git ls-files`. |

### Found during revision (not in the critique)

Mutation-checking the `nightly` → `daily` claim surfaced a **fifth stale site**:
`models/ghost_reconcile.py:36`, "the same one the nightly reflection calls". It sits in
the same docstring as line 23, and it carries **no `popoto-index-cleanup` token** — so
neither normative sweep, and neither version of the durable guard test, can reach it.
It was found only because the anti-criterion `grep -rc 'nightly'` across the four named
files returned `ghost_reconcile.py:2` where the plan's inventory implied `:1`.

This is worth naming plainly: the plan's root-cause diagnosis is "every prior gate was
scoped narrower than the claim it certified," and the plan was about to repeat it. The
claim is "no live prose calls this sweep nightly"; the gate was keyed on the *name*
token. Both the site and a `nightly`-keyed row are now in scope, and the site is listed
explicitly in Task 1 because no sweep will find it for the builder.

---

## Open Questions

None. Both issues' premises were re-verified against main `f491306c5` at plan time, the
one false premise (#2853's "actual cadence is worker startup only") was corrected and
recorded as a `## Recon Summary` on the issue, and the site inventory was replaced with
a repo-wide token sweep. No decision requires human input.

**Issue comments synced.** #2853 comment `5337658489` (the supervisor's premise
correction) is folded into the Freshness Check and the Technical Approach. #2839
comments `5324500746` and `5324617995` are folded in as well: both ask "does
`SessionType.GRANITE` still have live producers before you repoint the path?" and answer
their own question — it does not, the member stays for historical-record hydration, and
the fix is a one-row doc edit. `5324617995` also surfaced `models/agent_session.py:4, 102`
as a third in-repo wording precedent, now cited in the Technical Approach. Its closing
recommendation ("no plan needed, a direct commit on `main`") is superseded: #2839 is
being carried through the pipeline as the companion of #2853 in one two-issue lane, so
it lands via the lane's PR with `Closes #2839` rather than as a hotfix.
