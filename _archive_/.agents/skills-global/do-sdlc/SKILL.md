---
name: do-sdlc
description: "Advance or complete a development workflow from its current issue, plan, build, test, review, documentation, or merge state."
---

# Do Sdlc

Resolve the requested scope: one named stage, one next-stage decision, or end-to-end work. A normal Codex request stays in the current task and proceeds through relevant work; no Claude PM session, hook, slash-command dispatcher, or particular model is assumed.
For ordinary development use the corresponding skills as procedures: $do-issue, $do-plan, $do-plan-critique, $do-build, $do-test, $do-patch, $do-pr-review, $do-docs, and $do-merge. Read their SKILL.md files when needed and execute with available tools. Scale the workflow to the change and preserve already-completed stages.
If explicitly operating a managed Valor SDLC lane, first read `docs/sdlc/do-sdlc.md` and [run identity](RUN_IDENTITY.md). Use its actual CLI and lease identity. `next-skill` is read-only: record a dispatch before executing it. Preserve the returned run_id across state writes, honor foreign ownership, and never bypass the router's gates by hand. Terminal means success; blocked/error means investigate the stated cause rather than dispatching guessed stages. Verify fresh current-head REVIEW and required DOCS before merge.
A supervisor loops only for requested end-to-end work, with the declared cap (15 dispatches by default) and one writer per artifact. A single-stage request returns after that stage. Re-ensure ownership between stages, honor finalize/self-check requirements, and release the owned lease on a stopped/terminal exit as documented. Do not claim an OUTCOME or marker that was not actually produced.
