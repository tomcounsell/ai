# Step 6 session prompt

Paste everything below the line into a new Claude Code session started in
`~/src/valor-rebuild`.

---

We're doing Step 6 of the Valor rebuild: planning the rebuild itself, in
conversation with me. You are in the worktree `~/src/valor-rebuild`, branch
`valor-cori-rebuild`. Never switch `~/src/ai` off `main`; the live bridge and
worker run from it. The previous system lives unchanged on `main`; read old
code with `git show main:<path>`. The cori reference is tomcounsell/cori at
`0336d106`.

Read these first, in order, before saying anything to me:

1. `docs/plans/valor-cori-rebuild-setup.md`: the setup plan. Steps 0 to 5
   are done. Step 6 lists the questions it expected; most are now answered.
2. `docs/plans/rebuild-open-questions.md`: my answers from 2026-10-01 and
   the defaults I accepted. Treat these as decided. Don't re-ask them.
3. `docs/plans/rebuild-demonstration.md` and `docs/plans/rebuild-baseline.md`:
   what the minimal kernel and bare Opus did on real requests (one
   demonstration, 13 replay runs, against the old system).
4. `docs/mission.md`, `docs/architecture.md`, `docs/sdlc-state-machine.md`,
   then the rest of `docs/*.md` and `docs/bridges/`. These describe the
   target. Where they mark something "design", it isn't built yet.
5. `core/README.md` and the code in `core/`, `harnesses/`, `tools/`,
   `scripts/`: the minimal kernel that exists today (38 tests,
   `.venv/bin/python -m pytest -q tests`).

Decisions that shape the plan (details in the open-questions file):
- Build order: kernel and its Postgres data, then the Telegram and email
  bridges, then the Claude Code harness (built for harness and model
  independence), then persona and routines, tools on demand, memory last
  (when popoto supports Postgres).
- Every component is built through the documented SDLC: every stage is a
  checkpoint with goal-oriented instructions. The plan doc sets 0 to 2
  critique and review loops by stakes. Plan and review may pull in related
  known tech debt. Patch runs in the builder's own resumed session. Test,
  review, and docs run in parallel after every build and patch. The reviewer
  is Opus or Opus-class.
- One machine per install. Budgets are for visibility, not a hard wall.
- Judgement tier: Jev; OpenAI's Decisions API for images; the backup is the
  same open-weight model hosted by a second provider.
- The governance paragraph at the top of `CLAUDE.md` binds the plan: no
  check, gate, hook, or review step without a named incident and mission
  item, and my grant.

What I want from this session:
1. A rebuild plan doc at `docs/plans/valor-rebuild.md`: one milestone per
   component in the build order. Each milestone states its goal (tied to a
   mission item), what "done" means in evidence (tests on real services, a
   replay or emulator result where it applies), its stakes (which sets its
   critique and review loops), the known tech debt it absorbs, and what it
   deliberately leaves out. Include how the bridges are adapted from the
   code on `main` rather than rewritten, and where the kernel that exists
   today is kept versus replaced.
2. A decision on how the rebuild is executed: which milestones Valor
   builds through its own new kernel and pipeline (dogfooding) versus
   built by agents driven from a session like this one, and the point at
   which the new system takes over building itself.
3. Cutover is out of scope (my problem later), but name what must be true
   before it, in one short section.

How to work with me:
- Use the `ask-me` skill. One question at a time, plain language, a
  concrete example in each question, your recommendation first. Only ask
  what changes the plan. Decide reversible details yourself and list them
  as "decided by default".
- Use subagents for research and drafting. Keep my attention for decisions.
- Write in plain language: no em dashes, no AI-writing tells, no history,
  and don't use the word "cori" in docs.
- Commit and push to `valor-cori-rebuild` as you go. Before you finish,
  show me the milestone list and the open items in under 30 lines.

Also on your list, low priority, ask me before acting:
- `docs/plans/` still holds about 30 old plan docs from the previous system
  (for example `agent_wiki.md`, `sdlc-1249.md`). Propose which to delete.
- `~/src/valor-demo/runs/` is 12 GB of replay workspaces; the results are
  kept in `~/src/valor-demo/results/`. Propose deleting the runs.
- The old system's spend for the replayed PRs is unmeasured (its records
  are on the MacBook Air that owns psyoptimal). Decide whether the plan
  needs it.
