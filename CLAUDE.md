# CLAUDE.md

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, approval step, isolation, sandbox rule, denial, allowlist, refusal, capability removal, or verification layer is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance needs Tom's grant, one per instance, written in the plan file that adds it. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is listed for Tom, and deleted unless he keeps it. The reviewer asks whether the diff adds governance; a yes with no grant in the plan file is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

**Tests are not governance.** A test that asserts documented behavior or a documented invariant is part of the code. It is not a check, gate, hook, validator, review round, or approval step, and it needs no incident or grant.

## Hard rules

Adopted 2026-10-15 from "Valor rebuild: missteps and hard rules"
(https://claude.ai/code/artifact/b870a299-849d-4fd7-b449-15dda22f7035).

1. **Developer parity.** A turn gets everything a developer running Claude
   Code in that repository gets: the project's `CLAUDE.md` and skills, the
   team plugins, MCP, web, a real browser, and Valor's own accounts
   (GitHub, Sentry, email). Removing any of it is governance under rule 3.
2. **Capability on Tom's ask.** When Tom asks for a capability, or the old
   system had one Tom used, it is built or ported now. "Second need"
   applies only to abstractions Valor invents for itself. Nothing is
   deleted for disuse without Tom seeing the list first.
3. **Governance means any limit.** Isolation, sandboxing, denials,
   allowlists, refusals, capability removals, and verification layers are
   governance, as the paragraph above says.
4. **One mechanism per concern.** A job already done by one mechanism is
   never given a second. A second layer is a new instance under rule 3.
5. **Done means used.** A task, milestone or cutover is done when Tom has
   used its result on a real project, recorded with the date. Suites and
   replays are evidence, never the finish line.
6. **Bars do not move.** A done criterion is met or Tom drops it in
   writing. No agent rewrites, replaces or marks one done on its own say-so.
7. **Real work outnumbers self-work.** In any week, most merged tasks are
   on projects other than Valor.
8. **Size budgets, enforced as tests.** The full suite runs in under 5
   minutes on one Mac. Test lines stay at or under production lines. A plan
   file fits on one page. `tests/test_budget.py` holds the first two.
9. **A bug fix is a commit.** A defect gets a fix and a test, with no plan
   file, no critique round and no separate review.
10. **Reuse before writing.** Before writing a component, take it from
    Claude Code itself, an existing library, the old system or cori.
    Writing one from scratch names why none of those fit.

## Where things are

`main` is the live rebuilt system (cut over 2026-10-14). The old system is
at `backup/pre-sync-main-2026-10-15`; read it with `git show <ref>:<path>`,
never import from it. The cori copy is `tomcounsell/cori` at commit
0336d10642534f344e002d9a7b7abb22eb40a8a1. What the rebuild left to do is
`docs/plans/strip.md`, then `docs/plans/valor-rebuild.md`'s open items.
Setting up a machine is `docs/plans/rebuild-handoff.md`.
