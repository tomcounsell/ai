---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-10
tracking: https://github.com/tomcounsell/ai/issues/2658
last_comment_id: none
---

# Gates That Cannot Fire: Two-Pole Proofs for Verification Rows, Guards, and Skill Self-Checks

## Problem

On 2026-08-07 three independent gates in this repo each reported success while
being structurally incapable of detecting the failure they exist to catch.

1. A verification row's `sed` BRE address used `\|` and ranged to end-of-file on
   BSD sed; its replacement row used an ERE where the escaped `\|` is a literal
   pipe occurring nowhere in the tree. Both rows printed their expected output
   and "passed" while matching nothing (#2647, three of four critique rounds
   consumed by this class).
2. An AST recurrence guard matched on `node.func.id`, which only ever matches a
   bare-name call. Every real call site in the repo is `self.x()` or
   `module.x()`, which parse as `ast.Attribute`. The guard would have shipped
   green and detected a recurrence never (#2655).
3. `critique_roster_check` returned `{"complete": true}` in runs where the
   forked context had no Agent tool and the "independent" critic lenses ran
   serially in-process. The gate checks roster coverage; the property the roster
   exists to provide is independence (#2649). Its same-day cousin: watchdog unit
   tests wrote synthetic CRITICAL lines into the production log, so log-derived
   signals counted events that never happened in production (#2643).

Three same-day instances across unrelated subsystems is a pattern. Each one was
caught only by an agent re-deriving the check from first principles.

The same week produced two instances of the **inverse** failure, both on the
#2643 lane, both costing a full critique round:

4. TC5's AST walk forbade module-scope config calls and descended into
   module-level `if` bodies, while TC5b required exactly those calls as direct
   children of `if __name__ == "__main__":` — itself a module-level `if`.
   Measured 3 hits against byte-for-byte correct fixed source, so the row's
   "confirm zero hits" was unreachable.
5. TC15 `runpy`'d the fixed source in-process under pytest, where the root
   logger already carries pytest's handlers, so the relocated `basicConfig`
   early-returned and all four assertions failed against correct code.

**Current behavior:** a gate author writes the check forward ("this command
should print X"), runs it once against whichever tree is in front of them, sees
the number they hoped for, and ships. Nothing requires them to have watched the
result *change*.

**Desired outcome:** a gate earns trust by being run in two states — one where
the defect is present and one where it is absent — and observed to grade
differently. This is a **two-pole proof**. It is recorded in the plan next to
the row it justifies, and it is graded mechanically by the same matcher the
build runner uses.

## Freshness Check

**Baseline commit:** `8c6936c54`
**Issue filed at:** 2026-08-07T09:32:58Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `.claude/skills-global/do-plan/PLAN_TEMPLATE.md:441` — `## Verification` section
  spec. Still holds at line 441.
- `.claude/skills-global/do-plan/PLAN_TEMPLATE.md:283` — anti-criteria tip. Still
  holds at line 283. The one-pole rule text is at lines 464-465.
- `docs/features/machine-readable-dod.md:116-165` — "Authoring Rule: Red-State
  Proof". The heading is at line 132; the worked green/red `match count == 0`
  example runs to line 165. Lines 116-131 are the BRE/ERE escaping table that
  precedes it. Claim holds; the section boundary is 132, not 116.
- `agent/verification_parser.py` — `parse_verification_table` (line 116),
  `evaluate_expectation` (line 182), `split_row_cells` (line 63), `MalformedRow`
  (line 90). Module docstring documents the BRE/ERE trap at lines 39-46. All
  present.
- `docs/sdlc/do-build.md:97` — verification-table runner invocation. Still at
  line 98 (drifted by one). No behavioral change.

**Cited sibling issues/PRs re-checked:**

- #2647 — CLOSED. Instance 1's specific rows are fixed; the authoring gap is not.
- #2655 — OPEN. Owns instance 2's AST matcher. Out of scope here.
- #2649 — CLOSED. Instance 3's missing Agent tool is fixed. Partially by
  `337cc1f31` ("Let skills that mandate subagent dispatch actually dispatch
  (#2679)"), which touched three global skill bodies and added
  `tests/unit/test_skill_agent_tool_consistency.py`. That fix is a sibling of
  this discipline, not a substitute: it makes the roster genuinely independent,
  it does not make the roster gate demonstrate it can fire.
- #2643 — OPEN. Owns the watchdog log-pollution fix and is the lane that produced
  instances 4 and 5. Out of scope here.
- #2650 — OPEN. Shared-main-checkout coordination. Relevant only as the reason
  this plan commits each revision promptly.
- #2709 — OPEN, created during this planning pass. Owns the escalation from
  critique-time reporting to build-time and hook enforcement.

**Commits on main since issue was filed (touching referenced files):**

- `7773d8d97` "Stop resolving /update's merge target through the shared
  FETCH_HEAD (#2650) (#2669)" — irrelevant to this area.
- `337cc1f31` "Let skills that mandate subagent dispatch actually dispatch
  (#2679) (#2672)" — touched `.claude/skills-global/do-plan/SKILL.md` (one line),
  `do-issue/SKILL.md`, `do-pr-review/SKILL.md`. Partially addresses #2649's root
  cause; changes nothing this plan edits.

**Active plans in `docs/plans/` overlapping this area:** four plans were touched
today (`watchdog-import-time-log-handler`, `agent-session-updated-at-restamp`,
`sdlc-stall-auto-resume`, `context-recall-advisory-flag`). None edits the
planning skills, the verification parser, or the DoD doc. `watchdog-import-time-log-handler`
is the lane that produced instances 4 and 5, and its round-4 revision
(`bfbcfe1fa`) contains the measured-both-directions table this plan cites as
prior art. Coordination signal, not a blocker: all four have already passed
critique, so wiring the new checker into the critique path cannot retroactively
fail them.

**Notes:** `grep` on this machine is **ugrep 7.5.0**, not GNU grep and not BSD
grep. That is a third dialect in the BRE/ERE matrix `agent/verification_parser.py`
documents, and it means a verification row can be green on one machine and red on
another independently of the code under test. Recorded in Risks.

## Prior Art

**Searched:** closed issues and merged PRs for "red-state", "verification row",
"anti-criterion", "mutation", "demonstrate".

- **`docs/features/machine-readable-dod.md` "Authoring Rule: Red-State Proof"**
  (lines 132-165) — the closest existing durable statement. It is one-pole (red
  only), scoped to inverse rows only, and posture is "paper-trail PR checklist"
  (non-binding). It carries a worked `match count == 0` green/red example that
  is directly reusable. This plan generalizes and relocates it rather than
  competing with it.
- **`.claude/skills-global/do-plan/PLAN_TEMPLATE.md:464-465`** — the same
  one-pole rule as an opt-in tip on anti-criteria. Generalizing it is the core of
  this work.
- **#2570 / `agent/verification_parser.py`** — the pipe-escaping fix. Established
  that a row nobody can execute is not a passing check (`MalformedRow` fails the
  run). Same instinct as this plan, applied to parse-time rather than
  authoring-time.
- **A guard graded by mutation:** `tests/unit/test_conftest_isolation_guards.py`
  gains an autouse `_registry_left_intact` fixture in commit `c7cfa7a52` ("Grade
  writer-2's own teardown, and stop it stranding the registry empty"). Restoring
  the buggy teardown turns it red and names the offending test.
  **Correction to the cited claim:** `c7cfa7a52` is **not an ancestor of main**.
  It lives only on `session/suite-failure-rotation-db-ownership`, and neither
  `TestReloadedRegistryIdentity` nor `_registry_left_intact` exists in the tree at
  `8c6936c54`. The technique is real and worth copying; the artifact is unmerged
  and this plan does not depend on it landing.
- **Per-module red rather than collective red:** PR #2706 (issue #2637),
  "Reach lease helpers through the module, closing the #2469 freeze class".
  **Correction:** the PR is **OPEN**, not merged. Its "Demonstrated red" section
  shows `10 failed in 3.47s` then `10 passed in 7.72s` over
  `tests/unit/test_sdlc_lease_helper_binding.py`, plus a per-module
  `freeze_demo.py` before/after trace for `tools.sdlc_dispatch`,
  `tools.sdlc_meta_set`, and `tools.sdlc_stage_marker` individually. The lesson
  stands: a guard shown red once for a class can still be blind to two of its
  three instances.
- **The measured-both-directions table:**
  `docs/plans/watchdog-import-time-log-handler.md:821-831` (commit `bfbcfe1fa`).
  With the TC5 exemption: 5 hits on today's source, 0 on fixed. Without it: 5 and
  3. That table is the shape this plan mechanizes — it proves the gate
  discriminates rather than merely reporting a number, and the "without
  exemption / fixed / 3" cell is precisely the round-3 BLOCKER that a one-pole
  rule would never have surfaced.

## Research

**Skipped external search.** The work is internal: skill bodies, repo docs, one
new script, and a test. No new library, API, or ecosystem pattern is involved.
The one externally-flavored question — what `\|` means across regex dialects — is
answerable more reliably by measuring this machine than by reading a manual, and
is recorded under Spike Results.

## Spike Results

### spike-1: Where can a proof section live without corrupting the Verification table?

- **Assumption**: "Proof blocks can be a `###` subsection under `## Verification`."
- **Method**: prototype (in-process, read-only)
- **Result**: **FALSE.** `parse_verification_table`'s section regex is
  `^## Verification\s*$(.*?)(?=^## |\Z)`, so a `###` subsection is captured
  *inside* the Verification section, and every captured line starting with `|` is
  treated as a table row. Measured with a proof block whose pasted stdout
  contained a line beginning with `|`:

  ```
  nested as ### Two-Pole Proofs:
    checks:    [VerificationCheck(name='Lint clean', ...)]
    malformed: [MalformedRow(line='| something weird',
                reason='expected 3 columns, got 1. ...')]

  hoisted to ## Two-Pole Proofs:
    checks:    [VerificationCheck(name='Lint clean', ...)]
    malformed: []
  ```

  A `MalformedRow` fails the build runner. So nesting the proofs would make every
  plan whose pasted output happens to contain a leading pipe fail its own build —
  a gate firing on the wrong thing.
- **Confidence**: high (executed)
- **Impact if false**: n/a, it was false. **Decision: `## Two-Pole Proofs` is a
  top-level section, placed immediately after `## Verification`.**

### spike-2: Can `evaluate_expectation` grade a pasted pole?

- **Assumption**: "The production matcher can separate a false-green, a
  false-red, and a healthy row from their pasted pole outputs alone."
- **Method**: prototype (in-process)
- **Result**: **TRUE.** Measured against `match count == 0`:

  | Shape | RED pole (exit, stdout) | grades | GREEN pole (exit, stdout) | grades | Verdict |
  |---|---|---|---|---|---|
  | False green (instance 1) | `1`, `0` | **True** | `1`, `0` | True | RED must be False → **reject** |
  | False red (instance 4, TC5) | `0`, `5` | False | `0`, `3` | **False** | GREEN must be True → **reject** |
  | Healthy | `0`, `2` | False | `1`, `0` | True | **accept** |

  Both failure species are separable, by the same function `/do-build` already
  uses to grade the live run. No second matcher, no eyeballing.
- **Confidence**: high (executed)
- **Impact if false**: would have forced a bespoke grader and a much weaker check.

### spike-3: What does `\|` actually do on this machine?

- **Assumption**: "The BRE/ERE trap in instance 1 reproduces here."
- **Method**: prototype (shell, on a two-line fixture `alpha\nbeta`)
- **Result**: **TRUE, and worse than documented.**

  ```
  $ grep --version | head -1
  ugrep 7.5.0 aarch64-apple-macosx +neon/AArch64; -P:pcre2jit; ...
  $ sed --version
  sed: illegal option -- -

  $ grep -c 'alpha\|beta' t.txt     # BRE alternation
  2      exit=0
  $ grep -Ec 'alpha\|beta' t.txt    # ERE: \| is a LITERAL pipe
  0      exit=1
  $ grep -Ec 'alpha|beta' t.txt     # ERE alternation
  2      exit=0
  $ sed -n '/alpha\|beta/p' t.txt   # BSD sed BRE: matches nothing
  (no output)   exit=0
  ```

  `grep` here is **ugrep**, a third dialect alongside GNU and BSD. Instance 1's
  two halves both reproduce verbatim: `grep -E` with `\|` prints `0`, and BSD
  `sed` with `\|` prints nothing while exiting 0.
- **Confidence**: high (executed)
- **Impact**: gives the pattern-sanity detectors their two highest-value rules,
  and adds a Risk (a row's colour is machine-dependent).

### spike-4: Is a fourth table column viable instead of a separate section?

- **Assumption**: "Adding a `Red-state proof` column to the Verification table is
  backward-compatible."
- **Method**: code-read of `parse_verification_table`
- **Result**: **Technically yes, practically no.** `expected_columns =
  max(len(split_row_cells(rows[0])), 3)` reads the count from the header and only
  the first three cells are used, so a fourth column parses. But every pre-existing
  three-column row in the same table would then be `MalformedRow`, and a pasted
  multi-line shell transcript cannot live in a table cell. Rejected in favour of
  spike-1's section.
- **Confidence**: high
- **Impact if false**: n/a.

## Data Flow

```
AUTHORING (this plan's target)
  /do-plan writes docs/plans/{slug}.md
      ├── ## Verification            rows: Check | Command | Expected
      └── ## Two-Pole Proofs         one ### block per non-exempt row
                                     ```gate-proof  RED … --- … GREEN ```
                    │
                    ▼
  scripts/check_two_pole_proofs.py {plan}
      ├── parse_verification_table()          (agent/verification_parser.py)
      ├── classify each row: EXEMPT | NEEDS-PROOF
      ├── pattern-sanity scan on the Command  (BRE/ERE, literal-core-absent)
      ├── match ### block ↔ row by Check name; command must match verbatim
      └── grade both poles with evaluate_expectation()
                RED must grade False   GREEN must grade True   stdout must differ
                    │
      exit 0 = every non-exempt row carries a discriminating proof
      exit 1 = at least one row is ungraded, non-discriminating, or pattern-suspect
                    │
                    ▼
CRITIQUE (spot-check)
  /do-plan-critique Step 2f runs the checker, reports findings as BLOCKER
                    │
                    ▼
BUILD (unchanged by this plan)
  /do-build Step 5.1 runs the live rows through run_checks()
      The proof does not replace the live run. It certifies the live run means
      something.
```

The proof and the live run are deliberately different signals. The live run
answers "is the tree clean right now". The proof answers "would this row have
noticed if it were not".

## Why Previous Fixes Failed

The rule already exists twice and still did not prevent three same-day instances.

- **It was opt-in.** `PLAN_TEMPLATE.md:464` says "When authoring an
  anti-criterion, demonstrate it FAILS…" — conditional on the author choosing to
  write an anti-criterion at all. Instance 1's rows were ordinary positive rows.
  Instance 2 was a pytest guard. Neither is an anti-criterion, so neither was in
  scope of the existing rule.
- **It was one-pole.** Red-only catches "the gate cannot fire". It is silent on
  "the gate convicts correct code", which is what instances 4 and 5 were. A
  red-only author who watched TC5 go red on the unfixed tree would have shipped
  it and burned the round anyway.
- **It was honor-system.** `machine-readable-dod.md:150` is explicit: "The paste
  is non-binding evidence". Nothing reads it. `do-pr-review` "confirms the paste
  is present" — presence, not correctness. A paste of the *green* run pasted
  under a "red" heading satisfies a presence check perfectly.
- **It lived in the wrong place to be found.** The statement is in a doc about
  the DoD parser. An author writing a pytest guard or an AST matcher has no
  reason to open it.

This plan addresses each: mandatory by row shape rather than opt-in, two-pole
rather than red-only, graded by `evaluate_expectation` rather than by presence,
and stated in a doc named for the rule rather than for one of its consumers.

## Architectural Impact

- **New**: one script (`scripts/check_two_pole_proofs.py`), one durable doc, one
  test module, three committed fixtures.
- **Changed shape of a plan document**: a new top-level `## Two-Pole Proofs`
  section. Additive; every existing plan parses unchanged because the checker is
  only ever pointed at a plan explicitly.
- **No new dependency.** The checker imports from `agent.verification_parser`,
  which is already the single reader of these tables. Reusing `evaluate_expectation`
  is load-bearing, not convenience: a second matcher would drift and the proof
  would start grading differently from the build.
- **Global-vs-project split respected.** The rule and the proof format are
  generic and go in `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` and the
  `do-plan-critique` global body. Every concrete invocation goes in
  `docs/sdlc/do-plan.md` and `docs/sdlc/do-plan-critique.md`.

## Appetite

**Medium.** One script of roughly 250 lines, one doc, one test module, and five
small edits to skill and addendum files. The design work is done (see Spike
Results); what remains is writing it and demonstrating it in both states.

The appetite is deliberately not Small: the self-application requirement means
the checker must be watched failing on committed fixtures before it can be
trusted, and those fixtures are part of the deliverable rather than throwaway
scratch.

## Prerequisites

| Prerequisite | Check command | Expected |
|---|---|---|
| Verification parser importable | `python -c "from agent.verification_parser import parse_verification_table, evaluate_expectation; print('ok')"` | prints `ok` |
| Skill audit baseline is clean for do-plan | `python -c "import json,subprocess;o=subprocess.run(['python','.claude/skills-global/audit-skills/scripts/audit_skills.py','--skill','do-plan','--json','--no-sync'],capture_output=True,text=True).stdout;d=json.loads(o)['summary'];print(d['fail']+d['warn'])"` | prints `0` |
| Follow-up issue exists | `gh issue view 2709 --json state -q .state` | prints `OPEN` |

## Solution

### Key Elements

**1. One rule, stated once: demonstrate both poles.**

> A gate ships after it has been run in two states — one where the defect it
> targets is present, one where it is absent — and observed to grade differently.
> Record both runs verbatim next to the gate.

This replaces the existing one-pole red-state rule rather than sitting beside it.
The reasoning is in Why Previous Fixes Failed and in spike-2: red-only is a
strict subset. Watching a gate go red proves it *can* fire; watching it go green
on correct input proves it fires *only when it should*. Instances 1-3 need the
first half, instances 4-5 need the second, and both halves come free from a
single discipline — run it twice, look for a difference. Requiring red only
would leave the repo's two most expensive same-day failures uncovered while
costing the author the same trip.

**2. A machine-readable place to put the two runs.** A top-level
`## Two-Pole Proofs` section (spike-1), one `###` block per row, each block a
single ` ```gate-proof ` fence:

````markdown
## Two-Pole Proofs

### No raw Redis deletes

```gate-proof
pole: RED
why: temporary `r.delete(key)` added to agent/verification_parser.py
$ grep -c "r\.delete\|r\.srem" agent/verification_parser.py
1
exit: 0
---
pole: GREEN
why: main@8c6936c54, violation reverted
$ grep -c "r\.delete\|r\.srem" agent/verification_parser.py
0
exit: 1
```
````

Grammar, deliberately tiny: `pole:` (`RED` or `GREEN`), `why:` (free text, one
line), a `$ ` line carrying the command verbatim, zero or more stdout lines, and
a final `exit: N`. The two poles are separated by a bare `---`.

**3. A checker that grades the paste instead of counting it.**
`scripts/check_two_pole_proofs.py {plan.md}` runs four gates per non-exempt row:

| Gate | Rejects |
|---|---|
| **Coverage** — a `### {Check name}` block exists | an unproven row |
| **Fidelity** — each pole's `$ ` line equals the row's Command after `\|` unescaping | a proof pasted from a different command |
| **Polarity** — `evaluate_expectation(Expected, red_exit, red_stdout)` is `False` **and** `evaluate_expectation(Expected, green_exit, green_stdout)` is `True` | false-green rows (instances 1-3) and false-red rows (instances 4-5) |
| **Discrimination** — RED stdout differs from GREEN stdout | a proof where the same output was pasted twice with the exit codes fiddled |

Polarity is the load-bearing gate and spike-2 measured all three shapes through
it. Discrimination is the backstop for the one way polarity can be satisfied
dishonestly.

**4. Pattern-sanity detectors, run on every row's Command regardless of proof
status.** These catch match-nothing patterns without needing a second tree:

- `grep -E` / `grep -P` / `rg` / `sed -E` / `awk` whose pattern contains `\|` —
  an escaped pipe is a literal in ERE. Measured: `grep -Ec 'alpha\|beta'` → `0`
  (spike-3).
- `sed` without `-E` whose address contains `\|` — BSD sed BRE has no `\|`
  alternation. Measured: prints nothing, exit 0 (spike-3).
- A pattern whose longest literal run (no regex metacharacters) occurs nowhere
  under the repo root — grep-for-a-string-that-cannot-occur. Reported as a
  warning-grade finding with the literal named, because a plan may legitimately
  assert a pattern's absence.
- `python -c` containing `ast.Name` alongside a `.func` attribute access — the
  instance-2 shape, where `node.func.id` cannot see `self.x()` or `module.x()`.
  Reported with the `ast.Attribute` counterpart named.

**5. Which rows need a proof.** Closed and code-defined, no author discretion:

- **Exempt**: whole-suite `pytest` invocations naming no file or node id, and
  `ruff check` / `ruff format --check`. These are the template's boilerplate rows
  whose discrimination is established by the suite itself.
- **Required**: everything else, including a targeted `pytest path::node`
  invocation. A targeted pytest row is almost always a new guard, and instance 2
  was exactly that.

**6. Mutation-check each guard individually.** The third shape in this family is
a gate that is green because it reached no code at all. The durable doc states it
and `docs/sdlc/do-build.md` carries the operative line: mutate the specific
condition each guard asserts, one guard at a time, and re-measure after every
review round. A mutation run reporting everything surviving is evidence about the
harness before it is evidence about the code.

### Flow

1. An author writes a Verification row.
2. If the row is not on the exempt list, they run its command against a state
   where the defect is present and against a state where it is absent, and paste
   both transcripts into `## Two-Pole Proofs`.
3. `/do-plan-critique` Step 2f runs the checker. A non-zero exit becomes a
   BLOCKER finding naming the row and the gate that rejected it.
4. `/do-build` runs the live rows unchanged. The proof is not consulted at build
   time; it has already done its job at plan time.

### Technical Approach

**`scripts/check_two_pole_proofs.py`** — argparse CLI, one positional plan path.

```
--patterns-only    run only the pattern-sanity detectors, skip proof gates
--json             machine-readable findings for the critique path
--list-required    print the rows that need a proof and exit 0
```

Exit codes: `0` clean, `1` findings, `2` the plan could not be read or parsed.

Structure:

- `parse_proof_section(markdown) -> dict[str, Proof]` — the mirror of
  `parse_verification_table`, keyed by the `###` heading text. Uses the same
  `^## Two-Pole Proofs\s*$(.*?)(?=^## |\Z)` shape.
- `Pole` dataclass: `name`, `why`, `command`, `stdout`, `exit_code`.
- `classify_row(check) -> EXEMPT | REQUIRED` — the closed rule from Key Element 5.
- `scan_pattern(command) -> list[Finding]` — the four detectors from Key Element 4.
- `grade(check, proof) -> list[Finding]` — coverage, fidelity, polarity,
  discrimination, in that order, short-circuiting per row so one missing block
  does not emit four findings.

**Two table parsers, two pipe grammars.** Discovered while writing this plan's own
Prerequisites table. `agent/verification_parser.py::split_row_cells` splits on
`(?<!\\)\|` and unescapes `\|`, so a Verification row may carry a shell pipe.
`scripts/check_prerequisites.py:62` splits on **every** `|`
(`row.strip("|").split("|")`) and then regex-extracts the first backtick-quoted
run, so a pipe truncates the command mid-string and the check fails with
`/bin/sh: unexpected EOF while looking for matching backtick` — a parsing error
wearing the costume of a finding about the code, which is exactly the #2570 shape.
Measured on this plan before the fix. The durable doc records this as a named
authoring trap: **write Prerequisites commands pipe-free.** Unifying the two
parsers is out of scope here.

**Import path.** The script imports `from agent.verification_parser import
parse_verification_table, evaluate_expectation, split_row_cells`. Scripts in
`scripts/` already run from the repo root, so no path manipulation is needed;
`scripts/validate_build.py` establishes the precedent.

**Fixtures** at `tests/fixtures/gate_proofs/`, each a minimal well-formed plan
fragment carrying only the sections the checker reads:

| Fixture | Shape | Checker verdict |
|---|---|---|
| `green_discriminating.md` | healthy row, honest two-pole proof | exit 0 |
| `red_match_nothing.md` | instance-1 shape: RED pole grades True | exit 1, polarity |
| `red_convicts_clean_source.md` | instance-4 shape: GREEN pole grades False | exit 1, polarity |
| `red_ere_escaped_pipe.md` | `grep -E` with `\|` | exit 1, pattern-sanity |
| `red_pasted_twice.md` | identical RED and GREEN stdout | exit 1, discrimination |

These fixtures make the self-application permanent rather than a one-time paste:
every build re-runs the checker against a state where it must fail and a state
where it must pass. That pair *is* the checker's own two-pole proof, and unlike a
PR-body blob it cannot rot.

**Documentation placement.** `docs/features/two-pole-gate-proof.md` becomes the
single normative statement, and it absorbs the worked `match count == 0`
green/red example currently in `machine-readable-dod.md`. That doc's "Authoring
Rule: Red-State Proof" section (lines 132-165) is deleted and replaced by a
one-line cross-reference, so the rule is stated exactly once. The BRE/ERE
escaping material at lines 116-131 stays where it is — it is parser mechanics,
not the rule.

**Global-vs-project split.** Per `docs/features/skill-context-convention.md`:

| File | Scope | Gets |
|---|---|---|
| `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` | global, generic | the `## Two-Pole Proofs` section with its grammar; the generalized rule replacing lines 464-465 |
| `.claude/skills-global/do-plan-critique/SKILL.md` | global, generic | a Step 2f structural-check bullet, deferring the invocation to the context file |
| `docs/sdlc/do-plan.md` | repo-only | the concrete `python scripts/check_two_pole_proofs.py` invocation |
| `docs/sdlc/do-plan-critique.md` | repo-only | the Step 2f invocation and the BLOCKER severity mapping |
| `docs/sdlc/do-build.md` | repo-only | the mutate-each-guard-individually line |

`rule_13_coupling_signals` flags the literals `sdlc-tool`, `python -m tools.`,
`reflections.`, `valor-`, `config/identity.json`; `rule_21_bucket_c_coupling`
flags `sdk_client.py`, `SDLC_TARGET_REPO`, and slash-tokens naming project-only
skills. None of the global-body text above contains any of them. A
`docs/features/...` path reference is not a flagged token and
`PLAN_TEMPLATE.md:465` already carries one.

## Failure Path Test Strategy

### Exception Handling Coverage

- Plan path does not exist, or is a directory → exit 2 with the path named, never
  a traceback and never exit 0.
- Plan has a `## Verification` table but no `## Two-Pole Proofs` section at all →
  exit 1 listing every required row, not exit 2. A missing section is a finding
  about the plan, not a tool error.
- A `gate-proof` fence that is unparseable (no `$ ` line, no `exit:`, three poles,
  one pole) → exit 1 with the block named and the specific grammar violation.
  This must never be silently skipped: a skipped block is a row with no proof
  reported as covered.
- `evaluate_expectation` returns `False` for an unrecognized `Expected` string.
  A row whose Expected is a typo would therefore fail polarity on the GREEN pole,
  which is the correct direction — surface it as `unrecognized expectation` so the
  author is not chasing the wrong gate.

### Empty/Invalid Input Handling

- Plan with no `## Verification` section → exit 0 with `no verification table`
  on stderr. Not every plan has one, and failing here would make the critique
  step useless for prose plans.
- A row whose Command is empty → already a `MalformedRow` from the parser;
  surface the parser's own malformed list and exit 1 rather than re-deriving it.
- A proof block with an empty stdout for both poles → discrimination fails
  (identical), which is correct: two empty transcripts prove nothing.

### Error State Rendering

Findings render one per line as
`{plan}:{section} [{gate}] {check name}: {what was wrong}` on stdout, with the
count on the last line. `--json` emits `{"findings": [...], "required": N,
"proved": N}` for the critique path to embed in a BLOCKER finding without
re-parsing prose.

## Test Impact

- [ ] `tests/unit/test_verification_parser.py` — UPDATE if it asserts the
  Verification section runs to end-of-document. Adding `## Two-Pole Proofs` after
  it changes where the section terminates in any fixture that gains one. Verify
  first: the existing fixtures are self-contained strings and most likely
  unaffected.
- [ ] `tests/unit/test_skill_agent_tool_consistency.py` — UPDATE if it enumerates
  the structural-check steps of `do-plan-critique`. Adding Step 2f may shift an
  index. Added by `337cc1f31`, so it is fresh and worth checking.
- [ ] `tests/unit/test_do_plan_critique_barrier.py` — UPDATE if it asserts the
  full set of Step 2 sub-checks.
- [ ] Any test asserting the exact content of
  `docs/features/machine-readable-dod.md` — UPDATE: the "Authoring Rule" section
  is being removed from that file. Grep for the heading string across `tests/`
  before editing.
- [ ] `tests/unit/test_gate_two_pole_proofs.py` — NEW: the checker's own suite,
  one test per detector and per gate, each mutation-checked individually.

## Rabbit Holes

- **Writing a regex engine.** The pattern-sanity detectors are four named
  heuristics against known shapes, not a general "will this pattern match
  anything" analysis. That question is undecidable in the presence of shell
  interpolation and is not worth approaching.
- **Executing the poles.** It is tempting to have the checker *run* the RED pole
  itself. It cannot: the red state is usually a different commit or a temporary
  edit. The checker grades a transcript. Fidelity plus polarity plus
  discrimination is the honest ceiling.
- **Backfilling the whole `docs/plans/` corpus.** Out of scope, tracked in #2709.
- **Making the fence a general-purpose evidence format.** One grammar, one
  consumer. Resist adding fields.
- **Rewriting `machine-readable-dod.md`.** Only the "Authoring Rule" section moves.
  The escaping table and the expectation grammar stay untouched.

## Risks

### Risk 1: The checker becomes another gate that cannot fire

The most likely way this plan fails is by shipping a checker that exits 0 on
everything. Mitigated structurally rather than by care: three of the five
committed fixtures exist specifically to make it exit 1, and Verification rows
assert those exit codes. If a refactor neuters a detector, the corresponding
fixture row goes green and the build fails.

### Risk 2: Row colour is machine-dependent

spike-3 measured `grep` here as **ugrep 7.5.0** — a third dialect alongside GNU
and BSD. A row's pasted GREEN pole may have been produced on a machine whose
`grep` behaves differently from the one running the live check. Mitigation: the
`why:` line is required and should name the machine or commit; the durable doc
states that a proof produced under a different toolchain is worth re-running.
Fully solving this means pinning the toolchain, which is out of scope.

### Risk 3: Adoption friction on plans in flight

Four plans were revised today. Wiring the checker into the critique path means
any *future* critique of a plan with pattern-bearing rows will emit BLOCKERs.
Mitigation: all four have already passed critique, and the checker is not wired
into `/do-build` or any hook, so nothing already in flight can be retroactively
blocked. Escalation is tracked in #2709 and is explicitly gated on the corpus
converging first.

### Risk 4: Authors paste a plausible-looking transcript they never ran

Fidelity catches a transcript from a different command. Discrimination catches
the same output pasted twice. Neither catches a wholly fabricated pair that
happens to grade correctly. This is not fully closable by a checker; it is
closable by the critique reading the `why:` lines. Accepted, and named in the
durable doc so nobody mistakes the checker for proof of honesty.

### Risk 5: Two competing statements of the rule survive the edit

Acceptance criterion 3 says "stated once". The failure mode is deleting the
`PLAN_TEMPLATE.md` tip but leaving `machine-readable-dod.md`'s section, or vice
versa. Mitigated by a Verification anti-criterion asserting the old heading
string occurs nowhere.

## Race Conditions

None. The checker is a synchronous read-only script over one file. It performs no
Redis access, no network calls, and no writes. The only shared-state concern is
the `docs/plans/` commit-on-main convention (#2650), handled by committing each
revision promptly rather than holding the file dirty across an await.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2655] Fixing the AST recurrence guard whose `node.func.id`
  matcher matches zero call sites. This plan makes that class detectable at
  authoring time; it does not repair the specific guard.
- [SEPARATE-SLUG #2649] Fixing `critique_roster_check`'s missing Agent tool and
  the roster-independence property. Already closed; the discipline is what remains.
- [SEPARATE-SLUG #2643] Fixing the watchdog tests that write synthetic CRITICAL
  lines into the production log, and the TC5/TC15 gates on that lane.
- [SEPARATE-SLUG #2709] Escalating the checker from critique-time reporting to
  `/do-build` enforcement and a `.claude/hooks/` validator, plus backfilling the
  existing `docs/plans/` corpus. Deliberately deferred: the hook directory is
  contended, and enforcing on day one would fail every plan already in flight.

## Update System

No update-system changes required. The deliverables are a script under
`scripts/`, a doc under `docs/features/`, test fixtures, and edits to files that
already propagate. `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` and
`.claude/skills-global/do-plan-critique/SKILL.md` are hardlinked to
`~/.claude/skills/` by the existing sync wiring in `scripts/update/hardlinks.py`;
no new directory is introduced, so no `RENAMED_REMOVALS` entry and no
registration step. No new dependency, env var, secret, or Popoto model, so no
entry in `scripts/update/migrations.py`.

## Agent Integration

No agent integration required. The checker is invoked by the plan-critique skill
through its existing Bash access, in the same shape as `check_prerequisites.py`
and `validate_build.py`. It is not a Python API the bridge calls, and it needs no
`[project.scripts]` entry point — adding one would put a plan-authoring
development tool on the same footing as the user-facing `valor-*` commands, which
it is not.

## Documentation

- [ ] Create `docs/features/two-pole-gate-proof.md` — the single normative
      statement of the rule: what a two-pole proof is, the `gate-proof` grammar,
      which rows need one, the four checker gates, the mutate-each-guard-individually
      companion rule, and the worked `match count == 0` example relocated from
      `machine-readable-dod.md`. Includes the three real instances and the two
      inverse instances as the evidence for why the rule is two-pole. Also records
      the named authoring traps: ERE `\|`, BSD sed `\|`, `ast.Name` vs
      `ast.Attribute`, and pipe-free Prerequisites commands
      (`scripts/check_prerequisites.py` splits on every `|`, unlike the
      Verification parser).
- [ ] Update `docs/features/machine-readable-dod.md` — delete the "Authoring Rule:
      Red-State Proof" section (lines 132-165) and its worked example; replace with
      a one-line cross-reference to the new doc. The escaping table and expectation
      grammar stay.
- [ ] Add `docs/features/two-pole-gate-proof.md` to the `docs/features/README.md`
      index table.
- [ ] Update `docs/tools-reference.md` with `scripts/check_two_pole_proofs.py` and
      its flags.
- [ ] Update `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` — add the
      `## Two-Pole Proofs` section after `## Verification`, and replace the
      one-pole tip at lines 464-465 with the generalized rule.
- [ ] Update `docs/sdlc/do-plan.md`, `docs/sdlc/do-plan-critique.md`, and
      `docs/sdlc/do-build.md` with the concrete invocations.

## Success Criteria

- [ ] `scripts/check_two_pole_proofs.py` exists, exits 1 on each of the four red
      fixtures for the stated reason, and exits 0 on the green fixture.
- [ ] The checker exits 0 against this plan document itself, with every
      non-exempt row in this plan's `## Verification` table carrying a real,
      graded two-pole proof.
- [ ] `docs/features/two-pole-gate-proof.md` exists, is indexed in
      `docs/features/README.md`, and states the rule as what to do.
- [ ] The string "Authoring Rule: Red-State Proof" occurs nowhere in the repo —
      the rule is stated once.
- [ ] `.claude/skills-global/do-plan/PLAN_TEMPLATE.md` carries the
      `## Two-Pole Proofs` section and the generalized rule, and the skill audit
      reports zero fail and zero warn for `do-plan` and `do-plan-critique`.
- [ ] `docs/sdlc/do-plan-critique.md` Step 2f invokes the checker and maps a
      non-zero exit to a BLOCKER.
- [ ] `tests/unit/test_gate_two_pole_proofs.py` passes, and every guard in it has
      been mutation-checked individually with the mutation recorded in the PR body.
- [ ] The PR body carries the checker's verbatim output from both the red-fixture
      and green-fixture invocations.

## Team Orchestration

### Team Members

Single builder. The work is one script plus coupled doc edits; splitting it
across agents would cost more in context handoff than it saves in wall time. The
one genuinely parallel slice — writing the five fixtures — is small enough to
stay inline.

| Role | Agent Type | Scope |
|---|---|---|
| Builder | builder | All tasks, sequentially |
| Validator | validator | Task 8 only, read-only |

## Step by Step Tasks

### 1. Branch, worktree, and red baseline

- **Task ID**: baseline
- **Depends On**: none
- **Agent Type**: builder
- **Parallel**: false
- Create the worktree at `.worktrees/gates-that-cannot-fire/`, branch
  `session/gates-that-cannot-fire`.
- Record the baseline: `git rev-parse HEAD`.
- Confirm `scripts/check_two_pole_proofs.py` does **not** exist yet and record
  the `No such file or directory` output. This is the first pole of the
  deliverable's own proof.

### 2. Write the five fixtures first

- **Task ID**: fixtures
- **Depends On**: baseline
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/fixtures/gate_proofs/{green_discriminating,red_match_nothing,red_convicts_clean_source,red_ere_escaped_pipe,red_pasted_twice}.md`.
- Each is a minimal plan fragment with a `## Verification` table and a
  `## Two-Pole Proofs` section, top-level per spike-1.
- `red_match_nothing.md` reproduces instance 1: a `match count == 0` row whose
  RED pole stdout is `0`. `red_convicts_clean_source.md` reproduces instance 4:
  a GREEN pole stdout of `3` against a `match count == 0` expectation.
- Writing the fixtures before the checker is what makes Task 3's first run a
  genuine red.

### 3. Write the checker

- **Task ID**: checker
- **Depends On**: fixtures
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/check_two_pole_proofs.py` per the Technical Approach:
  `parse_proof_section`, `Pole`, `classify_row`, `scan_pattern`, `grade`, and the
  argparse CLI with `--patterns-only`, `--json`, `--list-required`.
- Import `parse_verification_table`, `evaluate_expectation`, and
  `split_row_cells` from `agent.verification_parser`. Do not reimplement any of
  the three.
- Exit codes 0 / 1 / 2 per the Failure Path Test Strategy.
- Run it against all five fixtures and record every invocation's verbatim output.

### 4. Write the test module and mutation-check each guard

- **Task ID**: tests
- **Depends On**: checker
- **Agent Type**: builder
- **Parallel**: false
- Create `tests/unit/test_gate_two_pole_proofs.py`: one test per detector
  (BRE/ERE, BSD sed, literal-core-absent, ast.Name) and one per gate (coverage,
  fidelity, polarity, discrimination), plus the failure-path cases from the
  Failure Path Test Strategy.
- **Mutation-check each guard individually.** For each of the eight, break that
  one condition in `check_two_pole_proofs.py`, run only the tests for it, confirm
  they go red, and restore. Record the eight red outputs. A mutation that leaves
  everything green means the test never reached the code — suspect the harness
  before the code.
- Run with `scripts/pytest-clean.sh tests/unit/test_gate_two_pole_proofs.py -q -n0`.

### 5. Write the durable doc and retire the old rule

- **Task ID**: doc
- **Depends On**: checker
- **Agent Type**: builder
- **Parallel**: false
- Create `docs/features/two-pole-gate-proof.md` per the Documentation section.
  State the rule as what to do. No emdashes.
- Delete `docs/features/machine-readable-dod.md` lines 132-165 ("Authoring Rule:
  Red-State Proof" and its worked example); replace with a single cross-reference
  line. Relocate the worked example into the new doc.
- Add the new doc to the `docs/features/README.md` index table.
- Add `scripts/check_two_pole_proofs.py` to `docs/tools-reference.md`.

### 6. Wire the authoring and critique paths

- **Task ID**: wiring
- **Depends On**: doc
- **Agent Type**: builder
- **Parallel**: false
- `.claude/skills-global/do-plan/PLAN_TEMPLATE.md`: add the `## Two-Pole Proofs`
  section immediately after `## Verification` with the grammar and a worked
  block; replace the one-pole tip at lines 464-465 with the generalized rule.
  Keep the body generic — no repo-coupled tokens.
- `.claude/skills-global/do-plan-critique/SKILL.md`: add Step 2f to the Step 2
  structural checks, generic, deferring the invocation to the context file.
- `docs/sdlc/do-plan.md`: the concrete authoring-time invocation.
- `docs/sdlc/do-plan-critique.md`: the Step 2f invocation and the BLOCKER mapping.
- `docs/sdlc/do-build.md`: the mutate-each-guard-individually line.
- Re-run the skill audit for both edited global skills and confirm zero fail and
  zero warn.

### 7. Fill this plan's own Two-Pole Proofs section

- **Task ID**: self-apply
- **Depends On**: wiring
- **Agent Type**: builder
- **Parallel**: false
- For every non-exempt row in this plan's `## Verification` table, run the
  command in both states and paste the verbatim transcripts into
  `## Two-Pole Proofs`, replacing the stubs.
- The RED state for the checker rows is the baseline commit from Task 1, where
  the script does not exist. The RED state for the doc and template rows is that
  same commit.
- Commit the filled plan on `main`, per the commit-on-main rule.
- Run `python scripts/check_two_pole_proofs.py docs/plans/gates-that-cannot-fire.md`
  and confirm exit 0. This row cannot pass while any stub remains, which is the
  mechanical form of the self-application requirement.

### 8. Final validation and PR body

- **Task ID**: validate-all
- **Depends On**: self-apply
- **Agent Type**: validator
- **Parallel**: false
- Run every row in the `## Verification` table.
- Confirm the Success Criteria.
- PR body must carry, verbatim: the red-fixture invocation and its output, the
  green-fixture invocation and its output, and the eight mutation results from
  Task 4. Shipping a gate-quality checker that has never been watched failing
  would reproduce this issue inside its own fix.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `scripts/pytest-clean.sh tests/unit/test_gate_two_pole_proofs.py -q -n0` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Checker rejects the match-nothing fixture | `python scripts/check_two_pole_proofs.py tests/fixtures/gate_proofs/red_match_nothing.md` | exit code 1 |
| Checker rejects the convicts-clean-source fixture | `python scripts/check_two_pole_proofs.py tests/fixtures/gate_proofs/red_convicts_clean_source.md` | exit code 1 |
| Checker rejects the ERE escaped-pipe fixture | `python scripts/check_two_pole_proofs.py --patterns-only tests/fixtures/gate_proofs/red_ere_escaped_pipe.md` | exit code 1 |
| Checker rejects the pasted-twice fixture | `python scripts/check_two_pole_proofs.py tests/fixtures/gate_proofs/red_pasted_twice.md` | exit code 1 |
| Checker accepts the healthy fixture | `python scripts/check_two_pole_proofs.py tests/fixtures/gate_proofs/green_discriminating.md` | exit code 0 |
| Checker accepts this plan | `python scripts/check_two_pole_proofs.py docs/plans/gates-that-cannot-fire.md` | exit code 0 |
| Rule stated once | `grep -rc "Authoring Rule: Red-State Proof" docs/features/machine-readable-dod.md` | match count == 0 |
| Durable doc indexed | `grep -c "two-pole-gate-proof" docs/features/README.md` | output > 0 |
| Template carries the proofs section | `grep -c "^## Two-Pole Proofs" .claude/skills-global/do-plan/PLAN_TEMPLATE.md` | output > 0 |
| Global bodies stay generic | `python .claude/skills-global/audit-skills/scripts/audit_skills.py --skill do-plan --json --no-sync \| python -c "import sys,json;d=json.load(sys.stdin)['summary'];print(d['fail']+d['warn'])"` | match count == 0 |
| Critique path invokes the checker | `grep -c "check_two_pole_proofs" docs/sdlc/do-plan-critique.md` | output > 0 |

## Two-Pole Proofs

<!-- Task 7 (self-apply) replaces every stub below with the verbatim transcript of
     the command run in both states. The row "Checker accepts this plan" in the
     Verification table cannot pass while a stub remains: an unparseable
     gate-proof fence is a finding, not a skip (see Failure Path Test Strategy).
     Rows absent from this section are the exempt boilerplate: "Lint clean" and
     "Format clean". "Unit tests pass" is a targeted pytest invocation and is
     therefore NOT exempt. -->

### Unit tests pass

```gate-proof
STUB: RED = baseline commit from Task 1, where tests/unit/test_gate_two_pole_proofs.py
does not exist and pytest exits non-zero on collection. GREEN = branch HEAD after
Task 4. Fill with both verbatim transcripts.
```

### Checker rejects the match-nothing fixture

```gate-proof
STUB: RED = baseline commit, script absent, exit 127 or 2. GREEN = after Task 3,
exit 1 with the polarity finding naming the row. Fill with both verbatim transcripts.
```

### Checker rejects the convicts-clean-source fixture

```gate-proof
STUB: RED = baseline commit, script absent. GREEN = after Task 3, exit 1 with the
polarity finding on the GREEN pole. Fill with both verbatim transcripts.
```

### Checker rejects the ERE escaped-pipe fixture

```gate-proof
STUB: RED = the same fixture with the `\|` corrected to a bare `|`, which the
detector must accept (exit 0). GREEN = the fixture as committed, exit 1. This pair
proves the detector reads the pattern rather than the filename.
```

### Checker rejects the pasted-twice fixture

```gate-proof
STUB: RED = the same fixture with one pole's stdout altered so the two differ,
which must exit 0. GREEN = the fixture as committed, exit 1.
```

### Checker accepts the healthy fixture

```gate-proof
STUB: RED = the same fixture with its RED pole stdout replaced by the GREEN pole's,
which must exit 1. GREEN = the fixture as committed, exit 0.
```

### Checker accepts this plan

```gate-proof
STUB: RED = this plan with one proof block deleted, which must exit 1 naming the
uncovered row. GREEN = this plan complete, exit 0.
```

### Rule stated once

```gate-proof
STUB: RED = baseline commit, where docs/features/machine-readable-dod.md still
carries the heading and grep -c prints 1. GREEN = after Task 5, prints 0.
```

### Durable doc indexed

```gate-proof
STUB: RED = baseline commit, grep -c prints 0. GREEN = after Task 5, prints 1 or more.
```

### Template carries the proofs section

```gate-proof
STUB: RED = baseline commit, grep -c prints 0. GREEN = after Task 6, prints 1.
```

### Global bodies stay generic

```gate-proof
STUB: RED = a scratch edit inserting the literal `sdlc-tool` into
.claude/skills-global/do-plan/PLAN_TEMPLATE.md, which must make the sum non-zero.
GREEN = the committed state, sum 0. Baseline measured 2026-08-10 at 8c6936c54:
fail=0, warn=0, sum 0. Revert the scratch edit before committing.
```

### Critique path invokes the checker

```gate-proof
STUB: RED = baseline commit, grep -c prints 0. GREEN = after Task 6, prints 1 or more.
```

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Exemption scope.** This plan exempts only whole-suite `pytest` and the two
   `ruff` rows, so a targeted `pytest path::node` row needs a proof. That is the
   right call for guards, but a plan that adds ten targeted test rows now owes ten
   proofs. Is that the intended cost, or should a targeted pytest row be satisfied
   by a single "the suite went 10 failed then 10 passed" proof covering the group,
   in the shape PR #2706 used?

2. **Fixture location.** `tests/fixtures/gate_proofs/` puts plan-shaped markdown
   under `tests/`. The alternative is `docs/plans/examples/`, which reads more
   naturally but risks a plan-scanning script treating a deliberately-broken
   fixture as a real plan. Preference?

3. **Retiring the paper-trail posture.** `machine-readable-dod.md` currently calls
   the red-state paste "non-binding evidence" and names `do-pr-review` as the thing
   that confirms it is present. This plan replaces presence-checking with grading.
   Should the `do-pr-review` checklist item be removed outright, or kept as a
   human-readable echo of the graded proof?
