---
status: Planning
type: chore
revision_applied: true
revision_applied_at: 2026-09-07T03:43:39Z
tracking: https://github.com/tomcounsell/ai/issues/3178
appetite: Small
---

# Move mechanical plan checks out of critique rounds

## Problem

Critic subagents spend LLM budget re-deriving facts a cheap deterministic check could establish. In the run that motivated this (#2733 / PR #3174), a third-round critique produced findings of which several were mechanically checkable rather than judgment: a `file:line` citation asserting something false about the code, and Verification commands that did not behave as the plan claimed.

Five rounds of critique on this plan established which half of that is real.

**A citation that resolves but asserts something false is a genuine, recurring defect.** The original evidence: a plan claimed a call site was "not inside a `try`" when `draft_message` is called at `agent/output_handler.py:754` inside a `try` opened at `:751` with `except Exception` at `:885`. This plan then reproduced the same class twice more — citing a plan document that migrated to the archive between authoring and critique, and citing `SKILL.md:132` for content that lives at `:148-151`.

**Executing or evaluating the Verification table is not.** Round 5 measured it and the case collapsed in two independent directions, both documented under Decision D4.

## Appetite

**Extra small.** An extension to one existing structural check. No new module, no console script, no CLI, no fixture harness.

## Solution

Extend `do-plan-critique/SKILL.md` **Step 2c** from *"does this path exist"* to *"does the cited line say what the plan claims"*.

Step 2c already extracts file paths from a plan and reports non-existent ones. It stops at existence. The extension resolves each `path:line` citation and emits the cited line's text into the structural-check output, so a citation that resolves but misdescribes the code is visible to critics before they spend a finding deriving it.

That is the whole change.

**Explicitly cut (Decision D4):** evaluating or executing the `## Verification` table, in every form — subprocess, allowlisted subprocess, and native Python re-implementation alike.

## Technical Approach

The citation check is backward-looking: a `file:line` reference describes code that exists *now*, so it can be checked *now*. This is the property the Verification table lacks, and it is why one survives and the other does not.

Implementation sits inside the existing Step 2c pass. For each extracted `path:line`:

- resolve the path relative to the repo root; report unresolvable paths exactly as Step 2c does today
- if a line number is present and in range, emit that line's text alongside the citation
- if the line number is out of range, report it as a drifted citation

No subprocess, no shell, no execution of any plan-authored string. The check reads files and reports what it read.

## Freshness Check

Re-run at `5ee947700` (2026-09-07). The `agent/output_handler.py:751/754/885` try/except claim holds. `docs/sdlc/do-plan-critique.md` is 186 lines. `SKILL.md` Step 2 header is at `:132` with the 2c bullets at `:148-151`. Prior-art issue #1760 is CLOSED; #3178 OPEN; PR #3174 MERGED.

**Disposition: Unchanged** for everything this narrowed plan still depends on.

## Research

Phase 0.7 skipped: purely internal — one repo skill and its structural-check step. No external libraries, APIs, or ecosystem patterns.

## Prior Art

**#1760 — `/do-sdlc` PLAN↔CRITIQUE router never converges to BUILD** (closed 2026-07-11). Documents a revision pass that embeds critique notes into plan text, busting the plan hash and re-staling a recorded verdict, looping indefinitely. It records a lineage of five prior dead-end fixes: `3e1e3dae` (#1668), `6e943ea9` (#1639), `5bc6243a` (#1638/#1640/#1641), `8218c5af` (#1554), `627e3cf0` (#1755).

## Why Previous Fixes Failed

Every fix in that lineage adjusted *when the router re-dispatches*. None reduced *how many findings each round produces*. This plan targets finding volume instead — and, after five rounds, targets only the portion of it that is genuinely mechanical.

## Step by Step Tasks

1. **Extend Step 2c** in `.claude/skills-global/do-plan-critique/SKILL.md`: resolve `path:line` citations, emit the cited line's text, and report out-of-range line numbers as drifted citations.
2. **Record the severity** in the same bullet list Step 2c already uses: a drifted or out-of-range citation is a CONCERN, consistent with "non-existent file path → CONCERN".
3. **Repo addendum note** in `docs/sdlc/do-plan-critique.md` describing the extension at its existing structural-check section.

## Failure Path Test Strategy

- An unreadable or binary file → the citation is reported unresolvable; the structural pass continues and the verdict is unaffected.
- A path that resolves but has fewer lines than the citation → reported as drifted, not raised.
- A citation with no line number → existing Step 2c behavior, unchanged.
- The extension raising for any reason must not block critic dispatch; the structural check is advisory input to critics, never a gate.

## Test Impact

No existing tests affected. The change edits skill markdown, which carries no test coverage in this repo today. Verified: `grep -rl "plan_lint" tests/` returns nothing, and no test asserts on Step 2c output.

## Rabbit Holes

- **Do not evaluate or execute the Verification table.** Five rounds of critique closed this; D4 records why.
- **Do not build a general citation-claim checker.** Emitting the cited line for a human or critic to compare is the deliverable. Judging whether the prose *around* the citation matches the line is judgment, and stays with critics.
- **Do not rewrite the Verification-table format.**

## No-Gos

From the repo owner, load-bearing and unchanged across five rounds:

- Do **not** reduce planning thoroughness.
- Do **not** skip or shorten critique rounds, reduce roster size, or lower the round bound.
- Do **not** defer tech-debt review findings to follow-up issues.

This change alters *what critics spend budget on*, never *how much checking happens*.

## Update System

No update-system changes. The edit ships inside the repo and reaches every machine through the normal `scripts/remote-update.sh` pull. No new dependency, config file, or migration.

## Agent Integration

No agent integration required. The change edits a skill body that the critique pass already executes; there is no new CLI entry point and no bridge-internal import.

## Documentation

- [ ] Update `docs/sdlc/do-plan-critique.md` (186 lines, exists) to describe the Step 2c citation-content extension at its structural-check section.
- [ ] No `docs/features/` page: this is a step inside an existing skill, not a feature with its own surface.

## Verification

Re-executed at `5ee947700` before being written down. Every row is a plain argv-form command; no row depends on an exit status that inverts on the no-match path.

| Check | Command | Expected | Pre-build actual |
|---|---|---|---|
| Step 2c section exists to extend | `grep -q "2c. Internal References" .claude/skills-global/do-plan-critique/SKILL.md` | exit code 0 | exit 0 |
| Repo addendum exists | `test -f docs/sdlc/do-plan-critique.md` | exit code 0 | exit 0 |
| No plan-lint module was built | `test -e tools/plan_lint.py` | exit code 1 (nothing to build) | exit 1 |

## Success Criteria

- Step 2c resolves `path:line` citations and emits the cited line's text in its structural-check output.
- An out-of-range line number is reported as a drifted citation.
- The citation check never blocks critic dispatch.
- No module, console script, CLI flag, fixture harness, or subprocess is added anywhere by this plan.
- No change to round counts, roster size, or the concern re-critique bound.

## Critique Results

War room round 1, 2026-09-06. FULL depth, independent roster of 3. Verdict: **NEEDS REVISION** (3 blockers).

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Structural check; Risk & Robustness (Operator); History & Consistency (Consistency Auditor) | Verification row 3 and row 5 both target `docs/plans/rtr-unconditional-2733.md`, which no longer exists. Commit `bf0a5d577` ("Migrate completed plan: rtr-unconditional-2733", 2026-09-06 13:44) moved it to `docs/archive/plans-completed/rtr-unconditional-2733.md`, after this plan's stated baseline `d4d1519b2`. Re-run at HEAD `d5ba0ce45`, row 3 raises `FileNotFoundError` and exits 1, not the recorded "printed 25, exit 0". The `## Freshness Check` verdict **Disposition: Unchanged** is therefore contradicted at HEAD, and contradicted in the very category it claims is untouched — a plan being archived is a plan-format-surface event. This is the citation-drift defect class plan-lint exists to catch, live inside the plan that proposes plan-lint. | **Resolved** — revision r1. No Verification row cites any `docs/plans/` document; rows now target `tests/fixtures/plan_lint_sample.md`, a committed fixture that never migrates. Freshness Check re-run at `cd5f0572e` and re-dispositioned **Minor drift**, naming `bf0a5d577`. Every row re-executed and its Pre-build actual re-recorded. | Repoint rows 3 and 5 to `docs/archive/plans-completed/rtr-unconditional-2733.md` and re-run both to record real Pre-build actuals (row 3 against the archive path prints `25`, exit 0 — driver-verified). Correct the Freshness Check disposition to name commit `bf0a5d577`. Then make the fixture immune to recurrence: `tests/unit/test_plan_lint.py` must NOT hardcode a live plan path, because plan archival will break it again on a clean checkout. Copy a frozen 25-row sample into `tests/fixtures/` and point the test there, or resolve by issue number through `tools.lane_identity.find_plan_path`. |
| BLOCKER | Risk & Robustness (Adversary) | The denylist screens the command's literal text for destructive *verbs* and has no read-side restriction, so an ordinary, non-malicious row like `cat .env` or `printenv` executes and its stdout is captured by design. `/Users/tomcounsell/src/ai/.env` is a symlink to `~/Desktop/Valor/.env`, the vault of record, reachable from any worktree by the invoking user — so "execute in the lane's own worktree" bounds file writes and gives zero protection against reads. Captured stdout has a stated path into a findings block, and `docs/sdlc/do-plan-critique.md:173` commits and pushes plan/critique artifacts to `main`. That is a route from an ordinary-looking Verification row to a live secret in public git history. Separately, literal-substring matching is defeated by quote-concatenation: `r'm' -rf .` contains no adjacent `rm`, yet bash executes it as `rm -rf .` after quote removal. | **Resolved** — revision r1, adopted in full as Decision D1. Five controls now required together: opt-in `--execute` (default parses only), `shlex.split` tokenized matching so quote-concatenation cannot defeat it, a read-side secrets denylist (`.env`, dotfiles, `printenv`, `env`, `op`, `security find-generic-password`, out-of-worktree paths), process-group timeout kill, and a 2 KB stdout cap with secret-shaped scrubbing before any output reaches a committable artifact. The `.env`-symlink read path and the commit-to-`main` exfiltration route are both named explicitly in the Technical Approach. | Two changes in the executor. (1) Normalize before matching: `shlex.split` then rejoin, and run the destructive-pattern test against the normalized string, never the raw source text. (2) Add a second denylist keyed on argument tokens `.env`, `Desktop/Valor`, `credentials*`, `*.pem`, `id_rsa` and the bare commands `env`/`printenv`/`set`, reported identically as `skipped: not auto-executable`. Then cap and scrub captured stdout before it is placed in any artifact a later stage can commit — the scrub, not the execute flag, is what closes the exfiltration-to-commit path. Recorded as controls 1, 2 and 4 of decision D1. |
| BLOCKER | Scope & Value (Simplifier) | Checks 2 and 3 substantially reimplement machinery that already runs at this exact pipeline point. `.claude/skills-global/do-plan-critique/SKILL.md:132` Step 2c already specifies "Extract file paths mentioned in the plan... Check which ones exist and which don't — report non-existent paths as findings; Extract test file paths from Test Impact section — verify they exist", and Step 2e covers the cross-reference mapping check 3 performs. Step 2d further says "For each prerequisite with a check command, run it and report current pass/fail status", which partially overlaps check 1. The `## Technical Approach` names two rejected alternatives (inside `/do-plan`; as a pre-commit hook) and never considers extending the mechanism already running on the same document in the same round. Shipping both produces two findings for one fact — which is the finding-volume problem this plan exists to reduce. | **Resolved** — revision r1 narrowed Part A. A table in `## Solution` maps Steps 2c/2d/2e to what they already cover; the standalone disposition-differ is **dropped**, and citation checking is scoped as an *extension* of 2c (content comparison) rather than a duplicate. Part A now ships only the two genuine gaps: executing the Verification table, and comparing cited line content. | State the disposition explicitly in `## Technical Approach` for each of Step 2c, 2d and 2e: replaced by plan-lint (and delete the superseded SKILL.md bullet in Task 7's edit so one fact is checked once), or retained as additive with the reason named. The defensible additive claim is precision, not coverage: 2c tests file *existence* only, whereas check 2 resolves `path:line`, confirms the line is in range, and emits the cited line's text. Check 3's set-difference against the planned file set has the weakest independent claim — 2c already verifies Test Impact paths exist — so justify it or drop it from Part A. |
| CONCERN | Risk & Robustness (Operator) | No timeout value is named anywhere in the plan, and the plan's own Verification row 4 puts `scripts/pytest-clean.sh` — a wrapper that spawns xdist workers — directly into the executed path. A force-killed row with no process-group-aware termination leaves orphaned test workers, which CLAUDE.md flags as a shared-machine hazard whose only sanctioned remedy is `scripts/reap-xdist.sh`. | **Resolved** — revision r1. 30s per command, with each started in its own process group and the **group** killed, explicitly because `scripts/pytest-clean.sh` (xdist) is the kind of command a Verification row carries and a leader-only kill orphans workers. | Pick and document a concrete per-row timeout (30s is a reasonable default given `pytest-clean.sh` runs far longer and should therefore land as a `timeout` row, not a pass). Use `subprocess.run(..., timeout=N, start_new_session=True)` and on `TimeoutExpired` kill the process group via `os.killpg`, not the single PID. Add no pattern-based cleanup to `tools/plan_lint.py` — a broad kill is blocked by `.claude/hooks/validators/validate_no_broad_process_kill.py` and would take out other lanes' runs. |
| CONCERN | History & Consistency (Archaeologist) | The plan names #1760 as load-bearing prior art — a revision pass embedding notes into the plan text busts `compute_plan_hash` and re-stales an already-recorded verdict — then says "Findings are advisory, reported to `/do-plan` as a revision" without saying whether that revision ever edits the plan body after round 1. If it does, this is #1760's exact mechanism applied to mechanical findings instead of judgment ones, and the plan states no exemption. | **Resolved** — revision r1. The findings block attaches to the **critique input** and never edits the plan body, keeping plan-lint outside `compute_plan_hash` and the G7 `plan_revising` lock entirely. Stated in the Technical Approach with the #1760 rationale. | State which of two paths this is, in `## Technical Approach`. Path (a): lint findings are ephemeral critique-input context and never touch the plan file — no hash interaction, nothing further needed. Path (b): a lint-driven `/do-plan` pass writes into the plan document, in which case name how it avoids re-staling a recorded verdict against `tools.sdlc_verdict.compute_plan_hash` and the `plan_revising` G7 lock, which `docs/sdlc/do-plan-critique.md` documents as having no once-revised exemption. Path (a) is strongly preferred: it keeps Part A's promise that it touches no router logic. |
| CONCERN | Scope & Value (User) | The `## Problem` section's evidence names four mechanically-checkable categories from the measured run — verification-command mismatch, false citation, "a declared appetite contradicting the plan's own counts", and a missing disposition entry — but `## Solution` builds three checks. Appetite-vs-counts is dropped silently, appearing in no Rabbit Hole, No-Go, or Open Question, so the Problem section overstates what this plan delivers. | **Overstated in r1; genuinely resolved in r2.** The r1 text restated Blocker 3's fix and never addressed this finding — caught in round 2 by History & Consistency and Scope & Value independently. `## Solution` now carries a four-row disposition table giving an explicit exclusion rationale for both uncovered categories, including why appetite-vs-counts is not mechanically decidable and stays with the Scope & Value critic. | Add one line to `## Solution` or `## Rabbit Holes` either excluding appetite-consistency with a reason (the plausible one: "appetite" is a Shape Up judgment call, not an arithmetic fact, so it is not actually mechanical) or naming it as explicitly deferred. No fourth executor exists in Tasks 1-6 today. |
| NIT | History & Consistency; Structural check | The figures "37 of 43 plans" (`## Technical Approach`), "37 existing plans use it" (`## Rabbit Holes`) and "6 of 43 have none" (`## Failure Path Test Strategy`) do not match the tree. Driver-measured at HEAD `d5ba0ce45`: 23 top-level `docs/plans/*.md` plus 13 in `docs/plans/done/`, with 19 of 23 top-level plans carrying a `## Verification` section. Same archive-migration drift that broke row 3. | **Resolved** — revision r1. All three figures removed rather than recomputed. Measured at `cd5f0572e` they were 24 / 13 / 20 — already different from the critique's own `d5ba0ce45` measurement hours earlier, which is the argument for carrying no fixed count in a plan at all. | Recompute against current HEAD and state the corpus the figure covers, since `docs/archive/plans-completed/` now holds plans the original count included. A plan whose purpose is catching stale mechanical claims should not carry three of its own. |
| NIT | Scope & Value | The `## Documentation` bullet reads "Update `docs/sdlc/do-plan-critique.md` (or create it)" as though existence were uncertain. It exists, at 187 lines, carrying the roster-barrier mechanics, the `critique-roster-check` gate, the `MAX_CRITIC_REDISPATCH` cap and the Step 5.5 finalize block. | **Resolved** — revision r1. The hedge is gone; the task is scoped as a targeted insertion of the pre-dispatch paragraph, naming the file's 186 lines and leaving the roster-barrier and finalize-block sections undisturbed. | Drop the "(or create it)" hedge and scope the task as a targeted insertion of the plan-lint pre-dispatch paragraph, leaving the roster-barrier and finalize-block sections undisturbed. |

Open Question 1 (executing plan-authored shell) was put to all three critics as a required, explicitly-reasoned item. All three converged on a hybrid. It is resolved below as **D1** and removed from `## Open Questions`.


### Round 2 — 2026-09-06

**Verdict:** NEEDS REVISION — 3 blockers, 3 concerns, 1 nit.
**Critics:** Risk & Robustness, Scope & Value, History & Consistency (FULL depth, force-FULL: task 7 edits `.claude/skills-global/`).
**Mode:** independent roster (3 critics). Roster gate: `{"complete": true, "missing": [], "ungrounded": []}`, 3/3.
**Independent convergence:** Scope & Value and History & Consistency reached the appetite-disposition blocker separately, from different directions. Severity taken as the higher of the two.

| Severity | Critics | Finding | Addressed By |
|---|---|---|---|
| BLOCKER | Risk & Robustness | D1 specifies denylist matching on `shlex.split` argv[0], but Verification row 7 requires real shell expansion (`$(...)`) to work at all. If the executor uses a shell to satisfy that row, `echo ok; rm -rf .` has argv[0] `echo` and passes the check while the shell still runs the `rm`. Verified empirically: the row exits 0 under `shell=True` and exits 2 under `shell=False`; `echo ok; echo X` runs both segments with argv[0] `echo`. The plan requires shell semantics and argv-based safety simultaneously, which cannot both hold. | **Resolved** — r2. The shell is removed entirely rather than the denylist hardened. Execution is `shell=False` on a parsed argv; any row containing a shell metacharacter (`;` `&&` `\|\|` `\|` `$(` backtick, redirection) is reported `skipped: requires shell, not auto-executable` and never executed. This converts a mitigation into a guarantee, at a stated cost in coverage. The plan's own two subshell rows are rewritten into plain argv form. |
| BLOCKER | History & Consistency | Verification row "Lint targets a non-migrating fixture" records `Pre-build actual: printed 4, exit 0`; the command actually returns `6` (driver-verified). It was wrong when written — the measured value at the time was `5` — and drifted further as the plan was edited. A self-referential citation-drift defect inside the row built to demonstrate the technique this plan proposes. | **Resolved** — r2. The row no longer records a brittle absolute count. It asserts the fixture path is referenced at all, which is the property actually being verified, and the recorded actual is re-measured at the r2 HEAD. |
| BLOCKER | History & Consistency **+** Scope & Value (independent convergence) | The round-1 appetite-vs-counts CONCERN is dispositioned "Resolved" but the resolution text restates the fix for Blocker 3 instead. `## Problem` still names "a declared appetite contradicting the plan's own counts" as one of four evidence categories, while `## Solution` ships checks for two, and no line anywhere excludes or defers appetite-consistency checking. Confirmed by grep: the word "appetite" appears nowhere in `## Solution`. | **Resolved** — r2. `## Solution` now carries an explicit exclusion rationale for both uncovered categories, and the round-1 disposition is corrected rather than left overstated. |
| CONCERN | Risk & Robustness | D1's read-side denylist is narrower than the Technical Approach's description of the same control: D1 omits `op` and `security find-generic-password`, which the Technical Approach lists. `op read op://m-valor/<item>/credential` is this repo's own sanctioned non-interactive secret read and would execute, relying solely on the stdout scrub as backstop. | **Resolved** — r2. Single list, stated once, covering both. Made moot in the common case by the no-shell decision, but retained because an argv-form `op read …` is still executable. |
| CONCERN | Risk & Robustness | `os.killpg` reaches xdist workers but not a grandchild that self-detaches via `setsid`/`nohup`/`disown`, which survives the 30s timeout as an orphan outside even `scripts/reap-xdist.sh`'s recognition. | **Resolved** — r2. Documented as an accepted residual limit with its detection path, not silently carried. The no-shell decision removes the common route to `nohup`/`disown`, which are shell constructs. |
| CONCERN | Scope & Value | Success Criterion "Plan-lint raising an exception leaves critique dispatch unaffected" and the Failure Path fail-open guarantee have no counterpart in Task 7, which says only "invoke and attach". A literal implementation could let a plan-lint crash block critic dispatch, violating the No-Go against skipping critique rounds. | **Resolved** — r2. Task 7 now states the fail-open requirement explicitly at the wiring site. |
| NIT | History & Consistency | The Blocker-3 disposition cites `do-plan-critique/SKILL.md:132` for the Step 2c bullets; 132 is the `## Step 2` header and the quoted content is at 148-151. | **Resolved** — r2. Citation corrected to the range. |


### Round 3 — 2026-09-07

**Verdict:** NEEDS REVISION — 2 blockers, 2 concerns, 2 nits.
**Critics:** Risk & Robustness, Scope & Value, History & Consistency (FULL depth). **Mode:** independent roster (3 critics), gate 3/3, none ungrounded.
**Run under an explicit human override of the G2 critique cycle cap.**

**Central measurement (Scope & Value).** Counted every `## Verification` command row across `docs/plans/` and `docs/archive/plans-completed/` — 5,454 rows, after unescaping markdown's `\|` cell escaping. 1,149 (21.1%) contain a shell metacharacter and would be skipped under D2; in the live top-level corpus, 62 of 227 (27.3%). The scope-collapse hypothesis is refuted: roughly three rows in four remain executable, and the plan's motivating defect class is plain argv. **Execution stays in scope.**

| Severity | Critics | Finding | Addressed By |
|---|---|---|---|
| BLOCKER | Risk & Robustness | `shell=False` plus an argv[0] denylist is not the claimed guarantee. `sh -c 'rm -rf .'` and `python3 -c "__import__('os').system(...)"` contain none of the eight screened metacharacters, and neither `sh` nor `python3` appeared in the denylist. Any interpreter or uninspected wrapper as argv[0] re-introduces a shell one layer down. | **Resolved** — r3. The denylist is inverted into an **allowlist** of read-only binaries (`test`, `grep`/`rg`, `git` restricted to read subcommands, `ls`/`wc`/`head`/`tail`, `sed -n`, `python -m` with `-c` refused, `scripts/pytest-clean.sh`). Everything else is `skipped: not on the execution allowlist`. Enumerating the safe set is tractable; enumerating the dangerous set is not. |
| BLOCKER | Risk & Robustness | The `setsid` residual-limit paragraph claimed the denylist catches `setsid` as argv[0], but `setsid` appeared in no list anywhere in the plan — a control asserted in prose and never specified. `setsid python3 task.py` was argv-safe, uncaught, and survives the 30s `os.killpg`. | **Resolved** — r3. Under the allowlist the claim is true by construction: `setsid` is not permitted, so no enumeration is needed. The paragraph now states the genuine residual — an allowlisted binary that detaches its own child, of which `scripts/pytest-clean.sh` is the only candidate and the reason the group kill exists. |
| CONCERN | Scope & Value | `## Test Impact` still listed the "disposition differ" as covered by the new test file, though `## Solution` and the round-1 Blocker-3 resolution both drop that check from scope. Leftover from the r1/r2 narrowing. | **Resolved** — r3. Reference removed; Test Impact now names the citation-content check. |
| CONCERN | Scope & Value | The Success Criterion "run against the #2733 plan, it flags the six `grep -c`-style rows" had no backing task or Verification row, and cited a document the plan's own Freshness Check forbids citing because it has already migrated to the archive. | **Resolved** — r3. The criterion now targets `tests/fixtures/plan_lint_sample.md`, and task 8 requires the fixture to carry those six rows verbatim plus a metacharacter row, an off-allowlist row, and a resolving-but-false citation. |
| NIT | History & Consistency | "Their replacements avoid the trap a second way" is plural, but only one of the two removed rows was replaced; the other was dropped outright. | **Resolved** — r3. Singular. |
| NIT | Scope & Value | The shell-coverage cost was stated only qualitatively while every other quantitative claim in the document was driver-verified. | **Resolved** — r3. The measured 21.1% / 27.3% figures are recorded with their date and corpus, and explicitly marked a one-time measurement rather than a claim to re-verify as the corpus drifts. |

**Clean in this round:** History & Consistency re-ran all seven Verification rows from the worktree and every recorded Pre-build actual matched; all cited SHAs resolve; the `SKILL.md:132` / `:148-151` citation is correct; both round-2 blockers verified genuinely resolved rather than relabelled.


### Round 4 — 2026-09-07

**Verdict:** NEEDS REVISION — 2 blockers, 3 concerns, 1 nit.
**Critics:** Risk & Robustness, Scope & Value, History & Consistency (FULL depth, force-FULL: task 7 edits `.claude/skills-global/`). Roster gate: `{"complete": true, "missing": [], "ungrounded": []}`, 3/3.
**Mode:** sequential lenses (Agent tool unavailable: not in tool list). No finding below was independently corroborated — each lens was applied once, in sequence, by the same driver. Read the severities accordingly.
**Run under an explicit human override of the G2 critique cycle cap; last authorized round.**

Both blockers are residue from the r3 patch: the allowlist inversion reached one section and not the six that specify the build, and the fixture requirement r3 added inherited an unverified count from the evidence document it cites. Nothing settled in rounds 1-3 is reopened.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | History & Consistency (Consistency Auditor) | The r3 denylist-to-allowlist inversion landed only in `## Technical Approach` control 3. Every section a builder implements from still specifies the denylist round 3 proved unwinnable: task 2 (:133) orders "write-side and read-side denylists" and says to build them *before* the executor; task 3 (:134) says "run each non-denylisted row"; `## Failure Path Test Strategy` (:144) asserts "A denylisted command → row reported `skipped: not auto-executable`", which is also the superseded skip string; `## Test Impact` (:151) lists denylist coverage; `## Documentation` (:182) would write "the denylist and its limits" into `docs/features/plan-lint.md`; and Success Criteria (:213, :216) gate on "every non-denylisted Verification row" and "A denylisted command is reported skipped". `### Round 4 (independent roster) — 2026-09-07

**Verdict:** NEEDS REVISION — 3 blockers, 1 concern.
**Critics:** Risk & Robustness, Scope & Value, History & Consistency. **Mode:** independent roster (3 critics). Roster gate: `{"complete": true, "missing": [], "ungrounded": []}`, 3/3.
**Run under Tom's blanket G2 override, with explicit authorisation to continue while rounds keep returning value.**

**Concurrent-critique note.** A second round-4 critique ran independently and landed first as commit `a0b7772ec`, recorded in the section above. Its Mode line reads `sequential lenses (Agent tool unavailable: not in tool list)` — the spawn-depth collapse tracked in #3198, occurring inside this very lane. Its findings were verified here rather than displaced, and both sets are retained. Two of its findings corroborate two of this roster's independently.

| Severity | Critics | Finding | Addressed By |
|---|---|---|---|
| BLOCKER | Risk & Robustness | `git -c diff.external='touch /tmp/poc; #' diff` executes an arbitrary command through an allowlisted binary and allowlisted subcommand, with `shell=False`, no shell metacharacter, and no argument matching the rejection list. Demonstrated in the worktree, not reasoned. The allowlist constrained subcommands but never global flags, defeating the read-only premise the design rested on. | **Resolved by removal** — Decision D3. Plan-lint executes nothing; there is no allowlist to escape. |
| BLOCKER | Risk & Robustness | `python`/`python3 -m` runs any module's `__main__`. Refusing `-c` does not bound it — `python -m worker` is this repo's documented session execution engine, so a Verification row could launch a worker under `--execute`. | **Resolved by removal** — Decision D3. |
| BLOCKER | History & Consistency **+** the concurrent critique (independent corroboration) | Third false "Resolved" in this plan: the round-2 disposition claimed the `op`/`security` gap became "a single list, stated once", but `

### Round 5 — 2026-09-07

**Verdict:** NEEDS REVISION — 5 blockers, 2 concerns. **This round ended the feature's main check.**
**Critics:** Risk & Robustness, Scope & Value, History & Consistency. **Mode:** independent roster (3 critics). Gate: `{"complete": true, "missing": [], "ungrounded": []}`, 3/3.

| Severity | Critics | Finding | Addressed By |
|---|---|---|---|
| BLOCKER | Scope & Value | **The defect essentially never occurs.** All 799 corpus `grep -c` rows whose Expected reads as a zero/no-match claim were checked: the trap this plan was built to catch — correct output paired with a misleading exit code — produced almost no real findings. Native evaluation would silently confirm rows rather than surface anything. | **Resolved by cutting the check** — Decision D4. |
| BLOCKER | Scope & Value | **The false-positive rate is near total, and it is a category error.** Of 26 open plans carrying a `## Verification` section, exactly one uses the "Pre-build actual" reconciling column — this plan, which invented it for itself in round 1. The other 25 describe **post-build** state, so evaluating a forward-looking row at critique time reports a mismatch that is correct about the tree and wrong about the plan. Demonstrated against a live `Planning`-status plan. | **Resolved by cutting the check** — D4. |
| BLOCKER | Risk & Robustness **+** History & Consistency (independent corroboration) | `## Decisions` and `## Failure Path Test Strategy` still carried D1/D2-era text contradicting D3: the Decisions block declared "No `subprocess` import exists in the module" and two lines later listed `subprocess.run(..., timeout=N, ...)` as a control, with a Reasoning paragraph asserting execution was necessary. | **Resolved** — r5 wholesale rewrite. |
| BLOCKER | History & Consistency **+** Scope & Value (independent corroboration) | Stale execute-and-denylist language survived in four builder-facing sections — Failure Path Test Strategy, Test Impact, Documentation, Success Criteria — the last contradicting itself within one list. A literal build from those sections would have reconstructed the four-times-falsified executor D3 existed to remove. | **Resolved** — r5 wholesale rewrite of every affected section, rather than the anchored patching that produced this residue four rounds running. |
| BLOCKER | History & Consistency | Task 8 still demanded "the six `grep -c`-style rows" after round 4 corrected that count everywhere except the section a builder implements the fixture from. Fifth falsified count-based claim in this plan. | **Resolved** — the fixture and task no longer exist under D4. |
| CONCERN | Risk & Robustness | The shape table claimed to report "the exit status real `grep` would return" but ignored `grep`'s exit 2 on error, and specified Python `re` where `grep` uses POSIX BRE — `\|` alternation and `[[:alpha:]]` classes differ, so a pattern valid in one dialect and not the other yields a wrong verdict: a false finding of exactly the kind this plan exists to eliminate. | **Moot** — D4. |
| CONCERN | Risk & Robustness | The `git grep` row claimed scoping to "tracked files" while D3 forbade `subprocess`, leaving no stated mechanism short of parsing `.git/index`. | **Moot** — D4. |

**Outcome.** Rounds 1-4 hardened an execution design against escalating escapes. Round 5 asked whether the thing being hardened was worth having and measured that it was not. The plan drops from a module, console script, CLI flag, fixture harness and wiring point to a single extension of an existing structural check. Appetite falls from Small to Extra Small; the document loses a third of its length.

## Decisions

**D1 and D2 — RETIRED.** Opt-in execution behind `--execute`, bounded first by a denylist and then by an argv[0] allowlist. Rounds 2-4 falsified each bounding attempt in turn, ending with a demonstrated `git -c diff.external=…` arbitrary execution with every stated control in force.

**D3 — RETIRED.** Native Python re-implementation of recognised row shapes, replacing execution. Removed the escape surface but did not survive round 5's measurement.

**D4 — the Verification table is not checkable at critique time, in any implementation.** Two independent measurements, both round 5:

1. **The defect essentially never occurs.** All 799 corpus `grep -c` rows whose Expected reads as a zero/no-match claim were checked. The specific trap this plan was built to catch — correct output paired with a misleading exit code — produced almost no real findings. Native evaluation would silently confirm rows rather than surface anything.
2. **The false-positive rate is near total, and it is a category error rather than a bug.** Of 26 open plans carrying a `## Verification` section, exactly **one** uses the "Pre-build actual" reconciling column — this plan, which invented it for itself in round 1. The other 25 use a bare `Check | Command | Expected` form describing **post-build** state. Evaluating a forward-looking row before the build reports a mismatch that is correct about the tree and wrong about the plan.

A check that almost never finds a true defect and reports a false one on nearly every row would increase critic load, which is the opposite of this issue's purpose. Cutting it is the finding, not a retreat from it.

**What survives.** Citation-content checking, which is backward-looking and has three recorded instances in this plan's own history.

## Open Questions

1. Should a drifted citation be a CONCERN or a NIT? The plan proposes CONCERN by analogy with Step 2c's existing "non-existent file path → CONCERN", but a line number off by a few after a refactor is arguably cosmetic.
2. Is the `docs/sdlc/do-plan-critique.md` addendum needed at all, or is the skill-body edit self-documenting?
