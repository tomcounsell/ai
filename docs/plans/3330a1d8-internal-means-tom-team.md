# "Internal" means Tom's team, on every project (task 3330a1d8)

Data only: no code change, nothing to merge but this plan.

## Rule

Internal is Tom's own team: people who work for or with Yudame delivering the
work. A client's own staff are external on every project, including the
client's PM, owner or engineers Tom and Valor work alongside. Default from
#valor-product thread 1794834009.723979 (2026-11-16), unanswered a working day.

## How

The 2026-11-16 fill (task 32168c317ab5) ran one task per project on the Mac
holding it (`python -m core start --project <spec> --by valor` over ssh),
because `valor-record` answers only for its turn's own project and client data
stays on its own Mac. This does the same.

1. **Which projects.** Exactly the rows of the fill delivery's per-Mac table
   (task 32168c317ab5, turn 542be1c83259): Cowboy: valor, and "Yudame
   project 6" (fill task 3eaef1e2cf96) only if that task's ledger shows it
   finished, else reported as not filled; Pirate: valor, Yudame projects 1-4,
   client project A; Captain: valor, client B projects 1 and 2, client C,
   Yudame project 5. For each, count people whose roster entry reads internal
   (`valor-record person everyone`, or `python -m core facts --project <spec>
   read --kind team --json` folded per person by newest live fact) over ssh,
   printing counts only. A project whose only internal person is Tom needs
   nothing and is reported as 0/1/0. Valor's own project on the Cowboy is this
   task's project: done here directly.
2. **One task per remaining project, on its Mac**, `--by valor --ceiling act`,
   instruction: this rule; list internal people with
   `valor-record person everyone`; for each, decide from the project's own
   record (room texts, email domains, organisation and role facts, how Tom or
   the room describes them) by this table, carried word for word in every
   task's instruction so every project decides alike:

   | Evidence in the project's record | Mark |
   |---|---|
   | Email at the client's domain, or Tom or the room calls them the client's | external (change) |
   | yuda.me address, Tom names them as his team, or a contractor Yudame engaged | internal (keep) |
   | Hired by the client through Yudame, no record of who pays them | unresolved |
   | No evidence either way | unresolved |
   | Tom | internal (keep) |

   For an external one, supersede the newest live internal `team` fact (the
   one the dossier shows): `valor-record fact supersede <team fact>
   external --kind team --about <name> --since 2026-11-17 --source <this
   task's started row> --reason "<rule; task 3330a1d8f6c4>" --evidence "<the
   message or fact showing who they work for>"`; where the record does not
   show who they work for, change nothing and count them unresolved with what
   would resolve it; touch no other fact, delete nothing; Tom stays internal.
   End with `.valor/stop.md`: counts changed/kept/unresolved and, per
   unresolved person, what would resolve it, with no names.
3. **Collect** each task's stop note from its Mac's ledger over ssh, as the
   fill did; record the task ids in `.valor/fixes.txt`.
4. **Report** to #valor-product in plain terms, per project label ("client B,
   project 1"): changed to external, stayed internal, unresolved and what
   would resolve them. No names anywhere in the repo or the report.

## Out of scope

Code, the person lookup, ranking, the persona; any non-`team` fact; people not
marked internal (an external marked wrongly is not this task); inactive or
unfilled projects; copying any record between Macs.

## Checks (no tests: no code changes)

- Per project, before and after: people whose roster entry reads internal
  fall by exactly the "changed" number (count people, not facts: a person may
  hold more than one live `team` fact); counts by fact kind from `core facts
  read --json` move only for `team`.
- `facts --history <new fact>` for one changed person per project shows the
  old `internal` fact superseded, not removed, citing this task and evidence.
- Tom is internal in every project afterwards (`valor-record person "Tom
  Counsell"` in each task).
- Captain "client B, project 1": the 22 internal are each changed, kept with a
  reason (works for Yudame), or unresolved; changed + kept + unresolved = 22.
- Grep of the commit and `.valor/` report for any name other than Tom, Valor,
  Yudame: none.

## Stakes

Wrong marks change who a call names first as "the team" and who ranks higher in
lookups; each change is a supersede, so it undoes with one more supersede.
