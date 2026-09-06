---
status: Planning
type: chore
revision_applied: true
revision_applied_at: 2026-09-06T16:53:08Z
tracking: https://github.com/tomcounsell/ai/issues/3178
appetite: Small
---

# Move mechanical plan checks out of critique rounds

## Problem

Critic subagents spend LLM budget re-deriving facts a shell command could establish. In the one run measured (#2733 / PR #3174), round 3 produced 11 findings of which 6 were mechanically checkable — a verification command that does not behave as the plan claims, a `file:line` citation asserting something false, a declared appetite contradicting the plan's own counts, a disposition table missing an entry. Only 5 needed judgment.

Two of those reproduce directly:

- Six Verification rows phrased "match count == 0" were satisfied by a bare `grep -c`, which prints `0` and **exits 1**. Re-verified here: `grep -c nonexistent_token README.md` → prints `0`, exit `1`. A validator gating on exit status reads six correct results as failures.
- The plan asserted a call site was "not inside a `try`". False: `draft_message` is called at `agent/output_handler.py:754` inside a `try` opened at `:751` with `except Exception` at `:885`.

Neither needs a model. Both cost a critic round.

## Appetite

**Small.** One new module, one test file, one wiring point, one doc. The coding is a focused session. Parts B and C below are explicitly *not* in this appetite.

## Solution

A `plan-lint` pass that runs before the first critic is dispatched and reports mechanical defects in a plan document, so critics receive a plan whose checkable claims are already true.

**Scope narrowed after round-1 critique (Blocker 3).** The critique established that `do-plan-critique/SKILL.md` Step 2 (header at `:132`, the 2c bullets at `:148-151`) already runs structural checks at this exact pipeline point on this exact document:

| Existing step | What it already does | Originally-planned check it covers |
|---|---|---|
| 2c Internal References | Extracts file paths from the plan, reports non-existent ones; extracts Test Impact test paths and verifies they exist | Citation resolution (partly), disposition completeness (partly) |
| 2d Prerequisite Status | Runs each **prerequisite's** check command and reports pass/fail | Command execution — but only for prerequisites |
| 2e Cross-Reference Consistency | Success criteria map to tasks; No-Gos absent from Solution; Rabbit Holes absent from tasks | Disposition completeness (rest) |

Building all three originally-planned checks would emit two findings per fact, which is precisely the finding-volume problem this plan exists to reduce. So Part A reduces to the two things Step 2 genuinely does not do:

1. **Execute the `## Verification` table.** Step 2d runs *prerequisite* commands only; Verification-table commands are never executed at critique time. This is the gap that produced the original evidence — six `grep -c` rows whose real exit status is 1, and an over-broad `grep -rn` that would fire on correct work.
2. **Compare cited line *content*, not just path existence.** Step 2c answers "does this file exist"; it cannot catch a citation that resolves but asserts something false — the `agent/output_handler.py` "not inside a `try`" case. Extend 2c rather than duplicating it.

**Dropped, with rationale (round-2 Blocker 3).** `## Problem` names four mechanically-checkable categories from the original run. Part A ships checks for two. The other two are excluded deliberately:

| Category from `## Problem` | Disposition |
|---|---|
| Verification command does not behave as claimed | **Shipped** — check 1. |
| `file:line` citation asserting something false | **Shipped** — check 2. |
| Disposition table missing an entry present in the diff | **Dropped** — `SKILL.md` Steps 2c and 2e already extract Test Impact paths and cross-check tasks against criteria. A standalone differ would emit a second finding for the same fact. |
| Declared appetite contradicting the plan's own counts | **Dropped** — this is not mechanically decidable. "Small appetite" versus a task list's true size is a judgment about effort, not a countable property, and the round-1 instance was found by a critic weighing scope, not by counting. Automating it would produce false positives on any plan whose task list is long but shallow. It stays with the Scope & Value critic, which is where it was caught. |

Two of four is therefore the intended coverage, not an oversight. `## Problem`'s framing is evidence of what critics spend rounds on; it is not a specification of what Part A automates.

## Technical Approach

**Where it lives: a standalone module invoked by the critique skill.** `tools/plan_lint.py` with a `plan-lint` console script, called from `/do-plan-critique` before critic dispatch. Rejected alternatives: inside `/do-plan` (the author checking its own work is the weaker position, and a revision pass would need to re-run it anyway); as a pre-commit hook (plans commit incrementally by design, so a blocking hook would fire on every partial write).

**Findings are advisory, reported to `/do-plan` as a revision.** Not blocking. The two existing plan-document validators block, but they check for *presence* of a required section — a binary fact. Plan-lint reports *behavioral* mismatches whose correct resolution is sometimes "the expectation was written loosely", not "the plan is wrong". Blocking on a judgment call would trade critic rounds for lint rounds.

**Safety — Decision D1, resolving Open Question 1.** Execution is **opt-in**. `plan-lint` parses and reports by default; it runs commands only under an explicit `--execute` flag, which the `/do-plan-critique` wiring passes at its single call site.

Round-1 critique (Blocker 2) established that the flag alone closes nothing, because the original denylist was verb-based and write-oriented while the real exposure is **reads flowing into a committed artifact**. `cat .env` and `printenv` pass every verb check; `/Users/tomcounsell/src/ai/.env` is a symlink to `~/Desktop/Valor/.env`, so it is reachable from any worktree and confinement bounds writes while giving zero protection against reads; captured stdout has a stated path into a findings block, and `docs/sdlc/do-plan-critique.md` commits and pushes critique artifacts to `main`. That is a route from an ordinary-looking Verification row to a secret in public git history.

**No shell, ever (Decision D2, resolving round-2 Blocker 1).** Round 1 specified denylist matching on `shlex.split` argv[0]. Round 2 proved that incompatible with the plan's own Verification rows, empirically:

- A row of the form `test "$(grep -c X F)" -ge 3` exits 0 under `shell=True` and **exits 2 under `shell=False`** — the subshell never expands.
- `echo ok; rm -rf .` has `argv[0] == "echo"`, passing an argv[0] check, while a shell still executes the trailing `rm`.

So requiring shell semantics for rows and argv-based safety for the executor cannot both hold. Hardening the denylist does not fix this; any denylist over shell text is a mitigation, and the shell is what makes bypasses cheap.

The resolution removes the shell rather than patching the list:

1. **Opt-in execution** — no command runs without `--execute`. The default path executes nothing.
2. **`shell=False`, always** — `subprocess.run(shlex.split(cmd), shell=False)`. Any row whose text contains a shell metacharacter (`;`, `&&`, `||`, `|`, `$(`, backtick, `>`, `<`, `&`) is reported `skipped: requires shell, not auto-executable` and is **never executed**. This is a guarantee rather than a mitigation: with no shell there is no chaining, no substitution, and no redirection to defeat.
3. **Denylist on the resolved argv[0]** — one list, covering write-side verbs (`rm`, `git push`, `git reset --hard`, `curl`, `ssh`, `pkill`) **and** read-side secret readers (`env`, `printenv`, `set`, `op`, `security`), plus any argument referencing `.env`, a dotfile, `Desktop/Valor`, `credentials`, `*.pem`, `id_rsa`, or a path outside the worktree. Stated once here and nowhere else, so the two descriptions cannot drift apart as they did in round 1. `op read op://…` is this repo's own sanctioned secret read and is named explicitly.
4. **Process-group timeout kill** — 30s per command, started in its own process group, killing the group rather than the leader, because `scripts/pytest-clean.sh` spawns xdist workers. **Accepted residual limit:** a grandchild that self-detaches via `setsid` survives this. Control 2 removes the common route, since `nohup` and `disown` are shell constructs and a detaching row would have to invoke `setsid` as argv[0] — which the denylist catches. A survivor is detectable through `scripts/reap-xdist.sh`; this is recorded rather than claimed solved.
5. **stdout cap and scrub** — cap captured output at 2 KB per row and scrub it against a secret-shaped pattern set before any of it reaches a findings block or committable artifact.

**Stated cost.** Rows needing a shell are not executed, so coverage is partial by construction. That is the intended trade: a row that cannot be run safely is reported as unrun rather than run unsafely, and the reader sees which rows were skipped and why.

**Findings never edit the plan body.** The findings block is attached to the **critique input**, not written into the plan document. This is deliberate and load-bearing: #1760 documents that a revision pass which edits plan text busts `compute_plan_hash` and re-stales the just-recorded verdict, looping PLAN↔CRITIQUE indefinitely. Attaching to the critique input keeps plan-lint outside that mechanism entirely.

**Parsing.** The `## Verification` table is a stable three-column markdown form (`| Check | Command | Expected |`) with the command in backticks. Prevalence is high but drifts as plans are authored and archived, so this plan deliberately records no fixed count — see Rabbit Holes. The `Expected` column is free text.

## Freshness Check

**Re-run at `cd5f0572e` (2026-09-06T16:37Z), superseding the round-1 baseline `d4d1519b2`.**

Round-1 critique caught this section asserting "Unchanged" while two of the plan's own Verification rows had already broken. Commit `bf0a5d577` ("Migrate completed plan: rtr-unconditional-2733", 2026-09-06) moved `docs/plans/rtr-unconditional-2733.md` to `docs/archive/plans-completed/`, after the recorded baseline. The rows citing it returned `FileNotFoundError`, exit 1 — not the recorded "printed 25, exit 0".

That is the citation-drift class this plan exists to catch, occurring in this plan, between authoring and critique. It is the strongest available argument for the tool and is retained here deliberately rather than quietly corrected.

**Disposition: Minor drift.** The claims hold; two citations moved. Remediation:

- Verification rows no longer cite any document under `docs/plans/`. Plans migrate to `docs/archive/plans-completed/` on completion, so any such citation has a scheduled expiry. Rows now use a committed fixture under `tests/fixtures/` instead.
- Counts of the form "N of M plans" are removed throughout. Measured at this HEAD they are 24 top-level plans, 13 done, 20 top-level carrying a `## Verification` section — already different from the values the round-1 critique measured hours earlier. A number that drifts between critique rounds does not belong in a plan.
- `docs/sdlc/do-plan-critique.md` exists (186 lines); the Documentation task no longer says "or create it".

## Research

Phase 0.7 skipped: this work is purely internal — repo skills, a repo-local module, and plan-document format. No external libraries, APIs, or ecosystem patterns are involved. The one question with external literature (whether cross-critic duplication is a sound saturation proxy) belongs to Part C, which this plan defers.

## Prior Art

**#1760 — `investigation: /do-sdlc PLAN↔CRITIQUE router never converges to BUILD (notes-only revision re-stales a clean verdict)`** (closed 2026-07-11). This is the load-bearing prior art and it directly constrains scope.

It documents the same loop from the other end: a revision pass embeds critique notes into the plan text and sets `revision_applied: true`, which busts the plan hash and re-stales the just-recorded verdict, sending the router back to re-critique indefinitely. Both observed runs "had to manually drive the BUILD stage against a plan that was already marked build-ready, with zero code-correctness blockers ever raised."

It also records a lineage of five prior dead-end fixes to this router area: `3e1e3dae` (#1668), `6e943ea9` (#1639), `5bc6243a` (#1638/#1640/#1641), `8218c5af` (#1554), `627e3cf0` (#1755).

## Why Previous Fixes Failed

Every fix in that lineage adjusted *when the router re-dispatches* — stale-verdict supersession, empty-verdict dead-ends, re-fire guards. None reduced *how many findings each round produces*. The loop kept running because each pass minted fresh non-blocking prose findings, and the machinery had no way to run out of them.

That is the gap this plan targets, and it is why the ordering matters: **the concern re-critique bound is best understood as the circuit breaker for this known-recurring loop.** Removing or loosening it (issue #3178 Part C) without first reducing finding volume would remove a brake from a mechanism that has already resisted five repairs. Part A reduces the input to the loop and touches no router logic, so it is safe to land alone and makes any later Part C decision measurable rather than speculative.

## Step by Step Tasks

1. **`tools/plan_lint.py`** — parse the `## Verification` table; extract commands, the `Expected` text, and `file:line` citations from the whole document.
2. **Safety layer (D1)** — `--execute` flag defaulting off; `shlex.split` tokenization; write-side and read-side denylists; per-command 30s timeout killing the whole process group; 2 KB stdout cap with secret-shaped scrubbing. Build this *before* the executor, so no code path can run a command before the controls exist.
3. **Verification executor** — under `--execute` only, run each non-denylisted row and emit claimed vs. actual exit code and (capped, scrubbed) stdout.
4. **Citation content check** — for each `path:line`, resolve it and emit the cited line's text so a citation that resolves but asserts something false is visible. This extends `SKILL.md` Step 2c rather than duplicating its existence check.
5. **Report format** — a markdown findings block attached to the critique input. It must not write into the plan body (see Technical Approach, #1760).
6. **Console script** — register `plan-lint` in `pyproject.toml [project.scripts]`.
7. **Wire into `/do-plan-critique`** — invoke with `--execute` at the single call site, before critic dispatch, and attach the findings block to the critique input. **Fail-open is mandatory at this site:** wrap the invocation so that a non-zero exit, a timeout, or any exception from `plan-lint` is logged and discarded, and critic dispatch proceeds regardless. Plan-lint must never be able to block a critique round — that would violate the No-Go against skipping them.
8. **Fixture + tests** — `tests/fixtures/plan_lint_sample.md` (a committed plan-shaped document that never migrates) and `tests/unit/test_plan_lint.py`.

## Failure Path Test Strategy

- A Verification command that times out → row reported `timeout`, lint continues, exit status unaffected.
- A denylisted command → row reported `skipped: not auto-executable`, never executed. Assert the subprocess was not invoked.
- A malformed or absent `## Verification` section → lint reports "no verification table" and exits 0. A plan without one is valid; absence is not a defect.
- A `file:line` citation whose file was deleted → reported as unresolved, not raised.
- Plan-lint itself raising → the critique skill proceeds to critic dispatch regardless. Lint is advisory; it must never be able to block a critique round.

## Test Impact

- [ ] `tests/unit/test_plan_lint.py` — NEW: parser, executor, denylist, citation resolver, disposition differ, and the fail-open path.
- [ ] No existing tests are affected. `tools/plan_lint.py` is a new module with no importers; wiring into `/do-plan-critique` edits a skill markdown body, which carries no test coverage today. Verified: `grep -rl "plan_lint" tests/` returns nothing.

## Rabbit Holes

- **The free-text `Expected` column.** Observed forms include `exit code 1`, `output contains 2`, and `match count == 0`. Do **not** build a general expectation-grammar interpreter. Report claimed text alongside actual behavior and let the reader compare. Constraining the vocabulary is a separate change to the plan template.
- **Do not rewrite the Verification-table format.** Most existing plans use it, and the count drifts as plans are archived — which is why no fixed figure appears in this plan.
- **Do not extend into linting prose quality.** Mechanical checks only; judgment stays with critics.

## No-Gos

Load-bearing, from the issue's Non-goals — these are the repo owner's stated position:

- Do **not** reduce planning thoroughness.
- Do **not** skip or shorten critique rounds, reduce roster size, or lower the round bound.
- Do **not** defer tech-debt review findings to follow-up issues.

The goal is strictly to change *what critics spend their budget on*, never *how much checking happens*.

Also out of scope for this plan: Part B (post-revision sweep) and Part C (convergence-based exit). Part C is deferred with reasoning recorded under Why Previous Fixes Failed.

## Update System

No update-system changes required. `tools/plan_lint.py` ships inside the repo and reaches every machine through the normal `scripts/remote-update.sh` pull. The `plan-lint` console script is installed by the existing `uv sync` step in that script, the same path every other `tools.*` entry point already uses. No new dependency, config file, or migration.

## Agent Integration

A `plan-lint` console script is registered in `pyproject.toml [project.scripts]`, which is how the agent reaches it via Bash. No bridge-internal import is needed: the caller is the `/do-plan-critique` skill body, which invokes it as a shell command like every other `sdlc-tool` step. No new Telegram-facing surface.

## Documentation

- [ ] Create `docs/features/plan-lint.md` — what the checks are, the denylist and its limits, why findings are advisory, and the #1760 relationship.
- [ ] Add a row to `docs/features/README.md` index table.
- [ ] Insert a pre-dispatch lint paragraph into `docs/sdlc/do-plan-critique.md` (186 lines, exists), leaving the roster-barrier and finalize-block sections undisturbed.

## Verification

Every command below was re-executed against `origin/main` at `cd5f0572e` (2026-09-06T16:37Z) before being written down; the "Pre-build actual" column records what it really did. Rows are read from stdout unless the row says "exit code".

**No row cites a document under `docs/plans/`.** Round-1 critique found two rows broken by the archive migration of a completed plan. Plans migrate on completion, so citing one from a Verification table gives that row a scheduled expiry. Rows now target a committed fixture instead.

| Check | Command | Expected | Pre-build actual |
|---|---|---|---|
| Module exists | `test -f tools/plan_lint.py` | exit code 0 | exit 1 (absent, as expected pre-build) |
| Console script registered | `grep -q '^plan-lint' pyproject.toml` | exit code 0 | exit 1 (absent, as expected pre-build) |
| Fixture exists | `test -f tests/fixtures/plan_lint_sample.md` | exit code 0 | exit 1 (absent, as expected pre-build) |
| Lint parses the fixture without executing | `plan-lint tests/fixtures/plan_lint_sample.md` | exit code 0, findings block on stdout, no command run | n/a — not yet built |
| Execution requires the flag | `plan-lint tests/fixtures/plan_lint_sample.md --execute` | exit code 0, rows carry actual exit codes | n/a — not yet built |
| Tests pass | `scripts/pytest-clean.sh tests/unit/test_plan_lint.py` | exit code 0 | n/a — file does not exist yet |
| Lint targets a non-migrating fixture | `grep -q tests/fixtures/plan_lint_sample.md docs/plans/move-mechanical-plan-checks-out-of-critique.md` | exit code 0 | exit 0 |

**Every row above is argv-safe: no `;`, no `$(`, no pipes, no redirection.** This is deliberate, and it is the plan holding itself to Decision D2 — a row containing a shell metacharacter would be reported `skipped: requires shell` by the very tool this plan builds, which would make the table unrunnable by its own linter.

Round 2 removed two rows that violated this. Both used `test "$(…)"` subshells to work around the exit-status trap, and both would now be skipped rather than executed. Their replacements avoid the trap a second way — by asserting a boolean with `grep -q` instead of comparing a count:

- The fixture row previously counted occurrences and recorded `printed 4`. The real count was `5` when written and `6` by round 2, drifting as the plan was edited. An absolute count of a string inside the document that contains the count is self-referential and cannot be kept true. `grep -q` asserts the property actually being checked — the fixture is referenced — and cannot drift.
- The greenfield row (`git grep -l plan_lint -- tests/` expecting no match) was dropped entirely. It could only ever pass *before* the build; task 8 adds a test that references `plan_lint`, so the row was guaranteed to invert the moment the work landed. A Verification row that must fail after the work completes is not a verification.

The exit-status trap that motivated this plan is still real — `grep -c` prints `0` and exits `1` on no match — which is exactly why no row here depends on it.

## Success Criteria

- `plan-lint <plan.md>` executes every non-denylisted Verification row and prints claimed vs. actual exit code and stdout for each.
- Run against the #2733 plan, it flags the six `grep -c`-style rows whose actual exit status is 1 while the row reads as a success condition.
- It resolves `file:line` citations and surfaces the cited line text.
- A denylisted command is reported skipped and demonstrably not executed.
- Plan-lint raising an exception leaves critique dispatch unaffected.
- `/do-plan-critique` runs it before dispatching critics and passes the findings block into the critique input.
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

## Decisions

### D1. Executing plan-authored shell — resolved (critique round 1, 2026-09-06)

**Decision.** Execution is **opt-in**. `plan-lint <plan.md>` parses and reports without running anything; Verification-table commands run only under an explicit `--execute`. The `/do-plan-critique` wiring (Task 7) passes `--execute` at its single call site, and the plan says so rather than leaving it to be inferred. Execution, when enabled, is gated by four controls, all required:

1. The destructive-pattern denylist, matched **after** shell-quote normalization (`shlex.split` then rejoin) so quote-concatenation (`r'm' -rf .`) cannot slip a denylisted verb past a raw-text regex.
2. A **read-side** denylist keyed on argument tokens — `.env`, `Desktop/Valor`, `credentials`, `*.pem`, `id_rsa` — plus the bare commands `env`, `printenv`, `set`. Matches are reported `skipped: not auto-executable`, the same as a destructive match.
3. A hard per-row timeout with process-group termination (`subprocess.run(..., timeout=N, start_new_session=True)`, then `os.killpg` on `TimeoutExpired`).
4. A cap and a secrets scrub on captured stdout **before** it reaches any artifact a later stage can commit.

**Reasoning.** All three critics converged on a hybrid rather than a clean (a) or (b), for three independent reasons that compose.

The default belongs at report-only because that is this repo's posture on an ambiguous safety call. `op` fails closed and reports rather than degrading; the standing rules on pattern process kills and raw Redis writes remove the judgment call from automation instead of enumerating the bad shapes. A denylist is structurally the opposite move — it executes everything it did not think of — and this plan's own text already conceded it "is a mitigation, not a guarantee." The two existing plan-document validators cited as precedent block synchronously at pre-commit with the authoring agent present; plan-lint runs unattended inside a possibly-overnight pipeline. Same shell, materially different blast radius.

The pipeline still has to execute, because the plan's one evidenced value driver is check 1, and check 1 is inert without execution. A parse-only pass can repeat what a row *claims*; only running it reveals that a `grep -c` row reading as success actually exits 1. So a default-off flag protects the ad hoc human invocation, which is where opt-in genuinely earns its keep, at no cost to the automated path.

The controlling insight is that the flag alone closes nothing. The real exposure is not whether a command runs but what happens to its stdout afterward. `~/Desktop/Valor/.env` is a symlink target reachable from any worktree by the invoking user, so "the lane's own worktree" bounds file *writes* and gives no protection against reads. A perfectly ordinary row — `cat .env`, `printenv` — passes every verb-based check, and captured stdout has a stated path into a findings block that the critique finalize step commits and pushes to `main`. Controls 2 and 4 above, not the flag, are what close that path.

**Scope note.** This decision changes no round count, roster size, or the concern re-critique bound, and touches no router logic. It stays inside Part A.

## Open Questions

1. **Advisory vs. blocking.** The plan argues advisory. If lint findings are routinely ignored, blocking becomes the stronger position. Worth revisiting after real runs.
2. **Part C interaction.** Given #1760's five-fix lineage, does landing Part A measurably reduce findings per round? If it does not, the saturation hypothesis behind Part C loses its main support and should be reconsidered rather than built.
3. **Does the review side need the same treatment?** `/do-pr-review` findings were not categorized in the measured run, so there is no evidence yet either way.
