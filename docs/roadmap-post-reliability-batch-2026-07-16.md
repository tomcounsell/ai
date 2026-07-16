# Roadmap — What To Do Next (2026-07-16)

> This is a **strategic roadmap**, not a `/do-plan` feature plan — it clusters and sequences open work. Each item that gets picked up becomes its own tracked plan under `docs/plans/`.

**Context.** We just shipped a 12-issue reliability batch (8 PRs merged, 3 issues closed, #2026 left open as its tracking home). The headline fix, #2064, made the full-suite pytest lock machine-global so parallel SDLC lanes stop cross-reaping each other. Two things surfaced *during* that batch that now shape what's next:

1. **`run_email_bridge` async-teardown hang** — 6 of 8 merge gates wedged at ~99% with `coroutine 'run_email_bridge' was never awaited` in async teardown. Every lane merged on targeted-test evidence via the documented bypass. This is **not yet filed** and degrades *every* future full-suite gate.
2. **Per-worktree `.venv` contention** — the #2100 lane hit a phantom worktree venv missing `ruff`; sibling build lanes share one repo-root `.venv`. Already tracked as **#2052**.

This roadmap clusters the 20 open issues (window: updated since 2026-07-02), maps dependencies, and recommends an execution order. It mirrors the sequencing that just worked: **ship the enabling reliability fix first and solo, then fan out.**

---

## The 20 open issues (input set)

| # | Labels | One-liner |
|---|--------|-----------|
| #2114 | bug | valor-computer CLI drifted from bcu v0.1.0 API (POST routes, screenshot_window removed) |
| #2112 | bug, memory | `DocumentChunk.search()` returns raw `$CF:` ref for query-loaded rows |
| #2026 | plan, investigation | fork-vs-supervisor tracking home (open by design; hardened via #2076) |
| #2059 | chore, future | knowledge-graph regen pipeline for valorengels.com |
| #2079 | skills, testing | harden repo-agnosticism guards (rule_13 signal classes, RENAMED_REMOVALS enforcement) |
| #2065 | chore | clean up stale skill hardlinks, trim duplicated CLAUDE.md content |
| #2083 | plan, chore, memory | audit + remove descriptor-pollution scar tissue made dead by popoto 1.8.0 atomic index |
| #2082 | plan, memory | evaluate popoto 1.8.0 hybrid BM25+vector retrieval for memory recall |
| #2068 | reflections, skills | migrate remaining cloud-API-audit reflections to Claude Cowork (after #2067) |
| #2067 | reflections, skills | pilot: migrate sentry-issue-triage to Cowork + establish reusable pattern/skill |
| #2001 | plan | Phase 3: codex exec as opt-in dev-lane executor (under #1996) |
| #2052 | chore, skills | per-worktree venv isolation (UV_PROJECT_ENVIRONMENT) |
| #2031 | reflections, bridge | validate first fleet-wide stall-recovery actuation (48h watch, post-#1855) |
| #1883 | plan, chore, skills | skills architecture audit: disposition + model tier for all ~60 skills |
| #1819 | skills | spike: bake Firecrawl skill pack into skills-global |
| #1996 | plan | harness cross-compat: agent-agnostic headless runner (claude -p + codex exec) |
| #1813 | — | migrate secrets from plaintext .env to 1Password service account + op CLI |
| #1338 | chore, upvote | email bridge launchd boot-time installer |
| #1886 | chore | tool-budget: revisit deny-but-don't-halt default after live denial data |
| #728 | plan, memory | agent-maintained knowledge wiki in Obsidian vault |

Plus one **to-be-filed**: the `run_email_bridge` async-teardown hang.

---

## Dependency map

- **#2067 → #2068** — Cowork pilot establishes the pattern/skill; the rest follow.
- **#1996 (Phase 1 cleanup → Phase 2 HarnessAdapter) → #2001 (Phase 3 codex)** — codex dev-lane can't land until the adapter seam exists.
- **#2081 (merged) → {#2083, #2082}** — both harvest the popoto 1.8.0 upgrade; plans already written and recon-backed.
- **#2050 (merged guard) → #2052** — venv isolation lets the guard relax from block to warn.
- **#2064 (merged) + [teardown-hang fix] + #2052** — the three legs of "parallel SDLC lanes are actually reliable." #2064 done; the other two are next.

---

## Recommended execution order

### Wave 1 — Close the reliability loop (do this first; we have fresh context)
The goal: make the *next* parallel batch run clean merge gates on isolated venvs, no bypasses.

1. **[NEW] File + fix the `run_email_bridge` async-teardown hang.** Highest leverage — it blocked 6/8 gates this batch and will keep forcing targeted-test bypasses until fixed. Root-cause the un-awaited coroutine in email-bridge teardown; likely an `asyncio` cleanup path that isn't awaited under pytest's loop teardown. **Ship solo first**, like #2064 — it's the enabling fix that restores trustworthy full-suite gates.
2. **#2052 — per-worktree venv isolation.** The second enabling fix: each lane gets its own `.venv` (worktree-local `UV_PROJECT_ENVIRONMENT`), so `uv sync` and ruff installs never collide across lanes or with main. Relax the #2050 guard to warn for isolated worktrees.
3. **#2083 — remove popoto descriptor-pollution scar tissue.** Plan is written and recon-backed; deletion of code proven dead by the 1.8.0 atomic index. Low risk, aligns with the happy-path / scar-tissue-removal principle. Can run parallel to #2052 once the teardown fix lands.

### Wave 2 — Quick wins + popoto harvest (parallel, after Wave 1's enabling fixes)
4. **#2114 — valor-computer bcu v0.1.0 migration.** Well-specified route/contract migration (POST bodies, `get_window_state imageMode`, target/stateToken semantics). Self-contained.
5. **#2112 — `DocumentChunk.search()` decode.** Small; the #2085 repair helper already works around it, so it's cleanup not a blocker.
6. **#2082 — hybrid BM25+vector retrieval eval.** Recon-backed plan ready; `Memory` already has both `BM25Field` + `EmbeddingField`, so `retrieval_mode='auto'` resolves to hybrid. An eval that likely yields a recall-quality upgrade.

### Wave 3 — Strategic platform tracks (one phase at a time, sequential within each)
7. **#1996 harness cross-compat** — Phase 1 (session-runner cleanup + fold-in #1979/#1983/#1855) → Phase 2 (HarnessAdapter seam, `--json-schema` routing) → **#2001** (codex exec opt-in dev-lane). Large appetite; run one phase per batch, review between. Constraint (owner, 2026-07-10): bridge-connected top-level sessions stay claude-only; codex is a dev-lane executor only.
8. **#2067 → #2068 Cowork migration** — pilot sentry-issue-triage onto Claude Cowork, establish the reusable pattern + repo skill, then migrate the remaining cloud-API-audit reflections off the local worker. Offloads scheduler budget and removes single-machine gating for pure cloud audits.

### Wave 4 — Skills / repo hygiene (batchable maintenance)
9. **#2065** stale skill hardlinks + trim CLAUDE.md · **#2079** repo-agnosticism guard hardening · **#1883** skills architecture audit (disposition + model tier for ~60 skills) · **#1819** Firecrawl skill-pack spike. Group these — they touch the same skills/update wiring and share review context.

### Ongoing / assign-owner (NOT a build)
- **#2031 — stall-recovery 48h validation.** A monitoring watch, not a code task: over 48h post-restart on `5ac64a8`+, watch `session_events` for `stall_recovery_action` kill events (dry_run=False) and correlate against `StatusConflictError` (VALOR-DZ) / exit-143 spikes. Close as validated if clean; only tighten the gate ladder (never reintroduce a global dry-run flag) if it over-kills. **Assign an owner + a calendar reminder**, don't queue it as SDLC work.

### Backlog — needs a precondition or lower priority
- **#1886** tool-budget deny-but-don't-halt — needs live denial-distribution data first; gather, then decide.
- **#1813** secrets → 1Password — security infra, larger; schedule deliberately (one-time migration + per-machine op CLI).
- **#1338** email-bridge boot installer — upvoted chore; fold into a deploy/update batch.
- **#2059** site knowledge-graph regen · **#728** Obsidian agent-wiki — knowledge-surface investments; sequence after the platform tracks.

---

## Deferred from the batch we just shipped (carry-forward)
- **Operational:** run `scripts/refresh_baseline_detached.sh` on a **quiesced** machine to clear the 337-commit merge-gate baseline staleness (#2066 shipped the tool; the actual refresh wedged on this non-quiesced box).
- **Deploy:** `./scripts/valor-service.sh restart` on bridge/worker machines to activate #2071 / #2100 / #1370. #2104 is install-time (next `/update`).

---

## Suggested next action
Kick off **Wave 1** the same way #2064 ran: file + fix the teardown hang **solo first**, then fan out #2052 and #2083 once clean gates are restored. Everything downstream depends on trustworthy full-suite gates and isolated venvs.
