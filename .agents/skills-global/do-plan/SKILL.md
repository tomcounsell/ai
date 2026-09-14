---
name: do-plan
description: "Research and write an implementable development plan with explicit scope, acceptance criteria, dependencies, and verification."
---

# Do Plan

Resolve the target repo and tracking issue from the request; read current code, issue comments, prior fixes, relevant tests, and infrastructure constraints. Trace cross-component data flow and look for expected-failure tests that might encode the defect. Verify old issue claims against today's code. Research current external API details when the approach depends on them.
Set a proportionate scope and identify risks, concurrency/ownership boundaries, and assumptions worth a small isolated spike. Resolve questions through evidence where possible; ask the user only for decisions that materially affect the outcome. Do not require a planning ceremony for a trivial edit unless the user or repo contract requires it.
Write `docs/plans/<slug>.md` using [the plan template](PLAN_TEMPLATE.md), adapting sections to the work. Include problem, solution, non-goals, concrete files/tasks and dependencies, acceptance criteria, verification commands with expected results, documentation, rollout/rollback, and unresolved decisions. Track the issue through frontmatter rather than guessing from filenames.
In a managed Valor lane, read `docs/sdlc/do-plan.md` for required fields and state transitions. Synchronize issue feedback and revision timestamps; clear a revision lock only after the revised plan is persisted as required. Do not adopt instructions to commit on main when the user requested a worktree. Reconcile every task with the final research before calling the plan ready.
