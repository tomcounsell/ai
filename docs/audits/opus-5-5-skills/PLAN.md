# Opus 5.5 fit audit: skills and agents

Reference: prompting-opus-5-5.md (same directory). Every subagent gets that file plus its cluster's files, nothing else seeded.

## Track A: skills that run on Opus (inherit the session model)

About 52 skills plus the agents with no `model:` pin. Seven analyst subagents on Opus, one per cluster:

1. SDLC core: do-sdlc, sdlc, do-plan, do-plan-critique, do-build, agent dev (pinned opus; check effort), .claude/skills/_shared/test-quality.md
2. SDLC support: do-pr-review, do-patch, do-test, do-docs, do-merge, do-issue, do-investigation-issue
3. Design/visual: frontend-design, do-design-audit, do-design-system, pen-design, present, do-presentation, mermaid-render
4. Comms/social: de-slop, authenticity-pass, linkedin, x-com, email, telegram, do-debrief, do-voice-recording
5. Audits/meta: audit-skills, audit-hooks, audit-models, audit-tools, new-skill, new-audit-skill, do-integration-audit, do-discover-paths, reclassify
6. Infra/setup/improve: update, setup, prime, do-deploy, do-deploy-example, checking-system-logs, sentry, rsi, improve-research, improve-preflight, build-agent, imagine-agent
7. Personal/tools + unpinned agents: ask-me, grill-me, zoom-out, ontologies, weekly-review, calendar-sync, cowork, google-workspace, computer-use, officecli, ebook-ingest, reading-sms-messages; agents builder, code-reviewer, plan-maker, plan-reviewer, baseline-verifier, cruft-auditor, test-engineer, strategic-analyst

Checklist per skill (from the guide):
- Effort: what effort does this work need? Propose `effort:` (low / medium / high; xhigh+max only with a named quality reason).
- Thinking substitutes: "think carefully", "reason step by step", "write out your reasoning" lines. Remove, or flag reasoning_extraction refusal risk.
- Early stops: long multi-part skills run headless by the worker. Does it keep a checklist, name the stops it wants, and treat a text-only turn as a report?
- Background completion: does it wait for its own background commands/subagents before declaring done?
- Progress updates: does it ask for a one-line intent and an end recap where a human watches?
- Fan-out skills: do they give subagents a time budget or elapsed-time signal?
- Untrusted input: does it ingest pasted or fetched content (email, telegram, linkedin, x-com, web)? Mark it as data.
- Multi-app: explore-before-acting line for skills that span apps (calendar-sync, google-workspace, email, cowork).
- Frontend: do design skills name specific banned patterns rather than "avoid generic AI look"?
- Visual: scaffolding for charts/screenshots that Opus 5.5 no longer needs, or missing crop/zoom tools where it still helps.
- Over-scaffolding: instructions compensating for older-model weaknesses that can now be deleted (shorter bodies are a win).

Output: the rubric.md findings schema plus `opus55: {effort, remove[], add[], notes}`. Every "remove" and every effort below medium goes to a refute-first verifier.

## Track B: skills and agents pinned to Sonnet or Haiku

The question here is placement: is the model choice still right? Opus 5.5 at low effort may beat Sonnet on agentic coding at comparable cost. Reading the prompt cannot answer that, so this track is measured.

Inventory:
- SDLC stage table (do-sdlc + sdlc router): ISSUE, BUILD, TEST, PATCH, DOCS, MERGE → sonnet
- do-plan-critique: classifier + every critic → sonnet
- do-test/parallel-dispatch.md: test runners → sonnet
- new-skill/AGENT.md template defaults new agents to sonnet
- Agents on sonnet: documentarian, frontend-tester, sentry, stripe, validator
- Agents on haiku: linear, notion, render
- config/models.py: SONNET/OPUS constants still name 4.5-era IDs (stale, separate fix)

Steps:
1. Pricing and model facts from the claude-api skill (never from memory): Sonnet 5 vs Opus 5.5 per-token cost and speed.
2. Classify each pin: mechanical (runs a script, formats output) vs judgment (writes code, triages failures, decides).
3. For judgment pins (BUILD, PATCH, critique critics, validator): replay 3 real past runs from transcripts/ledger on Sonnet 5 vs Opus 5.5 at low vs Opus 5.5 at medium. Record tokens, wall time, cost, and outcome (tests pass, findings match the known answer). Same inputs, same checkout state.
4. Mechanical pins stay on Sonnet or Haiku, and their prompts get reviewed against the Sonnet/Haiku prompting guidance (Opus 5.5 advice does not transfer).
5. Output per pin: keep / move to opus+effort / move to haiku, with the measured numbers.

## Synthesis

One report, one row per skill and agent (fails if any is missing). Recommendations live in this PR; no GitHub issues are filed. The audit itself edits nothing.

Agent count: 7 Track A analysts + ~10 verifiers + 2 Track B analysts + the replay runs (~9 short sessions). Run in batches under the 10-agent guideline, or as a Workflow if Tom opts in.
