---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-19
tracking: https://github.com/tomcounsell/ai/issues/2836
also_closes: https://github.com/tomcounsell/ai/issues/2843
last_comment_id: 5324500130
revision_applied: true
revision_applied_at: 2026-08-19T06:04:26Z
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
whose tables carry no `Command` column fails loudly instead of quietly gating on nothing.

## Freshness Check

**Disposition: Unchanged.** Baseline `main` @ `ba8fdd9d6` (2026-08-19 12:56 +0700). This is the
single baseline SHA for the whole plan; `## Spike Results` pins the same one and no third SHA is
cited anywhere as a measurement baseline. Re-verified at this SHA:
`git log f491306c5..ba8fdd9d6 -- agent/verification_parser.py scripts/validate_build.py
tests/unit/test_verification_parser.py tests/unit/test_validate_build.py docs/sdlc/do-build.md
docs/sdlc/do-pr-review.md docs/features/machine-readable-dod.md` returns **zero commits**, so
every row below still holds unchanged at the newer baseline.

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
| **#2783** (reopened — closed in error by `842212ac`) | The *same three lines* as #2843, seen from the anti-criterion direction. See "Effect on #2783 and #2791" below. |
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

**Baseline: `main` @ `ba8fdd9d6`** — the same single SHA `## Freshness Check` pins.

**Corpus definition, stated so any round re-derives the same number.** The corpus is
`find docs/plans -name '*.md'` → **590** documents. `.worktrees/` is excluded by construction: it
is not under `docs/plans/`. A bare `grep -rl` from the repo root instead sweeps 19 live worktree
copies of the same tree — thousands of matches, hundreds of times the real corpus, and unstable
as lanes create and destroy worktrees. The counting unit is a *file whose heading matches*
`^## Verification\s*$`, the same regex `agent/verification_parser.py:129-135` uses: **505** files.
A prefix-matching `grep -l '^## Verification'` returns 507 instead, because it also catches
`## Verification Results`. That two-file gap, plus the four `docs/plans/` subdirectories
(`completed/`, `done/`, `critiques/`, `notes/`), is the whole reason three rounds produced three
different totals — no round's method was wrong, they counted different populations. Of the 505,
**25** are active plans in `docs/plans/*.md`.

Re-measured at this baseline, every figure below reproduces exactly: 505 sections, 502
pipe-blocks, 493 check tables. T14 re-measures at build time and records the refreshed figures
**in the PR body only** — see T14 for why nothing is written back into this document.

### spike-1: Does per-block header-signature scoping preserve legitimate multi-table check layouts?

- **Assumption:** #2836's own text — "first-table-only scoping ... breaks legitimate
  multi-table check layouts if any exist (**none known**)."
- **Method:** code-read + simulation over all 505 plan documents carrying a `## Verification`
  section.
- **Result: the assumption is FALSE.** Two plans carry a second, real check table headed
  `| Anti-criterion | Command | Expected |`:
  `docs/plans/completed/sdlc-lease-heartbeat-supervisor-lifetime.md` and
  `docs/plans/completed/sdlc-continuity-reensure-rebind.md`. First-table-only scoping drops
  **four genuine executable data rows** from each (six lines counting the header and the
  separator) — a gate that silently cannot fire, strictly worse than the defect being fixed.
  `docs/plans/completed/opus-skill-prompts-4-7.md` similarly carries a real check row in a
  second pipe-block.
- **Confidence:** high (direct measurement).
- **Impact:** decides the design. Header-signature scoping must be applied to **every**
  pipe-block in the section, not used to select a single one.

### spike-2: What is the blast radius of per-block scoping across the plan corpus?

- **Assumption:** "Scoping the parser will change how existing plans parse."
- **Method:** simulate the proposed parser against every plan with a `## Verification` section
  and diff check/malformed counts against the current parser.
- **Result:** 505 plans carrying a `## Verification` section examined. **0 of the 25 active plans**
  (`docs/plans/*.md`) change their parse result — re-measured at the pinned baseline `ba8fdd9d6`
  by simulating the per-block parser against every active plan and diffing both check and
  malformed counts against the live parser. 8 completed plans change; every delta removes junk
  rows or `.worktrees`-fenced shell lines that currently parse as checks. Two completed plans
  (`delivery_guard_resume_epoch_scoping.md`, `redis-replication-sentinel-failover.md`) head
  their only table `| # | Criterion | Check |` with no Command column and would yield zero
  checks plus one `MalformedRow` — a loud failure, not a silent pass. Both are completed plans
  and are never re-gated.
- **One active plan transited this state and is reported rather than rewritten.** During critique
  round 2, `docs/plans/doctor-console-script-interpreter-check.md` (#2748, active) carried two
  `| Check | Command | Expected |` tables in one `## Verification` section; the current parser read
  the second table's header and separator as two checks whose Expected cells were `Expected` and
  `---` — two guaranteed FAILs, a live second instance of #2836 biting a real lane. Its own lane
  independently collapsed it to a single 19-check table at `496a9cb5e`, before this revision.
  `## No-Gos` says an active plan changing "is a finding to report, not a document to quietly
  rewrite": the trigger fired, this bullet is the report, and this lane edited no plan document to
  suit the parser.
- **Confidence:** high (direct measurement, method and baseline recorded in the preamble).
- **Impact:** the fix is behavior-preserving for everything currently in flight, and every delta
  it does produce is the removal of guaranteed-fail junk rows — no plan, active or completed,
  loses a real check. The zero-check case must be a **loud** malformed error, or the fix trades
  one silent gate for another.

### spike-3: Strict cell-equality or loose substring for the check-table header signature?

- **Assumption:** "`scripts/validate_build.py:106`'s loose test (`"command" in row.lower()`)
  and a strict test (a first-three column named exactly `Command`) will disagree on real plans."
- **Method:** measure both over every pipe-block in every `## Verification` section in the corpus.
- **Result:** **502** pipe-blocks examined. Both tests classify the same **493** of them as check
  tables. **Zero disagreements.**
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
  **zero** active plans. Separately, **68** Expected cells across **9** active plans use
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
- **Section has pipe-blocks but no check table** → **exactly one `MalformedRow` per pipe-block**,
  never one per row. This **fails**. An author who wrote table rows in `## Verification` and got
  zero executable checks must be told, or the fix replaces guaranteed-fail junk with a gate that
  cannot fire. The row is constructed as:

  ```python
  MalformedRow(
      line=block[0],
      reason=(
          f"table has {len(block)} row(s) but none of its first three column "
          "names is Command; the ## Verification section yielded zero "
          "executable checks"
      ),
  )
  ```

  `line=block[0]` is the block's header line, which keeps `MalformedRow.line: str` honest — the
  dataclass models one row, not one block — and keeps the `- [MALFORMED] {m.line}` report line at
  `agent/verification_parser.py:348` readable. One entry per block also makes `len(t.malformed)`
  equal the pipe-block count, which is what makes the `malformed=1` assertion in this plan's own
  `## Verification` table deterministic. **In this branch `skipped` stays empty** — a block is
  either skipped or malformed, never both (round 3 critique NIT: without this, a builder emitting
  both a `SkippedTable` and a `MalformedRow` for the same block would double-report it in
  `format_results` and still pass the gate).
- **Section has no pipe-blocks** → empty `ParsedTable`, unchanged. See `## No-Gos` — closing this
  case is explicitly not in scope.

`ParsedTable` gains `skipped: list[SkippedTable] = field(default_factory=list)`. Existing
call sites reading `.checks` and `.malformed` keep working unchanged.

`format_results`' signature becomes `format_results(results: list[CheckResult], table:
ParsedTable) -> str`, reading `table.malformed` and `table.skipped` off the parse result. Both
parameters are **required**: the only two production call sites in the repo
(`docs/sdlc/do-build.md:185` and `docs/sdlc/do-pr-review.md:72` — measured, there are no others)
are edited in this same PR, so an optional parameter would be a back-compat bridge with zero
beneficiaries, and an optional diagnostic parameter is how a diagnostic stops reaching a reader.
#2570's `malformed` parameter was added optionally and `docs/features/machine-readable-dod.md:186`
still documents `format_results(results)` as a result; that is the failure being avoided here.

The report gains a "Non-check tables skipped" section that names each `SkippedTable`'s header and
row count and does not participate in the pass/fail verdict.

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
and with `output="0"` returns `True`. **Recommendation: the PR body carries a closing keyword
for 2783 alongside 2836 and 2843** (see T19 for the exact literal form -- not repeated here;
GitHub scans the whole commit message, and this issue was already closed in error once by a
plan-revision commit quoting this kind of sentence verbatim). No extra code — it is the same
deletion.

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
| Rows present, no check table → silent zero-check gate | `test_pipe_rows_with_no_command_column_are_malformed` — a fixture whose single pipe-block is headed `\| # \| Criterion \| Check \|` produces **exactly one** `MalformedRow` whose `line` is that header, and `format_results` reports the run as failed. |
| One malformed entry per block, not per row | `test_no_command_column_yields_one_malformed_per_block` — a fixture with two non-check pipe-blocks yields `len(malformed) == 2` regardless of how many data rows each block holds. |
| Skipped table silently changing the verdict | `test_skipped_table_does_not_fail_the_run` — checks all pass + one skipped block → `format_results` says all passed, and `validate_build.main()` exits 0. |
| Fixture missing its `## Verification` heading (vacuous green) | `test_every_fixture_declares_the_verification_heading` — every file in `tests/fixtures/verification/` starts with a literal `## Verification` line. Without it the section regex matches nothing and the fixture-backed assertions pass against an empty parse. |
| `output > N` false FAIL (#2843) | `test_validate_build_output_gt_passes` — a row `output > 0` on a command printing `1` reports PASS. Fails today. |
| `match count == 0` false FAIL (#2843) | `test_validate_build_match_count_zero_clean_passes` — `grep -c` on a clean file (prints `0`, exits 1) reports PASS. Fails today. |
| `match count == 0` false PASS (#2783 Severity-1) | `test_validate_build_violated_anti_criterion_fails` — `grep -c` finding 24 matches (prints `24`, exits 0) reports FAIL. Fails today (reports PASS). |
| Stripped-stdout divergence re-introduced | `test_validate_build_and_run_checks_agree_on_trailing_newline` — a command whose stdout is `"0\n"` evaluates identically through both paths. |
| Cross-runner divergence generally | `test_both_runners_agree_on_execution_fixture` — parametrized over `runner_agreement.md` only (instantaneous, hermetic commands covering all six expectation branches), asserts `validate_build`'s per-check verdicts equal `run_checks`' verdicts row for row. This is the regression test for the *class*, not the instance. |
| Parse-only fixtures silently diverging | `test_parse_only_fixtures_parse_identically` — the four real-text fixtures are compared through `parse_verification_table` alone. Their commands are never executed, so the unconverged 120s/30s timeout axis cannot manufacture a red. |

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
      `format_results` now takes a required `ParsedTable` as its second argument. Lines 488, 497
      and 498 currently pass a bare `list[MalformedRow]` positionally and must construct a
      `ParsedTable(checks=[], malformed=[...], skipped=[...])` instead. Assert the malformed
      section is unchanged and the skipped section does not alter the verdict.
- [ ] No xfail markers exist in either test file (searched: `pytest.mark.xfail`,
      `pytest.xfail(`). Nothing to convert.

## Rabbit Holes

- **Rewriting the grammar.** `prints N`, `output == N`, `output >= N`, and `≥ 1` all appear in
  active plans and none is supported. Tempting, in scope for #2791, out of scope here.
- **Converging the timeouts.** `run_checks` uses 120s, `validate_build.py` 30s. A real
  difference, no observed divergence, and unifying it changes what the build gate blocks on.
  Because this is the one axis on which the converged runners can still disagree, T13 asserts
  verdict equality only over commands that are instantaneous by construction — the axis is held
  out of the test rather than raced against a wall clock.
- **Fixing `validate_verification_section.py`.** A third divergent reader, but inert until
  #2778 registers it. Touching it here means changing a hook that gates plan writes, for zero
  present-day benefit.
- **Auto-repairing plan documents.** Rewriting an author's summary table into bullets is the
  #2741 workaround promoted to code. The parser reports; the author decides.
- **Making `## Verification` a strict single-table section.** Spike-1 proved multi-table check
  layouts are real and used. Do not simplify by forbidding them.

## Risks

1. **A real check table gets classified as a summary table.** Mitigated by the strict
   cell-equality signature measured against all 493 check-table blocks in the corpus (spike-3:
   zero disagreements with the loose test) and by the requirement that a section with rows but
   no check table fails loudly rather than skipping silently.
2. **Skipped tables are non-failing, so a genuinely-mis-headered check table goes quiet.** One
   case exists in the corpus (`opus-skill-prompts-4-7.md`, a data row orphaned by a blank line);
   it is a completed plan. Mitigated by printing the skipped block's header and row count in
   both runners' reports. Accepted deliberately: failing on skipped tables would re-create
   #2836.
3. **Convergence surfaces failures that `validate_build.py` used to hide.** The load-bearing
   claim is a property, not a count: **every row that newly FAILs under `validate_build.py` is
   already FAILing the canonical runner at `docs/sdlc/do-build.md:185` today**, so no lane gains
   a blocker it did not already have. These are rows using grammar neither evaluator recognizes
   (`prints N`, `output == N`, bare `> 0`, `ok`, `exit 0` — #2791's territory). Exposure is
   bounded further because `validate_build.py` runs against one plan at a time, the lane's own.
   Scale, indicative at `ba8fdd9d6` and re-measured by T14: ~68 rows across ~9 active plans. The
   integer is decorative — the property holds at any corpus size, and T14 must re-assert the
   property, not merely refresh the number.
4. **Command-cell extraction changes in `validate_build.py`.** It currently takes the first
   backtick-delimited span; the canonical parser takes `cells[1].strip("\`")`. The load-bearing
   claim is again a universal: **every row on which the two differ puts prose in the Command
   cell** — manual runbook steps that were never executable under either runner — so the
   canonical behavior wins without losing an executable check. Scale, indicative at `ba8fdd9d6`
   and re-measured by T14: ~21 rows across ~4 active plans. T14 re-asserts the universal (no
   differing row holds a runnable command); the delta is named in the PR body rather than
   discovered later.
5. **`format_results`' signature change is breaking, deliberately.** Both
   `docs/sdlc/do-build.md:185` and `docs/sdlc/do-pr-review.md:72` embed a one-liner calling
   `format_results(r, t.malformed)`; both become `format_results(r, t)` in this PR. These are the
   only two production call sites in the repo — measured, not assumed. Making the second
   parameter required rather than optional means a missed call site is an immediate `TypeError`
   instead of a diagnostic that silently stops printing, which is exactly how
   `docs/features/machine-readable-dod.md:186` came to still document `format_results(results)`
   after #2570 added `malformed` optionally. Three tests in
   `tests/unit/test_verification_parser.py` pass the old shape and are updated in the same PR.
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
- **Converging `scripts/validate_build.py::parse_file_assertions` (`:43-77`) and
  `::parse_success_criteria_commands` (`:149-180`).** Both read the same plan document with their
  own private grammar and both feed the same exit code, so they are a real third and fourth
  divergence living inside the very file this lane converges. `parse_file_assertions` captures a
  backtick-quoted path after Create/Add/Delete/Update and asserts it relative to the repo root,
  which turns a bare filename into a permanently-false assertion (this plan tripped exactly that
  in critique round 2 — see T2). `parse_success_criteria_commands` takes only the *first*
  backtick span of a criterion line, so a criterion naming two commands is silently reduced to
  one. Named here rather than left unnamed: leaving them out of scope is defensible, leaving them
  undocumented would repeat the #2570 carry-forward failure this plan diagnoses. They belong to
  their own issue, and T20's carry-forward comment covers all four readers so the successor has
  the full list.
- Editing any plan document's `## Verification` section to suit the new parser. Spike-2 measured
  zero active plans changing; if one did, that is a finding to report, not a document to quietly
  rewrite.
- Auto-repairing, reformatting, or linting plan documents from either runner.
- Expanding into #2658's demonstrated-red proof obligations.
- **Failing a `## Verification` section that contains no pipe-blocks at all.** A prose-only,
  empty, or absent section yields an empty `ParsedTable`; `all([])` is `True`, so
  `docs/sdlc/do-build.md:185` exits 0 and the gate passes on nothing. This lane closes only the
  narrower case — a section whose tables carry no `Command` column. Closing the wider case would
  block every plan in the repo that legitimately carries no verification table, which is a policy
  change about what a plan must contain rather than a parser fix, and it belongs to its own
  issue.

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
      the `SkippedTable` diagnostic, `ParsedTable`'s third field, the one-`MalformedRow`-per-block
      rule for a section with no check table, and state plainly that
      `scripts/validate_build.py` no longer carries its own evaluator. Correct `:186`, which
      still documents `format_results(results)`, to the current
      `format_results(results, table)` signature.
- [ ] Update `docs/sdlc/do-build.md` — the `:185` one-liner calls `format_results(r, t)`; note
      next to `:182` that `validate_build.py` and the canonical runner now share one table
      definition and one evaluator.
- [ ] Update `docs/sdlc/do-pr-review.md` — same one-liner change at `:72`.
- [ ] Comment on #2778 recording the canonical definitions the hook must import rather than
      reimplement: a table is a contiguous run of pipe-prefixed lines
      (`agent.verification_parser._iter_pipe_blocks`, per GFM); a check table is one with ≥3
      columns where one of the first three column names equals `Command`, case-insensitively;
      cell splitting is `agent.verification_parser.split_row_cells`. Registering
      `.claude/hooks/validators/validate_verification_section.py` in
      `.claude/hooks/manifest.toml` without importing those three gives the repo a write-time
      gate that can reject a `## Verification` section both runners accept. The comment also
      names the other two unconverged readers held out of scope by `## No-Gos` —
      `scripts/validate_build.py::parse_file_assertions` and `::parse_success_criteria_commands` —
      so the successor inherits all four rather than two. This is the carry-forward that #2570 did
      not leave, and its absence is why this lane exists.
- [ ] Update `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` — add to the `## Verification`
      guidance that a check table is identified by a `Command` column, that additional
      non-check tables in the section are skipped with a named diagnostic rather than executed,
      and that a section whose tables have no `Command` column fails the gate.
- [ ] No new entry in `docs/features/README.md` — this modifies the existing
      machine-readable-DoD feature rather than adding one. Confirm the index row's description
      still reads true after the edit.

## Success Criteria

- [x] A non-check markdown table in `## Verification` produces zero executable checks and zero
      guaranteed-fail rows (#2836 AC 1).
- [x] A skipped table is named in both runners' reports with its header and row count
      (#2836 AC 2).
- [x] The #2741 pre-fix text parses to exactly 16 checks, 0 malformed, 1 skipped block
      (#2836 AC 3).
- [x] Every new test fails against the current parser/validator before the fix (#2836 AC 4,
      demonstrated-red).
- [x] A second `| Anti-criterion | Command | Expected |` table still yields executable checks.
- [x] `## Verification` containing table rows but no `Command`-column table fails loudly, with
      exactly one `MalformedRow` per pipe-block.
- [x] `format_results` takes two required parameters, `results` and `table`, and both production
      call sites pass the `ParsedTable`.
- [x] Every file in `tests/fixtures/verification/` begins with a literal `## Verification` line.
- [x] `scripts/validate_build.py` reports PASS for `output > 0` on stdout `1` and for
      `match count == 0` on a clean `grep -c` (#2843).
- [x] `scripts/validate_build.py` reports FAIL for a violated `match count == 0` (#2783
      Severity-1).
- [x] `scripts/validate_build.py` contains no `parse_verification_table` definition, no
      `expected.startswith` chain, and no `actual_exit == 0` fallback.
- [x] `scripts/pytest-clean.sh tests/unit/test_verification_parser.py tests/unit/test_validate_build.py -q`
      exits 0. (Round 3 critique: a bare `python -m pytest` here would be the third of three bare
      invocations this plan carries, against the repo's explicit prohibition on bare `pytest` --
      see `CLAUDE.md`'s xdist-reaper note. `parse_success_criteria_commands` only admits commands
      starting with `python`, `pytest`, `grep`, `test `, `ls `, `cat `, `ruff`, so a `scripts/…`
      criterion additionally drops out of the duplicate 30s-bounded build-gate run entirely.)
- [x] `python -m ruff check agent/verification_parser.py scripts/validate_build.py` and
      `python -m ruff format --check agent/verification_parser.py scripts/validate_build.py` are
      clean. (Round 3 critique: scoped to the two changed files so `parse_success_criteria_commands`'s
      first-backtick-span-only extraction — see `## No-Gos` — does not turn this into a bare
      repo-wide `ruff check` under the 30s-bounded build gate.)
- [x] Both runners produce identical per-row verdicts over the execution fixture
      `runner_agreement.md`, and identical parses over the four parse-only fixtures. No test in
      this lane invokes `scripts/pytest-clean.sh` or `pytest` as a subprocess.

## Team Orchestration

Three agents, sequential — each one's work is defined by the previous one's return type.

Every task T1–T20 is assigned to exactly one agent below; an unassigned task is an unbuilt task.

### 1. Parser scoping and fixtures
- **Task ID**: parser-scoping
- **Depends On**: none
- **Agent Type**: builder
- **Parallel**: false
- **Owns**: T1, T2, T3, T4, T5, T6, T7, T8
- `agent/verification_parser.py`: `_iter_pipe_blocks`, the header signature, `SkippedTable`,
  `ParsedTable.skipped`, `format_results(results, table)`.
- `tests/fixtures/verification/` and the new cases in `tests/unit/test_verification_parser.py`.
- Records the demonstrated-red output for each new test before implementing.

### 2. Validator delegation, cross-runner guard, and docs
- **Task ID**: validator-delegation
- **Depends On**: [parser-scoping]
- **Agent Type**: builder
- **Parallel**: false
- **Owns**: T9, T10, T11, T12, T13, T15, T16, T17, T20
- Deletes `scripts/validate_build.py`'s parser and evaluator, wires the imports, rewrites
  `check_verification_table`, updates `tests/unit/test_validate_build.py`.
- Owns T13 — the cross-runner agreement test — because T13 *writes* a test file and the
  validator agent below is read-and-run only.
- All four documentation targets plus the #2778 carry-forward comment.

### 3. Final validation
- **Task ID**: validate-all
- **Depends On**: [parser-scoping, validator-delegation]
- **Agent Type**: validator
- **Parallel**: false
- **Owns**: T14, T18, T19
- T19 moved here (round 3 critique): T19's "corpus number from T14" input has no writer while
  T19 sat in `validator-delegation`, which finishes before `validate-all`'s T14 runs. T1/T3/T9's
  captured red-state text is handed off via `.build-notes/red-state.md` on the lane branch (see
  each task) rather than requiring T19's agent to have been present for their capture.
- Runs this plan's `## Verification` table through the changed code (T18), re-measures the
  505-section corpus to confirm zero active-plan drift and records the refreshed figures **in the
  PR body only** — it edits no plan document, for the G7 reason given in T14 (T14) — and confirms
  the cross-runner agreement test written in T13 passes.

## Step by Step Tasks

- [ ] **T1 — Capture the red state.** Before changing code, record and paste into the PR body:
      `parse_verification_table` on `61717ccb2^:docs/plans/docs-auditor-rename-detection.md`
      returning 27 checks; `validate_build.py`'s evaluator reporting FAIL for
      `output > 0`/`1` and for `match count == 0`/`0`; and reporting PASS for
      `match count == 0`/`24`. **Append this captured text to `.build-notes/red-state.md` on the
      lane branch** (create the file if absent) -- T19, dispatched from a later agent in this
      lane's roster, reads it back rather than requiring its own agent to have witnessed T1.
- [ ] **T2 — Create the fixture directory `tests/fixtures/verification/`.** Add
      `tests/fixtures/verification/2741_pre_fix_verification.md` (the `## Verification` section
      from `61717ccb2^:docs/plans/docs-auditor-rename-detection.md`, verbatim),
      `tests/fixtures/verification/two_check_tables.md` (a `Check` table plus an `Anti-criterion`
      table, **at least two data rows under each of the two headers — at least four executable
      checks total**, so the guarding `## Verification` row's `output > 3` assertion is satisfied
      by construction rather than by an under-specified builder choice — round 3 critique),
      `tests/fixtures/verification/no_command_column.md` (a `| # | Criterion | Check |`
      table), `tests/fixtures/verification/check_plus_summary.md` (one check table plus a prose
      summary table), and `tests/fixtures/verification/runner_agreement.md` — the **execution**
      fixture specified in T13, the only fixture whose commands are ever run.
      **Write every fixture name as a full repo-relative path, exactly as above.**
      `scripts/validate_build.py:43-77` captures the backtick-quoted path after Create/Add and
      evaluates `Path(p).exists()` from the repo root (`:190-207`), so a bare filename such as
      `2741_pre_fix_verification.md` resolves False forever — the fixtures land under
      `tests/fixtures/verification/`. `main()` then returns 1 (`:417`) and
      `docs/sdlc/do-build.md:182` routes a green build into three futile `/do-patch` iterations,
      chasing a failure no code change can clear. That is the outcome this plan's `## Problem`
      exists to eliminate, and this plan tripped it against itself in critique round 2. Only the
      first path on the line is captured today (the regex needs the verb immediately before the
      backtick), but pathing all five survives a rewrap.
      **Acceptance:** after T2 lands, every path returned by
      `scripts/validate_build.py::parse_file_assertions` on this plan with `action == "exists"`
      resolves on disk.
      **Every fixture file starts with a literal `## Verification` heading line and carries no
      second `^## ` heading before its tables.** `parse_verification_table` matches
      `^## Verification\s*$` (`agent/verification_parser.py:129-135`) and terminates the section
      at `(?=^## |\Z)`, so a body-only fixture parses to an empty `ParsedTable` and every
      fixture-backed assertion in T3, T13 and the `## Verification` table below would pass
      against nothing — this plan's own thesis reproduced inside its own test corpus.
- [ ] **T3 — Add the failing tests** to `tests/unit/test_verification_parser.py` for every row
      of the Failure Path Test Strategy that targets the parser. Run them; each must fail.
      Paste the failures into the PR body, and append them to `.build-notes/red-state.md` on the
      lane branch (see T1's handoff note).
- [ ] **T4 — Add `SkippedTable` and extend `ParsedTable`** in `agent/verification_parser.py`.
      `skipped` gets `field(default_factory=list)` so existing constructions stay valid.
- [ ] **T5 — Add `_iter_pipe_blocks` and the header-signature test.** A block is a check table
      when it has ≥3 columns and one of its first three column names equals `Command`
      case-insensitively.
- [ ] **T6 — Rewrite `parse_verification_table`** over blocks: parse every check table, skip
      every non-check table, and — when the section has pipe-blocks and no check table — emit
      **exactly one** `MalformedRow` per pipe-block with `line=block[0]` and the reason string
      given in Solution §1. One per block, never one per row. Separator rows are skipped only
      when they match `^\|[\s\-:|]+\|$`.
- [ ] **T7 — Change `format_results` to `format_results(results: list[CheckResult], table:
      ParsedTable) -> str`**, reading `table.malformed` and `table.skipped`. Both parameters are
      required; there is no optional-parameter shim. Add a "Non-check tables skipped" section
      that prints each skipped block's header and row count and does not affect the verdict.
      Update the module docstring to state the per-block rule and cite GFM.
- [ ] **T8 — Confirm T3's tests now pass** and the whole of
      `tests/unit/test_verification_parser.py` is green.
- [ ] **T9 — Add the failing validator tests** to `tests/unit/test_validate_build.py` for
      `output > N`, clean `match count == 0`, violated `match count == 0`, the skipped-table
      exit code, and the trailing-newline parity case. Run them; each must fail. Paste the
      failures into the PR body, and append them to `.build-notes/red-state.md` on the lane
      branch (see T1's handoff note).
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
- [ ] **T13 — Split the cross-runner guard by role. Never execute the parse-only fixtures.**
      Two tests, not one.
      **(a)** `test_both_runners_agree_on_execution_fixture` — parametrized over the single
      execution fixture `tests/fixtures/verification/runner_agreement.md`, asserting per-row
      verdict equality between `validate_build`'s loop and `run_checks`. Every Command cell in
      that fixture is instantaneous, hermetic, and deterministic (`echo 1`, `true`, `false`,
      `grep -c zzz /dev/null`), and between them its rows cover all six `evaluate_expectation`
      branches: `exit code N`, `exit code != N`, `output contains X`, `output does not contain X`,
      `match count == 0`, and `output > N`. This is the guard against the next divergence.
      **(b)** `test_parse_only_fixtures_parse_identically` — the other four fixtures
      (`2741_pre_fix_verification.md`, `two_check_tables.md`, `no_command_column.md`,
      `check_plus_summary.md`) are asserted through `parse_verification_table` alone. Their
      commands are never run.
      **Why the split is mandatory.** `2741_pre_fix_verification.md` is a verbatim real
      `## Verification` section, so executing it runs 16 real commands twice per case — including
      a nested `scripts/pytest-clean.sh` invocation (measured: 130 tests, 17.9s on an idle
      machine, 10 xdist workers) and two repo-wide ruff passes. A nested xdist run competes with
      its own parent for the same test-db claims (#2628). Worse, it is load-bearing red: after
      delegation the only axis on which the two runners can still disagree is the timeout this
      plan deliberately refuses to converge — `run_checks` at 120s yields
      `passed=False, exit_code=-1` (`agent/verification_parser.py:303-312`) while
      `validate_build` at 30s yields `status="SKIP"` (`scripts/validate_build.py:318-319`). A
      17.9s command against a 30s ceiling is a 1.7x margin on an *idle* machine, so the test would
      go red under load for a reason unrelated to the defect it guards. Scoping execution to
      instantaneous commands removes the axis instead of racing it.
      **No test added in this lane shells out to `scripts/pytest-clean.sh` or to `pytest`.**
      Open each parametrized body with
      `assert p.read_text().lstrip().startswith("## Verification")` so a heading-less fixture
      fails loudly at the top of the test rather than passing vacuously against an empty parse.
- [ ] **T14 — Re-run the corpus measurement; record it in the PR body only.** Use the corpus
      definition pinned in the `## Spike Results` preamble (`find docs/plans -name '*.md'`,
      counting files whose heading matches `^## Verification\s*$`, `.worktrees/` excluded by
      construction). Confirm zero active plans in `docs/plans/*.md` change their parse result, and
      re-assert the two **universals** rather than only refreshing integers: **Risks §3** — every
      row that newly FAILs under `validate_build.py` is already FAILing the canonical runner
      today; **Risks §4** — every row on which the two Command-cell extractions differ holds
      prose, not a runnable command. Report the refreshed figures and both universals in the PR
      body.
      **Write nothing back into this plan document, and commit no plan edit during BUILD.**
      `validate-all` depends on both builders, so T14 runs at the end of BUILD — inside the window
      guarded by the G7 mid-build plan-hash guard (`docs/sdlc/do-build.md:80-93`), which records
      `git log -1 --format=%H origin/main -- <plan-rel-path>` at build start, re-reads it before
      PR creation, and ABORTS the build (marking BUILD `failed`, "plan revised mid-build") on any
      difference. Plan documents commit on `main` in this repo, so a write-back would kill its own
      build immediately before the PR is opened. The corpus figures are explicitly indicative and
      the load-bearing claims are the universals, so the PR body is their correct home; nothing is
      lost and no G7 exposure is created.
- [ ] **T15 — Update `docs/features/machine-readable-dod.md`** per the Documentation section.
- [ ] **T16 — Update the `format_results` one-liners** in `docs/sdlc/do-build.md:185` and
      `docs/sdlc/do-pr-review.md:72` to `print(format_results(r, t))`. The exit guard stays
      `sys.exit(1 if t.malformed or not all(x.passed for x in r) else 0)` so `skipped` never
      reaches the exit code. Add the shared-implementation note beside `docs/sdlc/do-build.md:182`.
- [ ] **T17 — Update `.claude/skills-global/do-plan/PLAN_TEMPLATE.md`** `## Verification`
      guidance with the `Command`-column signature, the skipped-table diagnostic, and the
      no-check-table failure.
- [ ] **T18 — Run this plan's own `## Verification` table** through the changed code and
      confirm every row passes.
- [ ] **T19 — Write the PR body.** `Closes #2836`, `Closes #2843`, and `Closes #2783` (same
      three deleted lines — see "Effect on #2783 and #2791"). State explicitly that **#2791
      remains open** and that convergence makes its `prints N` rows fail under
      `validate_build.py` where they previously passed by accident. Include the red-state
      evidence from T1, T3, T9 (read back from `.build-notes/red-state.md` on the lane branch --
      this task's agent did not witness their capture directly) and the corpus number and both
      re-asserted universals from T14, which ran immediately before this task in the same agent.
- [ ] **T20 — Post the #2778 carry-forward comment.** `gh issue comment 2778` with the canonical
      definitions listed in `## Documentation`: `_iter_pipe_blocks` as the table definition, the
      `Command`-column signature, and `split_row_cells` as the cell splitter — plus the statement
      that registering the hook means importing all three rather than reimplementing them. No
      code change to `.claude/hooks/validators/validate_verification_section.py` in this lane;
      the comment is the entire deliverable. The comment must enumerate **all four** unconverged
      readers of a plan document, not two: the hook itself, and — per `## No-Gos` —
      `scripts/validate_build.py::parse_file_assertions` (`:43-77`) and
      `::parse_success_criteria_commands` (`:149-180`), which still carry private grammars inside
      the file this lane converges. A successor needs the full list or it inherits the same
      partial-convergence failure. This is the carry-forward #2570 did not leave, and writing it
      is what keeps this plan from repeating one surface over the mistake it diagnoses.
      **Also file a successor issue** (round 3 critique, Scope & Value): `gh issue create` naming
      `parse_file_assertions` and `::parse_success_criteria_commands`'s two measured failure
      shapes (repo-root path resolution turning a bare filename into a permanently-false `exists`
      assertion; first-backtick-span-only reduction collapsing a two-command criterion to one),
      labeled `bug,skills`, and reference the new issue number in the #2778 comment so it stays as
      the cross-reference rather than the sole tracker. Filed during this build as #2870.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Parser tests pass | `scripts/pytest-clean.sh tests/unit/test_verification_parser.py -q` | exit code 0 |
| Validator tests pass | `scripts/pytest-clean.sh tests/unit/test_validate_build.py -q` | exit code 0 |
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
| 2741 fixture parses to 16 checks, 0 malformed, 1 skipped | `python -c "from agent.verification_parser import parse_verification_table as p; t=p(open('tests/fixtures/verification/2741_pre_fix_verification.md').read()); print(f'checks={len(t.checks)} malformed={len(t.malformed)} skipped={len(t.skipped)} end')"` | output contains checks=16 malformed=0 skipped=1 end |
| Second anti-criterion check table still parsed | `python -c "from agent.verification_parser import parse_verification_table as p; t=p(open('tests/fixtures/verification/two_check_tables.md').read()); print(len(t.checks))"` | output > 3 |
| Summary table skipped, not executed | `python -c "from agent.verification_parser import parse_verification_table as p; t=p(open('tests/fixtures/verification/check_plus_summary.md').read()); print(f'malformed={len(t.malformed)} skipped={len(t.skipped)} end')"` | output contains malformed=0 skipped=1 end |
| Rows with no Command column yield one malformed per block, and skipped stays empty | `python -c "from agent.verification_parser import parse_verification_table as p; t=p(open('tests/fixtures/verification/no_command_column.md').read()); print(f'checks={len(t.checks)} malformed={len(t.malformed)} skipped={len(t.skipped)} end')"` | output contains checks=0 malformed=1 skipped=0 end |
| Every fixture declares its section heading | `grep -L '^## Verification' tests/fixtures/verification/*.md \| wc -l` | match count == 0 |
| Violated anti-criterion evaluates false (#2783) | `python -c "from agent.verification_parser import evaluate_expectation as e; print('ok' if not e('match count == 0', exit_code=0, output='24') else 'BAD')"` | output contains ok |
| Clean anti-criterion evaluates true (#2843) | `python -c "from agent.verification_parser import evaluate_expectation as e; print('ok' if e('match count == 0', exit_code=1, output='0') else 'BAD')"` | output contains ok |
| output > N evaluated numerically (#2843) | `python -c "from agent.verification_parser import evaluate_expectation as e; print('ok' if e('output > 0', exit_code=0, output='1') else 'BAD')"` | output contains ok |
| All six expectation forms still evaluate (grammar-frozen no-go guard) | `python -c "from agent.verification_parser import evaluate_expectation as e; print(sum([e('exit code 0', exit_code=0, output=''), e('exit code != 1', exit_code=0, output=''), e('output contains x', exit_code=0, output='x'), e('output does not contain y', exit_code=0, output='x'), e('match count == 0', exit_code=1, output='0'), e('output > 1', exit_code=0, output='2')]))"` | output contains 6 |
| Cross-runner agreement test exists | `grep -c 'both_runners_agree_on_execution_fixture' tests/unit/test_validate_build.py` | output > 0 |
| Parse-only fixtures compared without executing them | `grep -c 'parse_only_fixtures_parse_identically' tests/unit/test_validate_build.py` | output > 0 |
| No test shells out to the pytest wrapper (T13 blocker guard) | `grep -c 'pytest-clean' tests/unit/test_validate_build.py tests/unit/test_verification_parser.py` | match count == 0 |
| Execution fixture committed alongside the parse-only four | `ls tests/fixtures/verification/ \| wc -l` | output > 4 |
| No stale call to the deleted validator parser | `grep -c 'validate_build.parse_verification_table' tests/unit/test_validate_build.py` | match count == 0 |
| Feature doc records the skipped diagnostic | `grep -c 'skipped' docs/features/machine-readable-dod.md` | output > 0 |
| format_results takes two required parameters | `python -c "import inspect; from agent.verification_parser import format_results as f; p=inspect.signature(f).parameters; print('ok' if list(p)==['results','table'] and all(v.default is v.empty for v in p.values()) else 'BAD')"` | output contains ok |
| do-build gate one-liner passes the ParsedTable | `grep -c 'format_results(r, t))' docs/sdlc/do-build.md` | output > 0 |
| do-pr-review gate one-liner passes the ParsedTable | `grep -c 'format_results(r, t))' docs/sdlc/do-pr-review.md` | output > 0 |
| No stale malformed-only format_results call remains | `grep -c 'format_results(r, t.malformed)' docs/sdlc/do-build.md docs/sdlc/do-pr-review.md` | match count == 0 |
| Feature doc records the current signature | `grep -c 'format_results(results, table)' docs/features/machine-readable-dod.md` | output > 0 |
| Plan template documents non-check tables | `grep -ci 'non-check table' .claude/skills-global/do-plan/PLAN_TEMPLATE.md` | output > 0 |
| This plan's own table reads clean through the fixed parser | `python -c "from agent.verification_parser import parse_verification_table as p; t=p(open('docs/plans/verification-runner-convergence.md').read()); print(f'malformed={len(t.malformed)} skipped={len(t.skipped)} end')"` | output contains malformed=0 skipped=0 end |

## Critique Results

Round 3 (final critique round), FULL depth (3 lenses, force-FULL: the plan touches
`.claude/skills-global/`). Roster gate passed 3/3, 0 ungrounded. Verdict: **READY TO BUILD** —
0 blockers, 6 concerns, 2 nits. Round 2's three blockers are all verified closed (T13 split into an
instantaneous `runner_agreement.md` execution fixture plus four parse-only fixtures with a
`pytest-clean` anti-criterion; all five fixtures pathed repo-relative with zero bare-filename
`exists` assertions remaining; T14's plan write-back dropped entirely in favour of the PR body).

Every load-bearing measurement was independently re-executed against the live repo: all six corpus
figures (590 / 505 / 502 / 493 / 25 active / 0 active-plan deltas) reproduce exactly, spike-1,
spike-3 and spike-6 reproduce exactly, and `## Freshness Check`'s "Disposition: Unchanged" still
holds across the 10 commits `main` has taken since `ba8fdd9d6`.

**The self-gating hazard is cleared.** This plan's own `## Verification` section was re-parsed from
the committed blob under the **current, unfixed** parser: exactly one contiguous pipe-block,
**34 checks, 0 malformed**. The `## Critique Results` table sits under its own `## ` heading and does
not merge into the executable check list. The plan cannot poison its own build gate.

Round 3 opened two findings at blocker severity during discovery; both were re-scored to CONCERN on
the standing bar (a blocker must make the build fail, produce wrong behaviour, or send a green build
into a patch loop). Neither does: one is an external tracker-state error whose operative remedy is a
single `gh` command, **already executed during this critique** — #2783 is reopened; the other
affects only PR-body prose whose missing content is the corpus figure, settled as indicative and
non-load-bearing. Both retain full implementation notes below. The builder should apply them.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | History & Consistency | **#2783 was closed in error by this lane's own plan-revision commit while the defect is live — REOPENED during this critique.** Measured: `gh issue view 2783` → `state: CLOSED`, `stateReason: COMPLETED`, `closedAt: 2026-08-19T05:33:58Z`, closed by `842212ac` — "Plan revision (verification-runner-convergence): address critique findings (Refs #2836)", a docs-only commit on `main` touching only this plan document. Its body carried "Open Questions resolved into Decisions; PR carries Closes #2783" and GitHub honoured the keyword. The code is untouched: `scripts/validate_build.py:303` still reads `passed = expected.lower() in actual_output.lower() or actual_exit == 0` at HEAD. So (1) `## Prior Art`'s "**#2783** (open)" is wrong; (2) Decision §1 and T19's closing keyword are a no-op on an already-closed issue, so the plan's stated remedy does not restore tracking; (3) if this lane stalls, a live Severity-1 false-PASS defect is permanently untracked. The mechanism will repeat: `## Decisions` §1 and T19 both carry the literal keyword string and every plan-revision commit summarises plan prose into a `main` commit body. #2836 and #2843 verified still OPEN, so only #2783 fired. | **(a), (b), (c) all done** | Three parts. **(a)** DONE — `gh issue reopen 2783` was executed during this critique with an explanatory comment; `gh issue view 2783` now returns `state: OPEN, stateReason: REOPENED`. The plan's `Closes` keyword is live again and the tracker no longer reports a Severity-1 defect as done. Nothing for the builder here. **(b)** Change `## Prior Art`'s row to "**#2783** (reopened — closed in error by `842212ac`)". **(c)** Everywhere the plan *discusses* the keyword in prose, write it non-firing (e.g. "the PR body carries a closing keyword for 2783"); leave the literal `Closes #2783` only inside T19's instruction for the **PR body**, which is where it must fire. GitHub scans the whole commit message, so the `.githooks/commit-msg` disposition trailer (`Refs #2836` was present) does not prevent this. |
| CONCERN | Risk & Robustness | **T19 is owned by an agent that finishes before the agent owning T14 starts, so T14's only output channel has no writer.** (Re-scored from blocker: the affected artifact is PR-body prose, and the missing figure is the corpus number, settled as indicative and non-load-bearing. No gate reads it, so a green build cannot be sent to `/do-patch` by this. Still worth fixing — the note below is exact.) T19 ("Write the PR body … Include the red-state evidence from T1, T3, T9 and the corpus number from T14") sits in `## Team Orchestration` §2 `validator-delegation`'s `Owns` list; T14 sits in §3 `validate-all`'s, whose `Depends On` is `[parser-scoping, validator-delegation]`. Round 2's third blocker fix dropped T14's plan write-back and made the PR body its **only** home ("Write nothing back into this plan document"), so T14's refreshed figures and both re-asserted universals are now produced after the body that must carry them is written. Round 2's NIT asserted "every task T1–T20 is assigned to exactly one agent" — true, but the ordering was never checked. | done | In `## Team Orchestration`, change §2's `Owns` to `T9, T10, T11, T12, T13, T15, T16, T17, T20` and §3's to `T14, T18, T19`, and move T19 after T18 in `## Step by Step Tasks`. T1/T3/T9's red-state output then needs an explicit handoff: add to each "append the captured failure text to `.build-notes/red-state.md` on the lane branch", and to T19 "read `.build-notes/red-state.md`". `gh pr create` runs after all three agents return (`docs/sdlc/do-build.md:96` records the PR number immediately after creation), so agent 3 can still author the body. Do **not** solve this by restoring T14's plan write-back — that is the G7 blocker round 2 closed. |
| CONCERN | Risk & Robustness | **The plan runs bare `python -m pytest` three times, against an explicit repo prohibition.** `## Verification` rows 1-2 use `python -m pytest …`, and `## Success Criteria` bullet 12 feeds a third bare invocation into `scripts/validate_build.py::check_success_criteria` (30s timeout, exit 0 required). `CLAUDE.md`: "Use `scripts/pytest-clean.sh`, never bare `pytest`. The wrapper reaps xdist workers; interrupted bare runs leave orphan workers eating memory." `pyproject.toml:196` sets `addopts = "… -n auto --dist=loadfile …"`; measured, that command spawns 10 xdist workers claiming test dbs 1-10 and takes 8.7s idle. Under `check_verification_table`'s 30s ceiling on a loaded machine, `subprocess.TimeoutExpired` reaps only the direct child and leaves 10 workers holding claimed dbs. 21 of the 25 active plans already use `scripts/pytest-clean.sh`; this plan is one of the 3 that do not. | done | Rewrite the two `## Verification` Command cells as `scripts/pytest-clean.sh tests/unit/test_verification_parser.py -q` and `scripts/pytest-clean.sh tests/unit/test_validate_build.py -q`, and bullet 12 the same way. This does **not** trip T13's anti-criterion: row "No test shells out to the pytest wrapper" greps `tests/unit/test_validate_build.py tests/unit/test_verification_parser.py` — the *test files*, never the plan. Bonus: `parse_success_criteria_commands` only admits commands starting with `python`, `pytest`, `grep`, `test `, `ls `, `cat `, `ruff` (`scripts/validate_build.py:166-177`), so a `scripts/…` criterion drops out of the duplicate 30s-bounded run entirely. |
| CONCERN | Risk & Robustness | **The plan documents `parse_success_criteria_commands`' first-backtick-span reduction in `## No-Gos` and then walks into it.** Measured against the current plan text, that function returns exactly `['python -m pytest …', 'python -m ruff check']` — bullet 13 ("`python -m ruff check` and `python -m ruff format --check` clean on both changed files") is executed by the build gate as a bare **repo-wide** `python -m ruff check` with exit 0 required, under the worktree venv. A ruff newer than the pin turns an untouched file into a FAIL and `docs/sdlc/do-build.md:182` routes that into three futile `/do-patch` iterations — the outcome `## Problem` exists to eliminate. Repo-wide ruff currently exits 0 (ruff 0.15.9), so this is pre-emptive, not a present red. | done | Reword bullet 13 so its **first** backtick span is already the scoped command: "- [ ] `python -m ruff check agent/verification_parser.py scripts/validate_build.py` and `python -m ruff format --check agent/verification_parser.py scripts/validate_build.py` are clean." The first span still starts with `python` (so it is still admitted and still runs) but is now scoped to the two changed files, matching `## Verification` rows 3-4 exactly. |
| CONCERN | Scope & Value | **`two_check_tables.md` is under-specified relative to the assertion that guards it.** The `## Verification` row "Second anti-criterion check table still parsed" asserts `output > 3` — at least four checks — but T2 specifies that fixture only as "a `Check` table plus an `Anti-criterion` table" with no row count. A builder writing two data rows under the first header and one under the second produces 3 checks, and the plan's own gate reports FAIL for an otherwise-correct fixture, routing a green build to `/do-patch`. Same self-inflicted-gate class as round 2's three blockers. | done | Add to T2: "`tests/fixtures/verification/two_check_tables.md` carries at least two data rows under each of its two headers (≥4 executable checks total), so the `output > 3` row is satisfied by construction." Tightening the row to a printed sentinel (`print(f'checks={len(t.checks)} end')` with `output contains checks=4 end`) would also catch the over-count direction; `output > 3` currently passes on any value above three. |
| CONCERN | Scope & Value | **The carry-forward the plan says #2570 failed to leave is itself parked on someone else's issue.** `## No-Gos` names `scripts/validate_build.py::parse_file_assertions` (`:43-77`) and `::parse_success_criteria_commands` (`:149-180`) as a real third and fourth divergence inside the file this lane converges, and T20's entire remedy is a comment on #2778. But #2778 is about a *different* reader (the unregistered hook), it is open and unowned, and a comment on another issue is precisely the weak carrier the plan diagnoses #2570 as having used. No successor issue is filed, so the two divergences inside the converged file leave this lane with no tracker. Both failure shapes are measured on this plan today. | done — filed as #2870 | Add to T20: `gh issue create --title "validate_build.py carries two more private plan-document grammars: parse_file_assertions and parse_success_criteria_commands" --label bug,skills` with the two line ranges, the two measured failure shapes (repo-root path resolution turning a bare filename into a permanently-false `exists` assertion; first-backtick-span-only reduction collapsing a two-command criterion to one), and a link back to this PR. Then reference the new issue number in the #2778 comment, which stays as the cross-reference. |
| NIT | Risk & Robustness | **The no-check-table branch never says whether a block also produces a `SkippedTable`.** `## Solution` §1 specifies exactly one `MalformedRow` per pipe-block for a section with rows but no check table, and is silent on `skipped`. The guarding `## Verification` row asserts only `checks=0 malformed=1` and leaves `skipped` unconstrained, so a builder emitting both a `SkippedTable` and a `MalformedRow` for the same block would double-report it in `format_results` and still pass the gate. | done | Add to `## Solution` §1: "In this branch `skipped` stays empty — a block is either skipped or malformed, never both." Extend the guarding row's command to print `skipped=` and expect `output contains checks=0 malformed=1 skipped=0 end`, matching the trailing-sentinel style of the other count-triple rows. |
| NIT | History & Consistency | **spike-2's "would yield zero checks" predates the loud no-check-table rule and understates the new behaviour.** The two named completed plans (`delivery_guard_resume_epoch_scoping.md`, `redis-replication-sentinel-failover.md`) head their only table `\| # \| Criterion \| Check \|` (the second adds a `Type` column); under `## Solution` §1 as revised they now yield zero checks **and one `MalformedRow`, which fails the gate**. A reader checking blast radius would conclude they go quiet rather than red. Verified: both headers are exactly as spike-2 states, and both plans are completed, so nothing re-gates them. | done | Replace "and would yield zero checks" with "and would yield zero checks plus one `MalformedRow` — a loud failure, not a silent pass. Both are completed plans and are never re-gated." No behaviour change. |

**Round 3 verification log (driver-executed, HEAD `b4e4afa64`).** Corpus: 590 documents under
`docs/plans`, 505 matching `^## Verification\s*$`, 502 pipe-blocks, 493 check tables, 25 active
plans — every figure reproduces exactly. Simulating the per-block parser against all 25 active
plans and diffing check and malformed counts: **0 change**. spike-1: exactly two corpus plans carry
more than one check table, both completed, both already named. spike-6: `61717ccb2^` blob still
resolvable; 27 checks / 0 malformed today, 16 / 0 / 1 skipped block (header
`\| Row \| Pre-change \| Meaning \|`, 11 rows) under the proposed parser. `format_results` call
sites: exactly the two production and three test sites `## Test Impact` names.
`.claude/hooks/validators/validate_verification_section.py` confirmed absent from
`.claude/hooks/manifest.toml`. T2's `exists`-assertion acceptance holds vacuously — the current
text yields 6 assertions, all `modified`, all resolving on disk, and zero `exists`.

Self-gating re-check, run against the **committed** blob under the **current unfixed** parser:

```
git show HEAD:docs/plans/verification-runner-convergence.md \
  | python -c "import sys; from agent.verification_parser import parse_verification_table as p; \
    t=p(sys.stdin.read()); print(len(t.checks), len(t.malformed))"
→ 34 0
```

One contiguous pipe-block in `## Verification`; `## Critique Results` is correctly isolated under its
own `## ` heading and contributes no rows.

**Builder note on the `Closes` keyword.** GitHub scans the whole commit message, and a docs-only
commit on `main` already closed #2783 once by quoting a plan line. When committing on the lane
branch or writing any `main` commit body, refer to that issue as "2783" without a closing keyword.
The literal keyword belongs in exactly one place: the **PR body** written by T19.

---

## Decisions

Settled during the revision pass. None remains open; the build has no blocking question.

1. **The PR carries a closing keyword for 2783.** 2783 and 2843 name the same three lines
   (`scripts/validate_build.py:301-303`), and deleting the fallback in favour of
   `evaluate_expectation` resolves both directions of 2783 with no additional code. The critique
   independently confirmed that 2783's failure mode becomes structurally impossible under this
   design. **T19** carries all three closing keywords (the literal form belongs only there — see
   the builder note above), and states that #2791 stays open.

2. **A skipped non-check table does not fail the gate.** A summary table is legitimate plan
   authoring, and failing on it would reproduce #2836 with a nicer message. The cost is the one
   corpus case where a real check row was orphaned by a blank line
   (`docs/plans/completed/opus-skill-prompts-4-7.md`, a completed plan) and would go quiet. The
   mitigation is a loud printed diagnostic naming the skipped block's header and row count in both
   runners' reports, plus the loud failure when a section's tables carry no `Command` column at all.
   Recorded in **Risks §2** as a deliberate acceptance.

3. **`validate_build.py`'s bare `output <exact-string>` form is dropped.** It is the one form the
   losing evaluator supported that the canonical one does not, and measured usage in active plans
   is zero (spike-5). Keeping it means adding grammar, which `## No-Gos` forbids and #2791 owns.
   The one existing test that exercises it is retargeted at `output > N` per **Test Impact**.
