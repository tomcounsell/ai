# Answer about the backlog from the Sheet, in chat and in calls

Tom, #valor-product, 2026-11-13 15:55 UTC: "a fast-answering tool (not just
for calls, but all the time) to lookup items in the google sheet and know
its values (size, value, due date, pitch, etc)". 15:59: "humans will never
know its number or title, so that's just for you". 16:00: shape it from
the real questions asked. A capability Tom asked for; no governance added.

## The questions it answers (observed, #valor-product and its call, 2026-11-13)

1. "What are all our possible directions for product development?" (chat 10:01): the whole Queue, grouped by theme. `read`.
2. "What's in your backlog?" (call 10:30): a short overview. `read`.
3. "What is the top priority?" and "the criteria for what put it in that order" (call 11:02-11:03): the Queue in order, with lane, value, appetite; the rule is the persona's, already in the call. `read`.
4. "What evidence do we have…", "give me an example" (call 10:32): one item's tagged evidence. `lookup`.
5. A loose description of one item: its values and pitch. `lookup`.

So one new command, `lookup`; `read` already answers 1-3.

## What is built

**`sheet.py lookup SHEET "<their words, or an id>"`**: one `batchGet` of
Queue, Pitches and Blocked (0.8 s warm, 2.1 s cold on a real Sheet,
2026-11-13: no cache). An id (`P-4`, `p-004`) is Valor's own shortcut.
Otherwise each query word (filler, single letters and the names of the
values asked about dropped; common endings stripped) scores 2 in the title,
else 1 in the pitch's job, problem, evidence or solution; the best 5,
highest first, ties in Sheet order. Each match carries its Queue values,
its Pitches row and its Blocked rows. No match: `{"matches": []}`.

**Chat and task turns**: SKILL.md "Answering about the backlog": `lookup`
for one item, `read` for the whole; answer in the person's terms, never
by id or the Sheet's title.

**The call**: a Live tool `backlog` (`about`, optional), offered only when
the call's project spec's `rooms` name a Sheet (read once as the call's
Live lane starts). With `about` it runs `lookup`, without it `read` cut to
the overview columns; run with `asyncio.create_subprocess_exec` so the
call's audio keeps flowing; an error comes back as the result. One
instruction line says when to call it and to answer in the person's terms.

Cut: the Archive tab (no question needs it), a cache, writing from a call.

## Tests

tests/test_product_sheet.py: an id in each spelling in one read; values,
pitch and blockers joined, no other item's; a loose description found
through its pitch; several fitting, best first; field words and stray
letters ignored; nothing fits. tests/test_call.py: `backlog` and its line
offered only with a Sheet; the tool reads the spec's Sheet (lookup, the
whole queue's overview, an error); no Sheet, no tool.
