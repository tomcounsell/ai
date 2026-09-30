---
name: do-issue
description: "Turn a development request or confirmed defect into a researched, actionable GitHub issue with acceptance criteria."
---

# Do Issue

Resolve the repository and user intent, then inspect current code, related issues/PRs, and relevant logs or docs. Verify cited symbols still exist and distinguish observed behavior, root-cause evidence, and hypotheses. Search for duplicates and prior fixes before creating a new issue.
Write the problem in user terms with expected/actual behavior, reproduction evidence when applicable, affected scope, acceptance criteria, relevant files, constraints, and prior art. Include negative criteria where they materially constrain the solution. Do enough recon that planning can begin without rediscovering the premise; do not prescribe an unverified implementation as the only answer.
Use actual repo labels. In Valor there is no generic `feature` label; do not invent one. Unverified anomalies fit $do-investigation-issue. Capture actionable instructions from user feedback without copying secrets or unrelated private material.
When issue creation is requested or part of the authorized workflow, publish using a structured tool or a uniquely allocated body file with `gh issue create --body-file`. Verify the created issue and return its URL. If only drafting was requested, return the Markdown draft.
