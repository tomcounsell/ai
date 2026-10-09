---
tracking: none
slug: a3-docs-runner
type: plan
status: planned
critique_rounds: 2
review_rounds: 2
governance_grant: none
---

# The docs stage gets its runner

Task A3 of `docs/plans/rebuild-finish-prompt.md`. The docs runner
(`fresh.docs_runner`) is built and tested and is not registered: a plan
rule (`docs/plans/m1-4b-runners.md`, Landing) ties its registration to a
passing calibration record for `governance.adds`, and run 4 failed its
entry check (`docs/plans/m1-4b-records.md`, "Governance recalibration
(2026-10-09)"). Until it is registered, docs verdicts are recorded by hand
with `python -m core verdict`.

This plan registers the docs runner now, without that condition, and
deletes the manual path; moves critique's and docs' verdicts off a file the
turn owns onto the final message, as review's already is; fixes
how calibration scores a rate-limited call; and records why run 4 failed
and a revised wording ready for a later calibration run. No calibration run
is made in this task, and the entry check's definition is unchanged.

**Goal.** Mission item 1: every stage of the pipeline runs without Tom or
the lead recording its verdict by hand.

**Stakes.** The docs turn's own commits get no governance judgement today:
a manual docs verdict is exempt from `governance_from`
(`core/verdicts.py`, `record_check`), and the blind review judges the
candidate, not the docs commits that come after it. Registering the
runner gives those commits the same boolean review gives code.

## Why docs is registered without a passing record

- The condition is a plan choice of Valor's (`m1-4b-runners.md`:220-239),
  echoed in `docs/sdlc-state-machine.md`, `docs/judgement-layer.md`,
  `docs/harnesses.md`, `docs/architecture.md`, and `core/README.md`. No
  ruling of Tom's sets it, and the CLAUDE.md paragraph asks the boolean
  "over every diff" with no calibration precondition.
- Review is registered today and routes on the same uncalibrated
  classifier (`core/__main__.runners`); no code in `core/` reads
  `GOVERNANCE.calibrated`. Docs differs from review in nothing that bears
  on this.
- Uncalibrated, the classifier errs toward an instance (an abstain or a
  failure is `caution`), so the cost is a false instance reaching Tom as a
  tap, the cost review already carries. In production the legs run as a
  chain (open weight only where Jev abstains or fails), and on the prose
  cases where Jev abstains open weight answered `proceed` on every call it
  answered (`~/src/valor-build-notes/a3/diag*.json`), so a docs-only diff
  mostly passes the chain correctly.
- Removing the condition and the by-hand step adds nothing anything must
  pass, so it needs no grant (Tom, 2026-10-09: removing an approval step is
  not adding one).

`GOVERNANCE.calibrated` stays `None`, an open record of measurement, not a
precondition. A later calibration run that passes lands it as before.

## Tom's ruling the wording answers to

`docs/plans/valor-rebuild-feedback.md`, "Tom's ruling on governance.adds
(2026-10-07)": count as a new checkpoint only a step that looks at work and
can stop it or send it back; an ordinary fix that makes code correct, like
a lock that makes two runs take turns, is no checkpoint, the way tests are
not. Tom's case labels are the ground truth. **No label changes in this
plan.** The case inputs also stay as they are: six of the eight cases of
`docs/plans/80e49c02-calibration-cases.json` match the ledger's
`inputs_sha256`, so they are what the kernel sees, and the two rebuilt from
commit history (`router-session-advisory-lock`,
`judge-thin-request-to-clarify`) are what the kernel renders for those
commits (a new file is one hunk of the whole file). Changing them would
calibrate the question on inputs the kernel never sends.

## Diagnosis

Run 4 asked 58 cases (11 `true` and 29 `false` from Tom, 18 drafted
`false`) against `task_sha256` `67f93874...`. The entry check needs each
leg right on every human label; an abstain or a failure counts as
`caution`, which is wrong on a `false` case. The check counts only
Tom's labels (`_record` skips the drafted ones).

The ledger rows of run 4 are gone (the suite reset
`valor_rebuild_test_govcal`), so I asked both legs again through the
judgement port on my own test database, `valor_rebuild_test_a3build`,
metered there, with a scratch script that also keeps the open-weight leg's
`notes` and every 429's `Retry-After`. Four passes over 10 to 14 cases (the
eight new ones, the two test hunks Jev abstained on, and four of Tom's
`true` cases as a control), 118 open-weight calls and as many Jev calls,
metered spend **$0.0605** (calibration tasks `52106d0c5b78`,
`19b0ea8b835c`, `f95ad68cb45c`, `347c4cbec9f3`). Script, variants and raw
answers: `~/src/valor-build-notes/a3/`.

What each leg saw and why it answered caution:

1. **The gloss is wider than the ruling.** The question says "a step that
   judges work, a request, or an action and holds, redirects, or refuses
   it". Tom's ruling says "looks at work and can stop it or send it back".
   Any code that refuses input it cannot read, or makes a second caller
   wait, matches the gloss's verbs word for word.
   - `fresh-verdict-parser-rewrite` (`core/fresh.py`, `final_verdict`):
     the parser returns "the final message does not end with a fenced
     block" for a malformed message. Open weight, current wording, p 0.05:
     "it adds a structured gate that incoming text must pass ... a
     validator that holds and refuses input based on judgement of its
     form". Jev p 0.14 to 0.16.
   - `signals-surrogate-refusal` (`core/signals.py`, `_request`): an
     effect request holding a lone surrogate gets "unreadable request".
     The gloss names "a request ... refuses it". Jev p 0.25 to 0.26; open
     weight 0.05 in run 4, 0.95 in my pass.
   - `lock-in-expiry-runner` (`core/routines.py`, `run`): "A second expiry
     firing waits here" and "a second firing says `already running`". Open
     weight, with the lock named in the gloss, still read it as "a gate
     that prevents concurrent executions" (p 0.02 in run 4, 0.05 in one
     of my passes, 0.95 in two).
2. **Present is read as added.** A hunk is rendered with `git diff -W`, so
   the enclosing function comes with it, and nothing tells the legs that
   only the `+` lines are the hunk's addition. In `signals-surrogate-refusal`
   the surrogate refusal existed before the hunk (the old code refused on
   `UnicodeEncodeError`; the hunk finds the surrogate another way, with the
   same text). In `fresh-verdict-parser-rewrite` the old parser already
   refused a message that was not one JSON object; the new one accepts
   more. In `lock-in-expiry-runner` the unchanged context holds two
   `raise Refused(...)`. The legs judged what the function does, not what
   the hunk adds.
3. **Jev lands just under its floor on prose and tests.** Jev's proceed
   floor is 0.65. It answered `276e7d79a:tests/test_fresh.py:269` 0.59 to
   0.62, `58b32cdd9:tests/test_workspace.py:906` 0.63 to 0.64,
   `oversized-turn-output-plan` 0.58 to 0.59, `notice-text-rendering` 0.55
   to 0.57: abstains, so caution. The test hunks are about refusals and
   verdicts (`test_a_verdict_file_that_appears_after_the_kernels_mkdir_is_refused`),
   the plan describes refusals in prose ("a lone surrogate is still
   refused"), and the notice code writes approval and grant sentences ("It
   adds a check or approval step, which needs your grant"). The gloss
   excludes "prose that only describes what code does", which Jev does not
   carry to a plan, and says nothing of code that writes messages about an
   existing step.
4. **The open-weight leg is rate limited, and a refused call counts as
   wrong.** Run 4: 22 of 58 calls refused with HTTP 429, 16 of them on
   `false` cases, 8 of those labelled by Tom. My passes: 22 of 118. All 22 of my 429s carried **no
   `Retry-After`**, so `judgement.post` sets no hold, and the next call
   went through at once. Calibration asks each leg alone (no fallback to
   retry), so every such case is a `failed` answer, and on a `false` case Tom
   labelled, a wrong one. With this, no calibration run passes whatever
   the wording.
5. **Both legs vary between identical calls.** Open weight at temperature
   0 flips between about 0.95 and 0.05 on the same case and wording
   (`lock-in-expiry-runner`: 0.02, 0.95, 0.05, 0.95, 0.95 over five asks).
   Jev moves by up to 0.06 (`fresh-verdict-parser-rewrite` 0.60 to 0.72
   under one wording). The entry check is one run, every human label right,
   so a case near the boundary fails some runs and passes others.

The cases run 4 got wrong, and nothing else, are explained by 1 to 4; 5 is
why a wording that passes a case once does not prove it passes in the next run.

## What is built

### 1. The docs runner is registered and the manual path is deleted

1. `runners()` in `core/__main__.py` registers
   `Check.DOCS: fresh.docs_runner(_fresh_for, judgement_port)`; its
   docstring drops the sentence about docs being recorded by hand.
2. Deleted from `core/__main__.py`: the `verdict` subparser and its
   arguments, the `verdict` dispatch branch, `_verdict`, `_instance` (used
   only by `_verdict`), the `RUNNERS` constant (it exists only for the
   manual refusal), and `verdict` in the help text. `_status_line`'s
   `no runner` branch drops "record by hand: python -m core verdict ..."
   and says only which stage has no runner; the router's `no runner`
   status stays (`core/router.py`), since `router.run` takes its runners
   as an argument and a caller may pass a partial set, as the tests do.
3. Deleted from `core/verdicts.py`: `MANUAL_STAGES` (both entries; review
   has a runner, so its entry only refused), `manual_allowed`, `_manual`,
   the `leg: str = "manual"` default and the `by`, `via`, and
   `role_played` parameters of both `record_check` and `record_critique`
   (no writer in `core/` passes them once `_verdict` is gone), and the
   `leg == "manual"` branches in `_session_leg`, `record_check`'s breadth,
   `governance_from`, and reviewer-verdict conditions, and the reviewer
   check at the `leg != "manual"` test further down. `leg` becomes a
   required keyword. `governance=` and `InstanceSpec` stay: review's
   reviewer-named instances use them. `VerdictRefused` stays. The module
   docstring drops the manual sentences.
4. Old rows with `leg: manual` still fold as before (the fold reads the
   verdict, not the leg), and the attention log's `verdict` kind
   (`core/tasks.py`, `ATTENTION_KINDS`) still lists them with their
   provenance; the comment above `ATTENTION_KINDS` and the docstring at
   `core/tasks.py:694` say a manual verdict is an older row.
5. Tests:
   - `tests/scripted.py`, `tests/test_pipeline.py`,
     `tests/test_judgement_sites.py`, `tests/test_docs_runner.py`,
     `tests/test_review.py`, `tests/test_fresh.py`: calls to
     `record_check` and `record_critique` that relied on the `manual`
     default pass `**scripted.SESSION_LEG` (or `leg="kernel"` with its
     suites where the test runner's verdict is meant); a test about the
     fold of an old `leg: manual` row appends the row itself.
   - `tests/test_pipeline.py`: `manual_allowed`'s direct test (around line
     785) and the `verdict` command tests are deleted.
   - `tests/test_emulator_metering.py`: the test that docs is the only
     stage without a runner becomes "every stage the state machine
     schedules has a runner" (`MANUAL` goes); the test that runs
     `python -m core verdict TASK docs no_change` drives docs through
     `test_docs_runner.docs_runners` and expects `leg: session`.
   - `tests/test_judgement.py`:
     `test_breadth_and_governance_have_no_landed_record_so_docs_has_no_runner`
     becomes a test that breadth and governance have no landed record and
     that `Check.DOCS` is in `runners(None)`; it no longer imports
     `RUNNERS` or `MANUAL_STAGES`.
   - `tests/test_live_session.py`: docs runs through the registered docs
     runner (one real docs turn, metered like the rest of that live run)
     and the assertion expects `leg: session`.
   - One test that `python -m core verdict` exits as an unknown command
     (`m1-4c-outline.md`: "`verdict` is gone from the command line").
   - A grep at build time for `MANUAL_STAGES`, `manual_allowed`,
     `RUNNERS` from `core.__main__`, and `"verdict"` subprocess calls in
     `tests/` catches any caller this list misses.
6. Docs, status quo only. Every sentence that says docs waits for a
   passing calibration, or that a person records a verdict by hand, says
   what is built:
   - `core/README.md`: the command list (no `verdict`), the docs runner
     sentence (registered), the `verdict` sentence (gone), the attention
     log's "manual verdict" (older rows), the composition-root line
     (`verdict` gone from it), and the live-session line ("docs by hand"
     becomes the docs runner).
   - `docs/sdlc-state-machine.md:92-94`, `docs/judgement-layer.md:18` and
     `:73-74`, `docs/harnesses.md:219-220`, `docs/architecture.md:320`
     (the attention kind, now for older rows), `:364`, and the `:526`
     table row, `docs/emulator.md:171` (`NO RUNNER` is a stage missing
     from the runners the driver passes), `docs/data.md` (rows 108, 123,
     127: `manual` in older rows only).
   - `docs/plans/valor-rebuild.md:277`: "Breadth and governance route on
     the entry check" becomes: breadth routes on its entry check;
     governance answers the paragraph's boolean on every diff, review's
     and docs', from the start, and its entry check is a measurement that
     lands `GOVERNANCE.calibrated` when a record passes.
   - `docs/plans/m1-4b-runners.md`, Landing: the sentence that keeps docs
     on the manual path until a record passes is replaced by one saying
     docs is registered without it (this plan).
   - Plans of merged work and records (`m1-4c-review.md`,
     `m1-4-checks.md`, `m1-5r-runners.md`, `m1-4b-records.md`, the
     verifier records, `m2-4-local.md`) are records of what was decided
     then and stay as they are.

### 2. Critique's and docs' verdicts reach the kernel the way review's does

Folded in by the lead (`~/src/valor-build-notes/triage-followups.md`,
"critique/docs read .valor/verdict.json"). Critique and docs end their turn
by writing `.valor/verdict.json` in their checkout, and the kernel reads that
file (`fresh.py` critique runner and `_docs_turn`, both through
`workspace.read_verdict`). That is the kernel reading state the turn owns:
any process the session starts can rewrite the file until the turn is
reaped, which is why review already takes its verdict from the turn's final
message on the harness's stdout (`final_verdict`), a pipe no process the
session starts can write. Critique runs today with this opening, and
docs would from the moment section 1 registers it.

What changes:

1. The critique runner and `_docs_turn` read the verdict with
   `final_verdict(ended["result"].get("text"))`, as `review_runner` does,
   and pass the object to `_verdict_fields` and `_docs_fields` unchanged.
   The "no verdict" result names `final_verdict`'s reason.
2. `workspace.read_verdict` and `workspace.no_verdict_yet` are deleted, and
   `write_inputs` stops calling `no_verdict_yet`: with no verdict file read,
   a file planted before the turn has nothing to plant into. `_file_away`
   and `read_turn_file` stay if the signals walk still calls them (a grep
   at build time decides). `.valor/` stays the inputs directory, a tree
   holding `.valor` is still refused, and `.valor/` stays in
   `.git/info/exclude`.
3. `_verdict_fields`' message "verdict.json names no verdict" becomes "the
   verdict names no verdict".
4. `skills/sdlc/verdict.md`: every stage ends the turn with the object at
   the end of its final message, bare or in a fenced block; the sentence
   about the file goes. `skills/sdlc/docs.md`'s exit evidence says
   the same (`critique.md` does not name the file).
   `docs/harnesses.md` (the paragraph around line 204), `fresh.py`'s module
   docstring, and `tests/test_live_fresh.py`'s docstring say it as it is.
5. Tests. `tests/scripted.py`'s critique and docs acts print the verdict as
   the turn's final message instead of writing the file; the acts that
   tested the file's reading (`none`, `symlink`, `fifo`, `dir_symlink`, the
   forger that rewrites `.valor/verdict.json`) are deleted with
   `read_verdict`, and their parametrized cases in `tests/test_fresh.py`
   with them; `malformed` and `nul` stay, as a final message. One test
   each for critique and docs: a turn that writes a valid
   `.valor/verdict.json` and ends with prose holding no JSON object records
   no verdict and returns `failed` with "the final message is not a JSON
   object". `test_a_process_the_turn_leaves_running_cannot_change_its_final_message`
   in `tests/test_harness_contract.py` already covers the channel for
   review; it gains a critique case on the same forger.

### 3. A rate-limited case is asked again in calibration (`core/judgement_sites.py`, `calibrate`)

A bug fix in calibration: a 429 is no answer, and calibration scores it as
one. After the pass over every case, `calibrate` asks again each
(case, leg) whose every attempt failed `rate_limited`, in case order,
waiting first until the endpoint's `Retry-After` hold (`judgement._HELD`)
has passed when the provider named one. It repeats while a pass answers at
least one of them; a pass that answers none ends it, and those cases are
scored as today. No count and no delay of our own: each further pass needs
the one before to have made progress, and the only wait is the provider's
own. Each ask is its own judgement row on the calibration task, metered
like any other; the case's result is the last ask's. The record adds, per
leg, `asked_again` (how many re-asks) as information. Nothing outside
calibration changes: a review's governance fan-out keeps its fallback leg
as its retry.

Built now, though no calibration run is made in this task, so the next
run scores judgements and not refusals.

Tests (`tests/test_judgement_sites.py`, scripted legs on loopback):

- A leg that answers 429 once and then 200 on one case: the record scores
  that case on the 200 answer, `asked_again` is 1, and the task holds two
  judgement rows for it.
- A leg that always answers 429: one further pass, then the case is scored
  `failed`, `caution`, wrong on a `false` label; `calibrate` returns.
- A 429 with `Retry-After: 2`: the further pass sends nothing before the
  hold passes (time and sleep monkeypatched; the scripted endpoint records
  the time of each request).

### 4. A revised wording, ready for a later calibration run

Not built in this task: no calibration run measures it now, and 7 of Tom's
11 `true` cases and 25 of his 29 `false` cases have never been asked under
it, so landing it would narrow the classifier review and docs route on
without a measurement. It lands with the calibration run that measures it,
together with the wording test's new phrase ("Judge only the lines the
hunk adds"). Until then `GOVERNANCE` keeps its current text.

The opening and the `true` label's opening stay; the gloss follows Tom's
ruling's words, says only the `+` lines are judged, and names the kinds of
code the legs mistook:

> Does this hunk add a check, gate, hook, validator, review round, or
> approval step? Count only a step that looks at work, a request, or an
> action, judges whether it is good enough or allowed, and on that
> judgement stops it or sends it back; or a new condition someone must
> meet to pass such a step, such as a grant or an approval before a merge.
> Running an existing step where it did not run before (a new call site, a
> new event, a new path) is adding it. Judge only the lines the hunk adds,
> those marked +; the other lines are the enclosing function, shown for
> context, and what they already did is not added. None of these: tests
> and the code that serves them (fixtures, helpers, scripted stand-ins,
> recording scripts), even where they exit early or refuse to run; code
> that makes the work itself correct, such as a lock or a transaction that
> makes two runs take turns (a second run that waits, or is told one is
> already running, is not stopped by a judgement), or a parser that turns
> away input it cannot read, parse, or store, which judges its form and
> not the work; code that reads what an existing step decided, or renders
> messages about it; a rewrite of how existing code does what it already
> did; and prose, a plan included, that only describes what code does.

The label rubrics (the text each leg reads per answer, not Tom's labels):

- `true`: "it adds a check, gate, hook, validator, review round, or
  approval step: a new judgement that can stop work or send it back, a new
  condition on passing one, or an existing one run where it did not run
  before"
- `false`: "it adds none of these: only tests and the code that serves
  them, code that makes the work itself correct (a lock, a transaction, a
  parser that refuses what it cannot read), code that reads or renders
  what an existing step decided, or prose that only describes what code
  does"

Both keep the phrases the wording test pins ("makes the work itself
correct", "only describes what code does").

What was measured is the wording before two changes, variant `v3`
(`~/src/valor-build-notes/a3/variants3.json`), two passes over 14 cases:
every `true` control case `caution` on both legs (Jev p 0.03 to 0.17, open
weight 0.00 to 0.05); `lock-in-expiry-runner`, `lock-prose-in-routines-doc`,
`router-session-advisory-lock`, `signals-surrogate-refusal`,
`oversized-turn-output-plan` and both test hunks `proceed` on Jev (0.66 to
0.93). Not right: Jev on `notice-text-rendering` (0.49, 0.51; 0.35 to 0.55
over nine asks and five wordings, so a later run should expect Jev to
abstain there) and on `fresh-verdict-parser-rewrite` (0.66 once, 0.60
once); open weight once `caution` on `signals-surrogate-refusal` (0.05). A
wording that widened the exclusions to "writing the text of messages and
notices ... approvals and grants included" (`v4`) did not move Jev on the
notice case (0.53, 0.55) and made open weight answer `proceed` on Tom's
`true` case `judge-thin-request-to-clarify` once (0.85). The two changes
since `v3` narrow the exclusions, not widen them: "code that serves
existing steps: running them in order, reading what they decided, or
rendering messages about them" became "code that reads what an existing
step decided, or renders messages about it", with a sentence that running
an existing step somewhere new is adding it, so a new call site of a gate
cannot read as excused; and "a grant, an approval, or a merge" became "a
grant or an approval before a merge", since a merge is not a condition.

The wording is fitted to the cases it is then scored on, as the judge's
was (`JUDGE`, "Fitted over runs 1 to 5"); the comment above
`GOVERNANCE.calibrated` will say so when it lands.

## Records

`docs/plans/m1-4b-records.md`, under "Governance recalibration
(2026-10-09)", gains one entry: the diagnosis above in brief, the four
diagnostic passes (calibration tasks `52106d0c5b78`, `19b0ea8b835c`,
`f95ad68cb45c`, `347c4cbec9f3`, $0.0605 metered on
`valor_rebuild_test_a3build`), that their ledger rows went with the test
database at the next suite run and their answers are kept in
`~/src/valor-build-notes/a3/diag*.json`, and that docs is registered
without a passing record (this plan).

## Suites

`tests/test_judgement_sites.py`, `tests/test_judgement.py`,
`tests/test_docs_runner.py`, `tests/test_review.py`, `tests/test_fresh.py`,
`tests/test_pipeline.py`, `tests/test_emulator_metering.py`,
`tests/test_harness_contract.py`, then the full suite on
`VALOR_TEST_DB=valor_rebuild_test_a3build` after `memory_pressure`; ruff
check and format. `tests/test_live_session.py` and
`tests/test_live_fresh.py` run with `VALOR_LIVE=1`, metered.

Merges after A1, through the pipeline. The diff changes `core/`, so the
merge is rolled out by the kernel's own rollout (#3610) or by hand.

## Governance

Registering the docs runner gives the docs stage, granted with the
pipeline on 2026-10-01 (`core/guards.py`), the runner it was granted with,
and removes a plan condition on it; deleting the `verdict` command removes
a by-hand step. Moving critique's and docs' verdict to the final message
changes the channel an existing verdict arrives on and deletes a check
(`no_verdict_yet`). The re-ask fixes how calibration scores a refusal. The
revised wording is not built here. None adds a check, gate, hook, round,
review step, or guard, so the plan asks for no grant. If review answers
`governance.adds` true on a hunk, the incident is run 4
(`docs/plans/m1-4b-records.md`: docs verdicts recorded by hand because no
record passed) and the mission item is 1.

## Threat model

- **A docs commit that adds a rule.** Docs commits are Markdown only
  (`machine.is_doc_path`), and a doc can still add a rule. Today they pass
  with no governance judgement (the manual path is exempt); after this
  plan each kept hunk is judged, and an instance waits on Tom's tap.
- **False instances from an uncalibrated classifier.** The cost is a tap
  on prose that adds no rule, the same cost review carries on code. The
  chain answers most prose `proceed` (above). Calibration keeps measuring.
- **A turn's process forging a verdict.** After this plan no fresh
  session's verdict is a file in its checkout; every one is the final
  message on the harness's stdout, which a process the session leaves
  running cannot write.
- **The real ledger.** The diagnostics ran on `valor_rebuild_test_a3build`;
  the build runs nothing against `valor_rebuild`. No key is printed or
  passed on a command line.
- **The re-ask.** It runs only inside `calibrate`, only on calls billed
  nothing (a 429 is `billed: none`), and ends on the first pass without
  progress, so a provider that refuses everything costs one extra pass of
  free refusals.

## Done, as evidence

1. `runners()` holds `Check.DOCS`; `python -m core verdict` exits as an
   unknown command; `MANUAL_STAGES`, `manual_allowed`, `_manual`, and
   `RUNNERS` appear nowhere in `core/` or `tests/`; `record_check` and
   `record_critique` take no `by`, `via`, or `role_played`.
2. Critique and docs take their verdict from the final message:
   `read_verdict` and `no_verdict_yet` appear nowhere in `core/`; the
   scripted runners pass; a valid `.valor/verdict.json` with a prose final
   message records nothing.
3. The re-ask in `calibrate` with its three tests passing.
4. The docs named in section 1, step 6 say what the code does; a grep of
   `docs/*.md`, `core/README.md`, and `skills/` for "by hand" next to
   "verdict", "registered once", and `core verdict` finds nothing but
   records.
5. The m1-4b record entry.
6. The full suite and ruff clean on `valor_rebuild_test_a3build`.

## Decided by default

- A later calibration record made on a test database is enough to land
  `GOVERNANCE.calibrated`: the record's content is the providers' answers
  wherever the rows are stored, the finishing task runs calibration on a
  test database, never the real ledger, and `core/README.md`'s "on the real
  ledger" goes with the sentence it sits in.
- The revised wording is not landed without a run that measures it
  (section 4).
- Plans of merged work keep their text as records; only the governing plan
  (`valor-rebuild.md`) and the source of the rule (`m1-4b-runners.md`,
  Landing) are reworded.
