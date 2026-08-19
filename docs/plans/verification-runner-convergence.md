---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2836
also_closes: https://github.com/tomcounsell/ai/issues/2843
last_comment_id: 5324500130
---

# Verification-Runner Convergence: One Table Definition, One Expectation Evaluator

## Problem

This repo has **two** runners for the same artifact — the `## Verification` table in a plan
document — and they disagree. Measured on the same table, same run, during `/do-build` for
#2741 (PR #2842):

| Runner | Result on #2741's table |
|---|---|
| `agent/verification_parser.py` (`parse_verification_table` + `run_checks`) | 19 PASS, 0 malformed |
| `scripts/validate_build.py` | 8 PASS, 10 FAIL, 1 SKIP, exit 1 |

Both are named in `docs/sdlc/do-build.md` (`:182` and `:185`). A non-zero
`validate_build.py` exit routes to `/do-patch` for up to three iterations, so a clean build
gets sent to chase failures that do not exist.

Each runner is already correct exactly where the other is wrong.

**#2836 — `agent/verification_parser.py` has no table-boundary concept.** `:139` collects
*every* line starting with `|` anywhere in the `## Verification` section into one flat list
and `:149` skips only the first two. A second markdown table — a red/green summary, a
findings recap — contributes its header, its separator, and all its data rows as
"checks". Their Expected cells (`Meaning`, `---`, `demonstrated red — must reach 0`) are
unrecognized grammar, which `evaluate_expectation` returns `False` for, so each one is a
guaranteed FAIL. The gate then fails no matter how correct the real checks are, and the
failure names rows the author never wrote as checks.

Reproduced on the #2741 plan text as it stood before commit `61717ccb2`:
`parse_verification_table` returns **27 checks**. Sixteen are real. Eleven are the summary
table. The only fix applied at the time was reformatting the summary table into bullets — a
workaround, and every future plan author walks into the same trap.

**#2843 — `scripts/validate_build.py` carries a second, weaker expectation evaluator.**
`:291-303` understands `exit code N`, `output contains X`, and `output <exact>`, then falls
through to `expected.lower() in actual_output.lower() or actual_exit == 0`. Consequences:

- `output > N` reaches the `startswith("output ")` branch and string-compares stdout against
  `"> 0"`. Never true. `1 > 0` reports FAIL.
- `match count == 0` reaches the flexible fallback. A satisfied anti-criterion prints `0` and
  `grep -c` exits **1**, so both disjuncts are false → FAIL on a clean tree.
- The same fallback reports **PASS** when the anti-criterion is *violated*: `grep -c` prints
  `24` and exits `0`, and `actual_exit == 0` alone carries the row. This is #2783's Severity-1
  case, produced by these same three lines.

**Desired outcome.** The two runners agree on any input: one definition of what a table is,
one definition of what an expectation means. A non-check table in `## Verification` is
skipped with a named diagnostic instead of poisoning the gate. A `## Verification` section
that contains table rows but no executable check table fails loudly instead of quietly
gating on nothing.

## Freshness Check

**Disposition: Unchanged.** Baseline `main` @ `f491306c5` (2026-08-18 23:16 +0700).

| Re-verified | Result |
|---|---|
| `agent/verification_parser.py:115,139,149,184` | Still exact. `parse_verification_table` at `:115`, flat row collection at `:139`, `rows[2:]` at `:149`, `evaluate_expectation` at `:184`. |
| `scripts/validate_build.py:291-303` | Still exact. Four-branch evaluator with the `actual_exit == 0` fallback intact. |
| `docs/sdlc/do-build.md:182,185` / `docs/sdlc/do-pr-review.md:72` | Both runners still wired as described. |
| `git log --since=<issue filed> -- agent/verification_parser.py scripts/validate_build.py tests/unit/test_verification_parser.py tests/unit/test_validate_build.py` | **Zero commits.** No drift since either issue was filed. |
| Bug still reproducible | Yes. `parse_verification_table` on `61717ccb2^:docs/plans/docs-auditor-rename-detection.md` → 27 checks (16 intended). `evaluate_expectation` vs. `validate_build.py`'s evaluator disagree on `output > 0` and `match count == 0`. |
| Open PRs #2859, #2856, #2797, #2746 | None touches `agent/verification_parser.py`, `scripts/validate_build.py`, `docs/sdlc/do-build.md`, `docs/sdlc/do-pr-review.md`, or `docs/features/machine-readable-dod.md`. Main will move under this lane; the touched file set will not. |

**Overlap:** `docs/plans/gates-that-cannot-fire.md` (#2658, status Planning, never built) is
the one active plan in the same area. Its `spike-1` independently discovered the #2836 defect
and *worked around* it by mandating that `## Two-Pole Proofs` be a top-level section placed
after `## Verification` rather than a `###` subsection inside it. This is a coordination
signal, not a blocker: that plan's design keeps working after this fix, and this fix removes
the constraint that forced it. Not merged into this lane — #2658's subject is demonstrated-red
proof obligations, which is a different problem.

## Research

**Query:** GitHub Flavored Markdown spec — table termination, blank line ends table block.

**Finding.** GFM §4.10 (Tables, extension) treats a pipe table as a leaf block: the table body
consumes rows until a blank line or a line that cannot be part of the table. A blank line
delimits the table. Block-level constructs cannot live inside cells for exactly this reason.

**How it informs the approach.** "A contiguous run of `|`-prefixed lines" is not a heuristic
invented for this fix — it is the spec-correct definition of *one* markdown table. Scoping the
parser to pipe-blocks makes it agree with how GitHub renders the same document, which is what
the plan author sees when they review their own plan. `scripts/validate_build.py:113-115`
already terminates on the first non-`|` line, so the repo's own second implementation already
encodes the spec rule; the canonical parser is the one that diverges from both.

Sources: [GitHub Flavored Markdown Spec](https://github.github.com/gfm/),
[GFM Table Cheat Sheet](https://formatarc.com/en/blog/gfm-table-cheatsheet/).

## Prior Art

| Reference | Relevance |
|---|---|
| **#2570** (closed) — row splitting with no escaping | Forced the two runners to share `split_row_cells` so they "cannot disagree about what a row says." Established the convergence pattern this plan extends from *rows* to *tables* and *expectations*. `scripts/validate_build.py:25` already imports from `agent.verification_parser`; the import path is precedent, not new. |
| **#1627** (closed) — anti-criteria as first-class | Added the `match count == 0`, `exit code != N`, `output does not contain X` grammar to `evaluate_expectation` with its empty-stdout gates. That grammar is the contract this plan makes authoritative; it is not changed. |
| **#330** (closed) — machine-readable DoD | Created the `## Verification` table and both runners. `docs/features/machine-readable-dod.md` is its feature doc and must be updated here. |
| **#2783** (open) | The *same three lines* as #2843, seen from the anti-criterion direction. See "Effect on #2783 and #2791" below. |
| **#2791** (open) | `prints N` grammar. Not fixed here. See below. |
| **#2778** (open) | `.claude/hooks/validators/validate_verification_section.py` is a third reader of the table and is unregistered in `manifest.toml`, so it never runs. Deliberately out of scope. |
| **#2658** (open) | `docs/plans/gates-that-cannot-fire.md`. Overlap, documented above. |

**Why previous fixes failed.** #2570 converged the two runners on `split_row_cells` and stopped
there. Its stated rationale — the runners "cannot disagree about what a row says" — was correct
and incomplete: it left them free to disagree about what a *table* is and what an *expectation*
means, which is precisely the pair of defects this plan closes. The lesson is that partial
convergence on a shared artifact leaves the next divergence to be discovered the same way this
one was, by a build gate failing a plan that is fine.

## Spike Results

### spike-1: Does per-block header-signature scoping preserve legitimate multi-table check layouts?

- **Assumption:** #2836's own text — "first-table-only scoping ... breaks legitimate
  multi-table check layouts if any exist (**none known**)."
- **Method:** code-read + simulation over all 500 plan documents carrying a `## Verification`
  section.
- **Result: the assumption is FALSE.** Two plans carry a second, real check table headed
  `| Anti-criterion | Command | Expected |`:
  `docs/plans/completed/sdlc-lease-heartbeat-supervisor-lifetime.md` and
  `docs/plans/completed/sdlc-continuity-reensure-rebind.md`. First-table-only scoping drops
  six genuine executable rows from each — a gate that silently cannot fire, strictly worse than
  the defect being fixed. `docs/plans/completed/opus-skill-prompts-4-7.md` similarly carries a
  real check row in a second pipe-block.
- **Confidence:** high (direct measurement).
- **Impact:** decides the design. Header-signature scoping must be applied to **every**
  pipe-block in the section, not used to select a single one.

### spike-2: What is the blast radius of per-block scoping across the plan corpus?

- **Assumption:** "Scoping the parser will change how existing plans parse."
- **Method:** simulate the proposed parser against every plan with a `## Verification` section
  and diff check/malformed counts against the current parser.
- **Result:** 500 plans examined. **0 active plans** (`docs/plans/*.md`) change their parse
  result. 8 completed plans change; every delta removes junk rows or `.worktrees`-fenced shell
  lines that currently parse as checks. Two completed plans
  (`delivery_guard_resume_epoch_scoping.md`, `redis-replication-sentinel-failover.md`) head
  their only table `| # | Criterion | Check |` with no Command column and would yield zero
  checks.
- **Confidence:** high.
- **Impact:** the fix is behavior-preserving for everything currently in flight. The
  zero-check case must be a **loud** malformed error, or the fix trades one silent gate for
  another.

### spike-3: Strict cell-equality or loose substring for the check-table header signature?

- **Assumption:** "`scripts/validate_build.py:106`'s loose test (`"command" in row.lower()`)
  and a strict test (a first-three column named exactly `Command`) will disagree on real plans."
- **Method:** measure both over all pipe-blocks in all 500 plans.
- **Result:** both match **489** blocks. **Zero disagreements.**
- **Confidence:** high.
- **Impact:** use the strict cell-equality test. It costs nothing and it cannot be tripped by a
  summary table that happens to mention the word "command" in a data cell.

### spike-4: Does `scripts/evaluate_build.py` share the broken evaluator?

- **Assumption:** #2843's open question — "Whether `scripts/evaluate_build.py` shares the same
  evaluator."
- **Method:** code-read.
- **Result:** **No.** It is an LLM criteria judge — `evaluate_criteria(criteria_text,
  diff_text)` at `:95` reads `## Success Criteria` plus `git diff` and has no expectation
  grammar at all. Unaffected.
- **Confidence:** high.
- **Impact:** removes a file from the blast radius.

### spike-5: Is delegation lossless, or does `validate_build.py` support forms the canonical evaluator lacks?

- **Assumption:** "Deleting `validate_build.py`'s evaluator removes working behavior."
- **Method:** classify every Expected cell in every plan against both grammars.
- **Result:** `validate_build.py`'s unique form is bare `output <exact-string>`. It is used in
  **zero** active plans. Separately, **68** Expected cells across **11** active plans use
  grammar *neither* evaluator recognizes (`prints \`0\``, `output == N`, `> 0`, `ok`,
  `exit 0`); all 68 already FAIL the canonical runner at `docs/sdlc/do-build.md:185` today.
- **Confidence:** high.
- **Impact:** delegation is lossless in practice. Do **not** add grammar to
  `evaluate_expectation` — that is #2791's job, not this lane's.

### spike-6: Does the #2741 fixture hit the acceptance criterion exactly?

- **Assumption:** "#2836's AC — the pre-fix plan text parses to only its 16 intended checks."
- **Method:** run the proposed parser against `61717ccb2^:docs/plans/docs-auditor-rename-detection.md`.
- **Result:** current parser → **27 checks / 0 malformed**. Proposed parser → **16 checks /
  0 malformed / 1 skipped block (11 rows, header `| Row | Pre-change | Meaning |`)**. Exactly
  the AC.
- **Confidence:** high.
- **Impact:** the fixture is the demonstrated-red test. Commit the section as a test fixture so
  the assertion does not depend on reachable git history.

## Data Flow

```
plan document  docs/plans/{slug}.md
      │
      ├── ## Verification section  ── regex ──►  section text
      │                                              │
      │                              ┌───────────────┴───────────────┐
      │                              │  _iter_pipe_blocks()          │  NEW: contiguous runs
      │                              │  (GFM: blank line ends table) │  of |-prefixed lines
      │                              └───────────────┬───────────────┘
      │                                              │
      │                        per block ────────────┴──────────────────
      │                        header cells[:3] contains exactly "Command"?
      │                              │yes                        │no
      │                     split_row_cells per row         SkippedTable
      │                     (#2570, unchanged)              (named, non-failing)
      │                              │
      │                     VerificationCheck | MalformedRow
      │                              │
      │             ParsedTable(checks, malformed, skipped)   ◄── single source of truth
      │                              │
      ├──────────────────────────────┼──────────────────────────────┐
      │                              │                              │
docs/sdlc/do-build.md:185    scripts/validate_build.py      docs/sdlc/do-pr-review.md:72
run_checks() 120s            own loop, 30s, SKIP-on-timeout  run_checks() 120s
      │                              │                              │
      └──────────► evaluate_expectation(expected, exit_code, output) ◄──────────┘
                            ONE evaluator, unchanged grammar
```

Today the middle column forks: `validate_build.py` re-implements the section extraction, the
row collection, and the evaluator. After this change it owns only its execution loop (30s
timeout, SKIP-on-timeout) and reads everything else from `agent/verification_parser.py`.

## Appetite

**Medium.** Two files of production code, both small and well-tested; a fixture directory; four
docs. The design work was the expensive part and is done — six spikes, all resolved by direct
measurement against the 500-plan corpus. No new dependency, no schema change, no service
restart.

## Prerequisites

None. Both issues touch the same two files and share this lane.

## Solution

One shared definition of a table. One shared evaluator. `scripts/validate_build.py` keeps only
what is genuinely its own: how it runs commands and how it reports.

### 1. Table scoping in `agent/verification_parser.py` (#2836)

Add `_iter_pipe_blocks(section) -> list[list[str]]`, returning each contiguous run of
`|`-prefixed lines. This is the GFM definition of one table.

For each block, read the header row's cells. A block is a **check table** when it has at least
three columns and one of its first three column names is exactly `Command` (case-insensitive).

- **Check table** → skip a separator row if the next line matches `^\|[\s\-:|]+\|$`, then parse
  data rows exactly as today (`split_row_cells`, header-derived column count, `MalformedRow` on
  a mismatch or an empty cell). Every check table in the section contributes.
- **Non-check table** → one `SkippedTable(header, row_count, reason)`. Named, reported, and
  **non-failing**: a summary table is legitimate plan authoring, and failing the gate on it
  would reproduce #2836 with a friendlier message.
- **Section has pipe-blocks but no check table** → every block becomes a `MalformedRow`. This
  **fails**. An author who wrote table rows in `## Verification` and got zero executable checks
  must be told, or the fix replaces guaranteed-fail junk with a gate that cannot fire.
- **Section has no pipe-blocks** → empty `ParsedTable`, unchanged.

`ParsedTable` gains `skipped: list[SkippedTable] = field(default_factory=list)`. Existing
call sites reading `.checks` and `.malformed` keep working unchanged.

`format_results` gains a `skipped` parameter and prints a "Non-check tables skipped" section
that does not participate in the pass/fail verdict.

### 2. Evaluator delegation in `scripts/validate_build.py` (#2843, #2783)

Delete `scripts/validate_build.py::parse_verification_table` (`:80-146`) and the four-branch
evaluator in `check_verification_table` (`:290-303`). Import
`parse_verification_table` and `evaluate_expectation` from `agent.verification_parser`.

`check_verification_table` takes a `ParsedTable` and:

- emits FAIL for each `MalformedRow`, keeping the existing "fix the plan, not the code" message
  shape;
- emits an `INFO` line for each `SkippedTable` — not counted as PASS, FAIL, or SKIP, so a
  skipped summary table cannot change the exit code;
- runs each check on its own 30s timeout and calls
  `evaluate_expectation(check.expected, exit_code=proc.returncode, output=proc.stdout)`.

**`output` must be the unstripped stdout.** `run_checks` passes `proc.stdout`;
`validate_build.py` currently passes `result.stdout.strip()`. Passing a stripped copy re-creates
divergence at the exact seam this plan exists to close. The stripped value stays only in the
FAIL message.

`extract_section` stays — `parse_success_criteria_commands` still uses it.

### 3. What is deliberately *not* changed

- `evaluate_expectation`'s grammar. Not one form added or removed.
- `run_checks`' 120s timeout vs. `validate_build.py`'s 30s/SKIP. Different product contracts,
  no observed divergence.
- `.claude/hooks/validators/validate_verification_section.py`. A third reader with its own
  divergent cell splitting, but unregistered in `manifest.toml` and therefore inert (#2778).

### Effect on #2783 and #2791

Called out explicitly so the PR body can act on it.

**#2783 becomes structurally impossible.** Its Severity-1 case (a violated `match count == 0`
reporting PASS because `grep -c` exits 0) and its Severity-2 case (a satisfied anti-criterion
reporting FAIL) are both produced by `scripts/validate_build.py:301-303` — the same lines #2843
names. Deleting that fallback in favour of `evaluate_expectation` eliminates both directions.
Verified: `evaluate_expectation("match count == 0", exit_code=0, output="24")` returns `False`,
and with `output="0"` returns `True`. **Recommendation: the PR body carries `Closes #2783`
alongside `Closes #2836` and `Closes #2843`.** No extra code — it is the same deletion.

**#2791 is NOT fixed and must stay open.** `prints N` is absent from `evaluate_expectation`'s
grammar and stays absent. After convergence, `prints \`0\`` rows go from incidentally-PASS
under `validate_build.py` (flexible fallback, `grep` exits 0) to FAIL — which is what the
canonical runner at `docs/sdlc/do-build.md:185` already reports for them today. Louder, not
worse, and #2791 remains the fix. The PR body must say so rather than leaving a reader to infer
that convergence covered it.

## Failure Path Test Strategy

Every failure path gets a test that fails against the current code first.

| Failure path | Test |
|---|---|
| Second non-check table merged into checks (#2836) | `test_summary_table_does_not_become_checks` — the #2741 fixture parses to 16/0/1. Fails today at 27 checks. |
| Real second check table dropped (spike-1 regression) | `test_second_anti_criterion_table_is_parsed` — a fixture with `\| Check \| Command \| Expected \|` and `\| Anti-criterion \| Command \| Expected \|` yields both tables' rows. Guards against a future "first table wins" simplification. |
| Rows present, no check table → silent zero-check gate | `test_pipe_rows_with_no_command_column_are_malformed` — a `\| # \| Criterion \| Check \|` table produces malformed entries, and `format_results` reports the run as failed. |
| Skipped table silently changing the verdict | `test_skipped_table_does_not_fail_the_run` — checks all pass + one skipped block → `format_results` says all passed, and `validate_build.main()` exits 0. |
| `output > N` false FAIL (#2843) | `test_validate_build_output_gt_passes` — a row `output > 0` on a command printing `1` reports PASS. Fails today. |
| `match count == 0` false FAIL (#2843) | `test_validate_build_match_count_zero_clean_passes` — `grep -c` on a clean file (prints `0`, exits 1) reports PASS. Fails today. |
| `match count == 0` false PASS (#2783 Severity-1) | `test_validate_build_violated_anti_criterion_fails` — `grep -c` finding 24 matches (prints `24`, exits 0) reports FAIL. Fails today (reports PASS). |
| Stripped-stdout divergence re-introduced | `test_validate_build_and_run_checks_agree_on_trailing_newline` — a command whose stdout is `"0\n"` evaluates identically through both paths. |
| Cross-runner divergence generally | `test_both_runners_agree_on_fixture_corpus` — parametrized over the committed fixtures, asserts `validate_build`'s per-check verdicts equal `run_checks`' verdicts row for row. This is the regression test for the *class*, not the instance. |

## Test Impact

- [ ] `tests/unit/test_validate_build.py::TestParseVerificationTable` — REPLACE: the local
      `parse_verification_table` is deleted. Retarget these cases at the imported
      `agent.verification_parser.parse_verification_table` and its `ParsedTable` return type
      (they currently assert a `list[dict]`).
- [ ] `tests/unit/test_validate_build.py::TestCheckVerificationTable::test_output_check` —
      REPLACE: exercises the bare `output <exact>` form, which is deleted. Measured usage in
      active plans: zero. Replace with an `output > N` case, which is what the row shape was
      standing in for.
- [ ] `tests/unit/test_validate_build.py::TestCheckVerificationTable::test_passing_command` /
      `test_failing_command` / `test_timeout_skips` — UPDATE: `check_verification_table` now
      takes a `ParsedTable` rather than `list[dict]`. Assertions on status and message shape
      stay.
- [ ] `tests/unit/test_validate_build.py::TestMainEdgeCases::test_malformed_verification_table` —
      UPDATE: malformed rows now arrive from the shared parser. Keep the assertion that the
      message names the plan as the thing to fix.
- [ ] `tests/unit/test_validate_build.py::TestMainEdgeCases::test_pipe_bearing_command_runs_intact` —
      UPDATE: #2570's guarantee must survive delegation. Same assertion, new plumbing.
- [ ] `tests/unit/test_verification_parser.py::TestParseVerificationTable` (11 cases) — UPDATE:
      every fixture already uses a `| Check | Command | Expected |` header and stays green;
      add the boundary cases above. Verified by inspection, not assumed.
- [ ] `tests/unit/test_verification_parser.py::TestPipesInCommands::test_column_count_comes_from_the_header` —
      UPDATE: its 4-column `| Check | Command | Expected | Notes |` header must still be
      recognized as a check table under the new signature test.
- [ ] `tests/unit/test_verification_parser.py::TestMalformedRowReporting` — UPDATE:
      `format_results` gains a `skipped` parameter; assert the malformed section is unchanged
      and the skipped section does not alter the verdict.
- [ ] No xfail markers exist in either test file (searched: `pytest.mark.xfail`,
      `pytest.xfail(`). Nothing to convert.

## Rabbit Holes

- **Rewriting the grammar.** `prints N`, `output == N`, `output >= N`, and `≥ 1` all appear in
  active plans and none is supported. Tempting, in scope for #2791, out of scope here.
- **Converging the timeouts.** `run_checks` uses 120s, `validate_build.py` 30s. A real
  difference, no observed divergence, and unifying it changes what the build gate blocks on.
- **Fixing `validate_verification_section.py`.** A third divergent reader, but inert until
  #2778 registers it. Touching it here means changing a hook that gates plan writes, for zero
  present-day benefit.
- **Auto-repairing plan documents.** Rewriting an author's summary table into bullets is the
  #2741 workaround promoted to code. The parser reports; the author decides.
- **Making `## Verification` a strict single-table section.** Spike-1 proved multi-table check
  layouts are real and used. Do not simplify by forbidding them.

## Risks

1. **A real check table gets classified as a summary table.** Mitigated by the strict
   cell-equality signature measured against all 489 check-table blocks in the corpus (spike-3:
   zero disagreements with the loose test) and by the requirement that a section with rows but
   no check table fails loudly rather than skipping silently.
2. **Skipped tables are non-failing, so a genuinely-mis-headered check table goes quiet.** One
   case exists in the corpus (`opus-skill-prompts-4-7.md`, a data row orphaned by a blank line);
   it is a completed plan. Mitigated by printing the skipped block's header and row count in
   both runners' reports. Accepted deliberately: failing on skipped tables would re-create
   #2836.
3. **Convergence surfaces failures that `validate_build.py` used to hide.** Up to 68 rows across
   11 active plans use grammar neither evaluator recognizes and will now FAIL under
   `validate_build.py`. All 68 already FAIL the canonical runner at `docs/sdlc/do-build.md:185`
   today, so no lane gains a blocker it did not already have. `validate_build.py` runs against
   one plan at a time — the lane's own — so the exposure is bounded to that lane.
4. **Command-cell extraction changes in `validate_build.py`.** It currently takes the first
   backtick-delimited span; the canonical parser takes `cells[1].strip("\`")`. These differ for
   **15 rows across 6 active plans**, all of which put prose in the Command cell (manual runbook
   steps that were never executable under either runner). The canonical behavior wins; the delta
   is named in the PR body rather than discovered later.
5. **`format_results`' signature change.** Both `docs/sdlc/do-build.md:185` and
   `docs/sdlc/do-pr-review.md:72` embed a one-liner calling `format_results(r, t.malformed)`.
   `skipped` is added as an optional third parameter so the old call still works, and both
   one-liners are updated in the same PR so the diagnostic actually reaches a reader.
6. **Main moves under this lane.** Four PRs are open. None touches this file set (verified in
   the Freshness Check); rebase risk is confined to the plan document itself.

## Race Conditions

None. Both modules are synchronous, single-threaded, and process-local. `run_checks` and
`validate_build.py`'s loop each execute commands sequentially via `subprocess.run` with no
shared mutable state. No async, no cross-process handoff, no ordering hazard.

## No-Gos (Out of Scope)

- Adding, removing, or altering any expectation form in `evaluate_expectation`. The grammar is
  frozen. (#2791 owns grammar work.)
- Changing `run_checks`' timeout, its exception handling, or `CheckResult`'s shape.
- Touching `.claude/hooks/validators/validate_verification_section.py` (#2778) or
  `scripts/evaluate_build.py` (spike-4: unaffected).
- Editing any plan document's `## Verification` section to suit the new parser. Spike-2 measured
  zero active plans changing; if one did, that is a finding to report, not a document to quietly
  rewrite.
- Auto-repairing, reformatting, or linting plan documents from either runner.
- Expanding into #2658's demonstrated-red proof obligations.

## Update System

No update-script or update-skill changes are required for the Python code: both files are
imported from the repo checkout and carry no new dependency, config file, or migration.

One propagated artifact: `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` is hardlinked to
`~/.claude/skills/` by `scripts/update/hardlinks.py`. Adding the one-table-per-signature rule to
its `## Verification` guidance ships to every machine on the next `/update` with no wiring
change — the file is already registered. No `RENAMED_REMOVALS` entry is needed; nothing moves
between skill roots.

No Popoto model changes, so no entry in `scripts/update/migrations.py`.

## Agent Integration

No agent integration required. Neither file is reachable from the Telegram bridge, and neither
gains a CLI entry point in `pyproject.toml [project.scripts]`.

The agent already reaches both runners through the SDLC skills: `docs/sdlc/do-build.md:182`
invokes `python scripts/validate_build.py` via Bash, and `:185` plus
`docs/sdlc/do-pr-review.md:72` invoke `parse_verification_table` / `run_checks` /
`format_results` through an inline `python -c`. Both invocation sites are updated in this PR so
the `skipped` diagnostic reaches the agent's transcript. Coverage is via the unit tests, plus
this plan's own `## Verification` table, which the build gate executes through the changed code
path — the fix validates itself.

## Documentation

- [ ] Update `docs/features/machine-readable-dod.md` — document the per-block table signature,
      the `SkippedTable` diagnostic, `ParsedTable`'s third field, `format_results`' new
      parameter, and state plainly that `scripts/validate_build.py` no longer carries its own
      evaluator.
- [ ] Update `docs/sdlc/do-build.md` — the `:185` one-liner passes `t.skipped` to
      `format_results`; note next to `:182` that `validate_build.py` and the canonical runner
      now share one table definition and one evaluator.
- [ ] Update `docs/sdlc/do-pr-review.md` — same one-liner change at `:72`.
- [ ] Update `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` — add to the `## Verification`
      guidance that a check table is identified by a `Command` column, that additional
      non-check tables in the section are skipped with a named diagnostic rather than executed,
      and that a section whose tables have no `Command` column fails the gate.
- [ ] No new entry in `docs/features/README.md` — this modifies the existing
      machine-readable-DoD feature rather than adding one. Confirm the index row's description
      still reads true after the edit.

## Success Criteria

- [ ] A non-check markdown table in `## Verification` produces zero executable checks and zero
      guaranteed-fail rows (#2836 AC 1).
- [ ] A skipped table is named in both runners' reports with its header and row count
      (#2836 AC 2).
- [ ] The #2741 pre-fix text parses to exactly 16 checks, 0 malformed, 1 skipped block
      (#2836 AC 3).
- [ ] Every new test fails against the current parser/validator before the fix (#2836 AC 4,
      demonstrated-red).
- [ ] A second `| Anti-criterion | Command | Expected |` table still yields executable checks.
- [ ] `## Verification` containing table rows but no `Command`-column table fails loudly.
- [ ] `scripts/validate_build.py` reports PASS for `output > 0` on stdout `1` and for
      `match count == 0` on a clean `grep -c` (#2843).
- [ ] `scripts/validate_build.py` reports FAIL for a violated `match count == 0` (#2783
      Severity-1).
- [ ] `scripts/validate_build.py` contains no `parse_verification_table` definition, no
      `expected.startswith` chain, and no `actual_exit == 0` fallback.
- [ ] `python -m pytest tests/unit/test_verification_parser.py tests/unit/test_validate_build.py -q`
      exits 0.
- [ ] `python -m ruff check` and `python -m ruff format --check` clean on both changed files.
- [ ] Both runners produce identical per-row verdicts over the committed fixture corpus.

## Team Orchestration

Two agents, sequential — the second's work is defined by the first's return type.

### 1. Parser scoping and fixtures
- **Task ID**: parser-scoping
- **Depends On**: none
- **Agent Type**: builder
- **Parallel**: false
- `agent/verification_parser.py`: `_iter_pipe_blocks`, the header signature, `SkippedTable`,
  `ParsedTable.skipped`, `format_results(skipped=...)`.
- `tests/fixtures/verification/` and the new cases in `tests/unit/test_verification_parser.py`.
- Records the demonstrated-red output for each new test before implementing.

### 2. Validator delegation and docs
- **Task ID**: validator-delegation
- **Depends On**: [parser-scoping]
- **Agent Type**: builder
- **Parallel**: false
- Deletes `scripts/validate_build.py`'s parser and evaluator, wires the imports, rewrites
  `check_verification_table`, updates `tests/unit/test_validate_build.py`.
- All four documentation targets.

### 3. Final validation
- **Task ID**: validate-all
- **Depends On**: [parser-scoping, validator-delegation]
- **Agent Type**: validator
- **Parallel**: false
- Runs this plan's `## Verification` table through the changed code, confirms the cross-runner
  agreement test, and re-measures the 500-plan corpus to confirm zero active-plan drift.

## Step by Step Tasks

- [ ] **T1 — Capture the red state.** Before changing code, record and paste into the PR body:
      `parse_verification_table` on `61717ccb2^:docs/plans/docs-auditor-rename-detection.md`
      returning 27 checks; `validate_build.py`'s evaluator reporting FAIL for
      `output > 0`/`1` and for `match count == 0`/`0`; and reporting PASS for
      `match count == 0`/`24`.
- [ ] **T2 — Create `tests/fixtures/verification/`.** Add `2741_pre_fix_verification.md` (the
      `## Verification` section from `61717ccb2^:docs/plans/docs-auditor-rename-detection.md`,
      verbatim), `two_check_tables.md` (a `Check` table plus an `Anti-criterion` table),
      `no_command_column.md` (a `| # | Criterion | Check |` table), and
      `check_plus_summary.md` (one check table plus a prose summary table).
- [ ] **T3 — Add the failing tests** to `tests/unit/test_verification_parser.py` for every row
      of the Failure Path Test Strategy that targets the parser. Run them; each must fail.
      Paste the failures into the PR body.
- [ ] **T4 — Add `SkippedTable` and extend `ParsedTable`** in `agent/verification_parser.py`.
      `skipped` gets `field(default_factory=list)` so existing constructions stay valid.
- [ ] **T5 — Add `_iter_pipe_blocks` and the header-signature test.** A block is a check table
      when it has ≥3 columns and one of its first three column names equals `Command`
      case-insensitively.
- [ ] **T6 — Rewrite `parse_verification_table`** over blocks: parse every check table, skip
      every non-check table, and emit `MalformedRow` for all blocks when the section has
      pipe-blocks and no check table. Separator rows are skipped only when they match
      `^\|[\s\-:|]+\|$`.
- [ ] **T7 — Extend `format_results`** with an optional `skipped` parameter and a
      "Non-check tables skipped" section that does not affect the verdict. Update the module
      docstring to state the per-block rule and cite GFM.
- [ ] **T8 — Confirm T3's tests now pass** and the whole of
      `tests/unit/test_verification_parser.py` is green.
- [ ] **T9 — Add the failing validator tests** to `tests/unit/test_validate_build.py` for
      `output > N`, clean `match count == 0`, violated `match count == 0`, the skipped-table
      exit code, and the trailing-newline parity case. Run them; each must fail. Paste the
      failures into the PR body.
- [ ] **T10 — Delete `scripts/validate_build.py::parse_verification_table`** (`:80-146`) and
      import `parse_verification_table` and `evaluate_expectation` from
      `agent.verification_parser`. Keep `extract_section` for `parse_success_criteria_commands`.
- [ ] **T11 — Rewrite `check_verification_table`** to take a `ParsedTable`: FAIL per
      `MalformedRow` with the existing message shape, one non-counting `INFO` line per
      `SkippedTable`, and `evaluate_expectation(..., output=proc.stdout)` — **unstripped** — per
      check. Keep the 30s timeout and SKIP-on-timeout. Update `main()` to pass the `ParsedTable`
      and to guard on checks-or-malformed-or-skipped.
- [ ] **T12 — Update the existing `test_validate_build.py` cases** listed in Test Impact, and
      confirm T9's tests pass.
- [ ] **T13 — Add `test_both_runners_agree_on_fixture_corpus`**, parametrized over
      `tests/fixtures/verification/`, asserting per-row verdict equality between
      `validate_build`'s loop and `run_checks`. This is the guard against the next divergence.
- [ ] **T14 — Re-run the corpus measurement.** Confirm zero active plans in `docs/plans/*.md`
      change their parse result, and paste the number into the PR body.
- [ ] **T15 — Update `docs/features/machine-readable-dod.md`** per the Documentation section.
- [ ] **T16 — Update the `format_results` one-liners** in `docs/sdlc/do-build.md:185` and
      `docs/sdlc/do-pr-review.md:72` to pass `t.skipped`, and add the shared-implementation note
      beside `docs/sdlc/do-build.md:182`.
- [ ] **T17 — Update `.claude/skills-global/do-plan/PLAN_TEMPLATE.md`** `## Verification`
      guidance with the `Command`-column signature, the skipped-table diagnostic, and the
      no-check-table failure.
- [ ] **T18 — Run this plan's own `## Verification` table** through the changed code and
      confirm every row passes.
- [ ] **T19 — Write the PR body.** `Closes #2836`, `Closes #2843`, and `Closes #2783` (same
      three deleted lines — see "Effect on #2783 and #2791"). State explicitly that **#2791
      remains open** and that convergence makes its `prints N` rows fail under
      `validate_build.py` where they previously passed by accident. Include the red-state
      evidence from T1, T3, T9 and the corpus number from T14.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Parser tests pass | `python -m pytest tests/unit/test_verification_parser.py -q` | exit code 0 |
| Validator tests pass | `python -m pytest tests/unit/test_validate_build.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/verification_parser.py scripts/validate_build.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/verification_parser.py scripts/validate_build.py` | exit code 0 |
| validate_build defines no table parser of its own | `grep -c '^def parse_verification_table' scripts/validate_build.py` | match count == 0 |
| Flexible-match fallback deleted | `grep -c 'Flexible match' scripts/validate_build.py` | match count == 0 |
| exit-code-0 disjunct deleted (#2783 Severity-1) | `grep -c 'actual_exit == 0' scripts/validate_build.py` | match count == 0 |
| startswith expectation chain deleted (#2843) | `grep -c 'expected.startswith' scripts/validate_build.py` | match count == 0 |
| validate_build imports the canonical evaluator | `grep -c 'evaluate_expectation' scripts/validate_build.py` | output > 0 |
| Per-block scoping present | `grep -c '_iter_pipe_blocks' agent/verification_parser.py` | output > 0 |
| SkippedTable is importable | `python -c "from agent.verification_parser import SkippedTable; print('ok')"` | output contains ok |
| ParsedTable carries skipped | `python -c "from agent.verification_parser import ParsedTable; print('skipped' in ParsedTable.__dataclass_fields__)"` | output contains True |
| 2741 fixture parses to 16 checks, 0 malformed, 1 skipped | `python -c "from agent.verification_parser import parse_verification_table as p; t=p(open('tests/fixtures/verification/2741_pre_fix_verification.md').read()); print(f'{len(t.checks)}/{len(t.malformed)}/{len(t.skipped)}')"` | output contains 16/0/1 |
| Second anti-criterion check table still parsed | `python -c "from agent.verification_parser import parse_verification_table as p; t=p(open('tests/fixtures/verification/two_check_tables.md').read()); print(len(t.checks))"` | output > 3 |
| Summary table skipped, not executed | `python -c "from agent.verification_parser import parse_verification_table as p; t=p(open('tests/fixtures/verification/check_plus_summary.md').read()); print(f'{len(t.malformed)}/{len(t.skipped)}')"` | output contains 0/1 |
| Rows with no Command column fail loudly | `python -c "from agent.verification_parser import parse_verification_table as p; t=p(open('tests/fixtures/verification/no_command_column.md').read()); print(f'{len(t.checks)}/{len(t.malformed)}')"` | output contains 0/1 |
| Violated anti-criterion evaluates false (#2783) | `python -c "from agent.verification_parser import evaluate_expectation as e; print('ok' if not e('match count == 0', exit_code=0, output='24') else 'BAD')"` | output contains ok |
| Clean anti-criterion evaluates true (#2843) | `python -c "from agent.verification_parser import evaluate_expectation as e; print('ok' if e('match count == 0', exit_code=1, output='0') else 'BAD')"` | output contains ok |
| output > N evaluated numerically (#2843) | `python -c "from agent.verification_parser import evaluate_expectation as e; print('ok' if e('output > 0', exit_code=0, output='1') else 'BAD')"` | output contains ok |
| All six expectation forms still evaluate (grammar-frozen no-go guard) | `python -c "from agent.verification_parser import evaluate_expectation as e; print(sum([e('exit code 0', exit_code=0, output=''), e('exit code != 1', exit_code=0, output=''), e('output contains x', exit_code=0, output='x'), e('output does not contain y', exit_code=0, output='x'), e('match count == 0', exit_code=1, output='0'), e('output > 1', exit_code=0, output='2')]))"` | output contains 6 |
| Cross-runner agreement test exists | `grep -c 'both_runners_agree' tests/unit/test_validate_build.py` | output > 0 |
| Fixture corpus committed | `ls tests/fixtures/verification/ \| wc -l` | output > 3 |
| No stale call to the deleted validator parser | `grep -c 'validate_build.parse_verification_table' tests/unit/test_validate_build.py` | match count == 0 |
| Feature doc records the skipped diagnostic | `grep -c 'skipped' docs/features/machine-readable-dod.md` | output > 0 |
| do-build gate one-liner passes skipped | `grep -c 't.skipped' docs/sdlc/do-build.md` | output > 0 |
| do-pr-review gate one-liner passes skipped | `grep -c 't.skipped' docs/sdlc/do-pr-review.md` | output > 0 |
| Plan template documents non-check tables | `grep -ci 'non-check table' .claude/skills-global/do-plan/PLAN_TEMPLATE.md` | output > 0 |
| This plan's own table reads clean through the fixed parser | `python -c "from agent.verification_parser import parse_verification_table as p; t=p(open('docs/plans/verification-runner-convergence.md').read()); print(f'{len(t.malformed)}/{len(t.skipped)}')"` | output contains 0/0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Should the PR carry `Closes #2783`?** The plan's position is yes — #2783 and #2843 name
   the same three lines (`scripts/validate_build.py:301-303`), and the deletion resolves both
   directions of #2783 with no additional code. Confirm, or say to leave #2783 open and note the
   overlap in a comment instead.

2. **Should a skipped non-check table fail the gate?** The plan says no: a summary table is
   legitimate plan authoring, and failing on it reproduces #2836 with a nicer message. The cost
   is the one corpus case where a real check row was orphaned by a blank line and would go
   quiet (a completed plan, `opus-skill-prompts-4-7.md`). The mitigation is a loud printed
   diagnostic in both runners. Confirm, or ask for skipped tables to fail.

3. **Is dropping `validate_build.py`'s bare `output <exact-string>` form acceptable?** Measured
   usage in active plans: zero. It is the one form the losing evaluator supported that the
   canonical one does not, and keeping it means adding grammar — which this plan lists as a
   No-Go and assigns to #2791. Confirm the deletion.
