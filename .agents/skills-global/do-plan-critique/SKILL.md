---
name: do-plan-critique
description: "Review a development plan for feasibility, missing requirements, complexity, failure modes, and verifiable acceptance criteria."
---

# Do Plan Critique

Read the entire plan, tracking issue and comments, relevant prior work, and actual cited source files. Check structural requirements and whether verification can prove the intended outcome. Inspect assumptions through skeptical, simplifying, and domain-specific lenses; scale depth to risk.
If independent reviewers are available and authorized, freeze a roster and give each the plan plus raw evidence and a distinct lens. Otherwise perform the lenses sequentially and label the result as a single-reviewer critique. Do not claim independent corroboration. Account for every reviewer before aggregating; allow at most two retries for missing reports and return `CRITIQUE INCOMPLETE` if the roster cannot finish. A changed plan invalidates findings tied to its old hash.
Verify, deduplicate, and rank findings as BLOCKER, CONCERN, or NIT, with evidence and concrete implementation notes. Agreement alone does not turn a preference into a blocker.
Return `READY TO BUILD (no concerns)`, `READY TO BUILD (with concerns)`, `NEEDS REVISION`, `MAJOR REWORK`, or `CRITIQUE INCOMPLETE`. Nits do not block. Concerns require recorded disposition and, in a managed lane, its bounded revision/re-critique procedure. Read `docs/sdlc/do-plan-critique.md` for the actual verdict/roster/lock substrate; persist evidence and verdict together when that lane is active. A standalone critique does not create a managed session.
