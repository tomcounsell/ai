# skills

Versioned skills.

## Sources

A worker's skills come from three sources together:

- **Valor's own plugin**, `valor/` here, loaded first in every workspace turn (`settings.plugin_dirs`): the web agent `valor:web` and its guard hook (`valor/agents/`, `valor/hooks/`), the Mac-app agent `valor:mac-app`, which reads BAMA's skills by path (Imports, below), the SubagentStart hook that gives every subagent the Brief, the PreCompact hook that gives a chat session's compaction its instructions (`VALOR_COMPACT`), and Valor's own skills (`valor/skills/`: `authenticity-pass`, and `mercury-invoicing`, Yudame's Mercury invoices, pay links and reminders from valor@yuda.me). Each further skill is built by an ordinary task on this repository.
- **The yudame plugins**, from `yudame/skills` in the Agent Skills format: the `yudame` and `yudame-dev` plugins listed in `settings.plugin_dirs`, kept current by the marketplace. Codex and Pi read the same clone through `~/.agents/skills` ([docs/plans/skills-and-harnesses.md](../docs/plans/skills-and-harnesses.md)).
- **The project's own skills**, in the repository the task works in.

Workers load and use the skills; the persona turn does not. The persona reads and dispatches, and the work, skills included, is done by the subagents and kernel tasks it starts (planned: [docs/plans/orchestration.md](../docs/plans/orchestration.md), wave 2). Until that split is built, a workspace turn loads all three sources itself ([docs/harnesses.md](../docs/harnesses.md), Skill rendering).

## Requirements

- One source per skill, read by every harness (Claude Code, Codex, Pi) the same way.
- Valor improves a skill with an ordinary task on the repository that holds it; the merge reaches every harness on every Mac.
- A skill is craft, never a gate. The posting skills (de-slop in the yudame plugin, authenticity-pass in Valor's own) shape a post Valor makes on its own accounts; nothing waits on Tom's approval ([docs/performers.md](../docs/performers.md)).

## What is here

- `valor/`: Valor's own plugin (Sources, above), with the vendored BAMA (Imports, below).
- `sdlc/`: one file per stage of the SDLC state machine (`clarify.md`, `plan.md`, `critique.md`, `build.md`, `review.md`, `docs.md`, `patch.md`), each stating the stage's goal and exit evidence, not steps, `advice.md`, read by the advisor's fresh session, `channel.md`, how a working-session turn reaches Tom or the advisor through `.valor/`, `converse.md`, a chat session's in place of both, `compact.md`, what a chat session's compaction keeps (rendered per channel by `harnesses/claude_code.py`), and `verdict.md`, how a fresh session returns its verdict. `core/tasks.py` renders `channel.md` (with the effects the task's own performers offer), or `verdict.md` for a fresh session, and the current stage's file into a turn's Brief. The judge is a classifier prompt (`docs/judgement-layer.md`), the test check is a suite run and a classifier call, and the merge is the kernel's, so none of the three has a file. `critique.md` is read by the critique runner's fresh session; `review.md` and `docs.md` wait for their runners in milestone 1.4.

These are Brief text, rendered by the kernel, not skills a harness loads. [docs/harnesses.md](../docs/harnesses.md) (Skill rendering) and [docs/sdlc-state-machine.md](../docs/sdlc-state-machine.md) (stages are skills) state what skills must fit.

## Imports

- `valor/bama/`: BAMA's `skills/`, `specs/` and MIT `LICENSE` (github.com/daveebbelaar/bama), copied verbatim at commit 9b986714f248036cdf3bdf458b93cd4b67fe120f. Only `valor:mac-app` reads them, by path from its prompt; they sit outside `valor/skills/`, so no harness lists them to a session. Claude Code's agent `skills:` preload was measured (2.1.309, 2026-10-27) and cannot load a skill that sessions are not also shown. A newer BAMA is a task re-copying at a new pin ([docs/plans/mac-app.md](../docs/plans/mac-app.md)).

A yudame or project skill is loaded where it lives, never copied here.

## Effect classes

Holds none. A skill describes work; effects happen through the broker under the task's ceiling.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, approval step, isolation, sandbox rule, denial, allowlist, refusal, capability removal, or verification layer is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance needs Tom's grant, one per instance, written in the plan file that adds it. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is listed for Tom, and deleted unless he keeps it. The reviewer asks whether the diff adds governance; a yes with no grant in the plan file is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- A copy of a yudame or project skill.
- Kernel rules expressed as skill text. Authority is in `core/`.
