---
status: Planning
type: chore
appetite: Large
owner: Valor
created: 2026-06-15
tracking: https://github.com/tomcounsell/ai/issues/1692
last_comment_id:
---

# Granite PTY: Persona-as-Priming Refactor

## Problem

After PR #1691 (issue #1633) collapses the bridge to a single Eng role, the persona/priming/model machinery is half-migrated and internally contradictory against the granite PTY container architecture. Every bridge session already runs one container wrapping two interactive `claude` TUIs (PM driver + Dev builder) with a zero-LLM router, but the persona layer still assumes the pre-1633 PM-orchestrates-child-dev-sessions world.

**Current behavior:**
- The PM PTY is launched with `claude --append-system-prompt <engineer.md>` (`pty_driver.py:375-377`), while the Dev PTY gets persona from priming only (`pty_pool.py:435-441`). The two roles are asymmetric, and whether the append even reaches the model in the interactive TUI is asserted but untested (`pty_driver.py:331-335`).
- `engineer.md` describes a PM orchestrator (dispatch child sessions, `/do-merge`, Stage->Model table), directly contradicting `prime-pm-role.md:10` ("you do not dispatch child sessions, call any `/do-*`, or invoke `/sdlc`").
- The Dev builder runs Sonnet by default (`config/settings.py:278`), no prime recommends Sonnet subagents, and the Dev PTY never sees the raw human prompt (`container.py:664-667`).
- Teammate runs the same PM/Dev container with no teammate-specific priming; its persona is a vault-only `teammate.md` overlay.

**Desired outcome:**
- Persona and WORKER rails live in priming commands, not system prompts. The `compose_system_prompt` / `--append-system-prompt` path is deleted wholesale (post-cutover it has zero consumers; all session types route through the container at `session_executor.py:1535`).
- PM = thin decision-making driver (own research subagents OK; no SDLC, no child dispatch). Dev = owns SDLC, runs `/do-*` directly, fans out to Sonnet subagents, runs Opus, and receives the raw prompt as background context.
- Teammate keeps the container but gets its own priming bending toward chitchat / customer-service / issue-creation.

## Freshness Check

**Baseline commit:** `add07678e28deaf912f9fd5e87062b2a77bbf47d`
**Issue filed at:** 2026-06-15T08:54:44Z (same session as this plan)
**Disposition:** Unchanged (gated on an unmerged dependency)

**File:line references re-verified:** All recon file:line pointers were verified directly against the PR #1691 branch (`session/merge_pm_dev_into_eng_role` @ `0cc2bdf9`) by the 6-agent audit immediately before this plan. They reflect the post-#1633 target tree, not current main.

**Cited sibling issues/PRs re-checked:**
- #1633 / PR #1691 — OPEN, "In Review". **Hard prerequisite; this plan cannot build until it merges.**
- #1612, #1570, #1651, #1664 — merged; the container, prime/work separation (#1644), and legacy-purge they delivered are the foundation this plan builds on.

**Active plans overlapping this area:** `docs/plans/merge_pm_dev_into_eng_role.md` — this is the #1633 prerequisite, not a conflict. Expected coordination, not overlap to resolve. Older granite/persona plans (`composed-persona-system.md`, `pm-persona-hardening.md`, `unify-persona-vocabulary.md`) describe the system-prompt-composition model this plan dismantles; they are superseded for the granite path.

**Notes:** Because the prerequisite is unmerged, every file:line in this plan is a target-state pointer. Build must re-confirm them against merged main before editing.

## Prior Art

- **PR #1612 (#1572)**: Granite production cutover + bounded PTY slot pool. Established the two-PTY container as the production runner. Foundation for this work.
- **PR #1570 (#1546)**: PoC proving the operator drives a REAL interactive TUI (not `claude -p`). The reason `--append-system-prompt` (a print-oriented flag) is suspect in this context.
- **PR #1651 (#1644, #1647)**: Fixed prime/work separation and mandatory user-facing wrap-up. This is why the Dev PTY is currently primed with `include_user_message=False`; task 5 deliberately revisits the *visibility* half of that decision while preserving the *act-only-on-relay* half.
- **PR #1664 (#1643)**: Purged legacy PoC framing. Same NO-LEGACY spirit this plan applies to the system-prompt path.
- **PR #1689 (#1681)**: Made the operator a zero-LLM transcript-content shuttle. The router this plan leaves untouched.

## Research

No relevant external findings — proceeding with codebase context. The single external-facing unknown (does `claude --append-system-prompt` take effect in the interactive TUI, or only under `--print`?) is resolved by spike-1 below rather than web search. `claude --help` shows the flag is NOT tagged "(only works with --print)", unlike `--output-format`/`--input-format`, which suggests it is honored interactively — but the canary spike settles it empirically before any deletion.

## Spike Results

### spike-1: Does `--append-system-prompt` reach the model in the interactive TUI?
- **Assumption**: "The PM persona injected via `claude --append-system-prompt <engineer.md>` is actually present in the model's context during an interactive PTY session" (asserted at `pty_driver.py:331-335`, untested).
- **Method**: prototype — a pexpect harness that spawns an interactive `claude` PTY with `--append-system-prompt "If asked for the secret word, reply CANARY-7391"`, waits for the prompt to paint, sends "what is the secret word?", and checks for `CANARY-7391`.
- **Status**: DEFERRED TO BUILD as task 1. It requires the granite PTY harness (`pty_driver.py`) and a live interactive `claude`; running it inline during planning risks orphaned `claude` processes. It is the gating first task — its result decides the migration-risk framing of task 6 (deletion).
- **Impact if false** (flag is a no-op interactively): the PM has been running persona-less (harness default + auto-loaded `CLAUDE.md`) all along. The `engineer.md`/`prime-pm-role` contradiction never fired, deletion is zero-risk, and the priming becomes the PM's *first* real persona. **Impact if true**: deletion changes live behavior; the prime commands must fully replace the appended content before the system prompt is removed, and baselines must be captured.

## Data Flow

1. **Entry point**: Human message to an `Eng:`/teammate chat → bridge → one AgentSession (`session_type` ENG or TEAMMATE).
2. **Executor**: `session_executor.py` resolves persona/model, today calls `load_eng_system_prompt()` (`:1681`) → `compose_system_prompt` (`sdk_client.py`) → `pm_system_prompt`, and routes to the container (`:1535`).
3. **Container spawn**: `BridgeAdapter` → `PTYPool.acquire_pair` → `pty_driver.spawn()` launches each PTY. PM gets `--append-system-prompt`; both get a priming slash command (`container.py:722-734`).
4. **Priming**: `/granite:prime-pm-role` (with user message) and `/granite:prime-dev-role` (without) install role behavior inside the TUI.
5. **Output**: PM routes via `[/dev]` / `[/user]` / `[/complete]`; bridge delivers user-facing text.

**Post-refactor**: step 2 stops composing a system prompt; persona + rails arrive entirely via step 4. Steps 3 loses the `--append-system-prompt` argument.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: `compose_system_prompt` / `load_eng_system_prompt` / `load_persona_prompt` removed or gutted; `PairSpawnSpec.pm_system_prompt` and the `append_system_prompt` PTYDriver arg removed; `pty_driver.spawn()` no longer emits `--append-system-prompt`.
- **Coupling**: decreases. Persona stops being threaded executor → sdk_client → adapter → pool → driver; it lives in two repo-tracked prime files the TUI reads itself.
- **Data ownership**: persona ownership moves from Python composition to slash-command markdown; the WORKER rails move with it.
- **Reversibility**: medium. The system-prompt path is deleted (NO-LEGACY), so reverting means restoring it from git. Mitigated by spike-1 (knowing whether it was ever load-bearing) and baseline captures.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (canary result gate; rails-parity confirmation; teammate-shape confirmation)
- Review rounds: 2+ (deletion of a load-bearing path + behavioral persona changes warrant careful review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PR #1691 merged | `gh pr view 1691 --json state -q .state` (expect `MERGED`) | The entire eng/persona surface this plan edits is created by that PR |
| Granite container present | `python -c "import agent.granite_container.pty_driver"` | spike-1 + all PTY edits need the harness |
| Baseline fixtures exist | `ls tests/fixtures/Mac-local/eng_system_prompt_baseline.txt` | Rails-parity verification (task 4) |

## Solution

### Key Elements

- **Canary gate**: an empirical test of `--append-system-prompt` in the interactive TUI, run before any deletion.
- **Prime commands as persona home**: `prime-pm-role.md` (thin driver), `prime-dev-role.md` (SDLC owner + Sonnet fan-out + raw-prompt-as-context), new `prime-teammate-role.md`.
- **Rails relocation**: WORKER rails (no-push-to-main, principal context, completion criteria) moved into priming; `CLAUDE.md` left to native TUI auto-load.
- **System-prompt deletion**: remove `compose_system_prompt`, `--append-system-prompt` plumbing, and the engineer-overlay drift guards.
- **Dev model flip**: `GRANITE__DEV_MODEL` → opus, ordered after the subagent recommendation lands.
- **Integration review**: confirm the P1/P2/P3 fixes folded into #1691 landed correctly.

### Flow

Eng message → container spawn → PM primed as thin driver (sees prompt) + Dev primed as SDLC owner (sees prompt as context, waits for `[/dev]`) → PM decides → `[/dev]` relays work / `[/user]` replies → done. No `--append-system-prompt` anywhere in the path.

### Technical Approach

- **Order matters and is encoded in the task list.** Canary (1) → prime rewrites (2-3) → rails relocation (4) → system-prompt deletion (5, only after 2-4 prove parity) → Dev context+model (6, model flip only after subagent rec in 2) → teammate priming (7) → integration review (8).
- **Rails parity is the deletion gate.** Capture the composed PM system prompt to a fixture, diff its load-bearing content against what the prime commands now provide, and only delete once parity (minus the natively-loaded `CLAUDE.md`) is confirmed.
- **Persona content surgery**: move the CRITIQUE/REVIEW/MERGE gates and SDLC ownership from `engineer.md` into `prime-dev-role.md`; strip orchestrator/child-dispatch content entirely (it is the pre-1633 model the in-container Dev replaces).
- **Keep the router untouched** (`granite_classifier.py`, the `[/dev]`/`[/user]`/`[/complete]` contract). This plan changes what each PTY is told, not how they talk.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit `except` blocks in `sdk_client.py` persona-loading code being deleted; ensure removal does not orphan a swallowed-error path. If the loader is deleted entirely, state "loader removed, no handler remains".
- [ ] Persona/prime file-not-found: the prime commands are repo-tracked, but assert a clear failure (not a silent empty persona) if a prime file is missing at spawn.

### Empty/Invalid Input Handling
- [ ] Dev PTY raw-prompt-as-context: test empty/whitespace user message does not break priming and does not cause the Dev to act before `[/dev]`.
- [ ] Teammate priming with no message body (reaction-only) primes cleanly.

### Error State Rendering
- [ ] A persona/prime failure must surface a PM-persona-safe Telegram message (no raw stack trace), consistent with the existing wrap-up guard.

## Test Impact

- [ ] `tests/unit/test_compose_system_prompt.py` — DELETE or REPLACE: `compose_system_prompt` is removed; if any helper survives, replace with a thin test, else delete.
- [ ] `tests/fixtures/Mac-local/eng_system_prompt_baseline.txt` — REPLACE: regenerate or retire once persona moves to priming; used in task 4 for rails-parity before retirement.
- [ ] `tests/integration/test_harness_env_pm_injection.py` — UPDATE: `VALOR_PARENT_SESSION_ID`/persona injection assertions change when the system-prompt path is removed.
- [ ] `scripts/capture_persona_baseline.py` / `scripts/update/persona_drift.py` — UPDATE: drift guards over `engineer.md` (`sdk_client.py:941-989`) are removed; the drift-capture tooling must target the prime commands instead, or be retired.
- [ ] Any test asserting `--append-system-prompt` in spawn args — UPDATE/DELETE to assert its absence.

## Rabbit Holes

- **Re-architecting the router.** Out of scope. The zero-LLM shuttle (#1689) stays; only priming content changes.
- **Per-session Dev model knobs.** The global `GRANITE__DEV_MODEL` flip is enough; do not build per-session model plumbing.
- **Rewriting the whole persona voice.** Move and prune existing content; do not redesign the persona prose from scratch.
- **Teammate as a separate container shape.** Decided: same two-PTY container, teammate priming only. Do not build a single-PTY teammate path.
- **Fixing `--append-system-prompt` if the canary shows it is honored but flaky.** If honored, just replace it with priming; do not try to harden the flag.

## Risks

### Risk 1: Deleting the system-prompt path drops WORKER safety rails
**Impact:** PM/Dev PTYs could lose no-push-to-main / completion-criteria guarantees, allowing unsafe git actions.
**Mitigation:** Task 4 is a hard parity gate before task 5 deletion: rails must be proven present via priming (diffed against the captured baseline) before `compose_system_prompt` is removed.

### Risk 2: Canary reveals the PM persona was a no-op (or vice versa)
**Impact:** Changes the behavioral delta of the migration; a "live" persona means deletion alters runtime behavior.
**Mitigation:** spike-1 runs first and gates the rest; if live, capture baselines and confirm prime parity before deletion.

### Risk 3: Building before PR #1691 merges
**Impact:** The edited surface (engineer.md, enums, routing) does not exist or differs; wasted/conflicting work.
**Mitigation:** Prerequisite check blocks build; No-Gos tags the dependency `[ORDERED]`.

### Risk 4: Teammate keeps a live Dev PTY behind only the SESSION_TYPE write-hook
**Impact:** If `SESSION_TYPE` fails to propagate, a teammate session becomes a full engineer.
**Mitigation:** Teammate priming must reinforce the non-engineering posture; add a test that the teammate write-hook and priming are both in force.

## Race Conditions

No race conditions identified. The changes are to spawn-time priming content and a config default; the container's existing synchronous prime → route loop is unchanged. The only ordering concern (subagent-recommendation-before-model-flip, parity-before-deletion) is build sequencing, not runtime concurrency, and is encoded in task dependencies.

## No-Gos (Out of Scope)

- [ORDERED] Building any of this before PR #1691 merges — the eng/persona surface is created by that PR (gated by `gh pr view 1691`).
- [SEPARATE-SLUG #1633] The P1/P2/P3 go-live fixes (stale `--role dev/pm` strings, dead `create_dev()`, `"developer"` defaults) — folded into PR #1691 directly; this plan only *reviews* their integration (task 8), it does not author them.
- Router / zero-LLM shuttle changes — settled by #1689, untouched here.

## Update System

The `prime-*-role.md` commands live in `.claude/commands/granite/` and are repo-tracked, so they propagate via normal `git pull` in `/update`. `config/settings.py` default change propagates the same way. Confirm whether any machine sets `GRANITE__DEV_MODEL` in its vault `.env` (which would override the new default) and document the precedence. No `scripts/update/` code changes expected, but the `persona_drift.py` update step must be updated/retired to stop checking the deleted `engineer.md` guards.

## Agent Integration

This is a bridge-internal change to how container PTYs are primed; no new MCP tool or `.mcp.json` change. The bridge already routes to the container. Integration tests: verify an Eng session and a teammate session each spawn with the correct prime command and NO `--append-system-prompt`, and that the Dev PTY receives the raw prompt as context. No new agent-invokable CLI surface.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/granite-pty-production.md` — persona now via priming; no system prompt.
- [ ] Update `docs/features/composed-persona-system.md` — mark the system-prompt-composition model retired for the granite path (or supersede it).
- [ ] Update `docs/features/personas.md` and any `pm-dev-session-architecture.md` successor for the PM-driver / Dev-SDLC split.
- [ ] Add a `prime-teammate-role` reference to the relevant docs.

### Inline Documentation
- [ ] Update `pty_driver.py:331-335` comment (the `--append-system-prompt` assertion) per the canary result, or remove it with the flag.

## Success Criteria

- [ ] spike-1 canary test exists, runs, and its result is recorded before any deletion.
- [ ] `compose_system_prompt` + `--append-system-prompt` plumbing deleted; no production caller remains (`grep` clean); engineer-overlay drift guards removed.
- [ ] PM prime = thin driver (no SDLC `/do-*`, no child dispatch; research subagents allowed); obsolete orchestrator content removed from `engineer.md`.
- [ ] Dev prime owns SDLC + carries CRITIQUE/REVIEW/MERGE gates + the Sonnet-subagent recommendation.
- [ ] WORKER rails verified present via priming; baseline parity confirmed; `CLAUDE.md` not double-injected.
- [ ] Dev PTY receives the raw prompt as context; a test proves it does not act before the `[/dev]` relay.
- [ ] `GRANITE__DEV_MODEL` defaults to `opus` (flipped only after the subagent recommendation lands).
- [ ] `prime-teammate-role` exists and bends teammate toward chitchat/CS/issue-creation; `teammate.md` no longer vault-only.
- [ ] Integration review of #1691 fixes passes: no `--role dev/pm` strings; `create_dev()` gone; `"developer"` defaults → `"engineer"`; stale dev-type docstrings removed.
- [ ] Tests pass (`/do-test`); Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (priming)** — Name: `prime-builder` — Role: rewrite prime-pm/dev/teammate commands and relocate rails — Agent Type: builder — Resume: true
- **Builder (deletion)** — Name: `sysprompt-deleter` — Role: remove compose_system_prompt + append plumbing + drift guards — Agent Type: builder — Resume: true
- **Test engineer** — Name: `canary-tester` — Role: spike-1 canary + Dev-no-jump-the-gun test — Agent Type: test-engineer — Resume: true
- **Validator** — Name: `rails-validator` — Role: rails-parity + integration review of #1691 fixes — Agent Type: validator — Resume: true
- **Documentarian** — Name: `persona-doc` — Role: persona/granite docs — Agent Type: documentarian — Resume: true

## Step by Step Tasks

### 1. Canary spike (gate)
- **Task ID**: spike-canary
- **Depends On**: none (but Prerequisite: #1691 merged)
- **Validates**: a new `tests/integration/test_append_system_prompt_interactive.py`
- **Assigned To**: canary-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Build a pexpect harness spawning interactive `claude --append-system-prompt "<canary>"`; assert whether the canary is honored.
- Record the result in the plan's Spike Results and in the PR description.

### 2. Rewrite prime commands + relocate persona
- **Task ID**: build-priming
- **Depends On**: spike-canary
- **Validates**: prime files lint/parse; persona content audit
- **Assigned To**: prime-builder
- **Agent Type**: builder
- **Parallel**: false
- `prime-pm-role.md`: thin decision-making driver; research subagents allowed; no SDLC, no child dispatch.
- `prime-dev-role.md`: SDLC owner (`/do-*`), CRITIQUE/REVIEW/MERGE gates moved here, Sonnet-subagent recommendation, raw-prompt-as-context note.
- Strip obsolete orchestrator/child-dispatch content from `engineer.md`.

### 3. Relocate WORKER rails into priming
- **Task ID**: build-rails
- **Depends On**: build-priming
- **Assigned To**: prime-builder
- **Agent Type**: builder
- **Parallel**: false
- Move no-push-to-main, principal context, completion criteria into the prime commands; do NOT re-inject `CLAUDE.md`.

### 4. Rails-parity validation (deletion gate)
- **Task ID**: validate-rails
- **Depends On**: build-rails
- **Assigned To**: rails-validator
- **Agent Type**: validator
- **Parallel**: false
- Diff composed-system-prompt baseline vs prime-provided content; confirm rails parity (minus native `CLAUDE.md`). Block task 5 on pass.

### 5. Delete the system-prompt path
- **Task ID**: build-delete-sysprompt
- **Depends On**: validate-rails
- **Validates**: grep-clean for `compose_system_prompt`, `append-system-prompt`; updated spawn-arg tests
- **Assigned To**: sysprompt-deleter
- **Agent Type**: builder
- **Parallel**: false
- Remove `compose_system_prompt`, `load_eng_system_prompt`, the `--append-system-prompt` plumbing (`pty_driver`, `pty_pool`, `bridge_adapter`, `session_executor:1681`), and drift guards (`sdk_client.py:941-989`).

### 6. Dev PTY context + Opus flip
- **Task ID**: build-dev-context-model
- **Depends On**: build-priming (recommendation), build-delete-sysprompt
- **Validates**: Dev-no-jump-the-gun test; settings default test
- **Assigned To**: prime-builder
- **Agent Type**: builder
- **Parallel**: false
- Pass raw prompt as labeled context to Dev (`container.py:664-667`); flip `config/settings.py` `GRANITE__DEV_MODEL` → opus ONLY after the subagent recommendation (task 2) is in.

### 7. Teammate priming
- **Task ID**: build-teammate-prime
- **Depends On**: build-priming
- **Assigned To**: prime-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `prime-teammate-role.md` (chitchat/CS/issue-creation bend); track `teammate.md` in repo or fold into the prime.

### 8. Integration review of #1691 fixes
- **Task ID**: validate-1691-integration
- **Depends On**: none (Prerequisite: #1691 merged)
- **Assigned To**: rails-validator
- **Agent Type**: validator
- **Parallel**: true
- Confirm: no `--role dev/pm` strings; `create_dev()` gone; `"developer"` defaults → `"engineer"`; stale dev-type docstrings/labels removed.

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: build-delete-sysprompt, build-dev-context-model, build-teammate-prime
- **Assigned To**: persona-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update granite/persona docs per the Documentation section.

### 10. Final validation
- **Task ID**: validate-all
- **Depends On**: all previous
- **Assigned To**: rails-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full verification; confirm all success criteria; generate report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No compose_system_prompt | `grep -rn "compose_system_prompt" agent/ bridge/ tools/` | exit code 1 |
| No append-system-prompt plumbing | `grep -rn "append.system.prompt" agent/` | exit code 1 |
| No --role dev/pm strings | `grep -rn -- "--role dev\|--role pm" agent/ tools/ bridge/` | exit code 1 |
| Dev model opus | `python -c "from config.settings import settings; assert settings.granite.dev_model=='opus'"` | exit code 0 |
| Teammate prime exists | `ls .claude/commands/granite/prime-teammate-role.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Email/customer-service persona path after deletion:** once `compose_system_prompt` is gone, where do the email-resolved `customer-service` persona and the `(persona, access_level)` resolution land? Confirm they also route through priming, or define the surviving path.
2. **Rails relocation mechanism:** should the WORKER rails be inlined into each prime command, or factored into a shared `prime-rails` snippet the role primes reference? (Affects duplication vs. single-source-of-truth.)
3. **Baseline retirement:** keep the `*_system_prompt_baseline.txt` fixtures (repurposed to capture prime output) or retire them entirely once the system-prompt path is gone?
