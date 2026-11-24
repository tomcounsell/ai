# Product-sheet wording: weakest point, shipped notice, today's workaround

Tom approved items 1-3 of the pm-skills review (task eb89f0fa3202) in the
valor team chat, 2026-11-24 08:56 UTC: "Sounds good. Yes to both". Wording
only; no column, check, gate or mechanism. Item 4 (due dates on call
actions) is out.

## What changes, and how it reads

1. **Weakest point named while shaping** (pre-mortem, strategy-red-team).
   - `skills/valor/skills/product-sheet/doc.md`, Pitches `rabbit holes`
     meaning: "The risks that could blow the appetite or sink the value,
     each with its patch. Imagine it shipped and failed, and say why: the
     assumption its value rests on most, the cheapest way to check that
     before build, and the result after shipping that would mean revert."
   - Same file, lifecycle step 2 "Shaping": the rabbit holes clause becomes
     "the rabbit holes and how each is patched, starting from its weakest
     point (the assumption its value rests on most, the cheapest check
     before build, and the result that would mean revert)".
   - `persona/conduct.md` "Unknowns first" (the text rendered into every
     turn; the brief named `docs/persona.md`, whose line 198 is the sources
     entry for the same rule): add "Before building a bet, imagine it
     shipped and failed, and name the assumption it rests on most."
     `docs/persona.md`'s entry gains the source: Gary Klein's premortem
     (HBR, 2007).
   - `SKILL.md` "After shipping": the outcome is read "against the revert
     result its rabbit holes named".
2. **Shipped notice** (release-notes). `SKILL.md` "After shipping" gains
   one sentence: "Tell the room and whoever asked for it what the item now
   does for them, benefit first, with anything they must do up front; their
   `owed` fact becomes `delivered`, as for any send."
3. **Today's workaround in the job** (create-prd, job-stories). `doc.md`
   Pitches `job` meaning appends: "; the moment the need comes up, how they
   get it done today, and what they would use instead of us. A cheap
   workaround means the item is worth less."

Builder may tighten words; meaning stays as above.

## Out of scope (no-gos)

- The readiness review list in `doc.md` (checks 1-12) and the SKILL.md
  readiness paragraph are not touched: a stricter check is governance with
  no grant. Governance added by this diff: none.
- No new column, status, tab, or `sheet.py` code. Item 4. The
  product-discovery pass (its own task).
- No client or person names in commits or this file (public repo).

## After merge-ready edits: refresh the Sheets

Run `python3 skills/valor/skills/product-sheet/sheet.py doc <id>` on each
product Sheet named in the private project specs
(`~/Desktop/Valor/projects/*.toml`, six Sheet ids today), so each companion
Doc and Columns tab carry the new meanings. The delivery names which ones
refreshed (by product, in the chat text only) and any that failed, with
the error.

## Tests

- `pytest tests/test_kernel.py tests/test_product_sheet.py` (persona
  rendering pins "Unknowns first." and other headings; the Columns table
  parse), then the full suite under 5 minutes.
- `sheet.py`'s `columns()` still parses the edited table: four cells per
  row, the `job` and `rabbit holes` rows present with the new text (check
  by running it, and by reading one refreshed Sheet's Columns tab back).
- Grep the diff: no line in the readiness list changed; no new table row.
