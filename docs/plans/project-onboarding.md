# Onboarding a project to the product Sheet, and running it over every client

Tom, 2026-11-06: every client project with Valor active gets its own backlog
Sheet (as the first product room has), a companion Doc on how to read and
work it, both readable by other agents (Gemini in Workspace, Figma's Drive
connection), and a written checklist Valor runs for any new project. The
first project through it is the one whose product channel he made that day.
Which projects, channels and links are in the private run notes,
`~/Desktop/Valor/onboarding/2026-11-06-run.md`; this file names no client.

**Stakes.** Wrong room text or a Sheet that is not shared leaves a client's
rooms pointing at nothing; everything here is config, Drive files and one
script, all undoable, except that a client detail committed to this public
repo stays in its history.

## What is built (repo)

1. **`skills/valor/skills/product-sheet/doc.md`**, the companion Doc's one
   template: what the Sheet is for; each tab; one table `tab | column |
   values | meaning` covering every column and, as rows of `status`, every
   status and what it needs; how a raw idea becomes ready (raw, shaping,
   shaped, ready, approved); the readiness review; how another agent should
   read it (one header row per tab, ids, the Goal tab first). Placeholders
   `{{product}}`, `{{sheet}}`, `{{folder}}` only. SKILL.md's Tabs, Statuses
   and readiness-check text move here and SKILL.md points to it, so there is
   one copy.
2. **`sheet.py`**:
   - `new "<Product>" --share EMAIL`: makes the Drive folder `<Product>
     product`, copies the template into it as `<Product> product backlog`,
     renders `doc.md` into a Google Doc there (Drive's markdown import),
     shares the folder (its files inherit), writes the Doc's URL in the Goal
     tab (`Companion Doc`), sets each file's Drive description to name the
     other, and prints the three URLs.
   - `doc SHEET`: re-renders the Doc in the Sheet's folder (creates it if
     missing, else replaces its content) and rewrites the Sheet's `Columns`
     tab from `doc.md`'s table. Run on every Sheet when `doc.md` changes; that
     is how they stay alike.
   - `seed SHEET OWNER/REPO`: appends each open GitHub issue (not pull
     requests) that no row links yet as a `raw` row: next free id, `mandatory`
     if labelled bug else `bet`, the title, the issue URL, `source` GitHub;
     value and appetite blank for the room's first pass.
   - `source` joins `QUEUE` as the last column (where the item came from;
     the first Sheet already has it).
3. **Template Sheet**: the prose `Guide` tab becomes `Columns`, the data
   dictionary (header `tab, column, values, meaning`, written by `doc`);
   Queue and Archive get `source`. One header row per tab, no merges, no
   meaning in images.
4. **The checklist**, the "Onboarding a project" section of SKILL.md (one
   place; replaces "A new product room"). For each item: how to see it and
   how to fill it. A folder with the Sheet and Doc, owned by valor@yuda.me
   and shared with Tom; seeded where the repo has open issues; the Sheet's
   URL in the `rooms` line of every chat of the project (product rooms also
   name their dev chat; `chat_roles` `product`, not in `mentions`); a product
   channel only where Tom made one, received on the Mac in `machine` (spec
   synced there, bridge log shows the channel; restart the bridge if not); the
   decision board's canvas in the `board` chat; the Goal tab drafted. The run
   ends in a report per project: has, lacked, filled, links. It is a list
   Valor runs and reports on; nothing blocks on it.
5. `docs/bridges/slack.md` product-channel paragraph: a project without a
   product room names its Sheet in its dev rooms; points at the checklist.

## The run (private: specs and Drive, not committed)

1. **The first project, for real.** List its new product channel in its
   spec (as the first product room is), name the Sheet in its other rooms,
   `Spec.load` it, confirm on its Mac (ssh) that the file synced and the
   bridge receives the channel, `new`, `seed`, draft the Goal, check its
   board canvas. Every manual, unclear or broken step goes in the run notes;
   the checklist and script are polished from them and committed before step 2.
2. **The other active clients** (the run notes say which, and which were
   left out: internal, personal, dormant, paused): the same, with no product
   channel created; the Sheet named in their existing rooms.
3. **The first Sheet to standard**: into its own folder, `doc`, `Columns`
   tab, its two off-standard statuses set to what their rows support (named
   in the report), the Goal tab's Doc link.

## Out of scope

Creating Slack channels or inviting anyone but Tom (the first Sheet's other
writers keep the Sheet; the new Doc and folder are Tom's until he shares
them); role changes in non-product rooms; kernel code; any check, gate or
hook (no governance); rating or ranking seeded rows.

## Tests (`tests/test_product_sheet.py`, fake `gws` and `gh`)

- `doc.md` renders with every placeholder filled and none left; its table
  documents every column in `QUEUE` and `BLOCKED` and every status in
  `STAGES`, and nothing they lack.
- The `Columns` rows: one header row, four cells per row.
- `new`: folder made, template copied into it, Doc made in it from the
  rendered markdown, the folder shared with each `--share`, the Goal row
  written; the printed URLs.
- `doc` on a Sheet whose folder has a Doc replaces it (no second Doc); with
  none, makes one.
- `seed`: skips pull requests and issues already linked in any row;
  numbering continues after the highest `P-nnn` (gaps kept); bug label is
  `mandatory`; an empty repo appends nothing.
- `order` keeps `source` with its row (existing tests updated for the
  new column).
- Live, in the build: each Sheet's tabs read back with one header row and no
  merges; each Doc and folder open as Tom (permissions list); each edited
  spec loads; the first project's product channel is received on its Mac.
