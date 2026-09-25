# Opus 5.5 fit audit: synthesis

Inputs: `PLAN.md`, `reference-prompting-opus-5-5.md`, the seven analyst reports (`cluster-1..7-*.md`), the seven refute-first verifier reports (`verify-cluster-1..7-*.md`), and the Track B model-placement report (`track-b-model-placement.md`). Where a verifier and an analyst disagree, the verifier's ruling is used here. On model choice, Track B's measured results win. On effort level, a Track A verifier's refutation wins over a Track B proposal. Refuted items are dropped from the body and listed in the appendix. Modified items appear in the verifier's corrected form. Analyst recommendations the verifiers did not rule on (mostly additive lines at medium effort) stand as written in the cluster reports.

## 1. Headline

The audit covered all 43 global skills, all 18 project skill directories plus `.claude/skills/README.md`, all 17 repo agents, and the two unmanaged `~/.claude/agents` extras (designer, ui-ux-specialist). The coverage check found no gaps. Verifiers ruled on 189 recommendations: 97 confirmed, 58 modified, 34 refuted, so 155 survive. By cluster (confirmed/modified/refuted): SDLC core 15/9/4, SDLC support 25/13/2, design and visual 9/3/6, comms and social 11/7/7, audits and meta 11/5/2, infra and improvement 10/11/7, personal tools 16/10/6. The biggest fleet-wide finding is structural. Claude Code 2.1.282 honors `effort:` in skill and agent frontmatter, but the repo lint flags the field as unknown, and the best-practices sync is built in a way that can never detect new fields. That is why no skill sets effort today while every worker run happens at `high`. Most proposals to drop below medium effort were refuted, because the skills in question gate merges, publish outward-facing text, or carry their effort into whatever the session does next. The rest of the surviving work falls into three recurring prompt gaps (headless skills with no named stops, fetched third-party text with no data framing, fan-out without a time signal) and about 30 real bugs that do not depend on which model runs. The worst are calendar-sync rewriting real meetings, do-plan popping a foreign stash, and do-docs refusing every push outside this repo. Retirements are straightforward, but each one needs a `RENAMED_REMOVALS` entry, and about 20 earlier retirements never got one and still load on every machine. Track B replayed four real cases under three configs (12 runs, about $9.50) and found Opus 5.5 cheaper per task than Sonnet 5 and two to five times faster in model time, so BUILD, PATCH, and the plan critics should move to Opus, while mechanical pins stay on or move down to Haiku.

## 2. Fleet-wide findings

**Effort is a real field, with specific inheritance rules.** Claude Code 2.1.282 reads `effort:` in skill and agent frontmatter. A skill's effort does not propagate to subagents spawned with the Agent tool; those take effort from their agent definition, else from the session. It does carry into skills invoked through the Skill tool, unless the invoked skill declares its own. The Agent tool call accepts `model` but not `effort`, so the only way to give a subagent role a fixed effort is an agent definition with `effort:` in its frontmatter (this is why the review-judge recommendation creates a new agent). A practical consequence: any skill that can run nested under a lowered skill should declare its own `effort:`.

**Today every worker run is at `high`.** `~/.claude/settings.json` sets `effortLevel: high` (seeded fleet-wide by `hardlinks.py` when the key is missing), and the worker's `claude -p` argv never passes `--effort`. Interactive sessions on this machine run with `CLAUDE_EFFORT=medium`. Per the guide, Opus 5.5 at medium matches or beats Opus 5 at high, so explicit per-skill pins are both a cost lever and a guard against drift in the global default.

**The lint must be fixed before any skill adopts effort.** `audit_skills.py` `KNOWN_FIELDS` lacks `effort`, `when_to_use`, `arguments`, `disallowed-tools`, `paths`, and `shell`, so rule 11 warns on each adoption. `sync_best_practices.py` only surfaces doc fields already in its hardcoded list, so it cannot detect a new upstream field by construction.

**`allowed-tools` pre-approves and never restricts.** `disallowed-tools` (skills) and `disallowedTools` (agents) restrict. Agent `tools:` lists are honored (plan-reviewer transcripts show it). The repo holds the opposite belief in two places, `new-skill/AGENT.md:45` and the docstring of `tests/unit/test_skill_agent_tool_consistency.py`, and both should be corrected. The do-design-audit and mermaid-render "no Read tool" bug claims were refuted on this basis.

**Everything global ships to every repo.** Every `.claude/agents/*.md` and every skills-global skill is hardlinked into `~/.claude` fleet-wide. Fixes must stay repo-agnostic: no hardcoded `tools/...`, `scripts/pytest-clean.sh`, or `sdlc-push-guard` in a global body. Name the repo's runner or formatter through `.claude/skill-context/{skill}.md` or `docs/sdlc/{skill}.md`, and bundle helper scripts inside the skill directory. Several analyst fixes were modified for this reason (validator, builder, documentarian, do-test's swallow-gate script, baseline-verifier).

**Retirement needs `RENAMED_REMOVALS`.** `_cleanup_stale_commands` removes a `~/.claude` copy only when its inode matches a current source, so deleting a source file orphans the copy on every machine. About 20 pre-pivot agents (agent-architect, async-specialist, bowser, red-team, scout, designer, ui-ux-specialist, and others) already sit orphaned with link count 1 and still appear in every session's agent list. Each retirement also needs `scripts/sync_claude_to_opencode.py` rerun and its roster and listing docs updated.

**`context: fork` cannot serve as a synchronous gate.** A forked skill returns only a "launched (forked execution, running in the background)" message to its caller, and a fork gets one turn. The de-slop `context: fork` fix for #2684 was refuted for this reason. The working shape for a cold-read gate is a foreground Agent dispatch (`run_in_background: false`) whose prompt passes only the draft path, medium, and audience and invokes the skill, with the caller waiting on PASS/BLOCK.

**Efforts below medium mostly failed verification.** The ones that survived: `baseline-verifier` (low, or convert to a script), `cruft-auditor` (low), `reclassify` (low), `do-deploy` (low), the `sentry` skill (low), and `audit-hooks` (low only after its deterministic steps are scripted; medium until then). The customize-step advice in `do-deploy-example` ("set `effort: low` unless the deploy involves judgment") also survived. From Track B, do-patch at low is measured (one case, matching Sonnet at half the cost); DOCS, documentarian, frontend-tester, and validator at low are inferred only. Track B's TEST at low loses to verify-cluster-2's refutation and stays medium, and its ISSUE at low stays medium because verify-cluster-2 refuted low for the sibling do-investigation-issue on reasoning that applies equally. `do-voice-recording` at low is harmless standalone but carries into do-debrief's send step, so it stays unset. Refuted lows: do-sdlc, sdlc, do-test, do-merge, do-investigation-issue, test-engineer, mermaid-render, pen-design (CLI path), authenticity-pass, telegram, reading-sms-messages, audit-skills, the proposed strategic-lens agent, update, prime, improve-preflight, weekly-review, ebook-ingest. The recurring reasons: the skill gates something costly (merge, publish, paid freeze), the skill is pulled in mid-task so its low effort leaks into the rest of the turn, or there were zero runs so nothing would be saved.

**Headless skills need named stops.** The guide's advice for unattended agentic runs applies to every skill the worker runs without a human: a checklist, a statement of which stops are wanted, and "a text-only turn with open items is a progress note." Gaps found in do-sdlc supervisor mode (its per-iteration progress note ends the one-turn fork), do-build, agent dev, do-plan (Phase 3/4 "please answer" replies), do-issue RECON (waits for confirmation, a confirmed bug), authenticity-pass line 19 ("prompts if none"), zoom-out's closing question, do-debrief's review gate, the linkedin and x-com default runs, improve-research, and the agent recipes emitted by build-agent and imagine-agent. Human-in-the-loop skills (ask-me, rsi, setup) already name their stops and should not get the unattended block. One harness fact matters here: research sessions carry no `classification_type`, so a text-only stop ends them with no nudge, and the generic `NUDGE_MESSAGE` ("only stop when you need human input") is worded for chat sessions.

**Fetched third-party text needs data framing.** No skill that ingests outside text marks it as data. Highest risk: linkedin and x-com (DMs, posts, and profiles drive public replies, follows, and writes to `~/work-vault`; the drafter templates paste post text between `<<< >>>` with no note). Also email, telegram group reads, reading-sms-messages (2FA codes), do-debrief's collect phase, google-workspace, do-discover-paths (drives Tom's logged-in Chrome), checking-system-logs (bridge.log carries inbound Telegram text), improve-research web results, do-plan and do-investigation-issue (issue comments, web research), and frontend-tester (page text). The fix is one plain line per skill, plus the guide's tagged `pasted_content` form in drafter templates and an "observed facts only" rule for vault writes.

**Fan-out prompts carry no time signal.** do-plan-critique critics, the do-build builder template, the do-sdlc stage template, audit-skills `--arch`, audit-tools full runs, the new-audit-skill template, do-integration-audit's falsifier, strategic-analyst's 11 subagent prompts, and imagine-agent's Phase 2 Explore fan-out. Use an explicit budget where the work has a known size (builder: from plan appetite) and the guide's "time matters here" line where it does not (stage dispatch).

**Design skills name the old tells, not Opus 5.5's own defaults.** The guide names five Opus 5.5 fallbacks (cream or off-white ground, italic accent words in headlines, numbered 01/02/03 section labels, monospace labels, pill buttons). frontend-design covers one of them partly, and present prescribes one. Emoji bullets, one of Tom's global bans, appear in no design skill. Proposal: one shared `frontend-design/reference/anti-patterns.md` that frontend-design, do-design-audit, and present point to. do-presentation's Yudame House theme uses three of the five on purpose and keeps them.

**Templates propagate the gaps.** new-skill and new-audit-skill templates should generate `effort:`, a "Done when" checklist, an untrusted-input line where relevant, and a time-budget line for fan-out, so new skills start right.

## 3. Real bugs

Verified CONFIRMED or MODIFIED, independent of which model runs. Ordered by severity (data loss or fleet-wide breakage first).

| # | Item | Bug | Evidence | Fix |
|---|---|---|---|---|
| 1 | calendar-sync | Step 6 updates any time-overlapping event in place, real meetings included, with no approval gate; the calendar mapping can resolve to `primary` | `calendar-sync/SKILL.md:89-92`, `:59`, `:50`, `:25-26` | Tag events this skill creates and update only tagged events |
| 2 | do-plan | Bare `git stash --include-untracked` / `git stash pop`; with a clean tree the pop restores another session's stash (the stack is repo-wide), and the worktree hook blocks the push but allows the pop | `do-plan/SKILL.md:292-296`; `destructive_git_shapes.py:145-157` | Commit the plan file by path from a main checkout or through a main worktree; no stash |
| 3 | do-docs | Global body runs `sdlc-push-guard --assume-head \|\| exit 1`; in any other repo the command is missing, so every push is refused | `do-docs/SKILL.md:309-322` | Move the block to `.claude/skill-context/do-docs.md`; delete the NOTE at `:303-307` |
| 4 | do-deploy | Rollback `git revert HEAD` reverts whatever landed last on main | `do-deploy/SKILL.md:135` | `git revert {MERGE_COMMIT} --no-edit && git push origin main` (merges are squash, single-parent, so no `-m 1`) |
| 5 | do-issue | RECON waits for user confirmation; a pipeline ISSUE stage ends on the question without filing | `RECON.md:100,103,111`; dispatched unattended from `do-sdlc/SKILL.md:89,113`, `agent/sdlc_router.py:155` | Confirm only when a human invoked it; in pipeline runs, proceed |
| 6 | do-issue | Default `TYPE="feature"` passed to `--label`; the label does not exist here, and CLAUDE.md forbids it | `do-issue/SKILL.md:144`, `CHECKLIST.md:43`; `gh label list` | Drop the type-label default; take labels from the context file |
| 7 | linkedin, x-com | SKILL.md promises de-slop and authenticity-pass on every post, comment, reply, and DM; only the post path runs them, so public comments and replies ship ungated | `linkedin/SKILL.md:24`, `feed-engagement.md:231-239`, `messages.md:57-76`; `x-com/SKILL.md:22`, `timeline-engagement.md:256` | Add both gates to the comment, reply, and DM paths via foreground Agent dispatch (keep the promise; DMs go to leads) |
| 8 | do-build | Step 5.5 `cd $(git rev-parse --show-toplevel)` resolves to the worktree root, the state it claims to repair | `do-build/WORKFLOW.md:173-181`; reproduced | `cd "$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"`; `TARGET_REPO` (`SKILL.md:80`) has the same flaw |
| 9 | do-build | Step 3.5 marks a builder FAILED for "no changes" when it committed cleanly, since `git diff HEAD` and porcelain are both empty | `do-build/WORKFLOW.md:94-106`, `SKILL.md:162` (from git semantics; not reproduced live) | Record the pre-task HEAD; compare `git diff --stat <pre>..HEAD` plus porcelain |
| 10 | do-patch / builder | do-patch says "do not commit" and relies on single-commit authorship; builder's safety net always commits partial work as `[WIP]` | `do-patch/SKILL.md:134,222-229`; `builder.md:162-169,192-196` | do-patch's builder prompt overrides the commit rules, including the WIP safety net |
| 11 | do-debrief, do-voice-recording | Voice delivery via `valor-telegram send`, which agents must not use; `has_pm_messages()` stays false, so the session's closing text is also delivered | `skill-context/do-debrief.md:33-41`, `skill-context/do-voice-recording.md:57`; `telegram_bridge.py:3234-3247` | Add voice-note support to `tools/send_message.py` (cleaner), or document the exception and its side effect in both context files and `telegram/SKILL.md:21` |
| 12 | improve-research (harness) | Research sessions have no `classification_type`, so a text-only stop ends the session with no continuation; nothing enforces completion | `output_router.py:185-189`, `scheduler_adapter.py:254-273` | Early-stop block in the research dispatch system prompt keyed on `research_case_id`, or a bounded open-item continuation; plus the skill's done-list |
| 13 | baseline-verifier | Fixed `/tmp/baseline-results.xml` and a second-resolution worktree dir collide across lanes; bare `python -m pytest` | `baseline-verifier.md:51-52,94,115` | Script bundled in the do-test skill with `mktemp -d` paths and a venv the repo runner accepts |
| 14 | officecli | Pinned v1.0.29 lacks `help`, `swap`, `mark`, `dump`, `goto`, `save`, `set --prop find=`, and `view screenshot`, all documented in the body | `scripts/update/officecli.py:36`; direct runs | Test a newer release against every documented command, then bump the pin; otherwise delete the unsupported sections |
| 15 | pen-design, do-design-system | Addendum names MCP tools the Pen 1.2.8 server no longer exposes; `:164` wrongly says `.pen` files are encrypted (they are plain JSON) | server binary `strings`; `pen-design/SKILL.md:164-193,279,316`; `references/pen-editing.md:31-32,110,118` | Rewrite the MCP tool reference from `mcp__pen__read_skill`; fix `:164` |
| 16 | audit_skills.py, sync_best_practices.py | `KNOWN_FIELDS` lacks `effort` and five other valid fields; the sync can only ever report fields already in its hardcoded list | `audit_skills.py:70-83,426-433`; `sync_best_practices.py:233-240,310-311` | Add the fields (plus an effort value check); make the sync diff real doc fields against the list |
| 17 | update | Line 21 tells the operator to `git stash` (repo-wide stack) | `update/SKILL.md:21` | "If local changes block the checkout, commit them on a WIP branch" |
| 18 | audit-hooks | Latent false FAIL on the `__hook_rc` crash guard, whose suggested fix (`\|\| true`) would disable a deliberate exit-2 deny. Also reads only the project settings scope, missing 15 user-scope hooks | `.claude/settings.json` `pre_tool_use.py` entry; `audit-hooks/SKILL.md:22,31-35` | Explain the guard in `skill-context/audit-hooks.md`; audit both scopes |
| 19 | new-skill, test docstring | AGENT.md says `tools:` lists are "silently ignored" (false); the consistency test's docstring calls `allowed-tools` a restriction | `new-skill/AGENT.md:45`; `test_skill_agent_tool_consistency.py:3-6` | Rewrite both to the settled semantics |
| 20 | stripe, linear, notion, render agents | `permissions:` is not an agent field, so the gates do nothing; globs match no configured tool | 2.1.282 agent field list | Moot: retire (section 5) |
| 21 | do-docs | Skill-context stage markers omit `--run-id` and hide failures with `2>/dev/null` | `skill-context/do-docs.md:16,26` | Pass `--run-id`; drop the redirect |
| 22 | do-issue, do-investigation-issue | Start marker passes `--issue-number` before the issue exists, failure hidden; the `draft-owner` anchor line is published as the first line of every issue body | `skill-context/do-issue.md:12`; `do-issue/SKILL.md:114-153`; `do-investigation-issue/SKILL.md:45-84` | Drop the early marker; replace the anchor ritual with `mktemp` plus `--body-file -` |
| 23 | frontend-tester | Output field `ERRORS:` asks for console errors, but no step collects them | `frontend-tester.md:45-58,76-77` | Add a `browser_get_console_logs` step |
| 24 | do-presentation | Step 6 invokes mermaid-render, which is `disable-model-invocation: true`; a dead path with a fenced-code fallback | `do-presentation/SKILL.md:196-198`; `mermaid-render/SKILL.md:7` | Read mermaid-render's SKILL.md and follow Workflow B inline |
| 25 | calendar-sync | `allowed-tools` names `mcp__claude-in-chrome__*` tools no configured server provides | `calendar-sync/SKILL.md:11-17` | Name BYOB and `mcp__claude_ai_Google_Calendar__*` |
| 26 | cowork | Says a headless agent cannot create or verify a routine; the built-in `/schedule` skill (`RemoteTrigger`) does both | `cowork/SKILL.md:61-68` | Route through `/schedule` with human confirmation before `create`. Availability inside worker `claude -p` turns is unverified |
| 27 | config/models.py | Stale 4.5-era IDs: `SONNET`, `OPUS`, `MODEL_BEST`, `MODEL_REASONING`, and the OpenRouter constants name Opus 4.5 and Sonnet 4.5. `_MODEL_ALIASES` maps `opus` to 200K while sessions on the `opus` alias run Opus 5.5 with a 1M window, so context warnings fire early. They are not skipped: the "silently skipped" claim was refuted, since the lookup receives the alias and resolves it | `_MODEL_ALIASES["opus"]`; `settings.py:1147-1148`; Track B Python table | Repoint the aliases; add Opus 5.5 and Sonnet 5 `MODEL_INFO` entries; repoint `MODEL_REASONING` to `claude-sonnet-5` or delete it (no production caller found) |
| 27a | tools/paid_inference_meter.py | `PRICE_TABLE` lacks `claude-opus-5-5` and `claude-sonnet-5`; an unknown model falls back to $3/$15, under-pricing Opus 5.5 output when a response carries no `usage.cost` | Track B Python table | Add `claude-opus-5-5` ($4/$20) and `claude-sonnet-5` ($2/$10) |
| 28 | build-agent | `cma-primitives.md:127-128` `repository`/`repository_url` bullet contradicts the verified `github_repository` + `url` shape | `cma-primitives.md:117-128` | Rewrite with the verified shape; keep the use-case sentence |
| 29 | do-build addendum, validator, builder | Instruct bare `pytest` (and validator `black --check`) against repo rules | `docs/sdlc/do-build.md:227`; `validator.md:31,47,53,55`; builder TDD cycle | Addendum: `scripts/pytest-clean.sh`. Global agents: "the project's test runner / formatter check", named via context |
| 30 | do-plan | Slug case conflict (snake_case vs kebab-case; every plan is kebab); `grep -oP` fails outside Claude Code's shell function | `do-plan/SKILL.md:345,429`; `docs/sdlc/do-plan.md:162` | Kebab-case; `grep -oE` |
| 31 | agent dev | "Before opening a PR, run `/do-plan-critique`"; critique gates BUILD | `dev.md:26` | "Before building" |
| 32 | documentarian | Documentation map names directories and files that do not exist | `documentarian.md:38-55,156` | "Read `docs/README.md` and list `docs/` before editing"; drop `:156` |
| 33 | do-plan-critique addendum, do-sdlc, sdlc | Contradictory instructions: the addendum's "MCP server exposure" vs do-plan's definition; do-sdlc Step 3.4 "when in doubt, dispatch `/do-docs`" vs Hard Rule 2; sdlc rule 5 vs the `skipped` PLAN state | `docs/sdlc/do-plan-critique.md:96`; `do-sdlc/SKILL.md:232`; `sdlc/SKILL.md:38-43` | Align to do-plan; delete Step 3.4; decide the plan-less path on purpose |

## 4. Master table

Effort is post-verification. "unset" means leave the field out so the session value applies. Model changes from Track B are written into the top-change column and tagged "TB measured" (replayed) or "TB inferred" (extended from the measured pattern, not replayed; measure one real run before landing). Disposition values: keep, keep + trim, keep + add, script, retire.

| Item | Scope | Effort | Disposition | Top surviving change | Cluster |
|---|---|---|---|---|---|
| ask-me | global | medium | keep | Delete the Anti-Patterns section (each bullet repeats a step); reword line 34 without the write-out framing | 7 |
| audit-hooks | global | medium (low after scripting) | keep | Explain the `__hook_rc` guard in skill-context; audit both settings scopes; point Steps 1 and 4 at the `hooks_audit.py` reflection output | 5 |
| audit-models | global | medium | keep | Fix the line-80 example that contradicts the naming convention | 5 |
| audit-skills | global | medium | keep + add | Add `effort` and the other valid fields to `KNOWN_FIELDS`; fix the sync; rubric lens 4 becomes "model tier and effort"; rule 22 narrowed to `think carefully`, `think step by step`, `reason step by step` | 5 |
| audit-tools | global | medium | keep | Per-tool fan-out with a time budget; drop "(now `/new-skill`)" | 5 |
| build-agent | global | medium | keep + add | Record effort in the build-sheet only (send it in the payload after one live create confirms the field); early-stop block for scheduled agents; fix the repository bullet and the stale model example | 6 |
| calendar-sync | global | medium | keep | Update only events this skill created (bug 1); fix `allowed-tools`; keep line 55 | 7 |
| computer-use | global | medium | keep + trim | Look at a screenshot before coordinate clicks; delete addendum lines 112-116 and 146-151 | 7 |
| cowork | global | medium | keep + trim | Route routine creation and verification through `/schedule` with human confirmation; delete status banners | 7 |
| de-slop | global | medium | keep | Stay inline-invokable; rewrite line 22 to prescribe the caller's foreground Agent dispatch; add `skill-context/de-slop.md` with the em-dash rule; gate the PROSE.md read on length | 4 |
| do-build | global | medium | keep | Stage model sonnet to opus at medium in the stage tables and persona (TB measured, 2 cases); end-of-body early-stop paragraph; fix Steps 5.5 and 3.5 (bugs 8, 9); builder-template time budget; one-sentence TaskCreate note; keep Step 4.5 | 1 |
| do-debrief | global | medium | keep | Resolve voice delivery (bug 11); headless form of the review gate; data line for collected chat and PR text; drop line 87 | 4 |
| do-deploy-example | global | low (template) | keep + trim | One-sentence `$ARGUMENTS` fallback; customize step: "set `effort: low` unless the deploy involves judgment" | 6 |
| do-design-audit | global | medium | keep | Tell Step 3 to open each screenshot with Read; add Read to `allowed-tools` as pre-approval; mobile and @2x captures with a mandatory `preset='desktop'` reset; point at the shared anti-patterns list | 3 |
| do-design-system | global | medium | keep | Keep the plain-JSON `.pen` handling; refresh stale MCP names in `references/pen-editing.md` | 3 |
| do-discover-paths | global | medium | keep | Mark page content as untrusted data; fix the "comment" in a JSON trace | 5 |
| do-docs | global | low (TB inferred; medium until measured) | keep | Stage model sonnet to opus at low (TB inferred); move the push-guard block to skill-context (bug 3); `--run-id` on markers; fold Agent A inline after launching B and D; Tom decides the plan-file rule conflict | 2 |
| do-integration-audit | global | medium | keep | Time budget for the falsifier; remove duplicated bullets `:53-55` | 5 |
| do-investigation-issue | global | medium | keep | `mktemp` plus `--body-file -`; mark external research as data | 2 |
| do-issue | global | medium | keep | Stage model sonnet to opus (TB inferred), at medium since Track B's low conflicts with verify-cluster-2 row 31's reasoning; headless path through RECON; fix the label, start marker, and anchor (bugs 5, 6, 22) | 2 |
| do-merge | global | medium | keep + trim | Stage model stays sonnet pending one measured merge (TB); trim the addendum's predicate internals while keeping every test-pinned string; delete `:298-302` | 2 |
| do-patch | global | low (TB measured, 1 case) | keep | Stage model sonnet to opus at low; override builder commit rules including the WIP net (bug 10); merge the unique lint sentence into `:36-40` | 2 |
| do-plan | global | medium | keep | Replace the stash block (bug 2); name the pipeline stops for Phase 3/4 questions; widen the propagation check to Data Flow, Risks, Rabbit Holes, Verification; kebab slugs | 1 |
| do-plan-critique | global | medium | keep | Critics move from sonnet to a new `plan-critic` agent (`model: opus`, `effort: medium`) (TB measured, 1 case: Sonnet missed both known blockers); triage classifier to haiku; critic time budget; add IMPLEMENTATION NOTE to the template (tidy-up); rename the Skeptic/Simplifier rule to the sub-sections; positive "plan edits belong to `/do-plan`" line | 1 |
| do-pr-review | global | high | keep + trim | Replace the four false-alarm-bias lines with one calibrated blocker line; fold the checklist into the rubric per the verifier's mapping (update the pinned test); cut repeated finalize guidance; dispatch judges as a new `review-judge` agent at high | 2 |
| do-presentation | global | medium | keep | Read mermaid-render inline (bug 24); per-slide PNG visual pass; de-slop via foreground Agent with a literal `run_in_background: false` | 3 |
| do-sdlc | global | medium | keep + trim | Update the stage-to-model table and the "dispatching BUILD on sonnet" prose to Track B's placement; progress notes ride with the next tool call; end-of-body "how this run ends" paragraph; move the guard table and 3a-3e to `GUARDS.md`, keeping "Known gap" and "Merge gate" in the body; delete Step 3.4 | 1 |
| do-test | global | medium | keep | Stage model sonnet to opus (TB inferred) at medium, since verify-cluster-2 refuted Track B's low; suite and lint runners spawn on haiku with capped output (TB inferred); swallow-gate script bundled in the skill directory; delete the closure and empty-input scans; move router-fixture notes to `tests/README.md` | 2 |
| do-voice-recording | global | unset | keep | Shares do-debrief's delivery fix | 4 |
| email | global | medium | keep | Data line for inbound mail; explore-before-acting line for replies; drop the `npm install -g` line | 4 |
| frontend-design | global | medium | keep | Shared `reference/anti-patterns.md` with Opus 5.5's five defaults and emoji bullets; remove `:63` | 3 |
| google-workspace | global | medium | keep | Explore-before-acting and data lines; keep the full-day window and `attendeeResponseStatus` | 7 |
| grill-me | global | medium | keep + trim | Remove duplicates `:53` and `:57`; use contradictions with earlier answers | 7 |
| imagine-agent | global | medium | keep + add | Early-stop block in the Phase 3 `agent.system` recipe for scheduled agents; Phase 2 fan-out time budget | 6 |
| mermaid-render | global | medium | keep | Remove the "Claude is multimodal" parenthetical; add Read to `allowed-tools` as pre-approval; add an edge-endpoint check; keep the topology table and the export-dialog section | 3 |
| new-audit-skill | global | medium | keep | Template gains `effort:`, a fan-out time budget, and an untrusted-input line; drop "Version history" | 5 |
| new-skill | global | medium | keep | Fix AGENT.md:45 (bug 19); AGENT.md template omits `model` by default and documents `effort` (opus with explicit effort for judgment, haiku for mechanical); templates generate `effort:`, "Done when", untrusted-input, and time-budget lines; soften SESSION_CAPTURE caps | 5 |
| ontologies | global | medium | keep | Rewrite `:77` for first creation; drop `:76`; replace the `:28` announcement | 7 |
| pen-design | global | medium | keep | Rewrite the MCP tool reference below the addendum marker; fix `:164`; trim the description to the pen.dev surface | 3 |
| present | global | medium | keep | Render-and-look step before delivery; point at the shared anti-patterns list | 3 |
| reclassify | global | low | keep | Set `effort: low`; fix the context file's "enforced by hooks" header | 5 |
| weekly-review | global | medium | keep | Replace the "Analyze internally" phase with one line; keep the length cap and the #1955 delivery line | 7 |
| zoom-out | global | medium | keep | Branch for agent-invoked runs so the closing question does not stall | 7 |
| _shared (test-quality.md) | project | n/a | retire | Delete with the husk allowlist entry, and rewrite `test_find_husk_dirs_edge_cases` in the same commit | 1 |
| authenticity-pass | project | medium | keep | Line 19 returns BLOCK instead of prompting; line 13 credits de-slop; callers pass the path; remove "Why this gate exists" | 4 |
| checking-system-logs | project | medium | keep | Mark log text as data | 6 |
| do-deploy | project | low | keep | Fix rollback (bug 4); note that the fleet list is authoritative only on a fleet machine | 6 |
| ebook-ingest | project | medium | keep + trim | Move chunking and troubleshooting to `references/pipeline.md`; keep conversion in the body; delete `:88-90`; keep `:8`'s redirects | 7 |
| improve-preflight | project | medium | keep | Text changes only through the improvement pipeline (it is the #3311 eval subject); a subcommand can be added alongside | 6 |
| improve-research | project | medium | keep + add | Done-list, checklist, time budget, data marker; background evaluate with 60s polling and no report while `running` | 6 |
| linkedin | project | medium | keep + add | Data framing with `pasted_content` tags and an observed-facts vault rule; gates on comments and DMs (bug 7), then drop the Hunter round and em-dash scan there; checklist and end recap; move the "verified NOT to work" list to the BYOB doc | 4 |
| officecli | project | medium | keep + trim | Pin decision first (bug 14), then split the reference; visual verify with `view html`/`svg`; keep the fleet-upgrade sentence | 7 |
| prime | project | unset | keep + trim | Remove lines 13-15 and 69-75 (copies of CLAUDE.md) | 6 |
| README.md (.claude/skills) | project | n/a | index file | Drop the plan-maker listings at `:126,134` | 5 |
| reading-sms-messages | project | unset | keep | Data line and 2FA handling rule | 4 |
| rsi | project | medium | keep | None | 6 |
| sdlc | project | medium | keep + trim | Remove the duplicate stage table (so Track B's placement lives in do-sdlc and engineer.md only) and rules 1-3; keep rules 6-7; decide rules 4-5 | 1 |
| sentry (skill) | project | low | keep | None beyond effort | 6 |
| setup | project | medium | keep | None beyond effort | 6 |
| telegram | project | unset | keep + trim | Cut lines 73-162 to four lines plus `--help`; keep the `--voice-note` and `--await-reply` send examples; data line | 4 |
| update | project | unset | keep + trim | WIP-branch line in place of `git stash`; move orchestrator internals to a reference with the two noted dependencies; keep lines 13-19 | 6 |
| x-com | project | medium | keep + add | Same as linkedin; condense (rather than delete) the parent's "What works / What dies" | 4 |
| baseline-verifier | agent | low | script | Script inside the do-test skill with unique paths (bug 13) | 2 |
| builder | agent | medium | keep + trim | Delete the SQLite section and rationalization tables; repo-agnostic test-runner wording | 2 |
| code-reviewer | agent | unset | keep + trim | Cut to two Ground Truth sentences plus the calibrated line; delete the stale NOTE; the judge role moves to a new `review-judge` agent at high | 2 |
| cruft-auditor | agent | low | keep | Effort only | 2 |
| dev | agent | medium | keep | Reword "fan out to Sonnet subagents" to "builder and code-reviewer subagents" (TB); explicit `effort: medium`; early-stop counterpart to "end every turn with a text report"; "before building" (bug 31) | 1 |
| documentarian | agent | low (TB inferred) | keep + trim | Sonnet to opus at low (TB inferred); replace the map with "read `docs/README.md`" (bug 32); trim generic lists | 6 |
| frontend-tester | agent | low (TB inferred) | keep | Sonnet to opus at low (TB inferred); console-log step (bug 23); fold `:54`'s mechanism into `:83`; mark page text as data | 3 |
| linear | agent | n/a (haiku) | retire | Zero dispatches (verify-cluster-6): RENAMED_REMOVALS plus opencode sync. If kept: stays haiku with a ~40-line rewrite naming the real `mcp__claude_ai_Linear__*` tools (TB) | 6 |
| notion | agent | n/a (haiku) | retire | Zero dispatches: RENAMED_REMOVALS plus opencode sync. If kept: stays haiku with a ~40-line rewrite naming the real `mcp__claude_ai_Notion__*` tools (TB) | 6 |
| plan-maker | agent | n/a | retire | RENAMED_REMOVALS, the `.opencode` mirror, and four listing docs | 5 |
| plan-reviewer | agent | medium | keep | Remove "Do NOT modify files" | 5 |
| render | agent | n/a (haiku) | retire | Zero dispatches, and no Render MCP server is configured: RENAMED_REMOVALS plus opencode sync. If kept: stays haiku with a rewrite (TB) | 6 |
| sentry | agent | n/a | retire | Zero dispatches and overlaps the sentry skill, which Track B also flags: RENAMED_REMOVALS plus opencode sync. If kept: opus at low (TB inferred) | 6 |
| strategic-analyst | agent | medium | keep | Fold the Step 3 synthesis subagent into the lead; time budget on subagent prompts | 5 |
| stripe | agent | n/a (sonnet) | retire | Zero dispatches: RENAMED_REMOVALS plus opencode sync. If kept: stays sonnet with a body rule to confirm before any create, update, refund, or cancel (TB) | 6 |
| test-engineer | agent | medium | keep | do-test runner spawns pass `model: "haiku"` (TB inferred); rewrite the body (stale `docs-rebuild/`, Ollama, split ratios) | 2 |
| validator | agent | low (TB inferred) | keep | Opus at low as the do-build validator, haiku when spawned as the do-test lint runner (TB inferred); repo-agnostic test and formatter wording; keep the Promise Lifecycle block | 6 |
| designer (~/.claude/agents) | agent (orphan) | n/a | retire | RENAMED_REMOVALS entry | 3 |
| ui-ux-specialist (~/.claude/agents) | agent (orphan) | n/a | retire | RENAMED_REMOVALS entry | 3 |

Coverage check: `ls .claude/skills-global .claude/skills .claude/agents` lists 43 + 19 + 17 = 79 entries. Every entry has a row above, plus the two `~/.claude/agents` extras. The other ~18 orphaned pre-pivot agents in `~/.claude/agents` were not audited individually; the retirement PR should sweep them.

## 5. Suggested implementation order

1. **Effort enablement (prerequisite for every effort pin).** `KNOWN_FIELDS` plus a value check and a unit case; `sync_best_practices.py` diffs real doc fields; rubric lens 4; narrowed rule 22; correct `new-skill/AGENT.md:45` and the consistency-test docstring; new-skill and new-audit-skill templates. Small and independent.
2. **High-severity bug fixes (can run in parallel with 1).** Bugs 1-6 and 8-10: calendar-sync, do-plan stash, do-docs push guard, do-deploy revert, do-issue RECON and label, do-build 5.5 and 3.5, do-patch/builder. All are self-contained text or small shell edits.
3. **Effort pins, model placement, and the new agents (after 1).** Explicit pins from the master table, starting with the gating skills where below-medium was refuted. Add the `review-judge` agent at high and switch do-pr-review Step 2.5 to it, keeping the two pinned test strings. Measured Track B moves land here: BUILD to opus at medium, PATCH to opus at low, and critics to a new `plan-critic` agent (opus, medium), updated together in the do-sdlc stage table, `config/personas/engineer.md`, and the do-sdlc prose; the critique classifier to haiku; the new-skill AGENT.md template default; dev.md's "Sonnet subagents" wording. Inferred Track B moves (TEST, ISSUE, DOCS to opus; documentarian, frontend-tester, validator to opus at low; do-test runners to haiku) each wait for one measured real run, and MERGE stays sonnet until one merge is measured.
4. **Headless done-when guidance and fan-out time budgets.** do-sdlc, do-build, dev, do-plan, do-issue (after 2), authenticity-pass, zoom-out, do-debrief, the linkedin and x-com checklists, improve-research plus the research-session harness change (bug 12, a code change that needs a worker restart), and the build-agent and imagine-agent recipes. Time lines for critics, the builder, the stage template, the audit fan-outs, and strategic-analyst.
5. **Untrusted-content framing.** One line per skill from section 2, `pasted_content` tags in the linkedin and x-com drafter templates, and the observed-facts vault rule.
6. **Social gate restructure (after 5).** Foreground Agent dispatch wording in de-slop and its callers; gates added to the comment, reply, and DM paths; only then remove the LLM-Tell Hunter and em-dash scans from those loops; `skill-context/de-slop.md`.
7. **Remaining bug fixes.** Voice delivery (decide send_message voice support vs a documented exception first), officecli (pin decision before the reference split), the pen-design MCP rewrite, the do-presentation mermaid path, frontend-tester, the audit-hooks context, cowork, calendar-sync tools, config/models, build-agent, the baseline-verifier script, and bugs 17 and 21-33.
8. **Trims and over-scaffolding.** do-sdlc `GUARDS.md` (update the parity test), the sdlc shim, do-pr-review cuts with the calibrated line and checklist fold (update `test_pre_verdict_checklist_uses_validated`), code-reviewer, builder, telegram, update, the do-merge addendum, the ebook-ingest split, and the small cluster 7 edits. Land after 4 and 6 so the same files are not edited twice in flight.
9. **Retirements.** plan-maker, the five service agents, designer, ui-ux-specialist, and the other ~18 orphans in one `RENAMED_REMOVALS` change with the opencode resync and listing-doc updates; test-quality.md with the husk-test rewrite.

Decisions for Tom before the relevant PR: which do-docs plan-file rule wins (PR 2 or 7), whether plan-less issues are allowed (sdlc rules 4-5, PR 8), whether to bump officecli (PR 7), and the telegram voice path (PR 7).

## 6. Track B: model placement

Source: `track-b-model-placement.md`. Four real past cases (two BUILDs, one PATCH, one critique) were each replayed under three configs: `sonnet` at the Claude Code default effort (how the pins run today), Opus 5.5 at low, and Opus 5.5 at medium. Scoring used held-out tests from the merged PRs and, for the critique, recall against the plan's recorded findings.

Sample size caveat: one run per case per config, twelve runs, about $9.50 total spend, all on small changes. Run-to-run variance is unmeasured, so read the numbers as direction, not as a benchmark.

| Case | Sonnet | Opus low | Opus medium |
|---|---|---|---|
| b3354 BUILD | $1.03, avoided the known blocker | $0.45, repeated the known blocker | $0.69, avoided it |
| b3353 BUILD | $1.44, 58 turns, correct | $0.41, 12 turns, correct | $0.52, correct |
| p3354 PATCH | $0.73, all fixed | $0.37, all fixed | $0.68, all fixed |
| c3411 critics | $1.34, 0 of 2 blockers, passed the plan | $0.69, caught K1 (as a concern) and the late blocker | $1.09, both blockers plus the late one |

Opus finished in two to five times fewer turns, so it re-read cached context far less. Cache reads cost the same on both models ($0.20/MTok), so Opus's higher list price applies only to output and cache writes. Per task it cost 7% to 72% less (median about 50%).

Placement, reconciled with Track A:

| Pin | Placement | Effort | Basis |
|---|---|---|---|
| BUILD | sonnet to opus | medium | measured, 2 cases |
| PATCH | sonnet to opus | low (raise to medium when failures have no named root cause) | measured, 1 case |
| Plan critics | sonnet to new `plan-critic` agent, `model: opus` | medium | measured, 1 case |
| Critique triage classifier | sonnet to haiku | n/a | inferred (mechanical one-line vote) |
| TEST stage | sonnet to opus | medium (Track B's low refuted by verify-cluster-2) | inferred |
| ISSUE stage | sonnet to opus | medium (verify-cluster-2's do-investigation-issue ruling applies) | inferred |
| DOCS stage | sonnet to opus | low | inferred |
| MERGE stage | stays sonnet | medium | pending one measured merge |
| do-test suite and lint runners | sonnet to haiku, capped output | n/a | inferred |
| documentarian, frontend-tester, validator (as do-build validator) | sonnet to opus | low | inferred |
| sentry agent | retire (Track A); if kept, opus at low | low | inferred |
| stripe agent | retire (Track A); if kept, stays sonnet with a write-confirmation rule | unset | Track B |
| linear, notion, render agents | retire (Track A); if kept, stay haiku with ~40-line rewrites naming real tools | n/a | Track B |
| Python `MODEL_FAST` haiku calls (routing, classifiers, memory extraction, judges) | stay haiku | n/a | single-shot, latency-sensitive; no agentic loop for turn efficiency to pay back |

Where the pins bite: in the Telegram bridge path, the `dev` subagent (`model: opus`) already runs every stage inside its own turn, so the Sonnet stage pins apply only to local `/do-sdlc` supervisor runs and to child sessions spawned with `--model sonnet` per `config/personas/engineer.md`. The BUILD, PATCH, and critic moves therefore change local and child-session runs, and the critic move also changes bridge runs, because do-plan-critique spawns its critics on sonnet regardless of the parent.

The stale-ID findings (4.5-era constants, the `opus` alias answering 200K for a 1M session, and the paid-inference meter missing Opus 5.5 and Sonnet 5 rates) are bugs 27 and 27a in section 3.

## 7. Appendix: refuted recommendations

Listed so they are not re-proposed.

- do-sdlc `effort: low`: the supervisor makes merge-freshness and transient-vs-real-refusal calls itself.
- sdlc `effort: low`: same merge path, and a low layer carries into nested Skill invocations.
- do-plan, reduce the Phase 2.6 propagation check to one sentence: propagation misses are the most common plan-revision cause in git history.
- do-build, delete the Step 4.5 zero-commit check: without it an empty branch can be patched into commits and pass Step 6.5.
- do-plan-critique, remove the Skeptic/Simplifier rule outright: the sub-sections still exist (rewrite instead).
- do-test `effort: low`: TEST separates regressions from environment failures, a judgment call on a gate.
- TEST stage at effort low (Track B, inferred): same verify-cluster-2 refutation; the stage moves to opus at medium.
- do-merge `effort: low`: post-merge worktree cleanup, restarts, and plan migration are costly to get wrong.
- do-design-audit "cannot see its screenshots" bug: `allowed-tools` never restricts, so Read is available.
- mermaid-render "Step 8 cannot run" bug: same reason.
- pen-design, remove the `:134` "multimodal" line: it sits in the upstream-owned half and would revert on refresh.
- mermaid-render `effort: low`: Step 8 gates diagrams bound for client decks.
- mermaid-render, collapse the topology table: it records converter failure modes the model cannot know.
- mermaid-render, remove the Export dialog section: the only PNG route without `browser_eval`, which BYOB disables by default.
- de-slop `context: fork` to close #2684: forked skills return only a launch message, so gates would publish before the verdict.
- de-slop, manual `/de-slop` on pasted text still works under fork: a fork has no conversation history.
- linkedin, narrow the gate promise to posts: DMs go to leads and comments are public; add the gates instead.
- telegram `effort: low`: loaded mid-task, the override would lower the judgment work that follows.
- authenticity-pass `effort: low`: a publish gate, and the level would carry into the publish step.
- authenticity-pass `context: fork`: same background-delivery failure as de-slop, and it loses source context.
- reading-sms-messages `effort: low`: pulled in mid-flow for 2FA; 38 lines leave nothing to save.
- audit-skills `effort: low`: the same skill runs `--arch`, which assigns retire dispositions fleet-wide.
- strategic-analyst, new `strategic-lens` agent at low: lens output is the report's substance; no measurement and zero runs.
- config/models.py "context warning silently skipped for Opus": the lookup receives the alias `opus`; the real issue is a stale window.
- validator, remove the Promise Lifecycle block: a generic cross-store check that applies here and elsewhere.
- update `effort: low`: its failure path debugs a production bridge machine.
- prime `effort: low`: feature work follows in the same session, and there are zero runs to save on.
- improve-preflight as a script: the SKILL.md is the recorded #3311 eval subject, pinned by tests.
- improve-preflight, move provenance prose to docs: same eval-subject constraint, and two of the lines are behavioral.
- improve-preflight `effort: low`: it gates a paid freeze.
- Skill listing truncated by a description budget: the missing skills are `off` in `skillOverrides` or `disable-model-invocation`.
- calendar-sync, remove the `.calendar_hook_*` line: those files still exist beside the config.
- cowork, drop the literal beta header: it is the anchor for the "check current docs" instruction.
- weekly-review `effort: low`: stakeholder-facing synthesis prose.
- weekly-review, remove the `:108` delivery line: it is the #1955 incident fix.
- ebook-ingest `effort: low`: the ownership premise, edition choice, and a daily download cap need judgment.
