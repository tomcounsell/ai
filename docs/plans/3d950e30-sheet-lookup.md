# Look up one backlog item, in chat and in calls

Tom, #valor-product, 2026-11-13 15:55 UTC: "a fast-answering tool (not just
for calls, but all the time) to lookup items in the google sheet and know
its values (size, value, due date, pitch, etc)". A capability Tom asked
for; no new check or gate; no governance added.

## What is built

**`sheet.py lookup SHEET "<id or words>"`** (skills/valor/skills/product-sheet/sheet.py).
One `gws sheets spreadsheets values batchGet` over `Queue!A2:Z`,
`Pitches!A2:Z`, `Blocked!A2:Z`, `Archive!A2:Z`, parsed with the existing
`rows()` and fixed column lists (`QUEUE`, `BLOCKED`, new `PITCHES` and
`ARCHIVE`, as `doc.md` names them). Prints JSON `{"matches": [...]}`:
each match is the Queue (or Archive) row's fields with `"tab"`, its Pitches
row as `"pitch"` (or null), and its Blocked rows as `"blocked"` (all of
them; each carries its status).
- **By id:** `P-4`, `p-004`, `P-004` match P-004 (compared by number).
- **By words:** the rows whose `item` contains the most of the query's
  words (`\w+`, lowercased); ties all returned, at most 5, Queue before
  Archive. No word matches: `{"matches": []}`, exit 0.
- Measured live (2026-11-13, a real Sheet): a three-range batchGet took
  0.8 s warm, 2.1 s cold. No cache: nothing shows a read is slow.

**Chat and task turns.** SKILL.md gains "Looking up an item": when anyone
asks about one item (its value, appetite, due date, status, blocker,
pitch), run `lookup` on the room's Sheet and answer with the row's values,
naming the item; never from memory. Every product-room turn already loads
the skill; the docstring lists the command.

**The call.** Today the Live model reaches the Sheet only through `ask`,
a full worker turn posted in the chat (tens of seconds), so a spoken
answer waits. Simplest fit: a sixth Live tool, `item` (`query`), offered
only when the call's project spec names a Sheet. `Call._tools` runs
`sheet.py lookup` as a subprocess (`sys.executable`, the skill's path)
and returns its JSON as the tool result, which Live speaks from. The
Sheet id comes from the project spec's `rooms` text (the first
`docs.google.com/spreadsheets/d/<id>`; every spec with a Sheet names
exactly one, checked 2026-11-13), read where `_links()` reads the spec.
One instruction line: "When someone asks about a backlog item's values,
call `item` with its id or words from its title and answer from the row
(say which item you found; with several, ask which); `ask` your worker
only for what the row does not hold." A lookup that fails returns its
error as the result, so Live says it could not read the Sheet.

## Out of scope

Writing to the Sheet from a call; fuzzy or semantic matching; a cache; a
Sheet for projects without one; changing `read`, `order`, `due`.

## Tests

tests/test_product_sheet.py, fake `gws`:
- by id, in each spelling; exactly one gws call (one batched read);
- by words: best match wins; a tie returns both; Archive row marked;
- no match: `{"matches": []}`;
- a row with a Pitches row and a Blocked row returns both joined;
- a Pitches/Blocked row of another item is not joined.

tests/test_call.py:
- `live_config` with a Sheet offers `item` and its instruction line;
  without one the tool list stays the five (existing test unchanged);
- an `item` tool call returns the lookup's JSON for the spec's Sheet
  (subprocess faked), and a failing lookup returns its error;
- the Sheet id is read from a spec's `rooms` text.

Suites: those two, `tests/test_bloat.py`, then the full suite.
