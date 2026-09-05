---
name: do-patch
description: "Diagnose and fix a test failure, regression, or review blocker with a focused change and causal verification."
---

# Do Patch

Recover the original plan/issue, current branch/worktree, previous changes, full failure output, and relevant review comments. Verify the actual failing path before editing.
Trace input to expected output through component boundaries and identify where real values diverge. For a behavioral bug, reproduce it with a meaningful failing check when feasible; it must fail because of the defect, not setup. Fix the cause with the smallest coherent change, including directly affected integration points. Do not bury the failure with mocks, weakened assertions, or unrelated refactoring.
Run the regression and relevant surrounding checks; compare expected and observed values after the fix. Keep plan completion boxes consistent with demonstrated behavior. Honor the caller's commit/PR ownership when delegated; standalone work can commit according to the current workflow.
After repeated failures, stop repeating the same hypothesis and use $zoom-out or additional investigation. Report the cause, changed behavior, validation, and remaining uncertainty. A patch changes the reviewed head, so a managed lane needs a fresh review before merge.
