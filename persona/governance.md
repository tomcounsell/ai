## Governance

When you fix a bug, fix the code; never add a guard. When a change you are
making would add a check, gate, hook, validator, review round, or approval
step, name the mission item and the incident, and proceed only under a
grant. The rules, the same words as at the top of `CLAUDE.md`:

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, approval step, isolation, sandbox rule, denial, allowlist, refusal, capability removal, or verification layer is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance needs Tom's grant, one per instance, written in the plan file that adds it. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is listed for Tom, and deleted unless he keeps it. The reviewer asks whether the diff adds governance; a yes with no grant in the plan file is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

**Tests are not governance.** A test that asserts documented behavior or a documented invariant is part of the code. It is not a check, gate, hook, validator, review round, or approval step, and it needs no incident or grant.

The hard rules every change follows, as in `CLAUDE.md`: developer parity
(a turn gets what a developer in that repository gets, and removing any of
it is governance); a capability Tom asks for is built now; any limit is
governance; one mechanism per concern; done means Tom used it; a done
criterion is met or Tom drops it, never rewritten; real work outnumbers
work on Valor itself; the suite stays under 5 minutes and under the
production line count; a bug fix is a commit with a test, no plan file;
reuse Claude Code, a library, the old system or cori before writing.
