# Track B: model placement for Sonnet and Haiku pins

Question: for every place this repo pins work to Sonnet or Haiku, is that still the right model now that Claude Opus 5.5 exists?

Short answer: for the judgment pins we measured (BUILD, PATCH, and the plan critics), Opus 5.5 was cheaper per completed task than Sonnet 5, two to five times faster in model time, and at least as good. At `medium` effort it matched or beat Sonnet on every case. At `low` effort it was the cheapest config everywhere and matched Sonnet on PATCH and one BUILD, but it repeated a known review blocker on the other BUILD. The critic replay is the sharpest result: Sonnet missed both known blockers and passed the plan; both Opus configs caught the main blocker plus a real blocker that the original Sonnet critics also missed. Mechanical pins (classifier, test runners, the Haiku service agents, Python classification calls) should stay on Haiku or move down to it.

Sample size is small: one replay per case and config, four cases. Read the numbers as direction, not as a benchmark.

## Method

1. Pricing, speed and effort facts came from the `claude-api` skill bundled with Claude Code 2.1.282 (details and source below). No figures from memory.
2. Every pin was found by grep over `.claude/`, `config/` and the Python tree, then classified as mechanical (runs a script, formats, moves data, one-shot classification) or judgment (writes code, triages, reviews, decides).
3. Four real past cases were replayed under three configs each, twelve runs in total:
   - `sonnet`: `--model sonnet` (resolved to `claude-sonnet-5`), effort left at the Claude Code default, which is how the pins run today.
   - `opus low`: `--model claude-opus-5-5 --effort low`.
   - `opus medium`: `--model claude-opus-5-5 --effort medium`.
4. Each run used a fresh detached worktree at the case's base commit, with the repo venv linked in, and `claude -p` with a plain prompt built from the plan doc or review (never `/do-build` or `/do-sdlc`). Flags: `--output-format json --setting-sources local --strict-mcp-config` with an empty MCP config, `--no-session-persistence`, and a deny list for `git push`, `git commit`, `gh`, `redis-cli`, `pkill`, `killall` and `valor-session`. `REDIS_URL` and `ANTHROPIC_API_KEY` were unset (subscription auth), and pytest ran with one xdist worker to spare the shared test-DB pool. Every worktree ended at its base commit with no commits, and all were removed afterwards.
5. Controls: the targeted test files passed at each case's merged commit in the same replay environment before any model ran (72, 3 and 75 tests), so failures would point at the model and not the harness.
6. Scoring used the case's own targeted tests, the merged PR's test file dropped in as a held-out oracle, a wording-independent behavioral probe where the oracle asserts exact prose, and for the critic, recall against the findings recorded in the plan's Critique Results table.

Cases:

| Case | Stage | Base | What the run was given | Known answer |
|---|---|---|---|---|
| b3354 | BUILD | `8f508ffbd` | plan `widen-pattern-kill-validator.md` | merged build `6132ce517`; its review later found one blocker and three tech-debt items |
| b3353 | BUILD | `f73090a02` | plan `apply-defaults-embedding-provider-import-cycle.md` | merged build `7e9274a85`, 3 held-out tests |
| p3354 | PATCH | `6132ce517` | the PR #3354 round-1 review (blocker + tech debt + nits) | merged patch `23a0e503f`, 75 held-out tests |
| c3411 | CRITIQUE critics | `d9fbe94b0` | plan `lane-branch-identity-cleanup.md` before critique, three critic lenses from CRITICS.md, issue #3411 | round-1 table (`e3bde7854`): 2 blockers, 4 concerns; plus a blocker the round-1 critics missed and a coordinator later log-verified (revision 2, `c66fc7ae9`) |

## Pricing and model facts

Source: the `claude-api` skill in the Claude Code 2.1.282 bundle, `SKILL.md` model table (cached 2026-06-24), `shared/models.md`, `shared/prompt-caching.md` (cache multipliers) and `shared/model-migration.md` (effort, tokenizer). Cross-check: Claude Code's own `costUSD` for `b3354-sonnet` and `b3354-olow` reproduces to the cent from these rates with the 1-hour cache-write multiplier, which is the TTL Claude Code uses.

| Model | ID | Input $/MTok | Output $/MTok | Cache read | Cache write (5 min / 1 h) | Effort | Notes |
|---|---|---|---|---|---|---|---|
| Claude Opus 5.5 | `claude-opus-5-5` | 4.00 | 20.00 | 0.20 | 5.00 / 8.00 | low to max, default `medium`; thinking cannot be disabled | output tokens 30%+ faster than Opus 5 (prompting guide); same tokenizer as Sonnet 5 |
| Claude Sonnet 5 | `claude-sonnet-5` | 2.00 | 10.00 | 0.20 | 2.50 / 4.00 | low to max, default `high` | Opus 4.7-family tokenizer |
| Claude Haiku 4.5 | `claude-haiku-4-5` | 1.00 | 5.00 | 0.10 | 1.25 / 2.00 | no `effort`; thinking via `budget_tokens` | older tokenizer (Opus-family tokenizers produce about 1x to 1.35x as many tokens); 200K context |

The line that matters for agentic work: Opus 5.5 cache reads cost the same as Sonnet 5 cache reads, $0.20 per million. In a Claude Code loop most input is cache reads, so Opus's 2x list price only applies to output, cache writes and the small uncached input. The skill has no tokens-per-second figure comparing Sonnet 5 and Opus 5.5, so speed below is measured model time (`duration_api_ms`).

## Replay results

Tokens are from `modelUsage`. Output includes thinking. Wall time includes pytest runs on a loaded machine; API time is the cleaner speed measure. Cost is Claude Code's list-price calculation.

| Case | Config | Uncached in | Cache write | Cache read | Output (thinking) | Turns | Cost | API s | Wall s | Outcome |
|---|---|---|---|---|---|---|---|---|---|---|
| b3354 BUILD | sonnet | 54 | 88,213 | 2,189,328 | 23,681 (8,421) | 27 | $1.03 | 216 | 317 | own tests 68 pass; oracle 36/39 blocked, 24/24 allowed, stop hints 8/9 |
| b3354 BUILD | opus low | 14 | 33,384 | 218,779 | 6,698 (546) | 7 | $0.45 | 59 | 158 | own tests 73 pass; 36/39, 24/24, hints 7/9: **repeats the review's blocker** (dashboard hint names `valor-service.sh stop`, which stops the Telegram bridge) |
| b3354 BUILD | opus medium | 28 | 42,570 | 568,028 | 11,981 (2,194) | 14 | $0.69 | 107 | 208 | own tests 101 pass; 36/39, 24/24, hints 8/9; avoided the blocker and said why in its summary |
| b3353 BUILD | sonnet | 112 | 85,092 | 4,243,146 | 25,058 (10,759) | 58 | $1.44 | 373 | 1,361 | own 4 pass, held-out 3/3; diff equivalent to merged; also ran three extra session_health test files |
| b3353 BUILD | opus low | 22 | 29,481 | 343,604 | 5,249 (143) | 12 | $0.41 | 56 | 161 | own 4 pass, held-out 3/3; diff equivalent to merged |
| b3353 BUILD | opus medium | 34 | 34,523 | 597,127 | 6,393 (421) | 19 | $0.52 | 73 | 248 | own 4 pass, held-out 3/3; diff equivalent to merged |
| p3354 PATCH | sonnet | 56 | 59,352 | 1,578,732 | 17,184 (5,538) | 28 | $0.73 | 222 | 348 | held-out 75/75; blocker and all three tech-debt items fixed (39/39, 24/24, 9/9) |
| p3354 PATCH | opus low | 14 | 26,706 | 202,351 | 5,953 (494) | 7 | $0.37 | 53 | 212 | held-out 75/75; all fixed (39/39, 24/24, 9/9) |
| p3354 PATCH | opus medium | 24 | 43,227 | 473,263 | 12,124 (2,019) | 12 | $0.68 | 105 | 476 | held-out 75/75; all fixed (39/39, 24/24, 9/9) |
| c3411 critics | sonnet | 64 | 107,184 | 2,898,404 | 32,770 (24,917) | 42 | $1.34 | 333 | 339 | 0 of 2 blockers; partial on 2 concerns; missed the late blocker; verdict READY TO BUILD (with concerns) |
| c3411 critics | opus low | 24 | 56,329 | 590,478 | 6,003 (2,256) | 12 | $0.69 | 67 | 77 | blocker 1 (as a concern); caught the late blocker; verdict NEEDS REVISION |
| c3411 critics | opus medium | 32 | 76,469 | 982,796 | 14,281 (8,318) | 17 | $1.09 | 143 | 152 | blocker 1 (as a blocker, with timing evidence) and blocker 2; caught the late blocker; verdict NEEDS REVISION |

Totals across the four cases: Sonnet $4.54, Opus low $1.92, Opus medium $2.98. Total replay spend about $9.50.

Where the savings come from: Opus finished in 2 to 5 times fewer turns, so it re-read the cached context far less (cache reads 3x to 12x lower) and wrote less output. Per token Opus costs more; per task it cost 7% to 72% less (median about 50%).

### Case notes

**b3354 BUILD.** Every config blocked the plan's service kills and allowed every sanctioned or read-only command. The difference was the remediation text. The merged build (model unknown) sent the dashboard and the watchdog to `scripts/valor-service.sh stop`, and review flagged that as a blocker because following it stops the Telegram bridge. Sonnet and Opus medium both avoided the error. Opus low fixed the watchdog but repeated the dashboard error. No config handled `killall worker` or the reap-xdist bypass; the merged build missed the second too, and review raised both only as tech debt.

**b3353 BUILD.** A small, well-specified bug fix. All three configs produced the merged fix (lazy import at the single use site, a logged warning in place of `except: pass`, a subprocess regression test, a doc line) and passed the held-out tests. Sonnet spent 58 turns and 23 minutes, mostly running extra test files; Opus low took 12 turns and under 3 minutes.

**p3354 PATCH.** A review with one precise blocker and three precise tech-debt items. All configs fixed everything and passed all 75 held-out tests. Opus low did it for half of Sonnet's cost in a quarter of the model time.

**c3411 critics.** Known answers: K1 (blocker: the "trailing checkpoint in `finally`" the plan relies on does not exist), K2 (blocker: the named test file tests a git hook, not the guard), K3 to K6 (concerns: a grep row that cannot pass, `commit_sha` not saved, Race-2 recovery scheduled before its open question, confusable `lane_branch` names), and K7, the log-verified blocker the round-1 critics missed (`mark_work_done` runs its own unguarded `git branch -d`).

| Config | K1 | K2 | K3 | K4 | K5 | K6 | K7 | Verdict |
|---|---|---|---|---|---|---|---|---|
| sonnet | no | no | partial | no | partial | no | no | READY TO BUILD (with concerns) |
| opus low | yes (concern) | no | no | no | partial | no | yes | NEEDS REVISION |
| opus medium | yes | yes | no | no | no | no | yes | NEEDS REVISION |

Both Opus runs also tied the plan to #3413 (`checkpoint_branch_state` reads `session.working_dir`, not the worktree), an open issue the plan had deferred. Sonnet's other two concerns (an unused `branch_name` parameter, a test fixture that never sets `branch_name`) are plausible but were not verified here. Sonnet's verdict would have sent a plan with two known blockers to BUILD.

## Pin inventory and recommendations

"Measured" rows cite the replay table. "Inferred" rows extend the measured pattern (Opus finishes agentic tasks in fewer turns, which outweighs its per-token price) to stages that were not replayed.

How to express effort: skill and agent frontmatter both accept `effort:` in Claude Code 2.1.282. The Agent tool call accepts `model` but not `effort`, so inline spawns (plan critics, test runners) get a specific effort only through an agent definition or through the frontmatter of the skill they run. Otherwise they inherit the parent's effort.

### SDLC stages

The stage tables live in `.claude/skills-global/do-sdlc/SKILL.md` (Stage to Model Dispatch Table), `.claude/skills/sdlc/SKILL.md` (Pipeline Stages Reference) and `config/personas/engineer.md` (`valor_session create --model sonnet` for BUILD, TEST, PATCH and DOCS). What actually runs: in the bridge path, the `dev` subagent (`.claude/agents/dev.md`, `model: opus`) runs every stage inside its own turn, so these Sonnet pins apply only to the local `/do-sdlc` supervisor and to child sessions spawned per the engineer persona.

| Pin | Kind | Recommendation | Evidence | Cost per typical run (measured or inferred) |
|---|---|---|---|---|
| BUILD (sonnet) | judgment | **Move to opus, effort medium** (`effort: medium` in do-build frontmatter; `--model opus` in the stage tables and persona) | Measured, 2 cases. Medium matched Sonnet's quality on both, cost 33% to 64% less and ran 2x to 5x faster. Low was cheapest but repeated a real review blocker on b3354. | sonnet $1.03 to $1.44; opus medium $0.52 to $0.69 |
| PATCH (sonnet) | judgment | **Move to opus, effort low** (`effort: low` in do-patch frontmatter) | Measured, 1 case. All configs fixed everything; low cost half of Sonnet. A review hands PATCH a precise spec, which is where low effort holds up. Raise to medium for failures without a named root cause. | sonnet $0.73; opus low $0.37 |
| TEST (sonnet) | mixed: runs suites, then triages failures | **Move to opus, effort low** for the stage session; runners go to Haiku (below) | Inferred. Triage is judgment, and low effort's turn efficiency should make it no dearer than Sonnet. | not measured |
| ISSUE (sonnet) | judgment (structured writing, scoping) | **Move to opus, effort low** | Inferred from the per-task cost pattern. | not measured |
| DOCS (sonnet) | judgment (what to update, writing) | **Move to opus, effort low** | Inferred. do-docs already fans out to documentarian subagents; see that agent below. | not measured |
| MERGE (sonnet) | mechanical (programmatic gate) | **Keep sonnet** | Gate logic is scripted. The turn-count effect may make opus low cheaper here too; measure one merge before switching. Do not move an irreversible step down to Haiku. | not measured |
| `do-sdlc` prose "its internal critics self-pin to sonnet" and "dispatching BUILD on sonnet" | doc | Update alongside the table | follows the rows above | |

### do-plan-critique

| Pin | Kind | Recommendation | Evidence | Cost per typical run |
|---|---|---|---|---|
| Triage classifier (`sonnet`, SKILL.md ~line 190) | mechanical (one-line LITE/FULL vote) | **Move to haiku** (`model: "haiku"` on the spawn) | Short prompt and a fixed reply format suit Haiku. Force-FULL rules and "bias to FULL" already cap the downside of a wrong vote. | half the per-token price of Sonnet 5, fewer tokens (older tokenizer) and no default thinking |
| Every critic (`model: "sonnet"`, ~line 235) | judgment | **Move to opus, effort medium.** Implement as a `plan-critic` agent definition (`model: opus`, `effort: medium`) dispatched by `subagent_type`, since the Agent call cannot carry effort. | Measured, 1 case. Sonnet found neither known blocker and passed the plan. Opus medium found both blockers, one of them K1, plus the log-verified blocker the original critics missed, and cost 19% less. | sonnet $1.34; opus medium $1.09; opus low $0.69 (found K1 and K7, not K2) |

If the critics stay on Sonnet for any reason, the Sonnet 5 migration notes apply: review harnesses with conservative-reporting instructions ("Return 0-3 findings", "Do not invent problems") lose recall on Sonnet 5. Have the critic report everything and let the driver filter.

### do-test parallel dispatch

| Pin | Kind | Recommendation | Evidence |
|---|---|---|---|
| Suite runners (`test-engineer`, `model: "sonnet"`) | mechanical: run one command, report counts and raw output | **Move to haiku** | Inferred. The prompt is already concrete (hard bound, exact report fields) and fits Haiku. Ask for the failure section and the summary line rather than the full raw output, so a huge log cannot crowd Haiku's 200K window. |
| Lint runner (`validator`, `model: "sonnet"`) | mechanical | **Move to haiku** | Inferred. It runs `ruff` and reports pass or fail. |

### Agents in `.claude/agents/`

| Agent | Pin | Kind | Recommendation | Notes |
|---|---|---|---|---|
| documentarian | sonnet | judgment (writing docs to match code) | **Opus, effort low** | Inferred. Called from DOCS and from plans; same reasoning as DOCS. |
| frontend-tester | sonnet | mixed: drives BYOB, reads screenshots, returns pass or fail | **Opus, effort low** | Inferred. The prompting guide says Opus 5.5 reads screenshots better at its lowest effort than Opus 5 did at its highest, and computer use improved; screenshot misreads are this agent's main failure mode. |
| sentry | sonnet | judgment (error triage, root cause) | **Opus, effort low** | Inferred. Triage is judgment. The `sentry` skill covers the same ground, so consider retiring one of them. |
| validator | sonnet | judgment-light (checks acceptance criteria) | **Opus, effort low** as a do-build validator; Haiku when used only as a lint runner (above) | Inferred. The `model="sonnet"` in `agent/agent_definitions.py::get_agent_definitions` never runs: the function has no production caller (only `validate_agent_files` is imported by the worker and bridge). |
| stripe | sonnet | mechanical API reads and writes on financial data | **Keep sonnet** | Its `permissions:` block (accept, prompt, reject) is not Claude Code frontmatter and names tools that do not exist (`stripe_list_*`), so nothing gates writes. Put "confirm before any create, update, refund or cancel" in the body. |
| linear | haiku | mechanical | **Keep haiku**, rewrite the prompt | See the prompt review below. |
| notion | haiku | mechanical | **Keep haiku**, rewrite the prompt | See the prompt review below. |
| render | haiku | mechanical | **Keep haiku**, rewrite the prompt | No Render MCP server is configured in this session's tool list, so the agent may have nothing to call. Confirm it is still wanted. |
| dev.md prose "Fan out to Sonnet subagents" | doc | n/a | **Reword** to "fan out to builder and code-reviewer subagents" | `builder` and `code-reviewer` declare no model and inherit the dev session's Opus, which the measurements support. |
| new-skill `AGENT.md` template (`model: sonnet`, options `sonnet`, `haiku`) | template | n/a | **Change the default**: omit `model` (inherit) and document `effort`. Guidance: judgment agents use `opus` with an explicit `effort`, mechanical agents use `haiku`. Add `opus` and `effort` to the optional fields table. | A Sonnet default propagates the old placement into every new agent. |

### Python code paths (what actually runs)

| Path | Model that runs | Kind | Recommendation |
|---|---|---|---|
| `config.models.MODEL_FAST` = `HAIKU` (`claude-haiku-4-5-20251001`) via `agent/llm/router.py`: bridge routing and terminus decisions, `agent/intent_classifier.py`, `agent/memory_extraction.py` (extraction, refusal check, distill, outcome judge), `bridge/read_the_room.py`, `bridge/agent_catchup.py`, `agent/health_check.py` judge, `agent/session_completion.py` borderline judge, `tools/email_cs` triage, `tools/valor_calendar.py` naming | Haiku 4.5 (some routes go to local Ollama or TypeSafe Jev first when eligible) | mechanical: single-shot, latency-sensitive classification | **Keep Haiku.** Opus 5.5 cannot turn thinking off and costs four times as much per token; these calls have no agentic loop for turn efficiency to pay back. |
| `bridge/media.py` image description, `tools/impact_finder_core.py` rerank (`HAIKU` directly) | Haiku 4.5 | mechanical | **Keep Haiku.** |
| `MODEL_REASONING` = `SONNET` = `claude-sonnet-4-5-20250929` in `tools/test_judge`, `tools/documentation` | Sonnet 4.5 at $3/$15 | judgment-light | **Repoint to `claude-sonnet-5`** ($2/$10, current generation) or delete if unused: no production caller was found outside their own tests. Stale-ID fix, separate from placement. |
| `MODEL_VISION` = `OPENROUTER_SONNET` (Sonnet 4.5) in `tools/image_analysis`, `tools/image_tagging`; `OPENROUTER_HAIKU`/`OPUS`, `MODEL_BEST` = `OPUS` (`claude-opus-4-5-20251101`) | 4.5-era models | mixed | **Update the IDs.** Stale-ID fix. |
| `_MODEL_ALIASES` maps `"opus"` and `"sonnet"` to the 4.5 IDs for `get_model_context_window` | lookup only | n/a | **Fix.** Sessions run the `opus` alias, which resolves to Opus 5.5 with a 1M window, but the lookup answers 200,000. |
| `tools/paid_inference_meter.py` `PRICE_TABLE` | n/a | n/a | **Add** `claude-opus-5-5` ($4/$20) and `claude-sonnet-5` ($2/$10). An unknown model falls back to $3/$15, which under-prices Opus 5.5 output when a response carries no `usage.cost`. |
| `SessionRunnerSettings.pm_model` = `opus`, `session_default_model` = `fable`, `dev.md` = `opus` | Opus 5.5 / Fable 5.1 | judgment | No Sonnet or Haiku pin; out of scope. |

## Prompt review for the pins that stay on Sonnet or Haiku

Opus 5.5 prompting advice does not carry over to these models. Haiku does best with short, concrete instructions that name real tools and show the exact call shape. Sonnet 5 reads instructions literally and applies leftover style directives at face value.

- **linear, notion, render (Haiku).** These run 450 to 550 lines, mostly generic "expertise" prose (agile principles, sprint planning philosophy) that gives Haiku nothing to act on. The frontmatter `permissions:` blocks use tool globs (`linear_list_*`, `notion_search`, `render_deploy_*`) that match no real tool (the real ones are `mcp__claude_ai_Linear__*` and `mcp__claude_ai_Notion__*`), and Claude Code does not read that key. Rewrite each to about 40 lines: the real tool names, three or four common requests with the exact call for each, a rule to confirm before any write, and the output format.
- **stripe (Sonnet).** The same non-functional `permissions:` block; add an explicit write-confirmation rule in the body, as above.
- **Critique triage classifier (moving to Haiku).** Already a good Haiku prompt: a fixed one-line output, a stated tie-break ("Bias to FULL"), and a small input.
- **do-test runners (moving to Haiku).** Concrete and bounded already. The one change: cap the reported output (failures plus the summary line) instead of "Output the raw test-runner output".
- **MERGE (Sonnet).** Not reviewed in depth here; Track A covers do-merge.

## Caveats

- One replay per case and config, four cases, all small (under 300 changed lines). Variance between runs of the same config is unmeasured, so the b3354 Opus-low miss could be noise. The direction held on every case: Opus cheaper per task and faster, with medium never behind Sonnet on quality.
- Sonnet ran at the Claude Code default effort because the pins set none. A Sonnet run at `low` would be cheaper and was not tested.
- The critique replay was one agent applying all three lenses with read access to the checkout. Production critics are three separate agents that receive a pre-assembled SOURCE_FILES block and may not read files, with an Opus driver that verifies findings. The replay therefore measured a "critic with verification" role. The Opus advantage showed up mostly on findings that needed reading code beyond the plan, which a SOURCE_FILES critic can only match if the driver happened to include those files.
- Issue #3411's body was fetched as it stands today (last edited 2026-09-22), so it may carry context written after the critique. All three configs saw the same text.
- Wall times include pytest on a machine running other agents; `b3353-sonnet`'s 1,361 s wall against 373 s of model time is mostly test waiting. API time is the fairer speed comparison.
- Costs are Claude Code's list-price calculation (`costBasis: list`) under subscription auth, including its 1-hour cache writes. Actual subscription spend differs, but the relative ordering holds.
- The merged builds used as references were produced by an unknown model at an unknown effort, so "equivalent to merged" means equivalent in substance, not a head-to-head with a known config.
- Stages marked "inferred" (ISSUE, TEST, DOCS, the agents) were not replayed. Measure one real run of each before making the change, especially MERGE if it is ever moved.

Replay artifacts (JSON outputs, diffs, prompts, scoring scripts) are in the session scratchpad under `replays/` and were not committed.
