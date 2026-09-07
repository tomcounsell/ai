---
status: Planning
type: chore
revision_applied: true
revision_applied_at: 2026-09-07T01:40:58Z
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
3. **Allowlist on the resolved argv[0]** — round 3 proved a denylist unwinnable here: `sh -c 'rm -rf .'` and `python3 -c "__import__('os').system(...)"` contain none of the screened metacharacters, and any interpreter or wrapper script defeats an enumerate-the-dangerous approach by construction. Execution is therefore permitted only for an enumerated set of read-only binaries, and everything else is `skipped: not on the execution allowlist`:

   | Permitted argv[0] | Restriction |
   |---|---|
   | `test`, `[` | none |
   | `grep`, `rg` | none |
   | `git` | subcommands `grep`, `log`, `show`, `diff`, `rev-parse`, `cat-file`, `status` only |
   | `ls`, `wc`, `head`, `tail` | none |
   | `sed` | `-n` only (read-only invocation) |
   | `python`, `python3` | `-m` only — **`-c` is refused**, since it is an inline interpreter |
   | `scripts/pytest-clean.sh` | none |

   Every argument is additionally rejected if it references `.env`, a dotfile, `Desktop/Valor`, `credentials`, `*.pem`, `id_rsa`, or resolves outside the worktree. `sh`, `bash`, `zsh`, `env`, `printenv`, `op`, `security`, `setsid`, `nohup`, `curl`, `ssh`, `rm`, and `pkill` are absent from the allowlist and are therefore refused without needing to be enumerated anywhere — which is the point of inverting the list.
4. **Process-group timeout kill** — 30s per command, started in its own process group, killing the group rather than the leader, because `scripts/pytest-clean.sh` spawns xdist workers. **Accepted residual limit:** a grandchild that self-detaches via `setsid` survives a process-group kill. Round 3 correctly found that the r2 text claimed `setsid` was "caught by the denylist" when `setsid` appeared in no list anywhere — a control asserted but never specified. Under the allowlist above the claim is now true by construction: `setsid` is not a permitted argv[0], and `nohup`/`disown` are shell constructs already excluded by control 2. The residual that genuinely remains is an allowlisted binary that itself detaches a child; `scripts/pytest-clean.sh` is the only such candidate and it is the reason the group kill exists. A survivor is detectable through `scripts/reap-xdist.sh`.
5. **stdout cap and scrub** — cap captured output at 2 KB per row and scrub it against a secret-shaped pattern set before any of it reaches a findings block or committable artifact.

**Stated cost, measured.** Rows needing a shell are not executed, so coverage is partial by construction. Measured once on 2026-09-07 across every `## Verification` row in `docs/plans/` and `docs/archive/plans-completed/` (5,454 rows): 1,149 (21.1%) contain a shell metacharacter; restricted to the live top-level corpus, 62 of 227 (27.3%). So roughly three rows in four remain executable, and the plan's own motivating defect class — `grep -c pattern file` — is plain argv, unaffected. This figure is a one-time measurement recorded with its date and corpus, not a claim to be re-verified as the corpus drifts. That is the intended trade: a row that cannot be run safely is reported as unrun rather than run unsafely, and the reader sees which rows were skipped and why.

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
8. **Fixture + tests** — `tests/fixtures/plan_lint_sample.md`, a committed plan-shaped document that never migrates. It must carry, verbatim: the six `grep -c`-style rows from the original evidence, one row whose command contains a shell metacharacter (to exercise `skipped: requires shell`), one row whose argv[0] is off the allowlist such as `sh -c` (to exercise `skipped: not on the execution allowlist`), and one `file:line` citation that resolves but asserts something false. Plus `tests/unit/test_plan_lint.py` covering each.

## Failure Path Test Strategy

- A Verification command that times out → row reported `timeout`, lint continues, exit status unaffected.
- A denylisted command → row reported `skipped: not auto-executable`, never executed. Assert the subprocess was not invoked.
- A malformed or absent `## Verification` section → lint reports "no verification table" and exits 0. A plan without one is valid; absence is not a defect.
- A `file:line` citation whose file was deleted → reported as unresolved, not raised.
- Plan-lint itself raising → the critique skill proceeds to critic dispatch regardless. Lint is advisory; it must never be able to block a critique round.

## Test Impact

- [ ] `tests/unit/test_plan_lint.py` — NEW: parser, executor, denylist, citation resolver, and the fail-open path.
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

Round 2 removed two rows that violated this. Both used `test "$(…)"` subshells to work around the exit-status trap, and both would now be skipped rather than executed. The surviving row's replacement avoids the trap a second way — by asserting a boolean with `grep -q` instead of comparing a count:

- The fixture row previously counted occurrences and recorded `printed 4`. The real count was `5` when written and `6` by round 2, drifting as the plan was edited. An absolute count of a string inside the document that contains the count is self-referential and cannot be kept true. `grep -q` asserts the property actually being checked — the fixture is referenced — and cannot drift.
- The greenfield row (`git grep -l plan_lint -- tests/` expecting no match) was dropped entirely. It could only ever pass *before* the build; task 8 adds a test that references `plan_lint`, so the row was guaranteed to invert the moment the work landed. A Verification row that must fail after the work completes is not a verification.

The exit-status trap that motivated this plan is still real — `grep -c` prints `0` and exits `1` on no match — which is exactly why no row here depends on it.

## Success Criteria

- `plan-lint <plan.md>` executes every non-denylisted Verification row and prints claimed vs. actual exit code and stdout for each.
- Run against `tests/fixtures/plan_lint_sample.md`, it flags the six `grep -c`-style rows whose actual exit status is 1 while the row reads as a success condition. The fixture reproduces that pattern verbatim from the original evidence (preserved at `docs/archive/plans-completed/rtr-unconditional-2733.md:876,886-895`), so the criterion is backed by a Verification row and a task rather than by a document that has already migrated once.
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
| BLOCKER | History & Consistency (Consistency Auditor) | The r3 denylist-to-allowlist inversion landed only in `## Technical Approach` control 3. Every section a builder implements from still specifies the denylist round 3 proved unwinnable: task 2 (:133) orders "write-side and read-side denylists" and says to build them *before* the executor; task 3 (:134) says "run each non-denylisted row"; `## Failure Path Test Strategy` (:144) asserts "A denylisted command → row reported `skipped: not auto-executable`", which is also the superseded skip string; `## Test Impact` (:151) lists denylist coverage; `## Documentation` (:182) would write "the denylist and its limits" into `docs/features/plan-lint.md`; and Success Criteria (:213, :216) gate on "every non-denylisted Verification row" and "A denylisted command is reported skipped". `## Decisions` compounds it — it holds D1 alone, whose controls 1 and 2 *are* the two denylists, and carries no D2 entry at all though the Technical Approach cites "Decision D2" as governing. A builder working the task list and ticking the Success Criteria ships the rejected control and documents it as the design. | pending | Two skip strings now exist and are not interchangeable: `skipped: requires shell, not auto-executable` (metacharacter screen, control 2) and `skipped: not on the execution allowlist` (allowlist screen, control 3). Task 3, the Failure Path row and both Success Criteria must each name the one they mean, because the tests assert on these strings and a test written against the old `skipped: not auto-executable` passes on an implementation that never built the allowlist. Keep the argument-token rejection (`.env`, dotfiles, `Desktop/Valor`, `credentials`, `*.pem`, `id_rsa`, out-of-worktree) as a live control when rewriting D1 — the allowlist does not make it redundant, since an allowlisted `grep` or `sed -n` can still target `.env`; driver-verified that `/Users/tomcounsell/src/ai/.env` is a symlink to `/Users/tomcounsell/Desktop/Valor/.env` and is therefore readable from any worktree. |
| BLOCKER | History & Consistency (Archaeologist) | The fixture requirement is unsatisfiable against the source it cites. Task 8 (:139) orders the builder to carry "verbatim: the six `grep -c`-style rows from the original evidence", and the Success Criterion (:214) says plan-lint "flags the six `grep -c`-style rows whose actual exit status is 1", preserved at `docs/archive/plans-completed/rtr-unconditional-2733.md:876,886-895`. Driver-measured against that document: exactly **four** rows are phrased `match count == 0` (:887, :888, :893, :894), and no sixth candidate exists anywhere in the file — the other `grep -c` rows in the cited range (:876, :886, :889, :890, :895) expect `output contains 2` or `output > 0` and exit 0 on a match, so they are not the defect class. Two of the four (:893, :894) are `git diff origin/main -- <path> \| grep -c …` pipelines, which D2 reports `skipped: requires shell` and never executes. Only **two** rows are simultaneously the defect class and executable under D2. The figure "six" traces to the #2733 plan's own critique NIT at :918, which carries `Addressed By: pending` — an unverified count inherited from a finding never applied. This is the citation-drift class the plan exists to catch, inherited into the plan's own acceptance criterion. | pending | The fixture needs three distinct row classes from `:886-895`, not one bundle. (1) The two executable defect-class rows verbatim — `grep -c "^## Rollout" docs/features/read-the-room.md` and `grep -ci "flip criterion" docs/features/read-the-room.md`, both expecting `match count == 0`; driver-confirmed both print `0` and exit `1` today, the exact claimed-vs-actual mismatch check 1 must surface. (2) One of the two pipeline rows (:893 or :894) verbatim, which then satisfies task 8's separate "one row whose command contains a shell metacharacter" requirement rather than needing a synthesized row. (3) A control row expecting `output > 0`, e.g. `grep -c "is_group_chat" docs/features/read-the-room.md` (exits 0), so the test proves plan-lint distinguishes the defect class from an ordinary passing `grep -c` row instead of flagging every `grep -c`. The fixture's rows must not be re-executed against live repo paths in the test — assert on recorded expected/actual pairs, or `docs/features/read-the-room.md` becomes a live dependency of a fixture the plan requires never to drift. |
| CONCERN | Risk & Robustness (Skeptic) | `plan-lint` is not on its own execution allowlist, so this plan's Verification rows 4 and 5 (:197-198) resolve argv[0] to a binary the r3 allowlist does not permit and are reported `skipped: not on the execution allowlist`. The paragraph at :202 asserts the table is "the plan holding itself to Decision D2" and that a skipped row "would make the table unrunnable by its own linter", but it screens only for shell metacharacters — the r2 form of D2. The r3 allowlist added a second way for a row to be skipped and this self-consistency claim was not re-checked against it, so 2 of 7 rows are unrunnable by the tool this plan builds while the plan claims the opposite. | pending | If `plan-lint` is added to the allowlist, guard the recursion at the executor: before running a row, compare `shutil.which(argv[0])` against the resolved path of `sys.argv[0]`/`__file__` and refuse a self-invocation with `skipped: self-invocation refused`. Without that guard, `plan-lint <this plan> --execute` executes row 5, which is itself `plan-lint <fixture> --execute` — bounded today only because the fixture happens to carry no plan-lint row, which is an accident of fixture content, not a control. The alternative fix is smaller: amend :202 to state rows 4-5 are expected `skipped: not on the execution allowlist` and are covered by `tests/unit/test_plan_lint.py` instead. |
| CONCERN | Scope & Value (User) | The allowlist entry is written as the bare relative path `scripts/pytest-clean.sh` (:86), but the corpus this tool exists to lint writes the command both ways. Driver-measured across `docs/plans/` and `docs/archive/plans-completed/`: 437 rows use the bare form and 126 use `./scripts/pytest-clean.sh`. A literal argv[0] string comparison against the table as written skips all 126 with `skipped: not on the execution allowlist`, and the reader sees a skip reason that reads like a safety refusal rather than a path-spelling mismatch. This is silent coverage loss in the one place the plan works hardest to be honest about coverage — "Stated cost, measured" accounts for metacharacter skips at 21.1% and says nothing about spelling-driven skips. | pending | Normalize before matching rather than adding spellings to the table: compare `os.path.relpath(os.path.realpath(argv0), worktree_root)` against the entry. Guard the traversal case first — a normalized result starting with `..` resolves outside the worktree and must be refused, which is the rule :88 already applies to arguments. Do not resolve repo-script entries via `shutil.which`: a PATH lookup would let a same-named script elsewhere on the machine satisfy a repo-path allowlist entry. |
| CONCERN | Risk & Robustness (Operator) | The mandatory fail-open at the wiring site has no observability sink. Task 7 (:138) says a non-zero exit, timeout or exception is "logged and discarded" and dispatch proceeds regardless, but the wiring site is a skill markdown body that `## Test Impact` (:152) itself says "carries no test coverage today", and no artifact records whether plan-lint ran. A permanently-failing plan-lint — missing console script after a partial `uv sync`, an import error, a parse crash on a new plan shape — is indistinguishable from one that ran and found nothing. That silence confounds Open Question 2, whose purpose is measuring whether Part A reduces findings per round: a null result cannot be read as "no effect" when "never executed" produces the identical signal. | pending | In task 7, make the findings block unconditional: on success it carries the rows; on any failure it carries the single line `plan-lint: UNAVAILABLE (<exception class or exit code>)`. The wiring must never emit an absent or empty block, because absence is what makes the failure invisible. Since the block attaches to the critique input and reaches `## Critique Results` at finalize, that one line makes "did lint run this round" answerable from the committed plan alone — no new logging infrastructure, no change to the fail-open guarantee. |
| NIT | Scope & Value (User) | The evidence is a single run (#2733 / PR #3174) and the plan says so openly, which is honest, but available corroboration is not cited. On 2026-09-05, five of eight parallel SDLC lanes stopped at the two-round critique cap with round-2 findings that were plan-text residue or small named fixes, and all five converged in exactly one more round; the stage trails are on issues #3167, #2743, #3170, #2764 and #3072. That is the structural pattern Part A addresses, at n=5 rather than n=1. | pending | — |

**Clean in this round:** every cited SHA resolves at HEAD (`bf0a5d577` subject and 2026-09-06 13:44 date match the plan text; `d4d1519b2`, `cd5f0572e`, `d5ba0ce45`; all five #1760-lineage SHAs `3e1e3dae`, `6e943ea9`, `5bc6243a`, `8218c5af`, `627e3cf0`). #1760's quoted title matches verbatim and it is CLOSED; PR #3174 is MERGED. `SKILL.md:132` is the Step 2 header and the 2c bullets sit at :149-151 under the :148 sub-header, inside the range the plan cites. `agent/output_handler.py` still calls `draft_message` at :754 inside a `try` opened at :751 with `except Exception` at :885. `docs/sdlc/do-plan-critique.md` is 186 lines as stated. All seven `## Verification` rows re-executed at HEAD match their recorded Pre-build actuals, including `grep -rl "plan_lint" tests/` returning nothing. The r3 allowlist genuinely closes the round-3 `sh -c` / `python3 -c` bypass, and the Freshness Check baseline `cd5f0572e`, though 21 commits behind HEAD, has broken no claim in the document.

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
