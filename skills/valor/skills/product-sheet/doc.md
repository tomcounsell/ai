# {{product}} product backlog: how to read and work it

- The backlog Sheet: {{sheet}}
- Its Drive folder: {{folder}}

This Doc is for people and for other agents (Gemini, Figma, any tool that
reads Google Drive). It explains the Sheet beside it: what each tab holds,
what every column and status means, and how a raw idea becomes something
ready for engineering to build. Valor (valor@yuda.me) keeps the Sheet and
regenerates this Doc from one template, so edits made here are overwritten;
put corrections in the product room instead.

## How to read the Sheet

- Every tab has exactly one header row (row 1) and one record per row
  below it. No merged cells, and no meaning is carried by colour, images or
  notes: the cell text is the data.
- Items are keyed by `id` (P-001, P-002, ...), never reused. The same id
  joins Queue, Pitches, Blocked and Archive.
- Read **Goal** first: every bet serves the product goal written there.
- **Queue** is the one ordered list of everything, bugs and features
  together. Its row order is the priority: nothing moves up without
  something else moving down.
- **Columns** is this Doc's column table, as a tab, for a tool that reads
  only the Sheet.
- A chart tab (such as Dashboard), when there is one, is drawn from Queue
  and holds no data of its own. Queue is the source.

## Tabs

- **Columns**: what each column of each tab means (the table below).
- **Goal**: one row per field. The product goal and its outcome metric,
  the Now, Next and Later themes, the strategy notes, the WIP limit, and
  the link to this Doc.
- **Queue**: one row per item (a bug or a feature).
- **Pitches**: one row per item being shaped or shaped: the written case
  for building it.
- **Blocked**: one row per thing an item waits on.
- **Archive**: dropped items, with why.

## Columns and statuses

| tab | column | values | meaning |
|---|---|---|---|
| Goal | field | Product goal, Outcome metric, Baseline, Target, Measured by, Review on, Set by, Now, Next, Later, Strategy, WIP limit, Companion Doc | The name of the setting on this row. |
| Goal | value | text | The setting. The goal and metric are drafted by Valor from what the product's owners said and replaced by their word; Set by says whose it is. Strategy holds what an incumbent proves users want (second mover) and what to eliminate, reduce, raise and create (blue ocean). WIP limit is how many items may be approved or building at once (default 2). |
| Queue | id | P-001, P-002, ... | The item's id, never reused. |
| Queue | lane | mandatory, bet | mandatory: a bug that breaks a job, security, compliance, or a dated commitment; it comes before every bet. bet: everything else. |
| Queue | rank | 1, 2, 3, ... or blank | The order to build in, on ready rows only; blank on every other row. |
| Queue | item | text | A short name for the bug or feature. |
| Queue | goal | text | The goal or Now theme the item serves; blank only in the mandatory lane. |
| Queue | value | high, medium, low | How much it moves the goal, as a coarse opinion with its evidence in Pitches. |
| Queue | appetite | small, medium, large | The most time it is worth, a cap and not an estimate: small up to 1 week, medium up to 3, large up to 6. |
| Queue | due | YYYY-MM-DD or blank | A date someone committed to; blank when there is none. |
| Queue | status | raw, shaping, shaped, ready, approved, building, shipped, reviewed | Where the item is; each status is a row below. |
| Queue | blocked by | text or blank | The open blockers in short (kind: owner); each is a row on Blocked. |
| Queue | issue | URL or blank | The GitHub issue for the item, where there is one. |
| Queue | dev thread | URL or blank | The engineering chat thread it was sent to for build. |
| Queue | review on | YYYY-MM-DD or blank | When to review what shipped; 14 days after it ships by default. |
| Queue | source | text | Where the item came from (GitHub, the product room, a call, a client). |
| Queue | status = raw | | An item name and the problem as it was heard. Nothing more is needed to add one. |
| Queue | status = shaping | | A Pitches row is being written. |
| Queue | status = shaped | | Its Pitches row has the job, the problem with evidence, the solution, the rabbit holes, the no-gos and the acceptance. |
| Queue | status = ready | | It passed the readiness review and nothing blocks it. Only ready rows are ranked. |
| Queue | status = approved | | Valor posted it in the engineering chat and work started there; dev thread is set. |
| Queue | status = building | | A plan, branch or pull request exists. |
| Queue | status = shipped | | Merged and live; review on is set. |
| Queue | status = reviewed | | The review's decision (keep, iterate or revert) is written in its Pitches row; an iterate is a new row. |
| Pitches | id | P-nnn | The Queue item this pitch is for. |
| Pitches | item | text | The item's name, as on Queue. |
| Pitches | job | text | Who it is for, the progress they are trying to make, and what happens if we do nothing. |
| Pitches | problem | text | The problem in the user's terms. |
| Pitches | evidence | lines tagged observed, reported or assumed | What the problem and its value rest on: observed (data, a report read), reported (someone told us), assumed. |
| Pitches | solution | text | The elements and the flow, at the level of a sketch, with the code it touches. |
| Pitches | rabbit holes | text | The risks that could blow the appetite, each with its patch. |
| Pitches | no-gos | text | What is deliberately left out. |
| Pitches | acceptance | text | Testable statements a reviewer can check, and the question the post-ship review will ask. |
| Pitches | builder questions | text | Each question an engineer would ask before starting, its answer, who answered and when. |
| Pitches | hook | text or blank | Optional: the habit loop (trigger, action, reward, investment) and the check that it uses no dark pattern. |
| Pitches | readiness | text | The last readiness review: its date, result, and what failed. |
| Blocked | item | P-nnn | The item that waits. |
| Blocked | blocker | text | What it waits on. |
| Blocked | kind | human, product, eng | human: a person's action or answer; product: another item; eng: an engineering task or issue. |
| Blocked | owner | a name, an id or an issue | Who or what clears it. |
| Blocked | asked on | YYYY-MM-DD | When the owner was asked. |
| Blocked | chase on | YYYY-MM-DD | When to ask again; three days after asking by default. |
| Blocked | chased | dates | When it was chased. |
| Blocked | status | open, cleared | Whether it still blocks. |
| Archive | id | P-nnn | The dropped item's id. |
| Archive | lane | mandatory, bet | As on Queue. |
| Archive | item | text | As on Queue. |
| Archive | value | high, medium, low | As on Queue. |
| Archive | appetite | small, medium, large | As on Queue. |
| Archive | status | a Queue status | Its status when it was dropped. |
| Archive | issue | URL or blank | As on Queue. |
| Archive | why | text | Why it was dropped. |
| Archive | source | text | As on Queue. |

## How a raw idea becomes ready to build

1. **Raw.** Anyone can raise an idea or a bug, in the product room, in a
   call, in a design file or a document. It becomes a Queue row with
   status raw, a first read of its value and appetite, and where it came
   from.
2. **Shaping.** Once there is more than a sentence, it gets a Pitches row:
   who it is for and the job, the problem and the evidence, a solution at
   the level of a sketch (a design or spec in Drive can be linked here),
   the rabbit holes and how each is patched, the no-gos, and the
   acceptance.
3. **Shaped.** Every one of those cells is written.
4. **Ready.** It passes the readiness review below and nothing on Blocked
   holds it. It is ranked among the ready rows.
5. **Approved.** When it is the top ready row and there is capacity, Valor
   approves it and starts it in the engineering chat; then building,
   shipped, and a review two weeks after it ships.

## The readiness review

An item is ready only when every check passes. Valor runs it and writes
each result in the item's readiness cell. People supply the goal, the
appetite, the job and the answers only they hold; Valor never answers a
person's question on their behalf.

1. **Goal.** A bet names the goal or Now theme it serves, and the Goal tab
   has a goal.
2. **Job.** The job names who, what progress, and the cost of doing
   nothing; the item stays inside it.
3. **Evidence.** At least one observed or reported line; value does not
   rest on an assumption alone.
4. **Appetite.** Fixed, and the solution fits inside it.
5. **Solution.** Read against the current code: the files, models and
   views it touches are named and exist, and nothing it needs is missing.
6. **Rabbit holes and no-gos.** Each rabbit hole has its patch; the no-gos
   close off the scope creep.
7. **Acceptance.** Testable statements, and the question the post-ship
   review will ask.
8. **Builder questions.** Every question an engineer would ask (data, edge
   cases, permissions, copy and translation, migrations, rollout) is
   answered from the code and docs, or, where only a person can answer
   (policy, wording, priority, a client's preference), by that person.
9. **Blockers.** None open on Blocked.
10. **Engagement.** A habit loop or prompt has no dark pattern (false
    urgency, nagging, hidden costs, hard exits).
11. **Capacity.** Fewer approved and building items than the WIP limit.
12. **First.** It is the top ready row.

## For another agent working the Sheet

- Read, don't restructure: keep the one header row, the column names and
  the tab names. Add a column only at the right end of a tab.
- Use the statuses and bands exactly as written above, in lower case.
- A new idea is a new Queue row with the next free id and status raw; a
  design, spec or prototype for an item goes in Drive, linked from its
  Pitches row.
- Ranking, the readiness review and approval for build are Valor's, in the
  product room.
