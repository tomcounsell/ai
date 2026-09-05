---
name: imagine-agent
description: "Turn a client outcome into an evidence-based agent build-sheet, including scope, evaluation criteria, delivery, and runtime."
---

# Imagine Agent

Interview in the client's language about users, desired outcome, what good looks like, cadence, and delivery. Recover answers already supplied. Research the target repo for capabilities, connected services, auth patterns, standards, and reusable tools before asking the client technical questions.
Write a build-sheet with original client wording separated from derived technical decisions. Include target repo, runtime/provider, account ownership, instructions, required tools/data, permission boundaries, three to six observable success criteria, eval examples, schedule/timezone if requested, delivery, and deferred work with its concrete dependency. Do not label simulated integrations as working.
Preserve an explicitly selected provider. If the user wants Codex, specify Codex skills/tools and supported automation; if they selected Claude Managed Agents, use [the CMA build-sheet contract](../build-agent/references/build-sheet.md) without pretending it is an OpenAI API schema. Leave unverified model choices unresolved until current docs and availability are checked.
Produce the reviewable build-sheet before any final authorization needed for deployment. Continue into $build-agent when implementation is part of the user's request; a design-only request ends with the spec.
