# Fill the dossiers, and a call that knows the team and takes tasks (P-034)

Tom, Meet call pxz-exmv-icy, 2026-11-16 14:40-14:42 UTC (call session
2cb59305f984, rows #122073-#122170): asked who is on the team, about two
named people and about himself, Valor had no one; told to fill the
dossiers, it refused three times as "people matters ... go to Tom".
#valor-product 14:44: "Valor surprisingly denied my request to note down
tasks." Observed 14:50: `valor-record person "Tom Counsell"` is "nobody fits".

## Causes (observed in the record and the code)

1. **Empty store.** P-005 (88d9b268c) shipped with no backfill; no project
   has a person entity, Tom included (the operator is internal by name only
   once an entity exists).
2. **The refusal.** The spoken refusals echo the persona's In a call line
   "Not in a call: legal and financial work, pricing, terms and people
   matters. Say they go to Tom" (since 1c3abac23, 2026-11-06) and
   core/call.py's "(pricing, terms, its people) ... is for Tom" (7e599dc49).
   "Dossiers" made "people matters" fit.
3. **No way to take a task.** The instruction never says a task given in a
   call is taken; its tools are ask, post, open_decisions, settle, person,
   leave. The report passes Gemini's summary file on unchanged, so its
   action items never reach the chat session as work.
4. **P-033's reading line stays all session.** `READING_LINE` ("answer from
   `backlog`, `person` and `open_decisions` meanwhile") is in the system
   instruction for the Live session's whole life, after the reading came in
   (#122065, before Tom's first question). The call echoed it at #122135
   ("I have access to the project backlog and open decisions").
   Contributing, not proven sole cause.

## What is built

- **Roster lookup, no new tool.** `memory/facts.py` `people`: a description
  that names no one (every word filler; `team`, `everyone`, `everybody`,
  `all` join `FILLER`) returns everyone in the project, internal first, then
  by name, uncapped, each entry its head line and its `team`, `role` and
  `organisation` facts only. Named and loose lookups unchanged.
  `valor-record person everyone` and the call's `person` reach it; the
  call's instruction and tool description say so. docs/memory.md People.
- **Wording (persona "In a call" and core/call.py).** Not in a call: legal
  and financial work, pricing and terms, and people decisions (hiring, pay,
  performance, disputes); say they go to Tom, and where. Recording who
  people are and a task anyone gives are ordinary work. A task given in a
  call: say in a sentence it is noted. call.py's "(pricing, terms, its
  people)" becomes "(pricing, terms, hiring or pay)". `READING_LINE` adds:
  everything else as usual, `ask` your worker and take tasks.
- **Tasks reach the chat session.** `SUMMARY` asks for each task given or
  taken, owner the one to do it. `summarise` returns its actions beside the
  path; `end` lists them in the report: "Tasks from the call: take, hand
  on or decline each in the thread". With no summary, the report says to
  read the transcript for tasks given in it. No new tool, ledger row or
  check.
- **The fill (data, not code).** This task fills the `valor` project from
  its own record on the Cowboy. For every other project placed on a Mac
  with a chat, message or task since 2026-10-14, one task is started on
  that Mac (ssh, `python -m core start --project NAME` in the kernel job's
  environment), so client data never leaves its Mac. Each fill: read the
  project's chats, messages, tasks, memory and room texts; `entity add`
  each person (Tom always) and `fact add` role, organisation, ask,
  decision, owed, delivered, each dated, citing its message or ledger row;
  `team internal` only where the record says they work for or with Tom or
  Yudame; nothing inferred; end with `stop.md` giving counts only. This
  task collects the counts. No script is kept.

## Out of scope

Facts written automatically by the kernel; any self-view; injecting a
dossier when a name is said; a task-note tool; changing ranking weights.
No governance is added: no check, gate, filter or refusal.

## Tests (tests/test_facts.py, tests/test_call.py)

- "who is on this team" and "everyone" return all the project's people,
  internal first (Tom with no team fact, a marked teammate), then external;
  entries hold role and organisation, not asks; another project's and
  client's people never appear; a name still returns one person.
- A call's `person` tool with "who's on this team" returns the roster.
- The instruction and persona section: people decisions name hiring, pay,
  performance and disputes; recording people and taking a task are
  ordinary work; "its people" is gone; the reading line keeps `ask`.
- `end` with a summary's actions lists each as a task in the report; with
  no summary, points at the transcript.
- Live (run once, not kept): Gemini Live on `live_config` for `valor`, the
  real `person` tool, text "Who's on this team?": it calls `person` and
  speaks names. Then "note down a task: X": it says it is noted.

Done: on each Mac `valor-record person "Tom Counsell"` and `person
everyone` return people in each active project (after rollout); the live
test passes; counts per project in the report, no client names.
