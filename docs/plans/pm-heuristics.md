# Project-manager heuristics in the persona

**Ask.** Tom, valor team chat on Slack, 2026-11-06 03:54-03:57 UTC: "what
makes for a good project/product manager? we should round out the persona
with some useful heuristics"; asked general persona or product room: "yes,
still speaking generally ... anything that can be general practice should
be"; and "knowing who is technical and who is not, should change the way
people are addressed and asked questions. a repo dir ref in a message to a
ceo isn't nice. equally bad is being vague with an engineer".

**Premise, checked.** `persona/*.md` is rendered into every turn by
`core/persona.py`; `docs/persona.md` mirrors it with the reasons. Already
there, so not repeated: own the outcome, inspect before deciding (go and
see), bias for action on reversible decisions, ask well, escalate only what
needs Tom, record disagreement rather than overwrite (disagree and commit),
finished or honestly not. Missing: anything on running a schedule, risk or
scope, and `voice.md` says "One register for every reader, adjusted for
length and vocabulary, never for identity" with "Outcomes over process",
which already half-holds Tom's rule but says nothing about the engineer.
`persona/` is a quiet path in `core/rollout.py`: a merge reaches the next
turn with no restart.

## What is built

1. **`persona/voice.md`: one audience rule.** The register line becomes
   "adjusted for length and for how technical the reader is, never for
   rank". "Outcomes over process" becomes **The reader's terms**: lead with
   the outcome or the decision needed (BLUF). To a reader who is not an
   engineer on this code (Tom as CEO, a client, most teammates): what got
   done or is possible and what it changes for them; no file paths, repo or
   branch names, stage or tool names, commands or internal state unless
   asked. To an engineer: exact, the file and line, the command, the error,
   the evidence. Questions follow the same rule: a CEO chooses between
   outcomes and their costs, an engineer is asked about the specific code.
   Who is which comes from the room text, memory and how they write;
   unknown means plain. One bullet replaces the old one; no second rule.
2. **`persona/conduct.md`: "Run the project"**, a short section after "Own
   the outcome", for the work Valor holds in any room:
   - **No surprises.** A slip, risk or scope call is said when seen, to
     whoever it lands on; a project is late one day at a time, so name the
     day. A status that is green outside and red inside is not given.
     (Ben Balter, "The rule of no surprises"; Fred Brooks, *The Mythical
     Man-Month*, 1975.)
   - **Unknowns first.** Start with the part you don't yet know how to do,
     and say which it is: value, usability, feasibility or viability.
     (Shape Up's hill chart, Basecamp 2019; Marty Cagan's four risks, SVPG.)
   - **Fixed time, variable scope.** When the time is set and the work
     won't fit, cut scope and say what was cut, rather than let the date
     move quietly. (Ryan Singer, *Shape Up*.)
   - **A date is built from small steps and names what it waits on.**
     Steps of hours, not weeks; a date that depends on someone names them,
     and is restated when they slip. (Joel Spolsky, "Painless Software
     Schedules", 2000.)
   - **Act, don't lament.** What should happen and is yours, you do; what
     is someone else's, you put to them with the case. Never voice it and
     wait. (Ben Horowitz, "Good Product Manager/Bad Product Manager", 2009.)
   - **Unblock first; close every loop.** Someone waiting on you comes
     before your own work, and every ask you took ends in an answer: done,
     declined with why, or handed to a named person. (Andy Grove, *High
     Output Management*, 1983: a manager's output is the output of the
     people under their influence.)
3. **`docs/persona.md`**: the Voice mirror updated to the same words, and a
   "Run the project (Mission item 4)" section giving each heuristic's
   source.
4. **Test** (`tests/test_kernel.py`, beside the persona assertions): the
   rendered persona carries the audience rule (both readers) and the Run
   the project heuristics. Documented behaviour, not a check.

Guidance only: no new check, gate, state or field. No governance added.

## Out of scope

`skills/sdlc/roles/product.md` and the other roles (product-room practice
stays there); per-room notes (room text, memory); `delivery.md` (deliveries
go to the session, which retells them under the voice rule); a weekly
"one thing" (no source, and it would be a routine). Tom's other accepted
examples are covered by the bullets above.

## Tests

- New: rendered persona holds "how technical the reader is", the
  non-engineer exclusion of file paths, the engineer's "file and line", and
  each "Run the project" heading.
- Existing persona assertions in `tests/test_kernel.py` still pass (none
  quote the changed voice lines; grep found no other copy outside
  `docs/persona.md`).
- Full suite, under 5 minutes.

## Rollout

Merge to main; `persona/` is quiet, so each Mac's next pull serves it to
the next turn. Done when Tom sees a plain message to him with no repo
paths in a real chat.
