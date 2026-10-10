---
tracking: none
slug: c11-governance-precision
type: plan
status: built
critique_rounds: 0
review_rounds: 0
---

# `governance.adds` flags a product's own behavior

A bug fix from the D4 cutover sweep (`docs/plans/d4-cutover-sweep-record.md`),
at stakes 2. It narrows one judgement and adds no check, gate, hook,
validator, round or guard. The CLAUDE.md governance paragraph is unchanged.

## The finding

In the sweep, 4 of the 7 routed tasks stopped for Tom's grant on 18 hunks
that `governance.adds` answered at caution. None of the 18 adds governance.

## What answered in D4

Every `governance.adds` row of the 7 routed tasks went to the real
providers: Jev (`jev-1.13.0`, `api.typesafe.ai`) and the open-weight leg
(`qwen/qwen3-235b-a22b-2507`, `openrouter.ai`). The 127.0.0.1 endpoint at
about 2 ms is the local upstream (`tests/judgement_upstream.py`) that the
forced bare and clarify arms point both legs at; no routed row used it. All
610 rows carry task digest `67f938745187`, the question of run 4, with
`calibrated_sha256` null.

Of the 18 caution answers, Jev gave 12 (p_proceed 0.15 to 0.35), the
open-weight leg 5 (0.02 to 0.05), and one is a Jev abstain (0.54) whose
open-weight call failed.

## The 18 hunks, classified by hand

Each input was rebuilt from the item's base and the routed diff with
`core.judgement_sites.diff_hunks`; all 18 match their row's hunk id and
inputs digest.

| # | item | path:line | answered by | class | reason |
|---|---|---|---|---|---|
| F1 | cut-a | `apps/podcast/services/synthesis.py:504` | Jev 0.19 | false | a docstring saying how bad figures are dropped |
| F2 | cut-a | `apps/podcast/services/synthesis.py:534` | Jev 0.20 | false | the product drops a figure that fails validation, so the rest of the report renders |
| F3 | cut-a | `apps/podcast/services/synthesis.py:521` | Jev 0.19 | false | the call to F2 in the report parser |
| F4 | pop-b | `src/popoto/fields/shortcuts.py:301` | Jev 0.19 | false | a docstring for the capped list |
| F5 | pop-b | `plans/capped-listfield.md:1` | Jev 0.33 | false | a plan document, prose |
| F6 | pop-b | `src/popoto/fields/shortcuts.py:314` | Jev 0.17 | false | docstring lines |
| F7 | pop-b | `src/popoto/fields/shortcuts.py:327` | Jev 0.15 | false | the library refuses a `max_length` that is not a positive integer |
| F8 | pop-b | `src/popoto/fields/shortcuts.py:224` | Jev 0.19 | false | the `CappedList` value class with `push()`, library code |
| F9 | pso-a | `apps/public/views/team.py:2018` | Jev 0.54, abstain | false | a permission check in the product's view |
| F10 | pso-a | `apps/public/templates/team/membership_detail_modal.html:232` | open weight 0.05 | false | the same permission shown in a template |
| F11 | pso-a | `apps/public/views/team.py:2140` | open weight 0.02 | false | a permission check in the product's view |
| F12 | pso-b | `apps/public/templates/report/individual_180_report.html:163` | Jev 0.28 | false | the report page's checklist of what is required |
| F13 | pso-b | same file `:112` | Jev 0.25 | false | a banner telling the user the report cannot be generated |
| F14 | pso-b | same file `:307` | Jev 0.35 | false | text saying why no report exists yet |
| F15 | pso-b | same file `:201` | Jev 0.31 | false | the generate button and its comment |
| F16 | pso-b | `apps/public/views/submission_report/individual_180_view.py:262` | open weight 0.02 | false | builds the checklist rows the page shows |
| F17 | pso-b | same file `:193` | open weight 0.02 | false | a docstring for F16 |
| F18 | pso-b | same file `:731` | open weight 0.05 | false | context values for the page's banner |

Counts: 0 true governance, 18 false positives. By kind: 5 prose (F1, F4,
F5, F6, F17), 7 product validation, permission or error handling (F2, F3,
F7, F8, F9, F10, F11), 6 product display or copy (F12 to F16, F18).

## The cause

The reading put to the test was that the question's text covers ordinary
product behavior. It holds for the 7 validation and permission hunks: the
question asked about "a step that judges work, a request, or an action and
holds, redirects, or refuses it", and a view refusing a user without a
permission is that, read literally.

It does not explain the other 11. Prose and display were already outside
the question. Those were flagged on context: the input is the `git diff -W`
hunk, which carries the whole enclosing function, or for a template the
whole file (F12 to F15 share one 46 KB input). The legs judged what the
context does, not what the added lines add. The 18 hunks are 9 distinct
inputs for this reason.

What the leg sees is the path, the hunk and every changed path. No
repository name reaches it. A `project` input is not added: Tom's rulings
already label code in Valor's own repository that makes a product correct
as not governance (`signals-surrogate-refusal`,
`fresh-verdict-parser-rewrite`), so the line falls by what the code does,
not by repository, and the paths already show a product.

## The fix

The question in `core/judgement_tasks.py` (`GOVERNANCE`) now:

- asks about steps over how work is done and approved: a step that looks at
  a request given to the agent, a plan, a change, a commit, a merge, a send,
  or an agent's commands or work, and can stop it, send it back, or
  redirect it, or a step such work must pass (a pre-commit or CI gate, a
  hook, a review or approval step, a merge check, a validator over what an
  agent runs or writes, a guard row), in code or in instructions a turn
  follows (a skill, brief, persona, or prompt);
- says a refusal added inside such a step adds to it, and so does calling,
  registering, or wiring an existing such step at a new place, or
  narrowing an exemption from one;
- says to judge what the added lines add, with the other lines as context;
- says such a step counts in any repository, the agent's own (Valor's)
  included;
- names the behavior of the software being built, serving its own users,
  as none of these, even where it refuses: input validation, permission and
  visibility checks, eligibility rules, error handling, and what its users
  see; this never covers a step over the work in the agent's own pipeline,
  kernel, skills, persona, or guards;
- puts outside the wording of a message whose sending is already decided,
  even one asking for an approval;
- keeps the existing exclusions for tests, correctness code (a lock or a
  transaction that makes a second run of the same work wait, or answers it
  that one is already running, and a parser that refuses input it cannot
  read; a step that refuses work on a judgement about the work is not
  correctness code) and prose.

The labels say the same. Process code stays in, in every repository; the
11 Tom positives are still answered caution by Jev on every run below.

The input shape is unchanged.

## Question for Tom

Is a project's own product behavior (validation, permissions, error
handling serving that product's users) outside the governance paragraph?

Assumed answer, which the fix carries out: "Product behavior in a project
Valor works on (validation, permissions, error handling serving that
product's users) is not governance; the paragraph governs steps over how
work is done and approved."

## Calibration

The case file `~/src/valor-demo/items/judgement/governance.adds.json` was
backed up as `governance.adds.json.20261010T055515Z` (SHA-256
`15939e08…`). The 9 distinct D4 inputs are appended as drafted `false`
cases, so the file holds 67: 11 `true` and 29 `false` from `tom`, 27
`false` drafted (SHA-256
`919a92bdf3c260ed785676ba5212065d52e39c70febde9f2a00391e1e9434111`). A
second file of all 79 distinct routed D4 inputs, drafted `false`, was
measured beside it and is not a case file.

Every run used `python -m core calibrate` against the test database
`valor_rebuild_test_c11`, never the real ledger. Runs 5 to 11 are on the 67
cases and are the site's calibration runs (`docs/plans/m1-4b-records.md`).
The 79-input runs are measurement. All calls on both legs were answered;
none was refused. Spend was about $0.31 over the eight runs.

| run | question | calibration task | Jev wrong | open weight wrong | Brier Jev, open weight | entry check |
|---|---|---|---|---|---|---|
| 5 | `67f938745187` (before) | `59201209e819` | 15: 6 `tom` negatives, all 9 D4 | 8: 2 `tom` negatives, 6 D4 | 0.0831, 0.1146 | false |
| | same, 79 D4 | `f73c6227e234` | 17 (3 caution, 14 abstain) | 9 | 0.0746, 0.1088 | |
| 6 | `ebffabc54915` (round 1) | `e0d2a2ce4bd0` | 0 | 4: 1 `tom` positive, 1 `tom` negative, 2 D4 | 0.0097, 0.0546 | false |
| | same, 79 D4 | `79a2ed64257c` | 0 | 3 | 0.0035, 0.0360 | |
| 7 | `c90e46687683` (round 2) | `e6098cc44f97` | 0 (1 abstain on a `tom` positive) | 6: 1 `tom` positive, 3 `tom` negatives, 2 D4 | 0.0110, 0.0808 | false |
| | same, 79 D4 | `44ea5f716b55` | 1 abstain | 3 | 0.0038, 0.0363 | |
| 8 | `ca8231d8b95d` (round 3) | `6ef3f1a2104a` | 1 abstain on a `tom` negative | 2: `lock-in-expiry-runner`, 1 D4 | 0.0107, 0.0283 | false |
| | same, 79 D4 | `7181f2cf1a20` | 0 | 2 | 0.0038, 0.0247 | |
| 9 | `eda026ef59e4` (round 4, draft d) | `34ea6bc64f35` | 0 | 6: 4 `tom` negatives (`lock-in-expiry-runner`, `lock-prose-in-routines-doc`, `router-session-advisory-lock`, `fresh-verdict-parser-rewrite`), 2 D4 | 0.0088, 0.0818 | false |
| | same, 79 D4 | `0c585c2504ba` | 1 abstain | 3 | 0.0037, 0.0361 | |
| 10 | `f9034dc3cf49` (round 4, draft e) | `5dd580773c08` | 0 | 4: 2 `tom` negatives (`router-session-advisory-lock`, `fresh-verdict-parser-rewrite`), 2 D4 | 0.0084, 0.0550 | false |
| | same, 79 D4 | `465829809fe4` | 1 abstain | 4 | 0.0037, 0.0497 | |
| 11 | `183b1eac42cc` (round 4, built) | `2bc99f121005` | 0 | 5: 3 `tom` negatives (`lock-prose-in-routines-doc`, `router-session-advisory-lock`, `signals-surrogate-refusal`), 2 D4 | 0.0088, 0.0693 | false |
| | same, 79 D4 | `ef26bd2a5a95` | 1 abstain | 4 | 0.0034, 0.0495 | |

The full task digest of the built question is
`183b1eac42cca02886714dc089c14150b9be36485f889e113c26b94e5c0c3144`
(run 11, event 14864).

On the built question every one of the 67 cases and the 79 D4 inputs ends
right as the site runs it: Jev answers all 67 right, and its one abstain on
the 79 (a plan document, 0.63) goes to the open-weight leg, which answers
it right. The review's 11 probes, asked three times, end right at the site
every time. The entry check asks both legs to be right on every `tom` case
on their own; Jev passes it, and the open-weight leg answers
`lock-prose-in-routines-doc`, `router-session-advisory-lock` and
`signals-surrogate-refusal` at caution (0.05). The open-weight leg varies
between samples; its `tom` misses moved among these lock and refusal cases
from run 8 to run 11.

The site is uncalibrated: `GOVERNANCE.calibrated` is `None` and the docs
runner is unregistered. A changed question is asked fresh, since reuse is
keyed on the task digest, so the next run of a governed diff asks the
built question.

## Patch rounds

- Round 1: the question asks about steps over how work is done and
  approved, names the product's own behavior as outside, and says to judge
  the added lines.
- Round 2: the open-weight leg answered Tom's
  `judge-thin-request-to-clarify` positive at proceed, so the question
  names a request sent back for clarification, adds eligibility rules and
  banners, and puts message wording outside.
- Round 3: the open-weight leg answered Tom's `337aba233:core/guards.py:139`
  positive (a grant refused) at proceed, so the question says a refusal
  added inside such a step adds to it, and names a lock that turns a second
  run away as correctness code.
- Round 4, from the review. The question says calling, registering or
  wiring an existing step at a new place, or narrowing an exemption from
  one, adds one; names instructions a turn follows (a skill, brief,
  persona, or prompt) as a place such a step is written; says such a step
  counts in any repository, Valor's own included, and the product
  exclusion never covers a step over the work in Valor's own pipeline,
  kernel, skills, persona, or guards; limits the message exclusion to a
  message whose sending is already decided; and names only a lock or a
  transaction that makes two runs take turns as correctness code, as in
  Tom's 10-07 ruling. Drafts were measured on the review's 11 probes
  (`~/src/valor-build-notes/review-c11-probes.json`), each asked two or
  three times. Two drafts were dropped: "judge what the change makes, its
  removed and added lines read together" made the open-weight leg answer
  caution on the product permission probe in 4 of 4 samples, and "never
  covers the agent's own pipeline, kernel" made it answer caution on 4
  `tom` negatives in Valor's `core/` (run 9). Without "narrowing an
  exemption", the narrowed-exemption probe ended at proceed in 2 of 3
  samples. Spend about $0.25. Tom labelled `router-session-advisory-lock`
  false, and that lock returns `already running` to a second run; with
  "turns a second run away" gone, the open-weight leg answered it caution
  on runs 9 and 10. Valor's call: Tom's label wins, so the question names
  a lock or a transaction that makes a second run of the same work wait,
  or answers it that one is already running, as correctness code, and a
  step that refuses work on a judgement about the work as not. On run 11
  the open-weight leg still answers that case caution (0.05).

## Follow-ups

- Distinct hunks in one function or template share one `-W` input, so
  they get one answer.
- `judgement_sites.diff_hunks` skips a hunk with no added lines, so a
  removal-only hunk is never judged. Removing an approval step needs no
  grant, but removing an exemption from one adds instances of it, for
  example deleting `if is_docs(path): return False` above
  `return require_tap(path)`. A separate item; it is not part of this fix.
- A hunk that adds only a guard row, with no name saying what it checks
  (`guard_id`, `incident`, `mission_items`, `expires`), is missed at the
  site in some samples on both the round 3 and round 4 questions: Jev
  abstains (0.40 to 0.59) and the open-weight leg answers proceed in 1 of 2
  samples. A named row, a row inside `SEEDED`, or a row in a list of guards
  is answered caution by both legs. The probes are in
  `~/src/valor-build-notes/c11p/probes-guard.json`.
