---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-06-03
tracking: https://github.com/tomcounsell/ai/issues/1546
last_comment_id:
---

# PoC: Granite Operator Drives a Real Interactive Claude Code Session via PTY

## Problem

Every existing path to Claude spawns `claude -p` (headless print mode). Both `agent/sdk_client.py:2186` (`get_response_via_harness`) and the prior PoC's `agent/claude_session.py:104` (`_build_cmd`) build commands with `-p --input-format stream-json`. Nothing drives the interactive Claude Code TUI programmatically.

The first PoC (issue #1486, PR #1487) deliberately avoided the TUI because stream-json requires `-p`. The follow-on production cutover (issue #1542) was cancelled for the same reason: a `-p` harness driven by granite is not meaningfully different from the harness `agent/sdk_client.py` already runs. Driving the real interactive session is the actual thesis, and the only hard part.

The substrate reached the TUI through a PTY is now confirmed drivable (spike #1547, "drivable with caveats" on 2026-06-02). Persona priming via custom slash commands is also confirmed (F1-F4 probe on 2026-06-03). What remains is the implementation: a standalone PoC that proves a local operator (granite4.1:3b) can drive the real interactive Claude Code session via PTY, end-to-end, unattended, with zero `claude -p`.

The PoC is the kernel validation for the architecture described in the issue's *Bridge integration* section. It does not wire to the bridge, does not change `AgentSession` schema, and does not orchestrate at scale; it proves the substrate. The production cutover is a follow-on issue, gated on this PoC + #1547.

**Current behavior:** No code anywhere drives the interactive Claude Code TUI programmatically. The richer affordances of the interactive session (real compaction, the two-stage ctrl-c interject, interactive menus, permission prompts) are inaccessible to the granite operator.

**Desired outcome:** A standalone PoC proving the hard kernel: a local operator drives a real interactive Claude Code session via a pseudo-terminal, with zero `claude -p`. Concretely it must, end-to-end and unattended, do all of: spawn an interactive `claude` session attached to a PTY; prime a persona via a custom slash command; survive the interactive session's affordances (numbered menu, permission/feedback prompt, multi-turn exchange, recovery from interruption via `claude --resume <uuid>`); let granite (not Python glue) classify PM's output tail and route accordingly; and run under Max subscription OAuth with no `ANTHROPIC_API_KEY` and no `claude-agent-sdk`.

## Freshness Check

**Baseline commit:** `9dc6929b` (HEAD of main at plan time)
**Issue filed at:** 2026-05-28 (issue updated through 2026-06-03T07:35:25Z; latest commit body change references the planner-prompt handoff artifact at `/tmp/granite-pty-spike/planner_prompt.md`)
**Disposition:** Unchanged

**File:line references re-verified:**

- `agent/claude_session.py:104` (`_build_cmd`) - still builds `["claude", "-p", "--verbose", ...]`; nothing in the file has changed since the spike (last touched in PR #1487, commit `4bd81d6c`). Holds.
- `agent/claude_session.py:49-52` (`_UUID_RE`, `_RESUME_HINT_RE`) - the regexes are still the canonical scrapers; the spike re-imported them at `scripts/granite_tui_pty_spike_pexpect.py:48`. Holds.
- `agent/granite_router.py:276` (`GraniteRouter.route()`) - still consumes `list[dict]` stream-json-shaped events with the 5-tool taxonomy (`OPERATOR_TOOLS` at line 38, `VALID_TOOLS` at line 141). The PoC must reduce this to 3 tools and re-shape events at the boundary. Holds.
- `agent/sdk_client.py:2186` (`get_response_via_harness`) - still spawns `claude -p`; nothing in the harness has changed. Holds.
- `docs/research/spikes/granite-tui-pty-spike.md` (v7 spike report, commit `8dc264ad`) - still authoritative for C1-C5 substrate facts. Holds.
- `scripts/probe_slash_arguments.py` - still authoritative for F1-F4 persona-priming facts. Holds.
- `docs/diagrams/granite-bridge-architecture.png` - present, 3-layer diagram. Holds.
- `pyproject.toml` runtime deps - `pexpect` and `ptyprocess` are in `[project.optional-dependencies] dev`, **not** in runtime `dependencies`; the PoC must promote them to runtime. Holds.
- `.claude/commands/` - currently empty; the PoC ships `prime-pm-role.md` and `prime-dev-role.md` as new files. Holds.

**Cited sibling issues/PRs re-checked:**

- #1547 (granite-tui-pty-spike) - **closed (completed) 2026-06-03**. Report landed at `docs/research/spikes/granite-tui-pty-spike.md`. PREREQUISITE SATISFIED.
- #1542 (production cutover) - **closed**. This PoC replaces it as the kernel-validation artifact; the cancelled cutover's framing is intentionally not inherited.
- #1486 (prior PoC) - **closed**. PR #1487 (commit `4bd81d6c`) merged. This PoC writes *new* code in a new module path; `agent/claude_session.py`, `agent/granite_router.py`, `agent/granite_agent_loop.py` are untouched.
- #1552 (resume-UUID capture in a model-reachable env) - **open**. Recommended as a follow-on to close C3 empirically. **Not a blocker for this PoC;** the plan's Q5 disposition is "exercise resume inside the PoC in a model-reachable env" (option b in the issue's Planner handoff), with #1552's findings as a corroborating reference.

**Commits on main since issue was filed (touching referenced files):**

- `9dc6929b` - "Scripts: add $ARGUMENTS probe - substrate confirmation of persona-priming" (added `scripts/probe_slash_arguments.py`; F1-F4 confirmed). **Strengthens the plan's premise** (Q3 fully resolved).
- `a3d6f67e` - "Update granite-bridge-architecture.png". Diagram refresh, no architectural change.
- `8dc264ad` - "Spike #1547 follow-up: tighten stdlib heuristic, add 15-min long-hold". Spike tightening only.
- `0d34aa71` - "Docs: refresh granite-bridge-architecture diagram to 3-layer". Diagram refresh.
- `c2f79a7a` - "Bump deps: claude-agent-sdk 0.2.87->0.2.88". Unrelated runtime dep bump.

No commits changed the architecture or the substrate. All drift is in the direction of strengthening the plan's premises.

**Active plans in `docs/plans/` overlapping this area:** `granite-tui-pty-spike.md` (the spike plan, completed) and `granite_root_session_runner.md` (production cutover framing that the cancelled #1542 was scoped under). Neither overlaps with the new PoC's kernel-validation scope. The PoC writes new code in a new path; the existing headless harness is untouched.

**Notes:**

- The planner-prompt artifact at `/tmp/granite-pty-spike/planner_prompt.md` is a scratch-dir handoff, not a repo file. It points back to the issue for the full invariant list. The plan below reads as a standalone artifact, but the issue body is the spec of record for the 10 invariants in *Bridge integration*.
- The `pexpect` import path in `scripts/granite_tui_pty_spike_pexpect.py:48` re-uses `_UUID_RE` and `_RESUME_HINT_RE` from `agent/claude_session.py`. The PoC's substrate driver follows the same pattern: reuse the regexes; do not duplicate them.
- The `INTERRUPTED_RE` regex in the spike script (`scripts/granite_tui_pty_spike_pexpect.py:64-72`) accepts both the v2.1.160 text ("Press Ctrl-C again to exit") and the older text ("Interrupted · What should Claude do instead?"). The PoC inherits this resilience.

## Prior Art

Closed issues and merged PRs related to the granite operator architecture. All sourced via `gh api` REST (the GraphQL `gh issue list` was rate-limited at plan time).

- **#1486** (PoC: granite-orchestrated dual Claude Code session executor) - *closed*. The first PoC. Used `claude -p` with stream-json I/O. Validated translation tools and one detection tool in a live 4-turn run; the judgment tools (`handle_choice`, `signal_done`) were validated by synthetic smoke tests only. Results at `docs/plans/completed/granite-agent-loop-poc-results.md`. **Relevance:** the prior PoC is the closest analog; this new PoC explicitly replaces its `-p`-driven approach with PTY-driven interactive sessions per invariant #4 and the drop list in the issue.
- **PR #1487** (PoC: granite4.1:3b drives dual Claude Code sessions over Max OAuth) - *merged 2026-06-01*. The implementation that landed #1486. **Relevance:** the `agent/claude_session.py` and `agent/granite_router.py` code paths it shipped are the "untouched" baseline; the PoC writes *new* code in a new path.
- **#1542** (Production cutover: granite-agent-loop as the unbypassable root session runner) - *closed (cancelled)*. The first attempt to scale the prior PoC to production. **Cancelled** for the reason in the issue's *Context* block: a `-p` harness driven by granite is not meaningfully different from the harness `agent/sdk_client.py` already runs. **Relevance:** the cancelled plan's framing is *not* inherited; the issue explicitly says "a fresh PoC scoped to exactly that kernel."
- **#1547** (Spike: can a PTY reliably drive an interactive Claude Code session?) - *closed (completed) 2026-06-03*. The v7 spike report at `docs/research/spikes/granite-tui-pty-spike.md`. **Relevance:** C1-C5 substrate facts are load-bearing inputs to the PoC. The 12-step start-up sequence and 10 invariants in the issue are the architectural frame the spike evidence sits inside.
- **#1552** (Spike: resume-UUID capture + scenario-4-resume-hint in a model-reachable env) - *open*. A pre-issue spike for #1546 to close C3 empirically. **Relevance:** the PoC's Q5 disposition is to exercise resume inside the PoC itself in a model-reachable env; #1552's findings are a corroborating reference, not a prerequisite. The plan explicitly does not block on #1552 closing first.

## Research

External research is not applicable to the load-bearing substrate questions (C1-C5, F1-F4) - they are all empirically settled by the spike and probe transcripts in this repo. The only external research that might apply is the Claude Code slash-command mechanism documentation (which the probe and issue both reference at `https://code.claude.com/docs/en/agent-sdk/slash-commands`). The PoC inherits the existing probe's documented behavior; no new WebSearch queries were generated.

**Queries used:** none (skipped - the only relevant external resource is the slash-command docs link already cited in the issue's *Definitions* table).

**Key findings:** The slash-command mechanism is fully documented; this repo has simply never authored any custom commands. The PoC ships `prime-pm-role.md` and `prime-dev-role.md` as part of the substrate (per the issue's *Revised* and *Dropped* lists). No new external research findings.

## Spike Results

The substrate assumptions have been validated by the prior spike (#1547) and probe (the F1-F4 probe) - those are the spike results for this plan. No new spikes are needed in Phase 1.5; the load-bearing facts are:

- **C1: Submit key is `\r` (CR, 0x0D), not `\n` (LF).** Validated empirically by `pexpect/scenario-2.bin` (TUI returned to idle 141ms after `hello\r` was sent, indicating submit) vs the stdlib 1st-run transcripts (text remained in input box after `hello\n`, no submit). Confidence: high. Impact: the PoC's substrate driver must send `b'\r'` at the end of every PTY write, never `b'\n'`.
- **C2: First-ctrl-c interjection text is `Press Ctrl-C again to exit` in TUI v2.1.160.** Validated empirically by `stdlib/scenario-4.bin`. The prior PoC's `docs/features/granite-agent-loop.md:294-296` (`Interrupted · What should Claude do instead?`) is out of date. Confidence: high. Impact: detection regexes match v2.1.160 wording; `INTERRUPTED_RE` in the spike accepts either form for resilience.
- **C3: Resume-UUID capture is environment-gated.** The on-exit hint is only emitted on a successful model response; in a model-unreachable env, no session is opened, no hint is printed. Validated by both libraries' scenario 5 transcripts. Confidence: high. Impact: the PoC's resume-acceptance test requires a model-reachable env (option b in the issue's *Planner handoff* Q5 disposition).
- **C4: `/help` renders as a non-dismissing overlay.** The bottom-bar text changes to `esc to cancel` while the overlay is active. Validated by `pexpect/scenario-6.bin`. Confidence: high. Impact: idle detection must recognize the `esc to cancel` bar state; the Q6 test case (b) for slash-command overlay state is required.
- **C5: Idle/ready signal is the bottom-bar text, not the prompt glyph.** Combine glyph + bar + `min_content_bytes` floor. Validated by pexpect's `wait_for_idle` heuristic. Confidence: high. Impact: substrate driver uses the combined signal, not the glyph alone.
- **F1: TUI parses custom slash commands at the TUI layer.** CONTROL phase produced inline `⏺ Unknown command: /xyz-unknown-command-99999` (TUI-side rejection). Confidence: high. Impact: persona-priming slash commands are recognized before the model is involved.
- **F2: `$ARGUMENTS` substitutes at invocation time (model-side).** KNOWN phase was routed to the model and was not rejected as unknown. Confidence: high (implied by F1 + non-rejection; documented behavior). Impact: the PoC passes the user message as `$ARGUMENTS`; the model-side dispatcher handles substitution.
- **F3: Multi-word arguments are preserved as a single arg string.** CONTROL rendered `⏺ Args from unknown skill: hello world`. Confidence: high. Impact: the persona-priming path passes the user message as one `$ARGUMENTS` token.
- **F4: Slash command body is invisible to the user/operator.** The marker text `ARG_PROBE_MARKER_BEGIN` was not visible anywhere in the transcript. Confidence: high. Impact: the PoC cannot verify at runtime that `$ARGUMENTS` substituted correctly; the only substrate signal is "did the model respond?" (which is the right signal). The PoC must not depend on a substitution-verification path that doesn't exist.

## Data Flow

The PoC's data flow differs structurally from the prior PoC's stream-json I/O. The PTY is byte-oriented; the loop is end-of-turn-driven; the routing decision is made by granite over the *text* of PM's output tail, not over a stream of `list[dict]` events.

1. **Entry point**: Operator invokes the PoC CLI (e.g., `valor-granite-loop --user-message "..."`). The CLI creates a sandbox cwd (a fresh tempdir under the operator's control) and instantiates the container.
2. **Container**: Spawns two `pexpect.spawn` children (PM + Dev) under separate PTYs, with `ANTHROPIC_API_KEY=""` (Max OAuth path) and `--permission-mode bypassPermissions`. CWD: the sandbox. Both children start as fresh interactive `claude` sessions, no `-p`.
3. **Persona priming**: Container writes `/prime-pm-role <user message>\r` to PM's PTY and `/prime-dev-role <user message>\r` to Dev's PTY (F1-F4). Both sessions now have the user message as context; PM knows to address the developer or the user; Dev knows to wait.
4. **Startup-phase parser**: Container's startup-state parser watches both PTYs for known startup shapes (login prompt, update notice, error modal, persona-prime acknowledgement, **trust-folder prompt** - see *Operator ergonomics note* below). The parser is a small Python function - a list of regex matches against a known enum, not a model. It does not interpret; it identifies which known shape was seen. When a known shape is detected, container asks granite for response text; container writes the text to the appropriate PTY.
5. **Steady-state begins** when both PM and Dev PTYs reach idle. The container's main loop runs.
6. **PM turn**: Container waits for PM's PTY to reach idle (glyph + bar + content-floor per C5). When idle fires, container reads the accumulated buffer (PM's output tail), strips ANSI, and passes it as a single ollama.chat() call to granite with: (a) a system prompt that defines the 3 tools (`extract_dev_prompt`, `summarize_for_pm`, `classify_pm_output`); (b) the current turn's content as the only user message.
7. **Granite classification + translation**: Granite returns a tool call. If the call is `classify_pm_output`, the `destination` field is one of `dev` (developer-address), `user` (user-address), or `complete` (TASK_COMPLETE / structural completion signal). If `dev`, granite's call also returns a translated `extract_dev_prompt` payload (the user-turn prompt that will be written to Dev's PTY). If `user`, the payload is the user-address text written to a log file (bridge wiring is out of scope). If `complete`, the loop exits with the final payload.
8. **Container writes to Dev's PTY**: Container writes the `extract_dev_prompt` payload + `\r` to Dev's PTY (C1 submit key). Container waits for Dev's PTY to reach idle.
9. **Dev turn → granite summary**: Container reads Dev's accumulated buffer and calls granite with `summarize_for_pm`. Granite returns a one-paragraph summary. Container writes the summary to PM's PTY + `\r`.
10. **PM's next turn**: Container waits for PM's PTY to reach idle. PM now has the summary in its context. PM chooses (via natural language) whether to send the next Dev instruction or to address the user. Granite classifies; container routes.
11. **Loop continues** until PM emits a structural completion signal (granite classifier detects it; container exits) or `max_turns` is reached (safety cap; container exits with a non-zero code).
12. **Results doc**: Container writes a JSON trace (turn-by-turn: who spoke, granite's classification + tool call, latency, byte counts) to a results file the results doc renders.

### Operator ergonomics note: trust-folder prompt

The probe at `scripts/probe_slash_arguments.py:243-247` surfaced a "Yes, I trust this folder" trust prompt on first run in a fresh tempdir. The production PoC's startup-phase parser will hit the same prompt in any fresh tempdir. The parser's pattern set includes it; dismissal is `1\r` (per the probe's confirmed dismissal). This is a concrete, line-cited startup event the implementer must handle.

## Why Previous Fixes Failed

The prior PoC (#1486, PR #1487) is the only prior attempt at the granite operator architecture. It is the analog for *why the kernel needs to be re-proven*; it is not a record of failed fixes per se, but its design choices explain the architectural pivots the new PoC makes.

| Prior PoC Design Choice | Why It Is Inadequate for the New PoC | Source |
|---|---|---|
| Stream-json I/O via `claude -p` | Requires `-p`; bypasses the interactive TUI entirely. The richer affordances (real compaction, the two-stage ctrl-c interject, interactive menus, permission prompts) are inaccessible. The cancelled production cutover (#1542) confirmed a `-p` harness is not meaningfully different from `agent/sdk_client.py`. | `agent/claude_session.py:104-123`; issue body *Context* |
| `GraniteRouter` 5-tool taxonomy (`extract_dev_prompt`, `summarize_for_pm`, `handle_choice`, `probe_session`, `signal_done`) | The 3 non-translation tools are judgment/detection calls, not translations. The prior PoC's results doc at `docs/plans/completed/granite-agent-loop-poc-results.md` shows `handle_choice` and `signal_done` were validated by synthetic smoke tests only - they were not exercised in the live 4-turn run. The new PoC reduces the taxonomy to 3 (translation x2 + classification x1) and routes judgment calls to PM (the persona with the user relationship). | `agent/granite_router.py:38-141`; results doc |
| Custom PM-side tools (`send_to_dev`, `reply_to_user`) | Both rejected by invariant #6 and #7. A `send_to_dev` tool couples PM to Dev's interface, eliminating granite's translator value. A `reply_to_user` tool is a custom-tool-registration question; the user-address content is a regular PM output, classified by granite. | Issue *Bridge integration* invariants #6, #7 |
| `--append-system-prompt` persona injection | Replaced by slash-command priming. The PoC ships `.claude/commands/prime-{pm,dev}-role.md` and invokes them as the first PTY input. | Issue *Revised*; `agent/sdk_client.py:1017` |
| `HISTORY_KEEP_LAST_N = 8` in `GraniteRouter` | Each call now sees a whole PM output tail (a paragraph or two of natural language), which makes even an 8-message history lossy, slow, and prone to anchoring. Fresh context per turn is the right default. | `agent/granite_router.py:31`; invariant #5 |
| Stream-json `session_id` field for resume | Replaced by scraping the on-exit hint only. C3 / Q5 partial. | Invariant #3; `agent/claude_session.py:49-52` |

**Root cause pattern:** the prior PoC inherited the `-p` harness from `agent/sdk_client.py` and built its architecture on top of the headless substrate. When the production cutover (issue #1542) tried to scale it, the architectural mismatch surfaced: a `-p` harness driven by granite is not architecturally distinct from a `-p` harness driven by the existing CLI. The new PoC fixes this by switching the substrate (interactive TUI via PTY) before scaling - the kernel is proven first, then the production cutover can build on a substrate that actually delivers the thesis.

## Architectural Impact

- **New dependencies**: `pexpect>=4.9.0` and `ptyprocess>=0.7.0` are currently in `[project.optional-dependencies] dev`. The PoC promotes them to runtime `[project] dependencies`. The PoC ships its own stdlib fallback in a parallel module (`agent/granite_container/pty_driver_stdlib.py`) for the zero-dep path; the stdlib path is not exercised in the PoC's default run.
- **Interface changes**: None to public APIs. `agent/sdk_client.py` and `agent/claude_session.py` are untouched. The PoC's container is a new module path (`agent/granite_container/`) that the operator CLI invokes end-to-end.
- **Coupling**: The PoC *adds* a new boundary (the container) and *reduces* coupling between granite and the TUI (granite no longer has tools for judgment calls; PM absorbs them). The PoC does not couple to the bridge (no Telegram import; user-address output goes to a results log).
- **Data ownership**: The container owns two PTYs and the loop. Granite is stateless (no cross-turn history). The persona-priming slash commands live in `.claude/commands/` (repo-owned) and are version-controlled.
- **Reversibility**: The PoC is additive. Removing it deletes `agent/granite_container/`, `.claude/commands/prime-{pm,dev}-role.md`, the `valor-granite-loop` CLI entry point, and the runtime dep promotion - all small and atomic. The `agent/claude_session.py`, `agent/granite_router.py`, and `agent/sdk_client.py` files are untouched, so rollback does not affect the headless harness.
- **Persistent artifacts**: `.claude/commands/prime-pm-role.md` and `.claude/commands/prime-dev-role.md` are persisted in the repo so the slash command shape is auditable. The two PTYs the PoC spawns are short-lived (per-invocation); no PTY state survives across invocations.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer, validator

**Interactions:**

- PM check-ins: 2-3 (Q4 event-bridge shape decision; Q6 classification-accuracy mid-build measurement; verdict on the results doc)
- Review rounds: 2 (one after the substrate driver + persona priming land; one on the steady-state loop + results doc)

The PoC's appetite is large not because of coding volume but because the substrate risk is high and the results doc's verdict is the input to a downstream production-cutover issue. The substrate driver phase has the most "could break everything" risk (C1-C5 must be honored); the steady-state loop has the architectural claim (PM↔Dev multi-turn coordination). Both deserve a review round.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `claude` CLI on PATH, TUI v2.1.160+ | `claude --version` | Substrate is the TUI; TUI version governs the C2 interjection text and idle signal |
| `ollama` running with `granite4.1:3b` | `curl -s http://localhost:11434/api/tags \| python -c "import json,sys; assert any(m['name'].startswith('granite4.1:3b') for m in json.load(sys.stdin)['models'])"` | Granite is the local operator; the PoC cannot run without it |
| Max subscription OAuth (no `ANTHROPIC_API_KEY` in env) | `[ -z "$ANTHROPIC_API_KEY" ]` | Invariant; the PoC forces Max OAuth by blanking the key on the subprocess env |
| Python 3.11+ | `python --version` | `pexpect.spawn` with `preexec_fn` and `encoding="utf-8"` are stable on 3.11+ |
| `pexpect>=4.9.0`, `ptyprocess>=0.7.0` | `python -c "import pexpect; assert pexpect.__version__ >= '4.9.0'"` | Promoted from dev to runtime in this PoC |
| `claude` reachable from Max OAuth (model API responsive) | `claude --print "ping"` exits 0 within 10s | C3 + Q5: resume-UUID capture is environment-gated; the resume-acceptance test requires a model-reachable env |

Run all checks: `python scripts/check_prerequisites.py docs/plans/granite_interactive_tui_poc.md` (the check script is invoked by `/do-build` against the plan's *Prerequisites* table).

## Solution

### Key Elements

- **`agent/granite_container/pty_driver.py`**: A thin pexpect-backed PTY driver class with `spawn()`, `write()`, `read_until_idle()`, `send_ctrl_c()`, `close()`. Honors C1 (`\r` submit), C2 (interjection regex), C5 (glyph+bar+floor idle signal). Reuses `_UUID_RE` and `_RESUME_HINT_RE` from `agent/claude_session.py:49-52` and the `INTERRUPTED_RE` from `scripts/granite_tui_pty_spike_pexpect.py:64-72`.
- **`agent/granite_container/pty_driver_stdlib.py`**: A stdlib `pty` + `select` fallback in a parallel module, sharing the same interface. Not exercised in the default run; provided for the zero-dep path the spike validated as competitive.
- **`agent/granite_container/startup_parser.py`**: A small Python function that watches both PTYs for known startup shapes (login prompt, update notice, error modal, persona-prime acknowledgement, trust-folder prompt). Returns a `StartupEvent` enum value; routes to granite for response text. Pattern-matches a small enum; does not interpret.
- **`agent/granite_container/granite_classifier.py`**: A new granite router (not the existing `agent/granite_router.py`) with the reduced 3-tool taxonomy: `classify_pm_output` (returns `dev` / `user` / `complete`), `extract_dev_prompt` (translation: PM output tail → Dev user-turn prompt), `summarize_for_pm` (translation: Dev output → one-paragraph PM summary). Stateless: each call has only the system prompt + the current turn's content. Mirrors the Q4 event-bridge shape decision (see *Open Questions*).
- **`agent/granite_container/container.py`**: The loop. Owns both PTYs. Runs the 12-step start-up sequence from the issue. The main loop alternates PM and Dev turns, calling granite between each, writing to the appropriate PTY, and waiting for idle.
- **`.claude/commands/prime-pm-role.md`**: Persona-priming slash command. Authored as a write-time conversion of `config/personas/project-manager.md` + segments, with the routing instruction appended. Invoked as `/prime-pm-role <user message>`; the model sees the persona text and the user message; the input box shows only the literal typed text (F4).
- **`.claude/commands/prime-dev-role.md`**: Same shape, persona is `config/personas/developer.md`. The body instructs Dev to wait for the project manager's instructions.
- **`tools/granite_interactive_tui_poc/__init__.py` + `cli.py`**: The PoC CLI entry point. Declared in `pyproject.toml [project.scripts]` as `valor-granite-loop` (see *Agent Integration*). Invokes the container end-to-end with a user-message arg; writes the results JSON to a path the results doc reads.
- **`docs/plans/granite_interactive_tui_poc-results.md`**: The results doc. Captures latency, reliability, parse-failure modes, **PM-output classification accuracy**, single-turn latency, and an honest "this is / isn't viable" verdict. New spikes or issues filed for any constraint the PoC discovers to be wrong.

### Flow

```
Operator → valor-granite-loop --user-message "..." → Container.spawn(pm_pty, dev_pty)
                                                              ↓
                                  Container writes /prime-pm-role <msg>\r → PM PTY
                                  Container writes /prime-dev-role <msg>\r → Dev PTY
                                                              ↓
                              Startup-phase parser watches both PTYs; on trust-folder prompt,
                              asks granite for response text, writes "1\r" to dismiss
                                                              ↓
                                            Both PTYs reach idle → steady state
                                                              ↓
                          Container reads PM's idle buffer → calls granite(classify_pm_output)
                                                              ↓
                            granite returns {destination: dev, payload: <translated prompt>}
                                                              ↓
                                            Container writes <payload>\r → Dev PTY
                                                              ↓
                              Container reads Dev's idle buffer → calls granite(summarize_for_pm)
                                                              ↓
                                            Container writes <summary>\r → PM PTY
                                                              ↓
                                                  Loop until granite returns complete
                                                              ↓
                                                  Container writes results JSON
```

### Technical Approach

- **Substrate driver before everything.** Phase 1 lands `pty_driver.py` and self-tests it against the v7 spike's scenarios 1, 2, 3, 6 in isolation. If the driver does not pass these four scenarios, the PoC is blocked at the substrate; do not move on. The v7 spike's `scripts/granite_tui_pty_spike_pexpect.py` is the reference implementation; the PoC's driver adapts its `wait_for_idle` and `_send` helpers directly.
- **Persona priming before classification.** Phase 2 lands `.claude/commands/prime-{pm,dev}-role.md` and self-tests the priming path: spawn a single PTY, send `/prime-pm-role hello`, confirm the TUI reaches idle and the model responds to a follow-up. If the model does not respond, F2 substitution is broken in the env - investigate the model-reachability prerequisite before continuing.
- **Two-PTY coordination is the early risk.** Phase 5 (steady-state loop) is the first time two TUIs run side-by-side under one Python process. The container's idle-heuristic tuning must validate this *before* deeper PM/Dev logic lands; a coordination problem in the idle heuristic is a fundamental blocker. The substrate-driver phase is single-PTY; Phase 5 is the first multi-PTY milestone. Build a minimal two-PTY test (spawn both, prime both, wait for both to idle, write a "ping" to PM, wait for PM to idle, write a "ping" to Dev, wait for Dev to idle) before adding the granite classification layer.
- **Q4 event-bridge shape (decided in the plan):** map PTY output to the existing `list[dict]` event shape at the boundary. The new `granite_classifier.py` does not consume raw PTY bytes; the container strips ANSI, slices the buffer at the idle boundary, and wraps the text in `[{"type": "pm_output", "text": <tail>}]` (or `dev_output` for the Dev side). This keeps granite's interface stable across the prior PoC and the new PoC; the difference is in how events are *produced* (PTY → text slice) not how they are *consumed* (a small list of dicts). Justification: the granite `SYSTEM_PROMPT` and tool definitions are the same; the production cutover can adopt the new substrate without rewriting granite's prompt.
- **Q5 resume-UUID disposition (decided in the plan):** exercise resume inside the PoC itself in a model-reachable env (option b in the issue's *Planner handoff*). The PoC's resume test runs as part of Phase 6; it requires the `claude --print "ping"` prerequisite check to pass. The PoC does not block on #1552 closing first; if #1552 closes before the PoC's resume phase, the PoC inherits the spike's findings.
- **Q6 classification-accuracy measurement (decided in the plan):** the results doc reports classification accuracy on a *synthetic distribution* (constructed from the spike's transcripts) *plus* live measurements from the PoC's own runs. The test cases from the issue: (a) baseline with no overlay, (b) `/help` overlay state, (c) long output (1000+ tokens). Sub-95% accuracy is a *finding*, not necessarily a fail - but the plan must surface it. The classifier's prompt template is tuned against the measured data, not vibes.
- **Idempotent teardown.** Each phase's self-test ends with both children closed (`child.close(force=True)`) and a `pkill` fallback for orphans (mirroring the probe's teardown at `scripts/probe_slash_arguments.py:367-373`). The PoC's main loop uses `try/finally` to ensure teardown on any exception.
- **Idempotent slash-command authoring.** `.claude/commands/prime-{pm,dev}-role.md` are written once at Phase 2; the rest of the PoC invokes them by name. The persona-text content is a write-time conversion of `config/personas/{project-manager,developer}.md` (and the segments in `config/personas/segments/manifest.json`), not a live composition at runtime. The persona is versioned with the code; changing the persona is a code change.

## Failure Path Test Strategy

### Exception Handling Coverage

- The container's main loop uses `try/finally` to ensure both PTYs are closed on any exception. The teardown path also runs the `pkill` fallback so orphan children do not survive a hard-killed parent.
- The PTY driver's `read_nonblocking` calls catch `pexpect.TIMEOUT`, `pexpect.EOF`, and `pexpect.exceptions.ExceptionPexpect` (mirroring the spike's pattern at `scripts/granite_tui_pty_spike_pexpect.py:218-227`). Each catch is an observable behavior (continue the wait, break the loop, or log + return failure), not a silent pass.
- The startup-phase parser's regex matches are bounded - a parser miss is a structured `StartupEvent.UNKNOWN` return, not a swallowed exception.
- The granite classifier's ollama call is wrapped in `try/except Exception`; failures are logged with the model name + the failure type and surface as a `GraniteRoutingError` (the existing pattern in `agent/granite_router.py:163-169`).

### Empty/Invalid Input Handling

- The `valor-granite-loop` CLI rejects an empty `--user-message` (matches `ClaudeSessionError` in `agent/claude_session.py:81-83`).
- The PTY driver's `write()` rejects empty input. The PM output classifier's input is a non-empty text slice; if PM's idle buffer is empty (a degenerate case), the container logs and retries on the next idle cycle, not an infinite loop.
- The startup-phase parser's `StartupEvent.UNKNOWN` does not trigger an infinite retry; it surfaces as a single log line and the container continues (the steady state will detect any unhandled UI affordance via the next idle miss).

### Error State Rendering

- The PoC's results JSON captures: per-turn `granite_classification`, `granite_translation`, `idle_ms`, `pm_pty_bytes`, `dev_pty_bytes`, `parse_failures` (count), `exit_code` (per PTY), and `container_exit_reason` (one of: `pm_complete`, `pm_max_turns`, `dev_hang`, `startup_unresolved`, `exception`). The results doc renders these as a verdict.
- The trust-folder prompt dismissal is logged with a structured event (`startup_event: trust_folder`, `response: "1"`, `latency_ms`) so the results doc can confirm the parser handled it.
- A `max_turns` exit (safety cap) is a non-zero exit code with a `container_exit_reason: pm_max_turns` field; the results doc renders this as a finding, not a fail.

### Spike-regression test for the substrate driver

The substrate driver's self-test re-runs spike scenarios 1, 2, 3, 6 against the new driver. If any of these fail, the PoC is blocked at the substrate. The v7 spike's `pexpect/scenario-{1,2,3,6}.bin` transcripts are the regression reference; the new driver's per-scenario footer must match the spike's observed_state strings.

### Classification-accuracy test (Q6)

The synthetic distribution is constructed from the spike's transcripts (per the issue's Q6 test cases). The live measurement comes from the PoC's own steady-state loop runs (Phase 5). The results doc reports:
- Per-classification `accuracy_pct` (correct / total per class)
- Confusion matrix (developer-address ↔ user-address ↔ complete)
- `parse_failures_count` (granite produced no tool call)
- `idle_misses_count` (PTY read loop timed out waiting for idle)
- Per-turn `latency_ms` for granite, for PM PTY idle, for Dev PTY idle

The PoC's threshold for "viable" is sub-95% *only if* the failure mode is concentrated in a known-correlated class (e.g., long outputs misclassified as user-address); if failures are uniform across classes, the architecture is reconsidered.

### Startup-phase parser test

The parser's pattern set is enumerated in `agent/granite_container/startup_parser.py` as a list of `(regex, StartupEvent)` pairs. The self-test feeds each known pattern into the parser and asserts the right enum value. The trust-folder prompt is exercised in a real run as part of the steady-state loop; the results doc logs the dismissal.

### Resume test (model-reachable env only)

The resume test runs in Phase 6 only if the `claude --print "ping"` prerequisite passes. The test:
1. Spawn PM, prime, send a long prompt, wait for streaming to begin.
2. Send `\x03` (first ctrl-c) → expect `Press Ctrl-C again to exit` (C2).
3. Send `\x03` (second ctrl-c) → expect exit + on-exit hint with UUID (C3).
4. Re-spawn PM with `claude --resume <uuid>` → send a probe question whose answer requires the prior turn's context → assert the model references the prior context.

If the prerequisite fails, the resume test is *skipped* in the run, the results doc records `resume: skipped - env_unreachable`, and a follow-on issue is filed (the same gating logic the spike used at scenario 5).

## Test Impact

The PoC writes *new* code in a new module path (`agent/granite_container/`). Existing test surfaces are largely unaffected; the prior PoC's test surface is the most likely to require a `mock` update, but only if a test imports the prior PoC's `claude_session.py` for `-p` mode and the PoC's changes break that import path (the PoC does not change `claude_session.py` at all, so this is unlikely).

- [ ] `tests/unit/test_claude_session.py` - UPDATE: no source changes to `claude_session.py` are expected; if any test asserts on `_build_cmd` argv ordering, it still holds (the file is untouched). If a test asserts on the `INTERRUPTED_RE` text, it must be updated to the v2.1.160 text (C2) or accept either form. Verify in build.
- [ ] `tests/integration/test_granite_poc.py` (if it exists) - UPDATE: any test that imports `agent/granite_agent_loop.py` is testing the prior PoC's `-p`-driven loop, not the new PoC. The prior PoC is not modified; the test should continue to pass. Verify in build.
- [ ] `tests/unit/test_granite_router.py` - UPDATE: no changes to `agent/granite_router.py` are expected (the PoC ships a new `granite_classifier.py`, not a refactor of the existing router). The existing tests should continue to pass. Verify in build.
- [ ] New `tests/unit/granite_container/test_pty_driver.py` (create) - covers the PTY driver's `write()` submit-key behavior (C1), the `wait_for_idle` heuristic (C5), and the `_send("\x03")` two-stage interject path (C2). Spike-regression test compares the driver's per-scenario footer to the v7 spike's `pexpect/scenario-{1,2,3,6}.bin` observed_state.
- [ ] New `tests/unit/granite_container/test_startup_parser.py` (create) - covers the parser's `(regex, StartupEvent)` enumeration, including the trust-folder prompt.
- [ ] New `tests/unit/granite_container/test_granite_classifier.py` (create) - covers the 3-tool taxonomy's classification + translation; uses a recorded PM-output fixture (synthetic distribution from the spike transcripts) and asserts the right tool call per fixture.
- [ ] New `tests/integration/test_granite_container_loop.py` (create) - covers the container's end-to-end loop in a sandbox tempdir; runs with `--max-turns 3` and asserts the results JSON shape. **Integration test only runs in a model-reachable env** (gated on the `claude --print "ping"` prerequisite).

If the build discovers that the new tests in `tests/unit/granite_container/` collide with existing test discovery patterns, adjust conftest. The PoC does not modify `pyproject.toml` test config beyond adding `pexpect` and `ptyprocess` to runtime deps.

## Rabbit Holes

- **Extending `agent/sdk_client.py` or `agent/claude_session.py` rather than writing a new module path.** The PoC is additive; the headless harness is untouched. Reusing `claude_session.py`'s `_UUID_RE` and `_RESUME_HINT_RE` is encouraged (the spike does this); modifying the file to add PTY support is *not* - it would couple the PoC to the harness and break the "existing headless harness is untouched" invariant.
- **Re-introducing cross-turn history in granite.** Invariant #5: fresh context per turn. The new `granite_classifier.py` is stateless; a `HISTORY_KEEP_LAST_N` knob is explicitly out of scope. If a 1-line structured handoff field (e.g., "prior routing was user-address") becomes necessary, that's a follow-on optimization, not a hidden knob in the PoC.
- **Building a full bridge integration in the PoC.** The PoC writes user-address output to a results log, not to the Telegram bridge. The bridge wiring is a follow-on issue, not a PoC stretch goal. The `valor-granite-loop` CLI is the operator's invocation path; it is not invoked from the worker or the bridge in the PoC.
- **Attempting to verify `$ARGUMENTS` substitution at runtime.** F4: the slash command body is invisible to the user/operator. The only substrate signal is "did the model respond?" The PoC does not depend on a substitution-verification path; the priming is verified by the model responding correctly to a follow-up turn, not by parsing the input box state.
- **Custom `send_to_dev` or `reply_to_user` tools on PM.** Invariants #6, #7: rejected. The end-of-turn + granite classification path is the right level of abstraction. Adding a tool definition to PM is a *change to the persona*, not a knob in the PoC; the persona is fixed at the slash-command level.
- **Replacing the slash-command mechanism with `--append-system-prompt`.** The drop list in the issue explicitly rejects this. The persona-priming slash commands are part of the substrate; replacing them with a CLI flag is a regression to the prior PoC's design.
- **Bridging the spike's `pexpect/scenario-*.bin` transcripts to the new driver's tests as a one-shot import.** The transcripts are 1-MiB capped raw byte streams; the driver's per-scenario footer is a structured subset. The regression test re-runs the scenarios and compares the footers, not the bytes. This avoids coupling the test to the spike's transcript format.
- **Generalizing the trust-folder prompt dismissal to a "yes-to-all-prompts" pattern.** The trust-folder prompt is a *specific* startup event; the parser handles it with `1\r`. A generic "press 1 on every modal" pattern would suppress real error modals. The dismissal is bounded to the trust-folder pattern.

## Risks

### Risk 1: The substrate driver fails one of the spike-regression scenarios

**Impact:** The PoC is blocked at the substrate. The kernel cannot be proven. The architecture is reconsidered (or the substrate is investigated further; the issue is open to that escalation).

**Mitigation:** The substrate driver phase self-tests against scenarios 1, 2, 3, 6 *before* any other code is written. The spike's `pexpect/scenario-{1,2,3,6}.bin` transcripts are the regression reference. If a scenario fails, the failure mode is investigated at the substrate layer (idle heuristic, submit key, ANSI stripping) before moving on.

### Risk 2: Q6 classification accuracy is below 95% in live runs

**Impact:** The architecture's load-bearing routing decision is unreliable. Misrouting a developer-address as user-address (or vice versa) is exactly the unbounded failure mode the translator role is supposed to prevent.

**Mitigation:** The classification-accuracy measurement is part of Phase 5 (the steady-state loop). The PoC does not "fix" a low accuracy by tuning the prompt blindly; it reports the number and pauses for a PM check-in. The persona-prompt tuning is a follow-on, not a PoC stretch. The Q6 test cases (a, b, c) cover the known-correlated failure modes (baseline, slash-command overlay, long output) so the results doc's verdict can identify which class is failing.

### Risk 3: Two-PTY coordination in the idle heuristic is fragile

**Impact:** The first multi-PTY run is a fundamental blocker. A coordination problem in the idle heuristic would invalidate Phase 5 and force a redesign of the container's read loop.

**Mitigation:** Phase 5 includes a minimal two-PTY test (ping-pong both PTYs to idle) *before* the granite classification layer is added. The substrate driver is single-PTY; the steady-state loop is the first multi-PTY milestone. A multi-PTY regression test in `tests/integration/test_granite_container_loop.py` is the durable guard against coordination regressions.

### Risk 4: Resume-UUID acceptance test cannot run in the test env

**Impact:** The 9-point DoD's "Resume" criterion is paper-only. The architecture's resume invariant is not empirically validated.

**Mitigation:** The Q5 disposition is option (b) from the issue: exercise resume inside the PoC itself in a model-reachable env. The resume test is gated on the `claude --print "ping"` prerequisite; if the env is unreachable, the test is *skipped* with a structured log line and a follow-on issue is filed. The PoC's results doc renders this as a *finding*, not a fail.

### Risk 5: granite4.1:3b's classification is too noisy on long PM output (Q6 case c)

**Impact:** The 1000+ token test case is the most likely failure mode. Long PM output is exactly the case where a small-model classifier drifts; the spike did not exercise this (it tested idle stability, not continuous streaming load).

**Mitigation:** The synthetic distribution includes long-output fixtures (synthesized from the spike's 5-minute idle transcript). The live measurement comes from a deliberate long-output turn in Phase 5 (e.g., PM is asked to plan a 5-step architecture and respond in full). If accuracy drops on long output, the results doc reports the accuracy-vs-output-length curve and the plan defers a follow-on spike on streaming-only classification.

### Risk 6: Persona-priming slash commands don't work with a multi-line user message

**Impact:** F3 says multi-word args are preserved as a single arg string, but the probe tested only `"hello world"`. A multi-line user message (newlines, special characters, markdown) is a real production input shape; the probe did not exercise it.

**Mitigation:** The persona-priming phase self-tests with a multi-line user message (newlines, a markdown block, a special character). If the TUI rejects the input or the model sees a partial message, the issue is investigated at the substrate layer (input-box escape, character encoding) before moving on.

## Race Conditions

### Race 1: Both PM and Dev PTYs reach idle at the same instant, container reads PM's buffer but writes to Dev

**Location:** `agent/granite_container/container.py:main_loop` (the steady-state loop).

**Trigger:** PM's turn ends (PM PTY idle) and Dev's turn ends (Dev PTY idle) within the same read-tick window. The container's loop processes one PTY at a time; if PM's idle is detected first, the loop processes PM's output and writes to Dev's PTY. Dev's idle is then *re-detected* when Dev's PTY reaches idle *after* the write.

**Data prerequisite:** PM's output tail must be fully accumulated before granite is called; Dev's PTY must reach idle *after* the write, not before.

**State prerequisite:** Both PTYs are in known idle states at the start of the loop iteration; the loop's invariant is "PM's last action caused Dev's next action; process them in order."

**Mitigation:** The container's loop is single-threaded; reads from both PTYs are not interleaved within a single tick. The loop processes one PM→granite→Dev→granite→PM cycle per tick. The idle heuristic for each PTY is re-evaluated after each write, so a "Dev already idle when PM finished" condition is correctly handled as "Dev's prior turn is done; container writes PM's next instruction; Dev processes it."

### Race 2: Startup-phase parser detects a startup event on PM's PTY, but Dev's PTY is concurrently producing output

**Location:** `agent/granite_container/container.py:startup_phase`.

**Trigger:** The parser's regex matches a startup event on PM's PTY; the container asks granite for response text. While granite is computing (1-3 seconds), Dev's PTY is still producing output (its own startup phase).

**Data prerequisite:** The granite response is bound to the specific startup event that triggered the ask.

**State prerequisite:** The container's startup parser is event-scoped, not PTY-scoped; the parser's response handler writes to the same PTY that produced the event.

**Mitigation:** The startup parser's response handler takes both the event and the PTY identifier (`pm` or `dev`) as arguments; the write goes to the correct PTY. The parser's events are processed sequentially (not in parallel), so a Dev startup event is handled before a PM startup event if both fire in the same window. The `startup_event: <name>, pty: <pm|dev>` log line makes the routing auditable in the results JSON.

### Race 3: PM's PTY reaches idle but the accumulated buffer is from a stale turn (PM is re-prompted faster than the model finishes a prior turn)

**Location:** `agent/granite_container/pty_driver.py:wait_for_idle`.

**Trigger:** The TUI briefly re-renders the bottom bar while the model is still processing; the bare `bypass permissions` text is visible. The container's `wait_for_idle` returns "idle" but the buffer is from a prior turn.

**Data prerequisite:** `min_content_bytes` (per C5) ensures the bar appears *after* response content has streamed in.

**State prerequisite:** The C5 heuristic requires glyph + bar + content-floor.

**Mitigation:** The `wait_for_idle` heuristic inherits the spike's `min_content_bytes` parameter (default 0 for "initial idle", default 400 for "post-reply idle"). The container's loop uses 400 for all post-reply waits. The spike's empirical observation (scenario 2 with stdlib: 627ms vs pexpect: 305ms on a 1-turn hello) is the calibration point; the new driver's heuristic is conservative (matches the spike's pexpect path).

### Race 4: Container writes to Dev's PTY before Dev has finished processing the prior turn

**Location:** `agent/granite_container/container.py:main_loop` (the steady-state loop, between PM and Dev).

**Trigger:** PM's turn ends, container calls granite, granite returns a translated prompt, container writes to Dev's PTY *before* Dev's PTY has reached idle from a prior turn.

**Data prerequisite:** Dev's PTY must be in the idle state before any new write.

**State prerequisite:** Invariant: the container only writes to a PTY that is in the idle state.

**Mitigation:** The container's `await_idle(pty)` is called before every `write(pty, text)`. If `await_idle` times out, the container treats the timeout as a Dev-hang operator event, surfaces it to the results JSON, and exits the loop with `container_exit_reason: dev_hang`. There is no path that writes to a non-idle PTY.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1542] Replacing `agent/sdk_client.py` as the unbypassable root session runner. The cancelled cutover's framing is intentionally *not* inherited; the production cutover is a separate follow-on, gated on this PoC + #1547. (Confirm: `gh issue view 1542 --json state` returns `closed`.)
- [SEPARATE-SLUG] Bridge integration (Telegram wiring, dashboard, dual-resume UI). The PoC writes user-address output to a results log, not to the bridge. The production wiring is a follow-on issue, not part of this PoC.
- [SEPARATE-SLUG] `AgentSession` schema change to store two `claude_session_uuid` fields (PM UUID + Dev UUID). The PoC's container is a standalone module; the schema change is a follow-on. (Filed as part of the production cutover scope.)
- [SEPARATE-SLUG #1552] Resume-UUID capture spike in a model-reachable env. The PoC's resume test (Phase 6) runs in the PoC's own model-reachable env; #1552's findings are a corroborating reference, not a prerequisite.
- [EXTERNAL] Validating granite's reduced 2-tool taxonomy on the full distribution of real PM output. The PoC validates on the synthetic distribution from the spike; the production deployment is a follow-on with a larger real-PM-output sample.
- [EXTERNAL] Validating the startup-phase parser against the long tail of unpredictable TUI startup events. The PoC validates the predictable set (login, update, error, persona-prime ack, trust-folder); the long tail is a follow-on.
- [ORDERED] Continuous token-by-token streaming load beyond a 5-minute idle hold. The spike exercised one long idle, not continuous streaming. The Q6 test case (c) for 1000+ token output is in scope; a sustained-streaming test is a follow-on.
- [SEPARATE-SLUG] Cross-turn history accumulation in granite. The PoC uses fresh context per turn; a 1-line structured handoff field is a follow-on optimization. The PoC's data should be sufficient to justify either fresh-only or fresh + handoff.
- [ORDERED] Model-per-role config at the runner level. The PoC hardcodes `claude --model sonnet --permission-mode bypassPermissions`; a per-persona model picker is a production-cutover concern.
- [SEPARATE-SLUG] Full-distribution PM-output classification accuracy. The PoC reports accuracy on the synthetic distribution + live measurements from the PoC's own runs; a 1000-sample real-PM-output study is a follow-on.
- [EXTERNAL] Bridging the spike's transcripts to the new driver as a one-shot import. The driver's regression test re-runs the scenarios and compares footers, not bytes. The spike's transcripts are reference material, not test fixtures.

## Update System

The PoC is purely additive (new module path, new persona-priming slash commands, new CLI entry point, new runtime deps). No existing update-script paths are affected.

- **`pyproject.toml` runtime dep promotion**: `pexpect>=4.9.0` and `ptyprocess>=0.7.0` are currently in `[project.optional-dependencies] dev`. The PoC promotes them to runtime `[project] dependencies`. The existing update script (`scripts/remote-update.sh`) syncs `pyproject.toml` on each machine, so the promotion is automatically propagated on the next `/update`.
- **No new secrets, config files, or env vars.** The PoC forces Max OAuth by blanking `ANTHROPIC_API_KEY` on the subprocess env (mirroring `agent/claude_session.py:90-101`); no new env var is required.
- **No new machines, services, or external API calls.** The PoC is local to the dev machine; the only external resource is the Claude Code TUI (already a runtime requirement) and `ollama` (already a runtime requirement for the prior PoC).
- **No migration steps.** The PoC is additive; existing installations continue to work without the new module path. The persona-priming slash commands are read at TUI runtime, so they take effect on the next `claude` invocation in a directory with the new `.claude/commands/` content.

No update-system changes required beyond the runtime dep promotion. The new deps propagate via the existing update script.

## Agent Integration

The agent receives Telegram messages via the bridge (`bridge/telegram_bridge.py`) and reaches new functionality through one of two surfaces: a CLI entry point declared in `pyproject.toml [project.scripts]` (invoked via the agent's Bash tool), or a direct Python import the bridge calls internally. New Python functions in `tools/` are invisible to the agent until wired into one of those two paths.

- **New CLI entry point `valor-granite-loop`** declared in `pyproject.toml [project.scripts]` as `valor-granite-loop = "tools.granite_interactive_tui_poc.cli:main"`. The CLI takes `--user-message <text>` and `--max-turns <int>` (default 10) and writes a results JSON to the path the operator specifies. The agent can invoke the PoC end-to-end via this CLI.
- **No bridge integration in this PoC.** The PoC writes user-address output to a results log, not to the Telegram bridge. The production wiring (bridge writes inbound messages to PM's PTY; container writes user-address to the bridge as outbound) is a follow-on issue.
- **No new MCP server in this PoC.** The PoC's components are reached via the CLI entry point and via direct Python import (`from agent.granite_container.container import Container`). The bridge does not import the PoC.
- **Integration test**: `tests/integration/test_granite_container_loop.py` invokes the `valor-granite-loop` CLI as a subprocess and asserts the results JSON shape. The test runs in a model-reachable env (gated on the `claude --print "ping"` prerequisite). In a non-reachable env, the test is *skipped* with a structured log line and the integration test gate is documented in the test's docstring.

## Documentation

### Feature Documentation

- [ ] Create `docs/features/granite-interactive-tui.md` describing the new architecture: the 3-layer model (Bridge → Container → Granite + PM/Dev), the 10 invariants, the persona-priming flow, the granite classification + translation taxonomy, the steady-state loop. Cross-reference the spike report (`docs/research/spikes/granite-tui-pty-spike.md`) for C1-C5 substrate facts and the probe (`scripts/probe_slash_arguments.py`) for F1-F4 persona-priming facts.
- [ ] Create `docs/features/pty-driver.md` describing the substrate driver class: the `wait_for_idle` heuristic (C5), the `\r` submit key (C1), the two-stage ctrl-c interject handling (C2), the env-blanking for Max OAuth, the `pexpect` import path and the stdlib fallback module. Include a "Why pexpect over stdlib" section (mirrors the spike report's verdict).
- [ ] Add an entry to `docs/features/README.md` index table for `granite-interactive-tui` and `pty-driver`.
- [ ] Update `docs/features/granite-agent-loop.md` (the prior PoC's docs) with a short notice that the prior PoC was superseded by the new interactive-TUI PoC and pointing to the new doc. (The prior PoC's docs at `granite-agent-loop.md:294-296` on the interjection text are *not* updated - the new doc is the source of truth, the prior doc is historical.)
- [ ] Update the spike report's *Constraints for #1546* section header to add a "validated by #1546 PoC at <commit>" link once the PoC lands.

### External Documentation Site

- [ ] No external documentation site. This is a local CLI tool with feature docs in `docs/features/`.

### Inline Documentation

- [ ] Code comments on the idle heuristic, the submit-key behavior, the persona-priming body (the "address the developer or the user" instruction is non-obvious without a comment), and the startup-phase parser's pattern set.
- [ ] Updated docstrings for the new `Container` class (`agent/granite_container/container.py`) and the new `granite_classifier.py` module.

## Success Criteria

- [ ] A PoC script drives a real interactive `claude` session via PTY end-to-end, unattended, with zero `claude -p`. Grep confirms: `grep -rn 'claude -p\|--print' agent/granite_container/ tools/granite_interactive_tui_poc/` returns no matches in source code.
- [ ] Both PM and Dev subprocesses are primed via persona-priming slash commands (`.claude/commands/prime-pm-role.md`, `prime-dev-role.md`) invoked as the first PTY input, with the user message passed as `$ARGUMENTS`. Both sessions know the user task; Dev's slash-command body instructs it to wait; PM's tells it to address the developer or the user.
- [ ] Granite handles, in a live run: a numbered multiple-choice menu, a permission/feedback prompt, ≥3 multi-turn PM↔Dev exchanges, a PM user-address message routed to the results log, and a `claude --resume` recovery after interruption with context intact (the last only if the model-reachable prerequisite passes; otherwise the test is skipped with a structured log).
- [ ] Runs under Max OAuth. `ANTHROPIC_API_KEY=""` is set on the subprocess env (no API key in the parent env's reach).
- [ ] The results doc records feasibility: latency, reliability, parse failure modes, **PM-output classification accuracy on the synthetic distribution + live measurements**, single-turn latency, and an honest "this is / isn't viable" verdict. New spikes or issues filed for any constraint the PoC discovers to be wrong.
- [ ] Tests pass: `pytest tests/ -x -q` (with the model-reachable integration test gated on the prerequisite).
- [ ] Lint clean: `python -m ruff check .` and `python -m ruff format --check .` exit 0.
- [ ] Code quality standards met (new code lands in `agent/granite_container/`; existing `agent/claude_session.py`, `agent/granite_router.py`, `agent/sdk_client.py` are untouched).
- [ ] Changes committed and pushed (PR is opened against `main`; tracking issue #1546 is *not* closed by the plan PR; the implementation PR closes #1546 per the project's SDLC conventions).
- [ ] Original request fulfilled: a standalone PoC that proves the granite operator drives the real interactive Claude Code session via PTY, end-to-end, unattended, with zero `claude -p`.

## Team Orchestration

When this plan is executed via `/do-build`, the lead agent orchestrates work using Task tools. The lead NEVER builds directly; they deploy team members and coordinate.

### Team Members

- **Builder (substrate-driver)**
  - Name: substrate-builder
  - Role: Land `agent/granite_container/pty_driver.py` + `pty_driver_stdlib.py`; self-test against spike scenarios 1, 2, 3, 6.
  - Agent Type: builder
  - Resume: true

- **Builder (persona-priming)**
  - Name: persona-builder
  - Role: Author `.claude/commands/prime-pm-role.md` + `prime-dev-role.md`; self-test the priming path against a single PTY.
  - Agent Type: builder
  - Resume: true

- **Builder (startup-parser)**
  - Name: startup-parser-builder
  - Role: Land `agent/granite_container/startup_parser.py`; enumerate the `(regex, StartupEvent)` pairs; self-test the trust-folder prompt dismissal.
  - Agent Type: builder
  - Resume: true

- **Builder (granite-classifier)**
  - Name: classifier-builder
  - Role: Land `agent/granite_container/granite_classifier.py`; reduce the 5-tool taxonomy to 3; ensure stateless (fresh context per turn).
  - Agent Type: builder
  - Resume: true

- **Builder (container-loop)**
  - Name: container-builder
  - Role: Land `agent/granite_container/container.py`; integrate the driver, parser, classifier; run the steady-state loop. Includes the two-PTY coordination test.
  - Agent Type: builder
  - Resume: true

- **Builder (cli-and-integration)**
  - Name: cli-builder
  - Role: Land `tools/granite_interactive_tui_poc/cli.py`; declare `valor-granite-loop` in `pyproject.toml`; promote `pexpect` and `ptyprocess` to runtime deps.
  - Agent Type: builder
  - Resume: true

- **Validator (substrate-regression)**
  - Name: substrate-validator
  - Role: Re-run the spike-regression test for the substrate driver; compare to the v7 spike's `pexpect/scenario-{1,2,3,6}.bin` footers.
  - Agent Type: validator
  - Resume: true

- **Validator (classification-accuracy)**
  - Name: accuracy-validator
  - Role: Build the synthetic distribution from the spike transcripts; run the live measurements from the PoC's steady-state loop; produce the Q6 accuracy-vs-class confusion matrix.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: doc-writer
  - Role: Create `docs/features/granite-interactive-tui.md` + `docs/features/pty-driver.md`; update the `docs/features/README.md` index; add the spike report link.
  - Agent Type: documentarian
  - Resume: true

- **Final Validator**
  - Name: final-validator
  - Role: Run the full acceptance checklist; verify the 9-point DoD; produce the verdict for the results doc.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Substrate driver
- **Task ID**: build-substrate-driver
- **Depends On**: none
- **Validates**: `tests/unit/granite_container/test_pty_driver.py` (create); spike-regression comparison to `pexpect/scenario-{1,2,3,6}.bin`
- **Informed By**: spike #1547 (C1-C5 substrate facts)
- **Assigned To**: substrate-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/granite_container/__init__.py` and `agent/granite_container/pty_driver.py` with `spawn()`, `write()`, `read_until_idle()`, `send_ctrl_c()`, `close()`. Reuse `_UUID_RE`, `_RESUME_HINT_RE` from `agent/claude_session.py:49-52` and `INTERRUPTED_RE` from `scripts/granite_tui_pty_spike_pexpect.py:64-72`.
- Honor C1 (`\r` submit), C2 (interjection regex), C5 (glyph + bar + content-floor idle).
- Create `agent/granite_container/pty_driver_stdlib.py` as a parallel stdlib fallback (not exercised in default run).
- Self-test against spike scenarios 1, 2, 3, 6 in isolation. If any fail, the PoC is blocked at the substrate; investigate the failure mode before continuing.

### 2. Substrate regression validator
- **Task ID**: validate-substrate-driver
- **Depends On**: build-substrate-driver
- **Validates**: re-runs spike scenarios 1, 2, 3, 6 against the new driver; compares per-scenario footer observed_state to the v7 spike's transcripts.
- **Assigned To**: substrate-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify the new driver's per-scenario behavior matches the spike's.
- Report pass/fail per scenario.

### 3. Persona priming
- **Task ID**: build-persona-priming
- **Depends On**: build-substrate-driver
- **Validates**: `tests/unit/granite_container/test_persona_priming.py` (create)
- **Informed By**: probe F1-F4 (`scripts/probe_slash_arguments.py`)
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: true
- Author `.claude/commands/prime-pm-role.md` (write-time conversion of `config/personas/project-manager.md` + segments, with the routing instruction appended).
- Author `.claude/commands/prime-dev-role.md` (write-time conversion of `config/personas/developer.md` + segments, with the "wait for project manager" instruction appended).
- Self-test by spawning a single PTY, sending `/prime-pm-role hello`, confirming the TUI reaches idle and the model responds to a follow-up.
- Self-test the multi-line user message case (newlines, markdown, special characters). If the TUI rejects or the model sees a partial message, investigate the input-box escape / character encoding.

### 4. Startup-phase parser
- **Task ID**: build-startup-parser
- **Depends On**: build-substrate-driver
- **Validates**: `tests/unit/granite_container/test_startup_parser.py` (create)
- **Assigned To**: startup-parser-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/granite_container/startup_parser.py` with a `(regex, StartupEvent)` enumeration covering: login prompt, update notice, error modal, persona-prime acknowledgement, **trust-folder prompt** (per the F-probe finding at `scripts/probe_slash_arguments.py:243-247`).
- Self-test: feed each known pattern into the parser and assert the right enum value. Self-test the trust-folder prompt dismissal (`1\r`) against the parser's response handler.

### 5. Granite classifier
- **Task ID**: build-granite-classifier
- **Depends On**: build-substrate-driver
- **Validates**: `tests/unit/granite_container/test_granite_classifier.py` (create)
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/granite_container/granite_classifier.py` with the reduced 3-tool taxonomy: `classify_pm_output` (returns `dev` / `user` / `complete`), `extract_dev_prompt` (translation), `summarize_for_pm` (translation).
- Ensure stateless: each ollama.chat() call has only the system prompt + the current turn's content. No cross-turn history.
- Wire to the Q4 event-bridge shape decision: container strips ANSI from the PTY buffer, slices at the idle boundary, wraps in `[{"type": "pm_output" | "dev_output", "text": <tail>}]`. Granite consumes this list, same shape as `agent/granite_router.py:276` consumes.
- Self-test by feeding a known developer-address / user-address / completion signal into granite and confirming the right classification.

### 6. Container (steady-state loop + two-PTY coordination)
- **Task ID**: build-container-loop
- **Depends On**: build-substrate-driver, build-persona-priming, build-startup-parser, build-granite-classifier
- **Validates**: `tests/integration/test_granite_container_loop.py` (create); two-PTY coordination test runs before the granite classification layer is added.
- **Assigned To**: container-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/granite_container/container.py` with the 12-step start-up sequence from the issue body and the steady-state loop.
- **Two-PTY coordination test FIRST** (before granite): spawn both PTYs, prime both, wait for both to idle, write a "ping" to PM, wait for PM to idle, write a "ping" to Dev, wait for Dev to idle. If this fails, the multi-PTY idle heuristic is broken; do not add the granite classification layer.
- Wire the granite classifier into the loop after the two-PTY test passes.
- Implement the Q6 classification-accuracy measurement: log per-turn classification + correct/incorrect + confusion-matrix fields. Report in results JSON.
- Implement resume test scaffolding (gated on the model-reachable prerequisite; skipped with structured log if not reachable).

### 7. CLI entry point + runtime dep promotion
- **Task ID**: build-cli
- **Depends On**: build-container-loop
- **Validates**: `tests/integration/test_granite_container_loop.py` invokes the CLI as a subprocess
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `tools/granite_interactive_tui_poc/__init__.py` + `cli.py` with the `main()` entry point. CLI takes `--user-message <text>` (required) and `--max-turns <int>` (default 10) and `--output <path>` (default `./granite_poc_results.json`).
- Declare `valor-granite-loop = "tools.granite_interactive_tui_poc.cli:main"` in `pyproject.toml [project.scripts]`.
- Promote `pexpect>=4.9.0` and `ptyprocess>=0.7.0` from `[project.optional-dependencies] dev` to runtime `[project] dependencies`.

### 8. Classification-accuracy validator
- **Task ID**: validate-classification-accuracy
- **Depends On**: build-container-loop
- **Validates**: `tests/unit/granite_container/test_granite_classifier.py` synthetic-distribution test; live measurements from the PoC's own runs.
- **Assigned To**: accuracy-validator
- **Agent Type**: validator
- **Parallel**: false
- Build the synthetic distribution from the spike's transcripts (per the issue's Q6 test cases: baseline, slash-command overlay, long output).
- Run the live measurements from the PoC's steady-state loop runs.
- Produce the Q6 accuracy-vs-class confusion matrix. Surface the result in the results doc.

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-substrate-driver, validate-classification-accuracy
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/granite-interactive-tui.md` describing the architecture, the 10 invariants, the persona-priming flow, the granite classification + translation taxonomy, the steady-state loop. Cross-reference the spike report and the probe.
- Create `docs/features/pty-driver.md` describing the substrate driver class.
- Add entries to `docs/features/README.md` index.
- Update the prior PoC's `docs/features/granite-agent-loop.md` with a short notice pointing to the new doc.

### 10. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature, build-cli
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands: `pytest tests/ -x -q`, `python -m ruff check .`, `python -m ruff format --check .`.
- Run the `valor-granite-loop` CLI end-to-end in a model-reachable env. Verify the 9-point DoD.
- Generate the final results doc at `docs/plans/granite_interactive_tui_poc-results.md` with the verdict (viable / not viable / viable-with-findings) and the per-criterion evidence.
- File any follow-on issues for constraints the PoC discovered to be wrong.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No `claude -p` in new code | `grep -rn 'claude -p\|--print' agent/granite_container/ tools/granite_interactive_tui_poc/ .claude/commands/prime-*.md` | exit code 1 (no matches) |
| Runtime dep promotion | `grep -A 20 '^\[project\]\ndependencies' pyproject.toml \| grep -E 'pexpect\|ptyprocess'` | exit code 0 (both present) |
| CLI entry point registered | `grep 'valor-granite-loop' pyproject.toml` | exit code 0 |
| Substrate driver regression | `python -c "from agent.granite_container.pty_driver import PTYDriver; driver = PTYDriver(); driver.spawn(); assert driver.wait_for_idle()"` (in a model-reachable env) | exit code 0 |
| Persona priming smoke | `python -c "from agent.granite_container.container import Container; c = Container('hello world'); c.run(max_turns=1)"` (in a model-reachable env) | exit code 0, results JSON contains `pm_prime: true`, `dev_prime: true` |
| Two-PTY coordination | `python -c "from agent.granite_container.container import Container; c = Container('ping'); c.run_ping_pong_test()"` (in a model-reachable env) | exit code 0, results JSON contains `coordination_test: pass` |
| Classification accuracy | `python -m tools.granite_interactive_tui_poc.eval_classification` (in a model-reachable env) | exit code 0, results JSON contains `accuracy_pct` ≥ 95 per class |
| Resume test (env-gated) | `python -m tools.granite_interactive_tui_poc.eval_resume` (skipped if `claude --print "ping"` fails) | exit code 0 OR exit code 0 with `resume: skipped - env_unreachable` in results JSON |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

The issue identifies three open questions (Q4, Q5, Q6) the plan's job is to resolve. All three are resolved in *Solution → Technical Approach*; this section records the resolution for the build's reference and for the critique review.

1. **Q4 (event-bridge shape):** Resolved. The container maps PTY output to the existing `list[dict]` event shape at the boundary: `[{"type": "pm_output" | "dev_output", "text": <tail>}]`. Granite consumes this list, the same shape `agent/granite_router.py:276` consumes today. Justification: the granite `SYSTEM_PROMPT` and tool definitions are unchanged; the production cutover can adopt the new substrate without rewriting granite's prompt.

2. **Q5 (resume UUID):** Resolved. The PoC exercises resume inside itself in a model-reachable env (option b in the issue's *Planner handoff*). The test is gated on the `claude --print "ping"` prerequisite; if the env is unreachable, the test is *skipped* with a structured log line (`resume: skipped - env_unreachable`) and a follow-on issue is filed. The PoC does not block on #1552 closing first; if #1552 closes before the PoC's resume phase, the PoC inherits the spike's findings as a corroborating reference.

3. **Q6 (PM-output classification accuracy):** Resolved. The PoC's results doc reports accuracy on a synthetic distribution (constructed from the spike's transcripts) *plus* live measurements from the PoC's own runs. Test cases per the issue: (a) baseline with no overlay, (b) `/help` overlay state (C4), (c) long output (1000+ tokens). Sub-95% accuracy is a *finding*, not necessarily a fail - but the plan surfaces it through the Q6 confusion matrix in the results doc. The classifier's prompt template is tuned against the measured data, not vibes.

4. **Steady-state multi-turn PM↔Dev coordination:** Resolved. The two-PTY coordination test is the first sub-task of the container phase, *before* the granite classification layer is added. A multi-PTY regression test in `tests/integration/test_granite_container_loop.py` is the durable guard against coordination regressions.

5. **Startup-phase parser pattern set:** Resolved. The parser's `(regex, StartupEvent)` enumeration covers: login prompt, update notice, error modal, persona-prime acknowledgement, **trust-folder prompt** (per the F-probe finding). The dismissal for trust-folder is `1\r` (per the probe's confirmed dismissal at `scripts/probe_slash_arguments.py:243-247`). The parser is enumerated in `agent/granite_container/startup_parser.py`; the implementer does not enumerate at runtime.
