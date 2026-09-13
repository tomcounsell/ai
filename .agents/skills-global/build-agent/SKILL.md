---
name: build-agent
description: "Implement, evaluate, and launch an agent from a build-sheet using the selected Codex, OpenAI API, or Claude Managed Agents runtime."
---

# Build Agent

Read the build-sheet and validate its target repo, runtime, account ownership, outcome, tools, data access, eval criteria, delivery, and optional schedule. Resolve missing technical details from the repo. Preserve the chosen product; a local Codex task, OpenAI API service, and Anthropic managed agent are different deployments.
For Codex work, build maintained skill instructions and callable tools; use native task/automation capabilities only within their stated creation and scheduling scope. For an OpenAI API agent, use the current official OpenAI documentation and installed agent tooling. Do not translate CMA endpoint names into invented OpenAI endpoints.
For an explicitly selected CMA deployment, read [CMA primitives](references/cma-primitives.md) and [its build-sheet schema](references/build-sheet.md), then verify current Anthropic documentation before API calls. Those references describe the product being operated, not the identity or toolset of Codex.
Stage configuration, instructions, kickoff input, grading rubric, and held-back evals. Save object IDs for resumability without credentials. Run one observable task, inspect outputs yourself as well as grader results, revise the smallest relevant variable, then run held-back evals against the pinned version. Report honest pass/fail evidence.
Schedule only if requested, with relative dates, timezone, deduplication, and a verified test run. Record launch instructions, live IDs, limitations, and next steps. A fallback to another runtime needs user agreement when it changes the requested product.
