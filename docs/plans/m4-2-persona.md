---
tracking: none
slug: m4-2-persona
type: build
status: merged
critique_rounds: 1
review_rounds: 1
---

# 4.2: the persona

Task 4.2 of [valor-rebuild.md](valor-rebuild.md), milestone 4. It puts
Valor's one identity, voice, conduct, and delivery format into `persona/`
and has the kernel render that text at the top of every turn, so the
ledger shows which persona each turn ran under. The governing doc is
[persona.md](../persona.md); the evidence it rests on is
[rebuild-demonstration.md](rebuild-demonstration.md) and
[rebuild-baseline.md](rebuild-baseline.md).

Planned on the rebuild branch at 4cbc33669 (328 test functions).

## Goal

One identity in every turn, with the conduct the evidence asked for
(Mission items 2 and 3; the constraint "One identity"; the README's
property **Correctable**: the persona lives in git and changes by
review).

## Where the code stands

- `tasks.dispatch` renders a turn's text as: the Brief head, the
  corrections in force, then the channel (or the verdict channel for a
  fresh session) and the stage file. It returns the text, the correction
  numbers, and the text's digest.
- `runs.run_turn` records that text on `turn.started` as `brief`, with
  `brief_sha256` and `corrections`.
- The persona is the harness default, `system_prompt="You are Valor."`,
  in both `claude_code.turn` and `claude_code.workspace_turn`. The harness
  prefixes it to the dispatched text when it builds the argv
  (`harnesses/claude_code.py`, the `--system-prompt` and
  `--append-system-prompt` values), after the digest is taken.
  `turn.started` records the argv (`core/runs.py`, `run_turn`), so the
  line is in the ledger; but `brief_sha256` does not cover it, and no
  digest names the persona a turn ran under.
- The governance paragraph reaches every turn as correction 1, recorded
  by `migrate` from the first `**Governance` line of `CLAUDE.md`
  (`corrections.governance_paragraph`).
- `persona/` holds only its README.

## Done, as evidence

From valor-rebuild.md, 4.2, each with what closes it here.

| Done item | Evidence | Waits for |
|---|---|---|
| `persona/` holds identity, voice, conduct, and delivery format | the six files below, merged; the tests on their rendering | nothing |
| The kernel renders it at the top of every turn, before the Brief and the corrections | `tasks.dispatch` puts the rendered persona first; tests on a working turn and on a fresh session show the order, through `runs.run_turn` on real Postgres, and the argv carries the dispatched text with nothing prefixed | nothing |
| The governance paragraph appears in it verbatim | the rendered persona holds `CLAUDE.md`'s paragraph byte for byte, read from `CLAUDE.md` at render time; a test changes a copy of `CLAUDE.md` and sees the change in the next render | nothing |
| The `turn.started` digest covers it | `brief_sha256` is the digest of the whole text the turn reads, persona included; `turn.started` also carries `persona_sha256`; tests show a changed persona file changes both on the next turn of the same task | nothing |
| The conduct says to ask before building when a request leans on an example or names existing UI | `persona/conduct.md` carries that habit; a test reads it in the rendered text | nothing |
| An emulator run over the baseline items shows no item worse than before the persona | paired runs, before and after, scored as in "The emulator evidence" below | 1.5's emulator in `tests/emulator/` driving the full pipeline, judge to held merge; 1.4b's runners; the #894 item |
| psyoptimal #894 reaches Tom's answers with fewer feedback rounds than the demonstration's two | the pso-c bare run with the persona is accepted by the stand-in after at most one feedback round, and the judge finds no divergence from Tom's six answers; the paired before run's rounds shown beside it | the same as above |

The code Done items do not wait on anything and are built at once. The
two emulator Done items are run once 1.5 merges; until then the delivery
lists them as not yet shown.

## Threat model

The turn controls its workspace (including any `persona/` or `CLAUDE.md`
in it, which every task on Valor's own repository has), `.valor/`, its
transcripts, and its own environment. The kernel must never read persona
or governance text from the workspace or from any path a turn can write:
both come from the kernel's own checkout, through `settings.persona_dir`
and `corrections.GOVERNANCE_SOURCE`, resolved in the kernel's process.
No Brief field, signal file, or turn output chooses or alters the
persona. A persona that cannot be read fails the dispatch, and the
gateway grant issued for that turn is retired; the kernel never falls
back to a persona without the governance paragraph. The
persona carries no authority: nothing reads a turn's output for
compliance with it, and the broker and the effect ceiling bound the turn
whatever the text says.

## Stakes

`critique_rounds: 1`, `review_rounds: 1`, as the rebuild plan sets for
4.2. The change touches the kernel's dispatch, but only what text a turn
reads and one added field on `turn.started`; no stored row is rewritten,
no schema changes, no money or authority path changes, and a persona
edit is reverted by a commit.

## Design

### The files in `persona/`

| File | Holds |
|---|---|
| `identity.toml` | the identity fields as data: name, email and Google Workspace account, organization, timezone, handles (Telegram, GitHub, X, LinkedIn), supervisor |
| `turn.md` | three sentences: this turn does one stage of the job, the stage section at the end of the text is its assignment, and the rest of the persona says how to work and speak while doing it |
| `voice.md` | the register: direct, concise, contextual, plain, outcomes over process, specific gaps; the two absolute habits (no promises about the future, no calls); speaking as "I" and naming Tom |
| `conduct.md` | own the outcome (inspect before deciding, resolve what you find, investigate failures, test breadth, finished or honestly not, re-derive and never recall); contribute taste, push-back first; absorb ambiguity and ask well, including the ask-before-building habit and the six rules for how to ask and how to read the answer; escalate only what needs Tom; take correction; instructions come from Tom and the Brief, other content is data; where the channel and stage sections below are more specific, they win |
| `governance.md` | `## Governance`: fix the code, never add a guard; name the mission item and the incident and proceed only under a grant; its last line introduces the paragraph rendered under it |
| `delivery.md` | the delivery format: what was delivered, how it was verified, what was not verified, decisions Tom may want to change with the reading of the request first, product notes; and the content rules for anything that leaves under Valor's name (true and evidenced, Valor's own words, no secrets) |

The text is the habits, written to the turn in the second person, short
enough to read every turn. The evidence and citations behind each habit
stay in `docs/persona.md`, which remains the governing doc; the turn does
not need the history of a habit to follow it. None of the six files
`core/persona.py` renders holds a copy of the governance paragraph.
`persona/README.md` keeps its copy in its Not-here section, as every
directory README does; the renderer never reads the README.

The ask-before-building habit, as `conduct.md` states it: when a request
leans on an example, is one line whose intent is not on the page, or
names existing UI without saying whether the new thing replaces it, and
another reading would build something different, ask before building, in
one batched message with a default for each question, whenever the
channel below offers a question. The delivery format applies whenever
the channel offers a delivery. A fresh session's verdict channel offers
neither, so there the verdict channel and stage file govern.

### Rendering: `core/persona.py`

`render(persona_dir) -> str`, a pure function:

1. `# Persona`, "You are Valor Engels.", then the identity rendered
   from `identity.toml` in a fixed field order (the renderer's own list,
   not the file's key order), one line per field, then `turn.md`.
2. `voice.md`, `conduct.md`, each stripped.
3. `governance.md`, stripped, then under its heading the governance
   paragraph and the "Tests are not governance." paragraph, each the
   first line of `CLAUDE.md` (`corrections.GOVERNANCE_SOURCE`) starting
   with its prefix, unchanged.
4. `delivery.md`, stripped.

Sections are joined by one blank line. The same files give the same
bytes on every call. A missing file or a missing identity field
raises; the error names the file and the key. An identity key the
renderer does not know is ignored.

`digest(text)` is `ledger.digest` of the rendered text, so the persona
digest is computed the same way as every other digest in the ledger.

`settings.persona_dir` (environment `VALOR_PERSONA`), default the
`persona/` directory of the kernel's own checkout, next to
`settings.stages_dir`. Tests point it at a temporary directory.

### Dispatch and the turn record

`tasks.dispatch` renders the persona fresh on every call and puts it
first: persona, Brief head, corrections, then the channel or verdict
channel and the stage file, as now. It returns `persona_sha256` beside
`text`, `corrections`, and `sha256`. A fresh session (critique, review,
docs) gets the persona too: every turn is Valor's.

`runs.run_turn` adds `persona_sha256` and `persona_bytes` to
`turn.started`. `brief` and `brief_sha256` keep their names, so every
reader and every stored row stays as it is; `brief` is the whole text
the turn reads. The persona then appears twice in each `turn.started`
row, in `brief` and inside `argv`, as the Brief already does; the row
grows by the persona's size, recorded as `persona_bytes`.

`run_turn` issues the turn's gateway grant before it dispatches. When
`dispatch` raises (an unreadable persona among other causes), `run_turn`
retires that grant before the error leaves, as it does for a stopped or
calibration task.

### The harness

`claude_code.turn` and `claude_code.workspace_turn` lose their
`system_prompt` parameter and the "You are Valor." default. The
dispatched text goes to `--system-prompt` (the test-only `turn`) or
`--append-system-prompt` (`workspace_turn`) exactly as dispatched, so
what the ledger records is byte for byte what reaches the model.
`--system-prompt-snapshot off` already re-renders the appended text on
every resumed turn, so a persona change reaches a running session at its
next turn.

### Correction 1 stays

Correction 1 is the governance paragraph as a ledger row, and the
ledger is append-only; withdrawing a correction is not built. Every turn
therefore carries the paragraph twice: in the persona and as correction
1. The duplicate costs a paragraph of tokens per turn and changes
nothing a turn may do.

## The emulator evidence

Run once 1.5's emulator is merged, with its driver, its Opus-class
stand-in, and the Sonnet judge, carrying each item through the full
pipeline, judge to held merge.

**Items.** The six baseline items (pso-a, pop-a, cut-a, pop-b, pso-b,
pop-c) and psyoptimal #894. #894 has no item in `valor-demo/items`: the
demonstration ran in a clone whose history held other items' answers.
This task builds it as `pso-c` under the emulator doc's rules: a clean
base (#894's base tree minus plan documents, squashed, no history), the
answer key as Tom's six answers from the Notion card, the reference diff, the hidden test from
the PR where one runs, and the base-tree leak check. The key's six lines
come from the Notion card itself when the build can reach it; otherwise
from rebuild-demonstration.md's "Against the reference answers" table,
each line marked as condensed from the card, as the emulator doc marks
inferred lines. The key is never called verbatim unless it came from the
card. The item is data in the experiment directory, outside the
repository.

**Arms.** Each item runs with the judge forced to the label the judge
site gave it in 1.3's seed run (thin: pop-b, pop-c, pso-c; precise: the
rest), which is what the routed pipeline does and keeps the judge's
variance out of the comparison (the seed labels are m1-3-judgement.md,
section 4). pso-c also runs bare (judge forced
precise): that is the demonstration's own condition, and the one where
only the persona can lead Valor to ask before building.

**Before and after.** Each run is paired: the same item and arm, once on
the rebuild branch head this task rebases onto (the persona as "You are
Valor."), from its own worktree, and once on this task's candidate. Same
emulator, stand-in, judge, and model.

**Worse**, per item, after the persona against before: fewer hidden
tests passing; fidelity or correctness lower by more than one point; or
more feedback rounds. One rerun of the after run is allowed per item
before an item counts as worse, as in 1.5's gate, since one-point
differences at n = 1 are noise. Questions asked, simplicity, spend, and
wall time are recorded per run as information.

**#894.** The pso-c bare run with the persona reaches a delivery the
stand-in accepts after at most one feedback round, and the judge's
divergences name none of Tom's six answers. The paired before run's
feedback rounds are shown beside it. The judge's fidelity and whether
the run asked before building are recorded. The routed pso-c run is
recorded as information.

**An item worse, or #894 missing its bar,** sends the task back to
patch: the persona text is revised and the affected pair is rerun. It
is not a stop.

The results go in this file under "Emulator runs", one row per run, with
the task id, outcome, feedback rounds, questions, judge scores, hidden
tests, and metered spending.

## Tech debt absorbed

- The persona outside the turn digest: the harness prefixes "You are
  Valor." after `dispatch` takes the digest, so `turn.started` does not
  record what the turn read. The prefix goes; the digest covers the text.
- `system_prompt` defaults in two harness builders that nothing passes;
  the parameter goes.
- `persona/README.md` says persona "may import `core/`" and is "imported
  by" core and harnesses, and its Scope says core renders it into
  "supervisor and brief alike"; it holds no code and there is no
  supervisor prompt. Both are corrected: persona holds text that
  `core/persona.py` renders into every turn's dispatched text.
- docs/persona.md ("What the current kernel renders", "What the turn
  record keeps") and docs/harnesses.md describe the single-line persona
  as the status quo; the docs stage brings them to the code.

## Left out

- Whether the appended system prompt reaches subagents Claude Code starts
  inside a turn (milestone 3 absorbs it).
- Any check of a turn's output against the persona; persona.md says none
  is added.
- Rendering the persona into judgement calls, the stand-in, or the
  emulator's judge: those are classifiers and measurements, not Valor's
  turns, and a persona there would change the judgement task digests.
- A command that prints the persona; `core.persona.render` is one call.
- Preferences learned about Tom (milestone 6).
- Trimming stage files that overlap the delivery format (`build.md`'s
  exit evidence names what `done.md` holds); each states its own exit,
  and the persona says how to write it.

## Tests

New in `tests/test_persona.py`, on real Postgres where a turn runs:

- The rendered persona holds every identity field from `identity.toml`,
  in the renderer's order whatever the file's key order.
- Two renders of the same files are byte-identical; `persona_sha256` is
  the same for two different tasks.
- The rendered persona holds `CLAUDE.md`'s governance line byte for
  byte; with `GOVERNANCE_SOURCE` pointed at a copy whose paragraph is
  edited, the next render carries the edit.
- The rendered persona holds `CLAUDE.md`'s "Tests are not governance."
  line byte for byte, once, under the governance heading after the
  governance paragraph; an edited copy shows in the next render. A
  missing `CLAUDE.md`, or one without either line, raises
  `PersonaUnreadable`.
- None of the six files the renderer reads holds the governance
  paragraph (one source); `persona/README.md` is outside the claim and
  keeps its copy.
- An `identity.toml` with a key the renderer does not know renders
  without error, the same bytes as without the key.
- The rendered persona carries the ask-before-building habit (its
  example and existing-UI clauses) and the delivery format's
  "not verified" and "reading of the request first" items.
- `dispatch` for a working turn: the text starts with `# Persona`, and
  `# Persona` precedes `# Brief`, which precedes `# Corrections from
  Tom`, which precedes the channel and the stage file.
- `dispatch` with `fresh="review"`: the same order, with the verdict
  channel in place of the working channel.
- Through `runs.run_turn` (the existing stand-in command): `turn.started`
  carries `persona_sha256` equal to `persona.digest(persona.render(...))`,
  and `brief_sha256` equal to the digest of `brief`, which contains the
  persona.
- A persona file edited between two turns of one task: the second
  `turn.started` has a new `persona_sha256` and a new `brief_sha256`, and
  its `brief` holds the edit (re-rendered, never copied at start).
- Not the workspace's persona: a task whose workspace holds a `persona/`
  directory and a `CLAUDE.md` with different text, including a different
  governance paragraph; the dispatched text holds the kernel's, and none
  of the workspace's.
- A persona directory missing `conduct.md`, and one whose
  `identity.toml` lacks `supervisor`: `run_turn` raises naming the file,
  no `turn.started` row is written, nothing is spawned, and the turn's
  gateway grant is retired (a call on its URL is refused).
- The argv: `--append-system-prompt`'s value equals the dispatched text
  exactly; it does not start with "You are Valor." and holds nothing the
  ledger's `brief` does not.
- Correction 1 and the persona both carry the paragraph: the dispatched
  text holds it twice, once under the persona's `## Governance` heading and once as correction
  1, and the correction numbering is unchanged.

Changed:

- `tests/test_session.py`: the argv assertion expects the dispatched text
  alone.
- `tests/test_corrections.py`: the system prompt assertion expects the
  brief alone and the persona at its top.

Live, in `tests/test_live_turn.py` under `VALOR_LIVE`: one real
tool-less turn (`claude_code.turn`) asked who it is and who it reports to answers as Valor
Engels and names Tom Counsell, with the turn metered through the
gateway, and its `persona_sha256` is the digest of the first
`persona_bytes` bytes of its `brief`. It shows the rendered text reaches the model; it measures
nothing about conduct. `tests/test_live_turn.py` and
`tests/test_live_session.py` are rerun under `VALOR_LIVE`.

## Files it changes

| File | Change | Other tasks touching it |
|---|---|---|
| `persona/identity.toml`, `turn.md`, `voice.md`, `conduct.md`, `governance.md`, `delivery.md` | new | none |
| `tests/test_live_session.py` | asserts every push and the merge held for Tom, not an exact count | none |
| `persona/README.md` | Scope and Imports corrected | none |
| `tests/test_live_turn.py` | the live identity turn | none |
| `core/persona.py` | new: `render`, `digest` | none |
| `core/tasks.py` | `dispatch` renders the persona first, returns `persona_sha256` | 4.1 (the Brief's parent), 2.1, 1.4d |
| `core/runs.py` | `persona_sha256` and `persona_bytes` on `turn.started`; the grant retired when dispatch raises | 1.4b, 2.1, 3 (harness version on `turn.started`) |
| `core/settings.py` | `persona_dir` | most tasks |
| `harnesses/claude_code.py` | `system_prompt` parameter removed | 3 (the harness contract), 1.4b |
| `tests/test_persona.py` | new | none |
| `tests/test_session.py`, `tests/test_corrections.py` | assertions above | 1.4b, 3 |
| docs (docs stage): `docs/persona.md`, `docs/harnesses.md`, `harnesses/README.md` | the rendered persona as the status quo | 3 |

No schema change and no migration. Per the waves in
`.claude/skills/build/SKILL.md`, 4.2 builds now and merges after 1.5,
rebasing onto each earlier merge that touches the same files.

## Rollout

1. `python -m core backup`.
2. Fast-forward the rebuild branch to the docs head, push, and pull the
   kernel's running checkout.
3. No schema change and no restart: the persona is read from disk at
   every dispatch, so the next turn of every task, running ones included,
   carries it. If the resident kernel (2.1) is merged by then, the same
   holds, since `dispatch` reads the files per turn.
4. On the next real turn, compare its `turn.started` `persona_sha256`
   with `core.persona.digest(core.persona.render(settings.persona_dir))`
   from the kernel checkout, and record both under "Merged".
5. Run the emulator evidence above if it has not run on the candidate,
   and record it under "Emulator runs".

## Decided by default

Recorded, not asked; each is reversible.

- The persona is six files in `persona/`: identity as TOML data, the
  rest as Markdown text; the renderer is `core/persona.py`, since core
  renders it and `persona/` holds no code.
- The governance paragraph is rendered from `CLAUDE.md` at render time,
  not copied into `persona/`, so it cannot drift; persona.md's "in the
  same words" holds by construction.
- Correction 1 stays, so the paragraph reaches each turn twice:
  `CLAUDE.md` requires it in the persona, and correction 1 is a separate
  mechanism; the ledger is append-only and withdrawal is not built. In
  the persona it has its own `## Governance` heading.
- `persona/turn.md` is kept: three sentences saying a turn does one
  stage.
- The identity's email label is "Email and Google Workspace account";
  "Email and Google Workspace:" would read as the Brief's `Workspace:`
  line.
- The persona text holds the habits; the evidence behind each stays in
  `docs/persona.md`.
- The persona renders into every harness turn, fresh sessions included,
  and into no judgement call, stand-in call, or emulator judge call.
- `turn.started` gains `persona_sha256` beside `brief_sha256`, so the
  ledger shows the persona version per turn without re-rendering; field
  names already stored are kept.
- An unreadable persona fails the dispatch; there is no fallback text.
- No length limit on the persona: any limit would be an invented cap.
  `persona_bytes` on every `turn.started` is metering information only.
- The identity is persona.md's Identity table (docs/persona.md lines
  16 to 33): Valor Engels, valor@yuda.me, yudame, UTC+7 (Asia/Bangkok),
  @valorengels on Telegram, GitHub, X, and LinkedIn, supervisor Tom
  Counsell.
- A turn reads the habits only: persona.md (line 409) keeps the persona
  "short enough to render on every turn", and the evidence stays in the
  doc.
- The voice is persona.md's Voice section as written.
- The ask-before-building habit and the delivery format apply when the
  channel offers a question or a delivery; the channel and stage
  sections win where they are more specific.
- The emulator comparison is paired (rebuild head before, candidate
  after), with the judge forced to each item's seed label, plus pso-c
  bare as the #894 evidence; "worse" and the one rerun follow 1.5's gate.
- This task builds the pso-c item for #894, under the emulator doc's
  clean-base and leak-check rules.

## Emulator runs

None yet; they run once 1.5 is merged.

## Critique round 1 (of 1): revise

Rounds spent; every finding is built in, and the task goes to build.

1. The one-source claim and test would fail on `persona/README.md`.
   Handled: limited to the four rendered files; the README keeps its copy.
2. The questions for Tom were not intent questions persona.md leaves
   open. Handled: deleted; identity, habits only, and voice are under
   Decided by default with persona.md's lines.
3. The pso-c key was called verbatim. Handled: from the card when
   reachable, else from rebuild-demonstration.md marked as condensed.
4. The #894 bar was acceptance alone. Handled: at most one round and no
   judge divergence from the six answers, beside the before run's rounds.
5. The gap was misstated: `turn.started` records the argv with "You are
   Valor.". Handled: the gap is the digest; `persona_bytes` recorded, and
   the persona appearing in both `brief` and `argv` noted.
6. Fresh sessions offer no question or delivery. Handled: those habits
   apply when the channel offers them; channel and stage win where more
   specific; "in any stage" dropped.
7. Missed cases. Handled: the gateway grant is retired when dispatch
   raises, with a test; unknown identity keys raise, with a test; the
   live turn and session tests rerun; a worse item sends the task to
   patch, not a stop.
8. `persona/README.md` Scope and Imports, and the forced-label source.
   Handled: both corrected; the source is m1-3-judgement.md section 4.

## Build

Every finding above is built in. The code Done items are built and
tested. The two emulator Done items, and the pso-c item they use, wait
for 1.5.

Live, under `VALOR_LIVE`:

- `tests/test_live_turn.py`: three of three pass, the identity turn
  included.
- `tests/test_live_session.py`: on the rebuild head without the persona,
  Haiku writes greeting.txt and requests the push during the plan stage
  in three of four recorded runs; the test still passes, nine of nine.
  With the persona as committed it passes thirteen of seventeen. Three
  failures request the push a second time in the build stage, so three
  effects are pending where the test expects two; one skipped the
  question and ran to the build on its first run. The same test with
  parts of the persona emptied: `voice.md`, `conduct.md` and
  `delivery.md` empty, four of four; `conduct.md` empty, four of four;
  only "Own the outcome" removed, four of five. With "Own the outcome"
  opening on the job as a whole ("you carry all of it"), two of six.

The rendered persona is 10,154 bytes.

Under Tom's rule that a limit or refusal needs a source or a function,
the raise on an unknown identity key (critique finding 7) is dropped: it
had neither. Unknown keys are ignored and a test shows an extra key
renders. The raise on a missing file or a missing identity field stays:
without them there is no text to render.

After the rebase onto the rebuild head, the extra pending effect in the
live session was read from the ledger of twelve metered runs: two of them
held two pushes. In one, the plan stage pushed one commit and the build
stage pushed a new one; in the other, two plan-stage turns each pushed a
different commit. Each extra push names a new SHA. A build turn that asked
again for an earlier push with the same payload got the existing pending
effect back: the broker already answers a repeated request with its
standing outcome (`broker._prior`), and `tests/test_kernel.py` asserts it
for a held `act`. So nothing in the kernel changes. A stage pushing its
own new commits is legitimate, and `tests/test_live_session.py` no longer
counts exactly two pending effects. It asserts that one merge and at least
one push are held for Tom, that nothing has an outcome before Tom's tap,
that every outcome follows an approval, that the branch holds the last
approved push, and that the last push is in the merged candidate.

`persona/governance.md` gives the governance paragraph its own
`## Governance` section, after the conduct; the paragraph itself is still
read from `CLAUDE.md` at render time, byte for byte. Conduct no longer
ends with a `### Governance` subsection.

Twelve metered runs with the persona and the new assertions: eleven pass.
The twelfth failed after release: the plan turn requested a push of a SHA
no commit has (a near copy of the real one), the push failed at release,
and the origin had no `valor/greeting`. That is the model's error in the
request, listed under Follow-ups.

## Patch round 1

Review round 1 answered `changes`; docs were fast-forwarded in first.

1. `persona/turn.md` said "leave the files it names", which reads as
   "do not touch"; it now says "write the files it names".
2. `CLAUDE.md`'s "Tests are not governance." paragraph renders under the
   governance heading, after the governance paragraph, read from
   `CLAUDE.md` at render time byte for byte like the governance
   paragraph, so a turn does not read a new test as a check needing a
   grant. `governance.md`'s lead line now introduces both. Tests show it
   once in the persona, under the heading, following an edited copy, and
   that no rendered file holds a copy.
3. `persona/README.md` says every fresh session (critique, review,
   docs) gets the persona.
4. `core/persona.py` reads both paragraphs itself and raises
   `PersonaUnreadable` for a missing `CLAUDE.md` or a missing line,
   naming which, so `dispatch`'s docstring is true; tests cover all three.

From the test check: `tests/test_live_session.py` drops the circular
count of held against pending effects and asserts that the effects with
an outcome are exactly the effects Tom approved, both directions. No
live run: the change to the persona text is one verb and one paragraph
already in force at the top of `CLAUDE.md`.

## Follow-ups

- In the live session, Haiku writes the file and requests the push in
  the plan stage, with or without the persona (three of four runs on
  the rebuild head without it). The plan stage building is not fixed
  here.
- A turn may request a `push_branch` of a SHA that names no commit; the
  push fails only at release, after Tom's tap. Seen once in twelve live
  runs.

## Checks after patch round 1, at 9b65a74fe (review round 1 of 1)

- Test: `pass`. 526 passed, 8 skipped. Both `CLAUDE.md` paragraphs render
  once each, byte for byte, under the governance heading;
  `PersonaUnreadable` names a missing file or line; the workspace's own
  `CLAUDE.md` and `persona/` are never read. No live spend.
- Review: `pass`; governance boolean no; no invented caps. Every round 1
  finding is fixed.
- Docs: `updated`, e958ec8be on `m4-2-docs2` (`docs/persona.md` names both
  paragraphs).

## Delivery: merge held for Tom's tap

The candidate is `m4-2-docs2`. Two notes from the review, not blocking:
the comment in `persona/identity.toml` says every key must be one the
renderer knows, while unknown keys are ignored; `persona/turn.md` assumes
a stage section ends the text, which a turn with no workspace lacks.

## Tom's feedback (2026-10-03)

Tom, asked to tap the five passed deliveries (3a, 3c, 4.2, 1.4d, 1.4c part one): "All five". The same day he ruled that merges are Valor's call from now on (valor-rebuild.md, Tom's feedback of 2026-10-03), so this tap is the last one asked.

Merge: tapped.

## Merged

Merged 2026-10-03 at `5582c4bf2`: the docs head (candidate `9b65a74fe`) rebased onto 3c's merge. Two doc tables (`docs/architecture.md` execution records, `docs/data.md` `turn.started` and `turn.ended`) were folded by hand to carry both 3a's and 4.2's fields; `docs/architecture.md` was brought to 599 lines by rewording the dispatch paragraph and the one-provider limit (the gateway meters OpenAI now; the reviewer is still one harness). Suite on the folded head: 698 passed, 11 skipped, 2 failed with "the task's Postgres did not start" (the known port race while ten builders ran suites); both passed alone. Ruff as above. Backup first: `valor_rebuild-20261003T133820Z.dump`.

Rollout:

1. Backup: done.
2. Fast-forwarded and pushed; the kernel checkout is `~/src/valor-rebuild` itself, `uv sync` run.
3. No schema change and no restart.
4. The next real turn: the 3c live turn on the merged head, three turns (critique, build, build), each `turn.started` with `persona_sha256` `f64edfd3fe0e3c0bdd9b29c91d2bd36fee21aee53143246f5031acd204851392` and `persona_bytes` 10,390; `core.persona.digest(core.persona.render(settings.persona_dir))` in the checkout gives the same digest and size. Equal.
5. Emulator evidence: waits on 1.5's merge, as the plan says; the lead runs it then and records it under "Emulator runs".
