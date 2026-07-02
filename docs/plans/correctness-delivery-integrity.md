---
status: Ready
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-01
tracking: https://github.com/tomcounsell/ai/issues/1817
last_comment_id:
revision_applied: true
---

# Correctness & Delivery-Integrity Hardening (lost steers, double-exec, TUI scrape, silent drops)

## Problem

A red-team sweep for *uncatalogued* failure modes found a cluster of bugs that
silently **drop, duplicate, or corrupt work with no crash required** — so none of
the recovery machinery we already own (launchd, watchdogs, the 300s health
backstop) ever engages. They are invisible, not un-recoverable.

Concretely, today:

- A human course-correction (steer) that lands after the worker binds its session
  instance is **saved over with `[]`** — the reply is lost with no trace.
- When the email resolver's OAuth token expires, **every inbound customer email is
  silently dropped and marked `\Seen`** — irrecoverable.
- A permanent IMAP auth failure (revoked app password) **retries forever** on a
  5-minute backoff with no alert.
- During iCloud `projects.json` sync lag, **two machines execute the same Telegram
  message twice** (two PRs, two replies).
- A malformed mid-write `projects.json` **crash-loops the bridge** under launchd
  `KeepAlive`.
- A routine `claude` CLI auto-update that reworded one scraped TUI string is a
  **silent fleet-wide outage** — every session hangs to timeout.
- Fire-and-forget circuit-breaker writes are never awaited: if the very Redis
  failure they record raises, **the breaker never trips**.
- The notify-listener silently wedges on a Redis failover, degrading pickup latency
  to 5 minutes with **zero logs**.

**Current behavior:** each hazard produces a wrong outcome (lost/duplicated/corrupt
work) that is undetectable from the outside — green dashboards, no exception, no
crash.

**Desired outcome:** each silent failure is converted into a **loud, recoverable
event** — routed through an atomic primitive so it cannot race, or surfaced on an
operator surface (alert / last-known-good log / contract-check failure) so the
recovery we already own can act.

This is the largest of the four resilience workstreams (#1817 under tracking
#1818). It collects many independent findings; this is **one master plan** whose
workstreams A–D are **independently shippable** (see the PR-split in Step-by-Step),
not four separate plans.

## Freshness Check

**Baseline commit:** `b99e295821573d011c2981c401c8977ee87fe045`
**Issue filed at:** 2026-06-29T09:22:54Z
**Disposition:** Minor drift (line numbers moved; one finding — B1 — Revised because the code changed materially since filing). Post-critique round 1: five findings revised in place against the same baseline. Post-critique round 2 (2026-07-02, HEAD `3514ed1b`): B2 blocker resolved + A1 anchors re-verified after further drift (`session_executor.py` `:1584→:1675`, `:2012→:2106`, `:647→:679`, `:1595→:1686`; `agent_session.py` methods `2015/2054→2046/2085`; `bridge_adapter.py` `:536→:624`) — see "Post-critique re-verification" below and the Critique Results table. Post-critique round 3 (2026-07-02, HEAD `f8544f0a`, anchors unchanged since `199bda9a`): B1 claim-before-enqueue orphan BLOCKER resolved (provisional short-TTL gate + delete-on-exception); A1 `health_check.py:573` re-verified as a CLI-harness DELIVERY write (NOT a dual-write — do not delete, repoint to the Redis list); A2 resolver-unavailable gains an operator alert matching A3; D1b/D2 re-verified against the merged #1842 per-role transport hedge (headless roles have no PTY pid — D2 made None-aware, D1b scoped to PTY transport); C2 heal anchor drift corrected (`agent_session.py` `973-1037→1004-1070`, `record.save()` `:1029→:1060`). See the Round-3 Critique Results block.

All Findings citations were HEAD-of-writing (2026-06-29). Five sibling resilience
PRs merged in the 2-day window before planning, moving many line numbers and
changing the landscape for B1 and D3. Every citation was re-verified against
`b99e2958` (full evidence in the issue's `## Recon Summary`). Corrected locations:

| Finding | Cited | Verified @ `b99e2958` | Status |
|---|---|---|---|
| A1 primitive | `agent/steering.py` RPUSH/LPOP | `push_steering_message`@72, `pop_all_steering_messages`@100 | OK |
| A1 non-atomic pop | `session_executor.py:1528` | `agent/session_executor.py:1675` (bound instance; re-verified 2026-07-02 @HEAD `3514ed1b`, was `:1584`) | DRIFT |
| A1 model field/methods | `agent_session.py:225,2027-2066` | field @225; `push_steering_message`@2046, `pop_steering_messages`@2085 (re-verified 2026-07-02 @HEAD `3514ed1b`, was @2015/2054) | OK (name is `push_steering_message`, colliding with the module fn) |
| A2 resolver | `routing.py:1404` | def @1404, `return None`@1490; `\Seen`@`email_bridge.py:1416`, drop@1189 | OK |
| A3 IMAP | `email_bridge.py:1490-1496` | `except imaplib.IMAP4.error`@1490, shared backoff@1496 | OK |
| B1 claim | *(new claim)* | **dedup ALREADY EXISTS** — `bridge/dedup.py`+`models/dedup.py`, wired via `bridge/dispatch.py:146` | **REVISED** |
| B2 CAS | `session_lifecycle.py:604-648` | same range; `get_authoritative_session()`+compare | OK |
| B2 pid | `session_health.py:2981` | `register_worker_pid`@2981 additive `_R.set`, no guard | OK |
| C1 finalize | `session_lifecycle.py:221,445,474` | def@221; parent @440-451; child save@474 | OK |
| C2 heal | `agent_session.py:973-1037` | `_heal_future_updated_at`@1004-1070; `record.save()`@1060 (re-verified 2026-07-02 @HEAD `f8544f0a`, was `973-1037`/`:1029`) | DRIFT |
| C2 freshness | `session_health.py:225,1112` | `HEARTBEAT_FRESHNESS_WINDOW=90`@225, use@1112 | OK |
| C4 config | `routing.py:134-135` | unguarded `json.load`@134-135; import read@`telegram_bridge.py:463-492` | OK |
| D1 which | `worker/__main__.py:299` | `shutil.which("claude")`@**726** (re-verified 2026-07-02 @HEAD `f8544f0a`, was `:712`; drift by #1815/#1816) | DRIFT |
| D1 markers | `pty_driver.py:95,96,112-115` | `IDLE_BAR`@99, `PROMPT_GLYPH`@100, `SPINNER_EVIDENCE_RE`@116 (re-verified 2026-07-02 @HEAD `f8544f0a`, drift by #1842/#1688) | DRIFT |
| D1 first-run | `startup_parser.py:120-124` | trust-folder patterns @117-121 (`TRUST_FOLDER_PROMPT`@49, pattern@121); markers exist, line moved | DRIFT (content) |
| D2 spawn | `pty_pool.py:438-459,517-529` | per-role spawn @527-548 (`pm.spawn()`@538, `dev.spawn()`@548), pid record @550-556 (re-verified 2026-07-02 @HEAD `f8544f0a`; **#1842 added per-role headless: `pm`/`dev` can be `None`** @527-528) | DRIFT (+#1842 semantics) |
| D2 reaper | `pty_pool.py` `_CLAUDE_CMDLINE_RE` | `agent/session_health.py:58` (`claude_agent_sdk/_bundled/claude`); matches SDK bundle not the PTY-TUI `claude`; headless `claude -p` turns already handled by `_is_stale_print_oneshot`@3880 (do NOT clobber) | DRIFT (wrong file) + #1842 |
| D3 circuit | `sdk_client.py:1842,1857,1923` | all OK verbatim | OK |
| D3 bridge tasks | `telegram_bridge.py:1610,1630` | emoji@1617, classify@1637; NOT in `_background_tasks`@183 | DRIFT |
| D3 memory | `memory_extraction.py:274,461,484,708` | swallow handlers @328,537,560,791,1029 (drift by #1822) | DRIFT (content) |
| D4 listener | `agent_session_queue.py:805-962,833-835` | `_session_notify_listener`@805, `socket_timeout=None`@851, comment@834-835 | OK |

**Cited sibling issues/PRs re-checked:**
- #1814 (Redis durability) — **merged** PR #1824. Cross-cutting root cause 1 addressed.
- #1815 (liveness wedge) — **merged** PR #1823. Deferred fixes → #1820 (lease/progress) + #1821 (out-of-domain recovery).
- #1816 (event-loop fault isolation) — **merged** PR #1832. Reworked `worker/__main__.py` startup (this is why D1's `shutil.which` drifted 299→712).
- #1822 (memory-extraction noise) — **merged** PR #1831. Touched `memory_extraction.py` filtering, NOT its exception-swallow paths (D3 line drift only).
- #1827 (Redis replication/Sentinel) — **merged** PR #1830.
- #1688 (hook-driven turn returns) — **merged** PR #1847. Added the settings/hook-forwarder spawn args to `pty_pool.py` (moved the spawn region) and reworked `pty_driver.py` markers (D1b line drift).
- #1842 (per-role transport hedge) — **merged** PR #1848 (re-checked 2026-07-02 @HEAD `f8544f0a`). **Materially changes D1b/D2:** a role is now config-selectable `pty` vs `headless`. A `headless` role spawns NO PTY process — `pty_pool.py:527-528` leaves `pm`/`dev` as `None` and the role runs a `claude -p` one-shot per turn via `HeadlessRoleDriver` (`container.py:756-772,2377-2490`). So D2's "record pid on spawn" has no pid to record for a headless role (not an orphan), and D1b's TUI-marker contract-check only governs PTY-transport sessions (a headless turn "carries NO PTY bytes"). Both findings re-scoped below; D2's premise for headless roles is adapted (no PTY pid → nothing to reap; the `claude -p` one-shot is covered by the existing `_is_stale_print_oneshot`).
- #1818 — OPEN tracking umbrella for the 4-issue cluster.

**Commits on main since issue filed (touching referenced files):** the five PRs above. None *fixes* any A–D finding; #1815/#1816 caused the D1 line drift; #1822 caused the D3-memory line drift.

**Active plans in `docs/plans/` overlapping this area:** none open. All siblings are in `docs/plans/completed/`. #1820 and #1821 are filed-but-unplanned deferrals — this plan must NOT smuggle in their scope (lease semaphore, progress-deadline, out-of-domain recovery).

**Notes:** The single most consequential drift is **B1** — a dedup layer already exists but is non-atomic and recorded post-enqueue, so the finding holds but the fix changes from "add a claim" to "make the existing dedup an atomic pre-enqueue claim, fold in, delete nothing that catchup needs." Captured under Revised.

**Post-critique re-verification (2026-07-01, against HEAD `b99e2958`):** the CRITIQUE-driven revisions re-grounded these anchors, all confirmed present verbatim:
- D4 blocker: `socket_timeout=None`@`agent_session_queue.py:851`; spurious-timeout rationale comment @822-828; conceded 300s-backstop drift @834-835; subscribe-time NUMSUB self-check @857-895.
- B1 recovery bypass: `dispatch.py:15-18` docstring; catchup enqueue@257 / record@276 (pre-check `is_duplicate_message`@176); reconciler enqueue@239 / record@254 (pre-check@176).
- C1 finalize contract: non-fatal parent-finalize swallow `[lifecycle] Parent finalization failed (non-fatal)`@451; `_finalize_parent_sync`@687 idempotent no-op on missing@721 / terminal@727-732.
- A1 steering model: non-atomic sequential-LPOP + single-consumer docstring @`steering.py:83-84` (range 80-109); multiple per-session consumers (`session_executor.py:2106/2108`, `session_pickup.py:182`, `health_check.py:507`, `bridge_adapter.py:624`) — the leftover-drain and bridge-adapter anchors re-verified 2026-07-02 @HEAD `3514ed1b` (were `:2012` / `:536`).
- C2 heal re-save: `record.save()`@`agent_session.py:1029` inside `_heal_future_updated_at` (function-scoped grep returns 1 at baseline — its own red-state).
- D1 native install: `~/.local/bin/claude` → `~/.local/share/claude/versions/2.1.197` (native installer symlink, NOT npm `node_modules`; not in `MANAGED_PACKAGES`).

## Prior Art

- **#1815 / `liveness-wedge-recovery.md` (merged)** — the sibling that established the "convert silent failure into a loud, recoverable event" pattern this plan extends. Its dead-man's-switch and bounded-wait primitives are the template for D-group's loud-failure conversions.
- **#1814 / #1827 (merged)** — Redis durability + replication. Removes cross-cutting root cause 1 (Redis SPOF); this plan can assume Redis is durable and rely on Redis-atomic primitives (SETNX, WATCH/MULTI) without re-solving persistence.
- **#1408 (merged)** — introduced `bridge/dedup.py` `LastProcessedRecord` cursor + the `DedupRecord` membership set. Directly relevant to B1: the dedup infrastructure exists; B1 hardens it.
- **#950 (merged)** — the origin of `queued_steering_messages` partial-save (`update_fields`) to "avoid clobbering status on stale worker references." A1 supersedes this half-measure: partial-save narrowed the clobber but did not make the RMW atomic; routing through the Redis list removes the RMW entirely.
- **#1192 (merged)** — `chat_message_log` inbound append in `dispatch.py`; shows the dispatch wrapper is the right seam for B1's claim.
- **#1271 (merged)** — `register_worker_pid` Redis PID key; B2's observability-only, liveness-gated pid probe builds on it (a refuse-guard would wedge the launchd respawn loop — see Blocker B2).
- Existing atomic idioms to reuse (no new machinery): `agent/steering.py` RPUSH/LPOP (A1); `_R.set(key,"1",nx=True,ex=…)` in `agent/session_health.py:1530,1658,1776` and `agent/messenger.py:319` (B1); the atomic temp-rename in `session_health.py:3009-3011` (C4).

## Research

No relevant external findings needed — this is internal hardening against known
Redis/asyncio/Popoto primitives already in the codebase. The one external contract
worth noting is the `claude` CLI release cadence (D1): the CLI (installed via the **native**
installer at `~/.local/bin/claude`, not npm) floats to latest and reworded TUI strings across
minor versions historically — confirming the version-assertion pin (D1a) + marker contract-check
(D1b) approach over "track and hope."

## Data Flow

Each workstream has a distinct flow; the shared theme is *close the window between a
decision and its durable record with a Redis-atomic op, or surface the silent branch*.

**A1 — steering inbox (make the turn-boundary read atomic):**
1. Human reply arrives → `bridge/telegram_bridge.py:947` `push_steering_message(session_id, text, …)` **RPUSHes to the Redis list** (already atomic today) — AND redundantly `agent_session.push_steering_message(text)` @946 appends to the `queued_steering_messages` ListField (the racy path).
2. Worker turn boundary → `session_executor.py:1675` pops the **ListField** on a bound-at-start instance (`agent_session.pop_steering_messages()`) — a stale-instance RMW that saves `[]` over a concurrently-pushed steer.
3. **Fix:** the turn-boundary read pops the **Redis list** via `pop_all_steering_messages(session_id)`
   — a *non-atomic* sequential-LPOP drain (see `agent/steering.py:80-109`, whose own docstring
   states this). It is safe NOT because the drain is atomic but because of a **single-consumer
   invariant**: exactly one process drains a given session's steering list at a time (the worker
   turn-boundary read for that session_id). Each individual `LPOP` is atomic vs. a concurrent
   `RPUSH`, so a steer pushed mid-drain is never clobbered — it either drains this pass or sits in
   the list for the next boundary. This is fundamentally different from the racy ListField RMW
   (read whole list into a bound instance, save `[]` back), which loses a concurrently-pushed steer.
4. Output: a steer pushed at any instant is drained at the next boundary; nothing is clobbered —
   **conditional on the single-consumer invariant holding**. A1 must PRESERVE and TEST that
   invariant (see Technical Approach + Race 1), because B1/B2/C1 are introducing *atomic* multi-actor
   claims elsewhere and a reviewer could wrongly assume the steering drain shares that atomicity.
   It does not: its safety rests on single-consumer, not on atomicity.

**A2/A3 — email intake (distinguish "unavailable" from "not a customer"; classify permanent auth):**
1. IMAP poll → `_process_inbound_email` → `resolve_customer()` (`routing.py:1404`).
2. On resolver/OAuth error `resolve_customer` returns `None` (@1490) — indistinguishable from "not a customer" → `email_bridge.py:1189` drops + `\Seen`@1416.
3. **Fix:** `resolve_customer` raises `ResolverUnavailable` (or returns a sentinel) on infrastructure error; `_process_inbound_email` leaves the message UNSEEN and logs on that branch (retry next poll), only `\Seen`-dropping true non-customers. On a *persistent* unavailable resolver (expired OAuth token — the flagship case), it also arms a threshold-gated `email:resolver_unavailable` operator alert + `logger.critical` (surfaced on the dashboard health field, matching A3), cleared on the first successful resolve — so a token expiry is loud, not log-only (Concern 2).
4. Permanent `imaplib.IMAP4.error` (auth) @1490 → classify as permanent → stop the backoff loop + write an operator alert (email watchdog surface) instead of looping forever.

**B1 — inbound Telegram claim (provisional short-lived gate before enqueue; durable dedup after):**
1. `NewMessage` → live handler `is_duplicate_message` check @`telegram_bridge.py:1155` → `dispatch_telegram_session` → `enqueue_agent_session` → `record_message_processed`@`dispatch.py:146` (AFTER enqueue). The wrapper's docstring (`dispatch.py:99-106`) pins the recovery contract: **enqueue must succeed before dedup is recorded; if enqueue raises, dedup stays unrecorded so the reconciler retries.** B1 must not break this.
2. Two machines during sync lag both pass the check, both enqueue, both record.
3. **Fix — a PROVISIONAL concurrency gate, not a durable claim.** `claim_message(chat_id, message_id)` does a `SET NX` with a SHORT TTL in **seconds** (`CLAIM_TTL_SECONDS`, provisional ~60s, env-overridable) evaluated **before** enqueue; only the winner enqueues. The gate's TTL is sized to the cross-actor *processing skew* between two near-simultaneous producers (seconds), NOT the ~1h iCloud sync-lag window. The durable cross-machine/catchup-replay dedup remains the existing **2h membership set** (`record_message_processed`), recorded AFTER a successful enqueue exactly as today. Folded into `bridge/dedup.py`.
4. **Fail-safe: delete the gate on ANY enqueue exception.** The enqueue is wrapped so that if it raises (or the caller propagates a failure), the claim key is `DELETE`d before re-raising — leaving NO orphaned gate. This preserves the dispatch contract: a failed enqueue leaves both the gate AND dedup clear, so the reconciler re-acquires the gate and re-enqueues. If the process DIES between claim and enqueue (SIGKILL/power loss — no chance to delete), the SHORT TTL self-heals the orphan within seconds, and the next reconciler scan (3-min cadence) re-acquires and re-enqueues. This is why the gate MUST be short-lived: a ~1h TTL would strand the message for up to an hour on a mid-window death — recreating the exact silent-drop B1 exists to eliminate.
5. **Recovery paths (catchup/reconciler) also gate — SHARED key, same fail-safe.** These bypass the dispatch wrapper by design and only pre-check `is_duplicate_message`, so two machines' recovery loops can double-enqueue under sync lag. The same `claim_message` gate is added in-line before the recovery enqueue sites, wrapped in the same delete-on-exception guard. The key is SHARED across live + recovery (a distinct per-path gate would reopen the cross-path double-enqueue race: live claims key-A, recovery claims key-B, both enqueue). Safety comes from the short TTL + delete-on-failure, not from splitting the key. A message the live path already enqueued+recorded is stopped at the durable `is_duplicate_message` pre-check before a racing recovery scan ever reaches the gate; the gate only arbitrates the seconds-wide window where two producers race before either records dedup.

**B2 — pending→running claim (WATCH/MULTI):**
1. Worker (or `valor-session` CLI / catchup / reflections) picks a pending session → `session_lifecycle.py:604-648` re-reads + compares status in Python → saves `running`.
2. Two actors both pass the compare → both run the session.
3. **Fix:** the transition executes inside a Redis `WATCH`/`MULTI` (or a `SET NX` claim key) so exactly one actor wins (this alone guarantees one actor per session). `register_worker_pid` gains only an observability-only, liveness-gated log-and-supersede probe — never a refuse-guard (a refuse would wedge the launchd respawn loop on a dead pid's residual TTL'd key).

**C1 — parent/child finalize (preserve child-independence; idempotent sweep):**
1. `finalize_session` @221 finalizes the parent best-effort (`_finalize_parent_sync`@440-451, wrapped in a non-fatal try/except by design) and saves the child @474; the child ALWAYS finalizes even if the parent finalize raises.
2. Crash *after* the child save but *before* the parent transitions strands the parent in `waiting_for_children` forever.
3. **Fix (Concern 2):** do NOT couple the two writes (that would invert the child-independent contract). Keep finalize as-is; add an idempotent worker-startup sweep that re-invokes the already-idempotent `_finalize_parent_sync` for any parent stuck in `waiting_for_children` whose children are all terminal. Same end-state guarantee, child-independence preserved.

**C2 — freshness (monotonic/relative, stop heal-by-clamp):** health staleness uses
relative age against a trusted clock, not local wall-clock vs a possibly-skewed
writer; `_heal_future_updated_at` stops re-saving clamped timestamps (which reshuffle
the `created_at` index).

**C3 — ghost sessions:** index/set membership gets a TTL aligned to the hash TTL, or
`query.filter()` reconciles-on-read (drops members whose hash is gone).

**C4 — config (atomic read + last-known-good):** `routing.py:134-135` reads config
through a guarded loader that, on `JSONDecodeError`/partial read, falls back to the
last successfully-parsed config (cached to a sidecar) and logs — never crashes import.

**D1–D4:** native-installer version-assertion pin (D1a) + startup contract-check on the scraped TUI markers (D1b);
record pid immediately on spawn + broaden the reaper regex to the npm `claude` (D2);
await/strongly-reference fire-and-forget tasks + log on failure (D3); a periodic PUBSUB NUMSUB
liveness probe on a SEPARATE connection that keeps `listen()`'s `socket_timeout=None` intact and
resubscribes only on a confirmed drop (D4 — NOT a finite socket_timeout; see Blocker note).

## Architectural Impact

- **New dependencies:** none. All fixes reuse stdlib + existing Redis/Popoto primitives.
- **Interface changes:** `resolve_customer` gains a raise-on-unavailable contract (A2);
  `bridge/dedup.py` gains `claim_message()` (B1); `models/agent_session.py` **loses**
  `queued_steering_messages` + its two methods (A1); `bridge/dedup.py`/config loader
  gain a guarded reader (C4). No public CLI/MCP surface changes.
- **Coupling:** A1 *reduces* coupling (deletes a redundant dual-write). B1/B2 add a thin
  Redis-atomic gate at existing seams. D1 adds a startup precondition (contract-check).
- **Data ownership:** unchanged. A1 consolidates steering ownership onto the Redis list
  (already the de-facto owner); C3 changes index-member lifetime only.
- **Reversibility:** high per-workstream. Each fix is behind either a deletion (A1),
  a new gate that fails safe (B1/B2/C4), or an env-gated flag (D1 contract-check,
  D3 hold-tasks). Workstreams ship as separate PRs and revert independently.

## Appetite

**Size:** Large

**Team:** Solo dev + async/Redis-atomics specialist framing (paste the async +
Redis/Popoto rules from `DOMAIN_FRAMING.md` into each task), PM check-ins for the
PR-split sequencing, code-reviewer for the atomic-claim correctness.

**Interactions:**
- PM check-ins: 2-3 (confirm the PR-split; confirm B1's dedup-consolidation decision; confirm A1 field-deletion scope)
- Review rounds: 2+ (Redis-atomicity correctness across B1/B2/C1; the A1 deletion blast radius)

This is Large because it spans ~12 findings across ~14 files. The mitigation is the
**PR-split**: it ships as up to 12 small, independently-reviewable PRs, most parallel.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; r.ping()"` | All atomic-claim work (B1/B2) needs Redis WATCH/SETNX |
| Python ≥ 3.11 | `python -c "import sys; assert sys.version_info >= (3,11)"` | asyncio primitives for D3/D4 |
| `claude` CLI present (native install) | `readlink ~/.local/bin/claude` | D1a pin target (native version-dir symlink, not npm) + D1b contract-check target |
| gh auth | `gh auth status` | issue/PR operations |

Run via `python scripts/check_prerequisites.py docs/plans/correctness-delivery-integrity.md`.

## Solution

### Key Elements

- **A1 — Steering consolidation** (`agent/session_executor.py`, `models/agent_session.py`,
  `bridge/telegram_bridge.py`, `agent/health_check.py`, `tools/valor_session.py`):
  route the turn-boundary read + all writers through `agent/steering.py`'s atomic
  RPUSH/LPOP; delete the `queued_steering_messages` ListField and its RMW methods.
- **A2 — Resolver classification + unavailable alert** (`bridge/routing.py`, `bridge/email_bridge.py`,
  monitoring): distinguish `ResolverUnavailable` from "not a customer"; never `\Seen`-drop on the
  unavailable branch; raise a threshold-gated operator alert (`email:resolver_unavailable`) matching
  A3's treatment so an expired OAuth token is loud, not log-only (Concern 2).
- **A3 — Permanent-IMAP alert + email watchdog** (`bridge/email_bridge.py`, monitoring):
  classify permanent `IMAP4.error`, stop the infinite backoff, raise an operator alert.
- **B1 — Atomic per-message claim** (`bridge/dedup.py`, `bridge/dispatch.py`,
  `bridge/catchup.py`, `bridge/reconciler.py`): a `SET NX` claim before enqueue on the live path
  AND both recovery enqueue sites; only the winner enqueues.
- **B2 — Atomic pending→running claim** (`models/session_lifecycle.py`): Redis `WATCH`/`MULTI`
  (or SETNX claim key) replaces the Python CAS — this is the correctness-critical double-exec fix,
  shipped as its own minimal PR4 (Concern 4).
- **B2-probe — Observability-only pid probe** (`agent/session_health.py`): SEPARATED into PR4b.
  `register_worker_pid` gains an observability-only, liveness-gated log-and-supersede probe (never
  refuse/exit) so it cannot wedge the launchd respawn loop. Optional; droppable without affecting the
  correctness fix.
- **C1 — Child-independent finalize + idempotent sweep** (`models/session_lifecycle.py`,
  `worker/__main__.py`): preserve the best-effort, child-independent parent finalize (no
  coupling); startup sweep re-invokes the idempotent `_finalize_parent_sync` for stranded parents.
- **C2 — Monotonic freshness** (`models/agent_session.py`, `agent/session_health.py`):
  relative-age staleness; remove heal-by-clamp re-save.
- **C3 — Ghost-member reconciliation** (`models/dedup.py`, `models/agent_session.py`,
  index members): TTL the members or reconcile-on-read.
- **C4 — Guarded config read** (`bridge/routing.py`): atomic read + last-known-good fallback.
- **D1 — Native-installer version-assertion pin (D1a: `scripts/update/verify.py`) + startup
  contract-check (D1b: `worker/__main__.py`, `agent/granite_container/pty_driver.py`)** — two
  separable PRs; NOT an `npm_tools.py`/`MANAGED_PACKAGES` change. D1b's TUI-marker check is scoped
  to PTY-transport roles (a #1842 headless role carries no PTY bytes).
- **D2 — Immediate (None-aware) pid record + reaper regex** (`agent/granite_container/pty_pool.py`,
  `agent/session_health.py`) — a #1842 headless role has no PTY pid; the reaper broadening must not
  reap in-flight headless `claude -p` turns.
- **D3 — Hold fire-and-forget tasks** (`agent/sdk_client.py`, `bridge/telegram_bridge.py`,
  `agent/memory_extraction.py`).
- **D4 — Bounded notify-listener** (`agent/agent_session_queue.py`).

### Flow

Human steer / inbound message / config read / spawn → **atomic gate or guarded path** →
exactly-once, loud-on-failure outcome → existing recovery (reconciler, launchd, sweep)
takes over when a gate loses/raises.

### Technical Approach

Grouped by workstream; each maps to one PR in Step-by-Step. **Provisional constants**
(TTLs, thresholds) are named env-overridable with a grain-of-salt comment.

**A1 — route the steering inbox through the atomic primitive (and delete the racy path):**
- Turn-boundary read: `session_executor.py:1675` — replace `agent_session.pop_steering_messages()`
  with `from agent.steering import pop_all_steering_messages; pop_all_steering_messages(session.session_id)`
  (LPOP; instance-independent, so a bound stale instance is irrelevant). The leftover-drain
  path already uses this at `:2106/2108` — the two now agree.
- Writers: delete the ListField dual-write at `telegram_bridge.py:946` (`agent_session.push_steering_message(text)`) and
  `session_executor.py:679` and `:1686` (`session.push_steering_message(...)` / `agent_session.push_steering_message(_remaining)`)
  — each of those is a genuine redundant dual-write co-located with a module `push_steering_message(session_id, …)` RPUSH.
- **`health_check.py:573` is NOT a dual-write — do NOT delete it (Concern 1, Round 3).** Re-verified @HEAD `f8544f0a`:
  `_handle_steering` (`agent/health_check.py:490`) runs on every tool call, `pop_all_steering_messages(session_id)` **LPOPs
  the Redis list** (@507), and when there is **no active SDK client** (a CLI-harness session), it must re-DELIVER the popped
  non-abort messages to the turn-boundary inbox so the worker consumes them at the next boundary — the write at `:573`
  (`agent_session.push_steering_message(...)`) is that DELIVERY write, not a redundant producer-side dual-write. Deleting it
  outright would DROP steers that were already LPOP'd off the Redis list with nowhere to be re-deposited. Because A1 makes the
  Redis list the SOLE turn-boundary inbox, the correct change is to **REPOINT** this delivery write to a Redis-list RPUSH:
  the two branches (`:573` model-write when the session is found, `:585` `_repush_messages` when it is not) COLLAPSE to a single
  `_repush_messages(session_id, non_abort)` (the module-level RPUSH helper at `:475`). The current "model-write vs re-push"
  split existed only because pre-A1 the worker read the ListField (so re-pushing to the Redis list "would never be consumed",
  per the `:569` comment); post-A1 the worker reads the Redis list, so re-push IS the delivery. Update the `_handle_steering`
  docstring (`:498-503`) accordingly. The abort-message re-push (`:590`) already uses `_repush_messages` and is unchanged.
- Resume path `tools/valor_session.py:725-728`: drop the `queued_steering_messages` full-save
  clobber; if resume must inject a steer, RPUSH via `push_steering_message(session_id, …)`.
  Also `:916,:950` (status dumps) — read via the module's `peek`/`has_steering_messages`.
- Delete `queued_steering_messages = ListField(...)` @`agent_session.py:225` and the
  `push_steering_message`/`pop_steering_messages` methods @2046/2085. Remove the field
  from the `agent_session_queue.py:182` serialization list.
- **Popoto migration** (`scripts/update/migrations.py`): idempotent field-drop for existing
  records (see Update System). Any un-drained ListField content at migration time is
  low-risk (steers are ephemeral), but the migration RPUSHes any residual entries onto the
  Redis list before dropping, so nothing in flight is lost.
- The `push_steering_message` **name collision** (model method vs module function) disappears
  with the deletion — reviewers should confirm no caller relies on the model method after A1.
- **Preserve + test the single-consumer invariant.** `pop_all_steering_messages` is a NON-atomic
  sequential-LPOP loop (`agent/steering.py:80-109`); its safety depends on exactly one consumer
  draining a given session's list at a time. Today there are several *call sites*
  (`session_executor.py:2106/2108` leftover-drain, `session_pickup.py:182`, `health_check.py:507`,
  `granite_container/bridge_adapter.py:624`) but they are single-consumer *per session* — no two
  run against the same `session_id` concurrently. A1 makes this list the SOLE steering inbox, so
  the invariant becomes load-bearing. Add a regression test asserting that under two concurrent
  drainers of the SAME session_id, the union of popped messages equals the pushed set with no
  duplicates and no losses (i.e. each message is popped by exactly one drainer) — documenting and
  locking the single-consumer safety model so a future atomic-claim refactor elsewhere can't
  silently invalidate A1's argument. Do NOT convert the drain to a Lua/atomic multi-pop; that is
  scope creep — the invariant, not atomicity, is the contract (add an explicit code comment at the
  turn-boundary read stating this).

**A2 — distinguish "resolver unavailable" from "not a customer":**
- `resolve_customer` (`routing.py:1404`): wrap the resolver/`gws`/OAuth call; on
  infrastructure error raise `ResolverUnavailable` (new narrow exception) rather than
  `return None`. `return None` stays ONLY for a definitively-resolved non-customer.
- `_process_inbound_email` (`email_bridge.py:1182-1189`): catch `ResolverUnavailable` →
  leave the message **unseen**, `logger.warning` with the msg id, and continue (retry next
  poll). Only the true-non-customer branch keeps the `\Seen`-drop at `:1416`.
- Guard: an unavailable-resolver storm must not busy-loop — the poll cadence already bounds it.
- **Operator alert for a persistently-unavailable resolver (Concern 2, Round 3).** Leaving the
  message unseen makes the intake *recoverable*, but the flagship case — an **expired OAuth token**
  that renders EVERY inbound customer email un-resolvable — must ALSO be **loud**, matching A3's
  treatment (A3 alerts on permanent IMAP auth failure; the resolver-unavailable branch must not end
  log-only). On the `ResolverUnavailable` branch, set an operator alert key
  `email:resolver_unavailable` (value = first-seen ts + last msg id) and `logger.critical`, surfaced
  on the same dashboard health field as A3 (see Agent Integration). Make it threshold/duration-gated
  so a single transient blip does not page: arm the alert only after the resolver has been
  unavailable across N consecutive polls or for a bounded duration (provisional: 3 polls /
  `EMAIL_RESOLVER_ALERT_AFTER`, env-overridable, grain-of-salt comment); clear the key on the first
  successful resolve. This converts "every customer email silently piles up unseen behind an expired
  token" into an operator-visible signal without paging on noise.

**A3 — classify permanent IMAP auth failure + alert:**
- `email_bridge.py:1490-1496`: inspect the `imaplib.IMAP4.error` message for auth-permanent
  signatures (`AUTHENTICATIONFAILED`, `Invalid credentials`, `LOGIN failed`). On permanent:
  stop doubling the backoff, set a `email:auth_failed` alert key + `logger.critical`, and
  emit an operator alert (reuse the existing crash/alert surface — see Agent Integration).
  Transient errors keep the exponential backoff.
- Add a minimal **email watchdog** signal: a monotonic `email:last_poll_ts` staleness check
  (the key exists @1454) surfaced on the dashboard / an alert if it exceeds a threshold —
  so a wedged poll loop is visible even absent an exception.

**B1 — Redis provisional concurrency gate (fold into the existing dedup, no parallel system):**
- Add `claim_message(chat_id, message_id, ttl=…) -> bool` to `bridge/dedup.py`:
  `acquired = _R.set(f"bridge:msgclaim:{chat_id}:{message_id}", "1", nx=True, ex=CLAIM_TTL_SECONDS)`
  (reuse the `_get_redis()` client already in the module). Returns `True` only for the winner.
  Add a paired `release_message_claim(chat_id, message_id)` that `DELETE`s the key (`_R.delete(...)`)
  — the fail-safe used on an enqueue exception.
- **`CLAIM_TTL_SECONDS` is a SHORT provisional gate TTL in seconds (provisional ~60s,
  env-overridable, grain-of-salt comment), decoupled from the 2h membership set.** It is sized to
  the cross-actor *processing skew* between two near-simultaneous producers, NOT the ~1h iCloud
  sync-lag window. A LONG TTL is the BLOCKER (below): it would orphan the gate for up to an hour on
  a mid-window process death, and the reconciler's retry — which also calls `claim_message` — would
  then hit the orphaned key, fail `SET NX`, skip ("a peer won" when none did), and SILENTLY DROP the
  message, recreating the exact silent-drop B1 exists to eliminate. The gate must be short so an
  orphan self-heals within seconds.
- **Fail-safe wrapping (BLOCKER resolution, Round 3).** The dispatch wrapper today (`dispatch.py:99-106`)
  documents: enqueue must succeed before dedup is recorded; a raised enqueue leaves dedup unrecorded
  so the reconciler retries. Adding a claim BEFORE enqueue must not break that contract. So the claim
  site is wrapped:
  ```python
  acquired = await claim_message(chat_id, telegram_message_id)   # SET NX, short TTL
  if not acquired:
      return                                                     # a peer producer won
  try:
      depth = await enqueue_agent_session(...)
  except BaseException:
      await release_message_claim(chat_id, telegram_message_id)  # DELETE the gate, no orphan
      raise                                                      # preserve the propagate-and-retry contract
  await record_message_processed(chat_id, telegram_message_id)   # durable 2h membership, AFTER enqueue
  ```
  On a raised enqueue the gate is deleted and dedup stays unrecorded — the reconciler re-acquires the
  gate and re-enqueues (contract preserved). On a process death BETWEEN claim and enqueue (no chance to
  delete), the short TTL expires the orphan within seconds and the next reconciler scan re-enqueues.
- **Design decision (Open Question 1 — RESOLVED by the decoupling).** The gate does NOT subsume the
  membership set; they coexist with DISTINCT lifetimes: the gate is a seconds-wide concurrency
  arbiter (SET NX, `CLAIM_TTL_SECONDS`), the 2h membership set is the durable catchup-replay dedup
  (`record_message_processed`, recorded post-enqueue). No-parallel-systems is satisfied because both
  live IN the dedup module, not a new subsystem. This decoupling is what makes the short gate TTL
  safe: the durable 2h set — not the gate — covers the long sync-lag/replay window.
- The gate must guard the same non-enqueue branches (`record_telegram_message_handled`) so a
  steered/finalized message is also claimed once (no enqueue there, so no exception window — but the
  claim is still released via the durable-dedup path or left to expire).
- **Recovery paths must also gate (Round-1 Concern + Round-3 BLOCKER).** `bridge/dispatch.py`'s module
  docstring states that `bridge/catchup.py` and `bridge/reconciler.py` **intentionally bypass** the
  dispatch wrapper and keep their explicit two-step `enqueue → record_message_processed` pairing. Both
  use only a pre-check `is_duplicate_message` before enqueue (catchup `:176`→enqueue `:257`→record
  `:276`; reconciler `:176`→enqueue `:239`→record `:254`). During iCloud `projects.json` sync lag, two
  machines' catchup/reconciler loops can BOTH pass the pre-check and BOTH enqueue the same message —
  the exact double-exec race B1 closes in the live path would remain open on the recovery paths.
  Therefore B1 extends the same `claim_message` gate to **both** recovery enqueue sites, with the SAME
  fail-safe wrapping: `claim_message(chat_id, message.id)` immediately before `enqueue_agent_session_fn(...)`
  in `catchup.py:~257` and `reconciler.py:~239`; on a lost claim skip the enqueue (a peer won) but still
  record dedup so the local scan treats it as handled; on an enqueue exception `release_message_claim`
  then re-raise (or let the loop's existing error handling continue) so no orphaned gate blocks the next
  scan. The key is **SHARED** across live + recovery (a distinct per-path gate would REOPEN the
  cross-path race — live claims one key, recovery claims another, both enqueue). A message the live path
  already enqueued+recorded is stopped at the durable `is_duplicate_message` pre-check before a racing
  recovery scan reaches the gate. We do NOT route these through the dispatch wrapper (preserving the
  documented two-step pairing) — the gate is added in-line and the `dispatch.py` docstring is updated to
  note that recovery paths now also gate (the bypass is about the wrapper, not about skipping the
  concurrency gate) and that the gate is released on any enqueue exception.

**B2 — atomic pending→running claim (the real double-exec fix) + an observability-only pid probe:**

> **PR split (Concern 4, Round 3):** the correctness-critical atomic status claim (PR4, `session_lifecycle.py`
> only) and the observability-only pid probe (PR4b, `session_health.py`) ship as SEPARATE PRs so PR4 stays
> minimal — the atomic status claim is the load-bearing correctness fix; the pid probe is diagnostic and
> droppable.
- Replace the Python CAS in `session_lifecycle.py:604-648` with either (a) a Redis
  `WATCH`/`MULTI` on the session-status field, or (b) a `SET NX` run-claim key
  `session:runclaim:{session_id}` (simpler, matches the existing SETNX idiom). Prefer (b)
  for simplicity unless the status field must remain the single source — decide in review.
  The loser raises `StatusConflictError` (existing) so callers already handle it.
- **No parallel system:** the WATCH/MULTI (or SETNX) claim REPLACES the re-read+compare;
  delete the Python compare once the atomic gate lands.
- **`register_worker_pid` is NOT a second concurrency gate (Blocker B2 resolution).** The atomic
  pending→running status claim above is the real and sufficient double-exec fix: it guarantees
  exactly one actor per session regardless of how many worker processes are alive. The prior
  plan text proposed a `register_worker_pid` singleton guard that would *refuse* (or
  log-and-supersede) a second live worker pid. **That "refuse" is withdrawn — it is fleet-hazardous.**
  Under launchd `KeepAlive`, an unclean worker exit leaves the dead pid's TTL'd key present until
  its TTL expires; a refuse-guard would then block the *healthy respawned worker* for the entire
  remaining TTL window, defeating the self-healing respawn the system depends on. A guard that
  can wedge the respawn loop trades a race the status-claim already closes for an availability
  outage — the wrong trade.
- **What B2 actually does with the pid key (log-and-supersede ONLY, liveness-gated, never refuse):**
  `register_worker_pid` (`session_health.py:2981`) keeps its additive `_R.set` write and gains an
  *observability-only* probe with strict liveness gating — it never `sys.exit`s, never refuses to
  start, and never treats key presence alone as a conflict:
  - Scope the comparison to the **same host + role** (a different machine or a different role
    legitimately owns its own worker pid — those are not conflicts).
  - **Exclude `os.getpid()`** so a worker never flags itself (re-registration on the same pid,
    or a stale key it is about to overwrite, is not a conflict).
  - Before logging anything, **liveness-check the competitor pid** via `os.kill(pid, 0)` AND a
    heartbeat-freshness check (reuse `HEARTBEAT_FRESHNESS_WINDOW`). A pid that fails the liveness
    check is a dead-worker residue (the exact launchd-respawn case) — it is silently superseded
    (overwrite the key), NOT reported as a conflict.
  - Only when a competitor pid on the same host+role is *confirmed live* (passes `os.kill(pid, 0)`
    and has a fresh heartbeat) does the probe emit a `logger.warning` ("second live worker for
    host/role — superseding pid registration") and supersede the key. Even then it does not exit
    or block; the status-claim, not the pid key, arbitrates who executes each session.
  - This makes the pid key a diagnostic signal (visible surprise: two live workers on one
    host/role) without ever being able to wedge the respawn loop or refuse a healthy worker.
- **Why the guard is safe to keep at all (vs. dropping it entirely):** both critics noted the guard
  is largely redundant with the atomic status claim. Keeping it *only* as a liveness-gated,
  supersede-only diagnostic preserves the operator signal (a genuinely-duplicated live worker is
  worth a WARNING) while removing every wedge/refuse hazard. If review prefers zero risk of any
  behavioral surprise, the fallback is to drop the pid-key probe from B2 entirely and rely solely
  on the atomic status claim — that is an acceptable and equivalent-correctness outcome, since the
  status claim is the load-bearing fix.

**C1 — preserve child-independent finalize; fix the crash-window orphan with an idempotent sweep
(NOT by coupling the two writes):**
- **Existing contract to preserve (Concern 2).** `finalize_session` (`session_lifecycle.py:221`)
  today calls `_finalize_parent_sync` (@440-451) inside a `try/except` that logs the failure as
  **non-fatal** and continues to the child save (@474). This is intentional: parent finalization
  is best-effort so the **child always finalizes independently**, even if the parent lookup/save
  raises (parent deleted, Redis blip, stale index). `_finalize_parent_sync` is itself idempotent
  (no-op if the parent is already terminal or missing — `session_lifecycle.py:719-732`). An
  all-or-nothing pipeline that couples parent+child writes would INVERT this: a parent-finalize
  failure would now roll back (or block) the child finalize, stranding the child. That is the
  wrong trade — a child must never be held hostage to its parent's finalize.
- **Fix (decouple, don't couple).** Keep the child-independent, best-effort-parent behavior
  exactly as-is (do NOT wrap the two writes in one transaction). The crash-window orphan — a
  process death *after* the child save but *before* the parent transitions out of
  `waiting_for_children` — is closed by an **idempotent re-trigger sweep**, not by coupling:
  - Startup sweep (`worker/__main__.py` recovery step): scan for parents stuck in
    `waiting_for_children` whose children are ALL terminal, and re-invoke `_finalize_parent_sync`
    for each (which is already idempotent). This re-finalizes any parent stranded by a crash in
    the window, without changing the per-call finalize semantics.
  - Because `_finalize_parent_sync` already no-ops on a terminal/missing parent and recomputes
    the parent's fate from the children's current statuses, the sweep is safe to run repeatedly
    and cannot corrupt a parent that finalized normally.
- **Why not the pipeline.** The prior plan text ("both writes all-or-nothing") is withdrawn: it
  contradicted the documented non-fatal parent-finalize contract (`session_lifecycle.py:439-451`,
  `687-732`). The sweep achieves the same end-state guarantee (no permanently-stranded parent)
  while preserving child-independence.

**C2 — monotonic/relative freshness; stop healing-by-clamp:**
- `_heal_future_updated_at` (`agent_session.py:1004-1070`, `record.save()`@1060; re-verified
  @HEAD `f8544f0a`, drifted from `973-1037`/`:1029`): stop **re-saving** the clamped
  value (the re-save reshuffles the `created_at` index and rewrites recent records backward).
  Clamp for read-time comparison only, or drop the heal entirely in favor of relative-age math.
- Health staleness (`session_health.py:1112`, `HEARTBEAT_FRESHNESS_WINDOW=90`@225): compute
  staleness from a monotonic/relative age against a single trusted clock rather than local
  `now` minus a possibly-skew-written `updated_at`, so a reader ≥90s ahead does not flag fresh
  sessions stale (spurious recovery/kills).

**C3 — TTL the index members or reconcile-on-read:**
- Align index/set-member lifetime with the hash `Meta.ttl` (`DedupRecord.ttl=7200`,
  `AgentSession.ttl=2592000`). Preferred: reconcile-on-read — when `query.filter()` yields a
  member whose hash is gone (the `{} in hashes_list` ghost path), drop the stale member and
  skip it. Prevents email subject-coalescing attaching a reply to a non-existent session.

**C4 — atomic config read + last-known-good fallback:**
- Replace the unguarded `json.load` (`routing.py:134-135`) with a loader that: reads the
  file, `json.loads`, and on success caches the parsed config to a sidecar
  (`data/projects.last_known_good.json`, atomic temp-rename per `session_health.py:3009-3011`).
  On `JSONDecodeError`/partial read, `logger.error` + return the last-known-good — never raise
  at import. This stops the launchd `KeepAlive` respawn storm on a mid-iCloud-write file.

> **On the D-group "brittleness" grouping (nit):** D1–D4 share only a loose thesis (each is a
> latent brittleness that fails silently). This is acknowledged and already mitigated by the
> 12-way PR split — each ships and reverts independently, so the weak shared framing costs nothing.
> No structural change; the grouping is a labeling convenience, not a coupling.

**D1 — pin `claude` (version-assertion, native-installer-compatible) + startup contract-check.
These are TWO SEPARABLE concerns and ship as two independent PRs (D1a pin, D1b contract-check):**

- **Prior fact that decides the pin mechanism (Concern 4 / OQ3, now resolved).** The live `claude`
  CLI is installed via the **NATIVE installer**, not npm: `~/.local/bin/claude` is a symlink to
  `~/.local/share/claude/versions/2.1.197` (verified against HEAD). It is NOT in npm's global
  `node_modules`. Therefore adding `@anthropic-ai/claude-code` to `scripts/update/npm_tools.py`
  `MANAGED_PACKAGES` is the WRONG mechanism — it would either be a no-op (npm doesn't own the
  binary) or force a fleet-wide switch to the npm install path, changing how every machine resolves
  `claude`. **Resolution:** do NOT add it to `MANAGED_PACKAGES`. Instead pin via a
  **version-assertion** compatible with the native install.

- **D1a — version-assertion pin (`scripts/update/verify.py`):** add a step that reads the installed
  version (`claude --version`, or the resolved `~/.local/share/claude/versions/<ver>` symlink
  target) and compares it to a pinned constant `PINNED_CLAUDE_VERSION` (grain-of-salt comment;
  env-overridable). On drift: warn (default) or block (env-gated), independent of install method.
  This works whether the binary came from the native installer or npm. Document the pinned version
  + bump procedure in `docs/features/`. No `MANAGED_PACKAGES` change.

- **D1b — startup contract-check (`worker/__main__.py:726`, `agent/granite_container/pty_driver.py`):**
  SEPARATE from the pin — this is a behavioral probe, not a version gate. After the `shutil.which`
  check at `worker/__main__.py:726` (re-verified @HEAD `f8544f0a`, drifted from `:712` by #1842/#1688),
  run a startup probe that asserts the scraped markers still match the installed CLI's behavior: verify
  `IDLE_BAR`@99 / `PROMPT_GLYPH`@100 / `SPINNER_EVIDENCE_RE`@116 (`pty_driver.py`, anchors moved by
  #1842/#1688 from `:95,96,112-115`) and the trust-folder patterns (`startup_parser.py:117-121`) are
  present in a dry TUI spawn / fingerprint. On mismatch: `logger.critical` + fail loudly (refuse to
  start / alert) rather than letting every session hang to timeout silently.
- **Scope to PTY transport (Concern 3, #1842).** The merged per-role transport hedge (#1842) makes each
  role config-selectable `pty` vs `headless`. TUI markers only govern **PTY-transport** sessions: a
  `headless` role runs a `claude -p` one-shot per turn via `HeadlessRoleDriver` and "carries NO PTY
  bytes" (`container.py:2489`), so it is IMMUNE to TUI-marker drift (its failure mode is a `claude -p`
  output-contract change, out of scope for D1b). Therefore the contract-check must NOT refuse-to-start
  a deployment whose configured roles are ALL headless — gate the hard-fail on "at least one role is
  PTY-transport" (read the same per-role transport config #1842 added, e.g. `pm_transport`/`dev_transport`).
  When PTY transport is in use, marker drift is fatal-if-unenforced as before; when the fleet is fully
  headless, the check is a no-op (log-and-skip). The env enforce flag still applies on top.
- The pin (D1a) and the contract-check (D1b) land as different PRs: the pin is a version-drift detector;
  the contract-check is a PTY-marker-drift detector. Either is independently valuable; neither depends
  on the other.

**D2 — record pid immediately on spawn (None-aware per #1842) + broaden the reaper regex:**
- `pty_pool.py:527-556` (re-verified @HEAD `f8544f0a`; region moved from `:523-530` by #1842/#1688):
  record each child's pid to `granite_pty_pids.json` **immediately after its own `spawn()`** returns,
  not after both — so a `dev.spawn()` (`:548`) failure after a successful `pm.spawn()` (`:538`) leaves
  the already-spawned `pm` pid persisted and reapable. Today the pid loop + `_persist_pids()` run only
  AFTER both spawns (`:550-556`), so a `dev.spawn()` raise orphans `pm`.
- **#1842 headless roles have no PTY pid (Concern 3).** Post-#1842 a `headless` role gets `pm`/`dev`
  set to `None` (`:527-529,539`) and runs via `HeadlessRoleDriver` (`claude -p` one-shot per turn) —
  there is NO PTY process and NO pid to record for that role, which is correct, not an orphan. So the
  per-spawn pid-record must stay None-aware: after each `pm.spawn()` / `dev.spawn()` that actually ran,
  guard `pty is not None and pty._child is not None` before `self._spawned_pids.add(pid)` +
  `self._persist_pids()`. A fully-headless pair persists an empty pid set (nothing to reap) — the
  invariant is "every PTY that was spawned is immediately reapable", which holds trivially when no PTY
  was spawned.
- Broaden the reaper regex (`_CLAUDE_CMDLINE_RE`@`session_health.py:58`) so it ALSO matches the
  native/PTY `claude` TUI process cmdline, not only `claude_agent_sdk/_bundled/claude` — a second line
  of defense against PTY orphans. **Do NOT clobber headless `claude -p` turns:** a live headless turn
  is a legitimate short-lived `claude --print` subprocess already governed by `_is_stale_print_oneshot`
  (`session_health.py:3880`, age-gated). The broadened TUI regex must target the persistent PTY TUI
  cmdline shape and not reap an in-flight `claude -p` headless turn — coordinate the two matchers so a
  running headless turn is never SIGKILL'd by the D2 broadening.

**D3 — await / strongly-reference fire-and-forget tasks:**
- `sdk_client.py:1842,1857,1923`: the circuit-breaker `record_failure/success` must not be
  lost. Either `await` them (they're cheap Redis writes) or wrap in a helper that appends to a
  module-held task set AND logs on exception — so if `record_failure` raises (Redis down, the
  very failure), the breaker still trips / the failure is visible.
- `telegram_bridge.py:1617,1637`: append the emoji + classify `create_task(...)` to the
  existing `_background_tasks` list (@183) — the documented pattern already used for catchup/
  watchdog (@3025,3059) — so the GC can't collect them mid-flight; add a done-callback that
  logs exceptions.
- `memory_extraction.py:328,537,560,791,1029`: the bare `except Exception: pass` handlers must
  `logger.debug/warning` with context instead of silently swallowing — keep them non-fatal
  (memory must never crash the agent) but no longer invisible.

**D4 — periodic pubsub liveness probe on a SEPARATE thread; keep `socket_timeout=None` (BLOCKER
revision — do NOT reintroduce the finite socket_timeout that a prior round already rejected):**

- **Documented prior art that forbids the naive fix.** `_session_notify_listener`
  (`agent_session_queue.py:805`) deliberately sets `socket_timeout=None` (@851) on its dedicated
  pubsub connection, with an in-code comment (@822-828) explaining WHY: the global
  `POPOTO_REDIS_DB` pool uses `socket_timeout=5` (tuned for request-response), and inheriting a
  finite timeout on the pubsub connection caused **spurious "Timeout reading from socket"
  exceptions and a 10-second reconnect cycle that DROPPED notifications published during the dead
  window.** A finite `socket_timeout` on the `listen()` connection is therefore a KNOWN-BAD design
  that was already tried and reverted. Reintroducing it — even to "unblock periodically" — would
  reproduce that exact churn: every timeout tick raises inside `listen()`, tears the connection
  down through the `finally` teardown, and re-subscribes, dropping any message published in the
  gap. **This plan does NOT do that.**

- **What already exists (do not duplicate).** #1804/#1811 added a *subscribe-time* NUMSUB
  self-check (@857-895): after `subscribe()`, it verifies `PUBSUB NUMSUB >= 1` (bounded retry,
  ~300 ms) and, on a confirmed 0, falls through the teardown so the outer `while True` re-subscribes
  after its 5 s backoff. The comment (@833-835) explicitly concedes the remaining gap:
  **post-subscribe drift** (a subscription that was good, then silently drops on a Redis failover
  with NUMSUB→0 and no exception) is "left to the existing 300 s health backstop." That 300 s silent
  gap is exactly what D4 closes.

- **Fix — periodic PUBSUB NUMSUB liveness probe, off the blocking path.** Keep `socket_timeout=None`
  and the blocking `pubsub.listen()` loop UNCHANGED (preserving the spurious-timeout-free semantics
  the comment protects). Add a SECOND, lightweight watchdog that runs *independently* of `listen()`:
  - Start a dedicated liveness thread (or an asyncio task on the coroutine side) that, every
    `NOTIFY_HEALTHCHECK_INTERVAL` seconds (provisional ~15 s, env-overridable, grain-of-salt
    comment), issues `PUBSUB NUMSUB valor:sessions:new` on a SEPARATE short-lived connection (NOT
    the `listen()` connection — so the probe never touches the blocking socket and cannot induce the
    forbidden timeout on it). This reuses the exact `_numsub_count`/`pubsub_numsub` idiom already in
    the module.
  - On a confirmed NUMSUB==0 for the listener's channel (a silently-dropped subscription), the probe
    logs a WARNING ("notify subscription dropped — forcing resubscribe") and signals the listener
    thread to tear down and re-subscribe: e.g. `loop.call_soon_threadsafe(notify_queue.put_nowait, None)`
    (the existing restart signal @935) plus closing the listener's pubsub so its blocking `listen()`
    returns and the outer `while True` re-subscribes. Because the probe runs on its own connection,
    the blocking connection keeps `socket_timeout=None` and never sees a spurious timeout.
  - **Why round two won't reproduce the documented failure:** the prior failure came from putting a
    finite timeout ON the `listen()` connection, which made `listen()` itself raise and reconnect on
    every idle tick. Here the `listen()` connection is untouched (still `socket_timeout=None`, still
    blocking); the only new activity is a NUMSUB read on a *different* connection that runs whether or
    not any message is in flight. A dropped subscription is detected within
    `NOTIFY_HEALTHCHECK_INTERVAL` (seconds) instead of 300 s, with a WARNING, and a resubscribe is
    triggered only on a CONFIRMED drop — not on every idle interval. No message is dropped, because
    the resubscribe only fires when the subscription is already dead (NUMSUB==0), which is precisely
    the state in which messages were already being lost.
  - Bound the probe's own failures: if the NUMSUB read itself raises (Redis unreachable), log at
    WARNING and skip that tick — do NOT tear down the listener on a transient probe error (only a
    confirmed NUMSUB==0 triggers resubscribe), so a flaky probe can't cause the very churn we're
    avoiding.

This converts the conceded 300 s silent post-subscribe-drift gap (@833-835) into a
seconds-latency, logged, self-healing resubscribe — WITHOUT the finite-`socket_timeout` design the
in-code comment already rejected.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] A2: assert the `ResolverUnavailable` branch leaves the message **unseen** and logs — a test simulating an OAuth error must NOT `\Seen`-drop; a PERSISTENT unavailable resolver (across the threshold) arms the `email:resolver_unavailable` operator alert and clears it on the first successful resolve (Concern 2).
- [ ] A3: assert a permanent `IMAP4.error` sets the alert key + `logger.critical` and stops the backoff doubling; a transient error keeps backing off.
- [ ] B1 (BLOCKER, Round 3): assert that when `enqueue_agent_session` raises between claim and enqueue, the claim key is DELETED (no orphan) and the message is re-enqueueable by the reconciler — NOT silently dropped; assert the gate TTL is in seconds (short self-heal on a simulated process death), decoupled from the 2h membership.
- [ ] D3: each formerly-silent handler (`sdk_client` circuit writes, bridge tasks, `memory_extraction.py:328,537,560,791,1029`) now emits an observable log/metric on failure — assert via `caplog`.
- [ ] C4: a `JSONDecodeError` from a partial config returns last-known-good and logs — assert import does NOT raise.

### Empty/Invalid Input Handling
- [ ] A1: `pop_all_steering_messages` on an empty list returns `[]` (no crash); a steer pushed between pop and the next turn is drained next boundary.
- [ ] A1: **single-consumer invariant** — two concurrent drainers of the same session_id split the pushed messages disjointly (each message popped exactly once; no duplicate, no loss). Locks the non-atomic-but-single-consumer safety model against a future refactor.
- [ ] B1: `claim_message` with a message already claimed returns `False`; with a fresh id returns `True`.
- [ ] C3: `query.filter()` over an index with a ghost member returns only live records (empty when all ghosts).

### Error State Rendering
- [ ] A3/email watchdog: the permanent-auth alert reaches an operator surface (dashboard/alert), not just a log line.
- [ ] D1: a contract-check mismatch fails loudly at startup (visible in `logs/worker.log` + refuses to serve), not a silent per-session timeout.
- [ ] D4: a dropped subscription (simulated NUMSUB==0) logs a resubscribe WARNING within `NOTIFY_HEALTHCHECK_INTERVAL` (seconds) via the off-path probe, not after the 300s backstop; the `listen()` connection retains `socket_timeout=None` (assert no finite timeout was introduced) and a transient probe error does NOT trigger a resubscribe.

## Test Impact

- [ ] `tests/unit/test_dedup.py` (or the bridge dedup tests) — UPDATE: add `claim_message` provisional-gate cases + `release_message_claim` DELETE; assert the gate TTL is in seconds (`CLAIM_TTL_SECONDS`, decoupled from the 2h membership); assert the existing membership behavior is unchanged (B1 coexists).
- [ ] `tests/unit/bridge/test_dispatch.py` (dispatch wrapper tests) — UPDATE: assert `claim_message` gates enqueue; a lost claim skips enqueue; **fault injection (BLOCKER, Round 3):** when `enqueue_agent_session` raises, the claim key is DELETED before the exception propagates (no orphaned gate) and dedup stays unrecorded — assert the reconciler contract holds (message is re-enqueueable, not silently dropped) (B1).
- [ ] `tests/**/test_catchup*.py` and `tests/**/test_reconciler*.py` — UPDATE/ADD: assert the recovery enqueue sites also call `claim_message` (shared key) and skip enqueue on a lost claim; a message already claimed/recorded by the live path is not double-enqueued by a racing catchup/reconciler scan; assert an enqueue exception on the recovery path releases the gate so the next scan re-acquires (no permanent drop) (B1, Concern 1 + Round-3 BLOCKER).
- [ ] `tests/**/test_*steering*.py` or a `health_check` steering test — ADD: assert the CLI-harness delivery path (`_handle_steering`, no active SDK client) re-deposits popped non-abort messages onto the Redis list via `_repush_messages` so they are NOT lost after A1 deletes the ListField method (Concern 1); a steer is delivered at the next turn boundary.
- [ ] `tests/**/test_*steering*.py` and any test asserting `queued_steering_messages` / `agent_session.pop_steering_messages()` — REPLACE: rewrite against `agent/steering.py` LPOP; the ListField and its methods are deleted (A1). Grep `grep -rln "queued_steering_messages\|\.pop_steering_messages(\|\.push_steering_message(" tests/` to enumerate before deletion. ADD a single-consumer-invariant test (two concurrent drainers of one session_id → disjoint split, no dup/loss) that documents why the non-atomic drain is safe.
- [ ] `tests/**/test_session_lifecycle*.py` (pending→running claim) — UPDATE: assert the atomic claim (WATCH/MULTI or SETNX) admits exactly one actor; two concurrent claimants → one `StatusConflictError` (B2).
- [ ] `tests/**/test_session_health*.py` (`register_worker_pid`, PR4b — separated per Concern 4) — ADD: assert respawn-safety — a stale/dead pid key (failed `os.kill(pid, 0)` or stale heartbeat) is silently superseded and does NOT block registration of a launchd-respawned worker; the worker never flags `os.getpid()` against itself; cross-host/cross-role pids are not conflicts; only a confirmed-live same-host+role duplicate logs a WARNING; assert `register_worker_pid` never calls `sys.exit`/`raise …Conflict` (B2 blocker resolution).
- [ ] `tests/**/test_*finalize*.py` / `waiting_for_children` tests — UPDATE: assert the child ALWAYS finalizes independently even when parent finalize raises (child-independent contract preserved, NOT coupled) and that a startup sweep re-finalizes a parent stranded by a crash after the child save (C1).
- [ ] `tests/**/test_session_health*.py` freshness/heal tests — UPDATE: assert `_heal_future_updated_at` no longer re-saves; relative-age staleness under simulated skew does not flag fresh sessions (C2).
- [ ] `tests/**/test_routing*.py` / email tests — UPDATE: `resolve_customer` raises on infra error; `_process_inbound_email` unseen-on-unavailable (A2); config loader last-known-good (C4).
- [ ] `tests/**/test_pty_pool*.py` — UPDATE: assert pid recorded immediately on each spawn; a `dev.spawn()` failure leaves `pm` reapable; a #1842 headless role (pm/dev `None`) records no pid and is not treated as an orphan (Concern 3) (D2).
- [ ] `tests/**/test_agent_session_queue*.py` notify-listener tests — UPDATE/ADD: assert the off-path NUMSUB liveness probe resubscribes on a simulated confirmed drop (NUMSUB==0) within `NOTIFY_HEALTHCHECK_INTERVAL` and logs a WARNING; assert `socket_timeout=None` is PRESERVED on the `listen()` connection (no finite timeout reintroduced); assert a transient probe error does not tear down the listener (D4).

If a listed file does not exist, that finding is greenfield-tested (add a new test); the
disposition then becomes ADD. No test is DELETE-only except those asserting the removed
`queued_steering_messages` field, which are REPLACE.

## Rabbit Holes

- **Do NOT build a general-purpose distributed lock manager for B1/B2.** The `SET NX` /
  `WATCH`/`MULTI` idioms already in the codebase are sufficient; a new lock abstraction is
  scope creep.
- **Do NOT implement lease-based slot ownership or progress-deadline cancel scope** — those
  are #1820, explicitly deferred. B2 is a claim on the *status transition*, not a slot lease.
- **Do NOT rework the memory-extraction filtering** — #1822 owns that. D3 only makes the
  *exception* paths observable; do not touch the noise filters.
- **Do NOT rewrite the PTY pool state machine or the startup TUI parser** — D1/D2 are a
  version-pin, a contract-check probe, a pid-record reorder, and a regex broadening. No
  redesign of the scrape contract.
- **Do NOT add a second dedup/steering system.** A1 and B1 both CONSOLIDATE onto an existing
  primitive and DELETE the redundant path — the no-parallel-systems rule is load-bearing here.
- **Do NOT try to make cross-machine clocks agree** (NTP tuning, clock sync) for C2 — the fix
  is to stop trusting wall-clock deltas, not to fix the clocks.
- **Do NOT couple the 12 findings into one mega-PR.** The value of this plan is the split.

## Risks

### Risk 1: A1 field deletion misses a live consumer
**Impact:** A caller still reading `queued_steering_messages` after deletion breaks (AttributeError / missing steers).
**Mitigation:** The blast-radius grep is enumerated (Verification anti-criterion asserts zero references outside the migration). The migration RPUSHes residual ListField content onto the Redis list before dropping. Ship A1 as its own PR with a full-suite run.

### Risk 2: B1 gate TTL too LONG orphans the claim on a mid-window failure (Round-3 BLOCKER)
**Impact:** The earlier plan sized `CLAIM_TTL` to the ~1h sync-lag window and reused the SAME key for the recovery paths. If `enqueue` raised — or the process died — between claim and enqueue, the claim key would persist for ~1h with NO session enqueued. The reconciler's retry (which also calls `claim_message`) would then hit the orphaned key, `SET NX` would fail, it would skip ("a peer won" when none did), and the message would be SILENTLY DROPPED for up to an hour — recreating the exact silent-drop B1 exists to eliminate, defeated by the plan's own long-lived shared key. This was the BLOCKER.
**Mitigation:** The claim is redefined as a PROVISIONAL concurrency gate with a SHORT TTL in **seconds** (`CLAIM_TTL_SECONDS`, provisional ~60s, env-overridable), sized to cross-actor *processing skew*, DECOUPLED from the 2h membership set (which is what actually covers the sync-lag/replay window). The enqueue is wrapped so ANY exception DELETES the gate (`release_message_claim`) before re-raising — no orphan on a handled failure. On a process death mid-window, the short TTL self-heals the orphan within seconds and the next reconciler scan re-enqueues. The gate must NOT be sized to the sync lag; the durable 2h membership, not the gate, is the belt-and-suspenders for catchup. Verified under a simulated sync-lag test (two dispatches race → exactly one enqueues) AND a fault-injection test (enqueue raises / process dies between claim and enqueue → message re-enqueueable, not permanently dropped).

### Risk 3: B2 atomic claim changes the observable error surface
**Impact:** More `StatusConflictError`s surface where the Python CAS silently double-ran.
**Mitigation:** Callers already handle `StatusConflictError`. The loud conflict is the *correct* outcome (it was a silent double-run before). Assert existing handlers cover it.

### Risk 3b: B2 `register_worker_pid` probe wedges the launchd respawn loop (Blocker B2)
**Impact:** A pid guard that *refuses* a second live worker pid on key presence would, after an unclean worker exit under launchd `KeepAlive`, block the healthy respawned worker for the dead pid's entire residual TTL — a self-inflicted availability outage, since the dead pid's key still exists.
**Mitigation:** The refuse-guard is withdrawn. The atomic pending→running status claim (not the pid key) is the real one-actor-per-session guarantee. The pid key becomes an observability-only, liveness-gated log-and-supersede probe: it excludes `os.getpid()`, scopes to host+role, liveness-checks the competitor via `os.kill(pid, 0)` + heartbeat freshness, silently supersedes any dead/stale pid (the exact respawn case), and WARNS + supersedes only on a confirmed-live duplicate — never `sys.exit`/refuse. A respawn-safety test asserts a stale-pid key does not block registration. Dropping the probe entirely is an acceptable fallback with equivalent correctness.

### Risk 4: D1 contract-check false-positive blocks worker startup
**Impact:** A benign CLI update the probe misreads refuses to start the worker fleet-wide.
**Mitigation:** Gate the hard-fail behind an env flag (`CLAUDE_CONTRACT_CHECK_ENFORCE`, default warn-then-start for the first release, then flip to enforce). Pin the version so updates are deliberate; the probe becomes a change-detector, not a gatekeeper, until proven.

### Risk 5: C1 sweep re-finalizes a parent that should stay open
**Impact:** A sweep that mis-detects "all children terminal" could finalize a parent that still has a live child, closing it prematurely.
**Mitigation:** The sweep reuses `_finalize_parent_sync`, which recomputes the parent's fate from the CURRENT children statuses and only transitions when ALL are terminal (it is the same logic the live path uses). It is idempotent and cannot corrupt a normally-finalized parent. NOTE: this replaces the withdrawn "pipeline coupling" approach — we deliberately did NOT couple parent+child writes, because that would strand a child whenever its parent finalize fails (inverting the documented non-fatal contract at `session_lifecycle.py:439-451`). Test the crash-after-child-save window with fault injection and assert the child stays finalized while the parent is recovered by the sweep.

## Race Conditions

### Race 1: Steer pushed between the turn-boundary drain and the next turn (A1)
**Location:** `session_executor.py:1675` sequential-LPOP drain (`pop_all_steering_messages`) vs `push_steering_message` RPUSH.
**Trigger:** Human steers exactly as the worker drains the list.
**Data prerequisite:** The Redis list is the single source of truth (post-A1).
**State prerequisite:** The drain is a NON-atomic loop of individual LPOPs (`agent/steering.py:80-109`), but each individual LPOP is atomic vs. a concurrent RPUSH, and the **single-consumer invariant** holds (only one process drains a given session_id at a time). A steer arriving after the drain sits in the list for the next boundary.
**Mitigation:** Per-LPOP atomicity + single-consumer — no steer is clobbered; worst case it is drained one boundary later. NOTE: the safety argument is single-consumer, NOT whole-drain atomicity — Race 1's test must assert two concurrent drainers of the same session_id split the messages disjointly (no dup, no loss), preserving the invariant A1 relies on.

### Race 2: Two machines dispatch the same message during sync lag (B1)
**Location:** `dispatch.py` `claim_message` before `enqueue_agent_session`; ALSO `catchup.py:~257` and `reconciler.py:~239` recovery enqueue sites.
**Trigger:** iCloud `projects.json` reassignment not yet propagated → both machines receive the `NewMessage` (live path) or both machines' catchup/reconciler scans replay the same message (recovery path).
**Data prerequisite:** Both machines share the same Redis (they do — single Redis).
**State prerequisite:** `SET NX` with a SHORT `CLAIM_TTL_SECONDS` admits exactly one writer for the seconds-wide concurrency window; the gate key is SHARED across live + recovery producers (a distinct per-path key would reopen the cross-path race), and the durable 2h membership set stops a much-later recovery replay of an already-enqueued+recorded message before it reaches the gate.
**Mitigation:** Only the `SET NX` winner enqueues; the loser skips (but still records dedup). The gate is DELETED on any enqueue exception (and self-heals via the short TTL on a process death), so a lost/failed producer never orphans the key and re-drops the message. Test simulates concurrent dispatch of the same `(chat_id, message_id)` on the live path AND a concurrent catchup/reconciler replay of an already-live-claimed message, PLUS the fault-injection case (enqueue raises between claim and enqueue → gate released → reconciler re-enqueues).

### Race 3: Two actors claim the same pending session (B2)
**Location:** `session_lifecycle.py:604-648`.
**Trigger:** Worker + `valor-session` CLI + catchup + reflection all eligible to pick a pending session.
**Data prerequisite:** Session status in Redis.
**State prerequisite:** WATCH/MULTI (or SETNX run-claim) admits one transition.
**Mitigation:** Atomic transition; loser gets `StatusConflictError`. Delete the Python compare.

### Race 4: Crash after child save, before parent transitions out of waiting_for_children (C1)
**Location:** `session_lifecycle.py:440-451` (best-effort parent finalize) vs `:474` (child save).
**Trigger:** Process death after the child save but before the parent's `waiting_for_children` transition lands.
**Data prerequisite:** Child is terminal; parent still `waiting_for_children`.
**State prerequisite:** `_finalize_parent_sync` is idempotent and no-ops on a terminal/missing parent (`:719-732`); the sweep can re-run it safely.
**Mitigation:** Idempotent worker-startup sweep re-invokes `_finalize_parent_sync` for stranded parents. NO pipeline coupling — the child-independent, best-effort-parent finalize contract is preserved (coupling would strand the child on a parent-finalize failure).

### Race 5: Notify subscription drops silently on failover (D4)
**Location:** `agent_session_queue.py:851` `socket_timeout=None` (PRESERVED) + a new separate-connection NUMSUB liveness probe.
**Trigger:** Redis failover (post-#1827) drops the subscription without raising (post-subscribe drift, NUMSUB→0, conceded @833-835).
**Data prerequisite:** The probe reads `PUBSUB NUMSUB` on a SEPARATE connection — the blocking `listen()` connection is never touched, so its `socket_timeout=None` is retained (no spurious-timeout churn, the failure the in-code comment @822-828 documents).
**State prerequisite:** A CONFIRMED NUMSUB==0 signals the listener to tear down + resubscribe; a transient probe error does not.
**Mitigation:** Periodic off-path NUMSUB probe + resubscribe-on-confirmed-drop + WARNING log. Reduces the silent gap from 300s to `NOTIFY_HEALTHCHECK_INTERVAL` (seconds) WITHOUT reintroducing the rejected finite `socket_timeout` on `listen()`.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1820] Lease-based slot ownership + progress-deadline cancel scope. B2 claims the *status transition* atomically; it does NOT introduce a slot lease or a no-progress cancel — those are #1820's wedge fixes #2/#3.
- [SEPARATE-SLUG #1821] Out-of-domain recovery + per-tool budget backstop (#1815 fixes #5/#6). Not touched here.
- [SEPARATE-SLUG #1829] LLM refusal-complement for memory extraction (follow-up of #1822). D3 only makes the exception paths observable; it does not extend refusal filtering.
- [EXTERNAL] Rotating the revoked IMAP app password that A3 detects — A3 *alerts* on it; a human must rotate the credential and re-auth `gws`. The alert is the deliverable; the rotation is a human action.
- [EXTERNAL] Fixing cross-machine clock skew at the OS/NTP level — C2 stops trusting wall-clock deltas; actually syncing the clocks is an operator/infra action.
- [ORDERED] Flipping `CLAUDE_CONTRACT_CHECK_ENFORCE` from warn to hard-fail — waits until one release of warn-mode telemetry confirms no false-positives on the live bridge machines.

## Update System

D1 has real update-system impact; the rest are internal.

- **D1a version pin (`scripts/update/verify.py`) — RESOLVED (OQ3):** the live `claude` (v2.1.197)
  is at `~/.local/bin/claude` → `~/.local/share/claude/versions/2.1.197` via the **native
  installer**, NOT npm, and is NOT in `MANAGED_PACKAGES`. Adding it to `MANAGED_PACKAGES` is the
  wrong mechanism (it would either no-op or force a fleet-wide switch to the npm install path).
  Resolution: add a **version-assertion** step to `scripts/update/verify.py` that reads the
  installed version (`claude --version` or the resolved version-dir symlink target) and compares it
  to a pinned constant `PINNED_CLAUDE_VERSION` regardless of install method — warn on drift by
  default, block behind an env flag. `/update` gains a claude-version gate that is native-installer
  compatible. Document the pinned version + bump procedure in `docs/features/`. NO
  `scripts/update/npm_tools.py` / `MANAGED_PACKAGES` change.
- **D1b contract-check is a SEPARATE PR** from the pin — it is a marker-drift probe in
  `worker/__main__.py`, not an update-system change. No `scripts/update/` change for D1b.
- **A1 Popoto migration (`scripts/update/migrations.py`):** dropping `queued_steering_messages`
  requires an idempotent migration registered in the `MIGRATIONS` dict — RPUSH any residual
  ListField content onto the Redis steering list, then remove the field. Recorded once in
  `data/migrations_completed.json`; uses `instance.save()` / `rebuild_indexes()`, never raw Redis.
- **C3 index reconciliation:** if implemented as a member-TTL rather than reconcile-on-read, a
  one-shot migration may be needed to expire existing orphaned members; reconcile-on-read needs
  no migration.
- **New env vars** (`CLAIM_TTL_SECONDS` (B1 provisional gate — SECONDS, decoupled from the 2h
  membership; NOT sized to sync lag), `EMAIL_RESOLVER_ALERT_AFTER` (A2 resolver-unavailable alert
  threshold, Concern 2), `PINNED_CLAUDE_VERSION`, `CLAUDE_CONTRACT_CHECK_ENFORCE`,
  `NOTIFY_HEALTHCHECK_INTERVAL` (D4 off-path probe interval — NOT a socket timeout), email
  watchdog threshold): all optional with safe defaults; add to `.env.example` with a comment
  line above each (completeness-check requirement) only for operator discoverability.
- No new services. Bridge/worker restarted via the standard `./scripts/valor-service.sh restart`
  after each PR merges.

## Agent Integration

Mostly bridge/worker-internal. Specific surfaces:

- **A2/A3 operator alerts + email watchdog:** BOTH the A3 permanent-IMAP-auth alert AND the A2
  persistent-resolver-unavailable alert (Concern 2) must reach an operator surface. Reuse the
  existing crash/alert path (`monitoring/crash_tracker.py` / the dashboard health JSON at
  `localhost:8500/dashboard.json`) rather than inventing a new surface — add an `email` health field
  (the dashboard already-consumes shape can render) that exposes both `email:auth_failed` (A3) and
  `email:resolver_unavailable` (A2). No new CLI/MCP tool.
- **D1 contract-check** surfaces via `logs/worker.log` + refusal-to-serve; optionally add a
  `python -m tools.doctor` check that runs the same probe (agent-invocable via Bash). This is
  the only optional new agent-reachable surface.
- No `.mcp.json` changes. No new `[project.scripts]` entry point required (the doctor check, if
  added, extends an existing tool). The bridge does not import new modules beyond the guarded
  config loader (C4) it already imports from `bridge/routing.py`.
- **Integration test:** a test that the dashboard JSON exposes the email alert field when either
  the `email:auth_failed` (A3) or `email:resolver_unavailable` (A2, Concern 2) key is set,
  verifying the agent/operator can actually observe both.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/delivery-integrity-hardening.md` describing all four workstreams: the steering-primitive consolidation (A1), the email resolver/IMAP classification + watchdog (A2/A3), the atomic claims (B1/B2), the finalize/freshness/ghost/config integrity fixes (C1–C4), and the brittleness fixes (D1–D4). Explain each as a "silent failure → loud/atomic" conversion.
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/session-steering.md` to state the Redis list (`agent/steering.py`) is now the SOLE steering inbox and `queued_steering_messages` is removed (no-historical-artifacts: describe the new status quo).
- [ ] Update `docs/features/email-bridge.md` with the resolver-unavailable vs non-customer distinction and the permanent-auth alert/watchdog.
- [ ] Document the D1a pinned `claude` version + bump procedure, noting the **native installer** path (`~/.local/bin/claude` → `~/.local/share/claude/versions/<ver>`) and the `PINNED_CLAUDE_VERSION` assertion in `scripts/update/verify.py` (NOT `MANAGED_PACKAGES`) — in the feature doc or `docs/deployment.md`.
- [ ] In the feature doc, describe the D4 notify-listener design: `socket_timeout=None` is PRESERVED on the blocking `listen()` connection (a prior finite timeout was rejected — it caused spurious timeouts + dropped notifications), and post-subscribe drift is detected by an OFF-PATH periodic `PUBSUB NUMSUB` liveness probe that resubscribes only on a confirmed drop.

### Inline Documentation
- [ ] Grain-of-salt comments on all provisional constants (`CLAIM_TTL`, `PINNED_CLAUDE_VERSION`, contract-check enforce flag, `NOTIFY_HEALTHCHECK_INTERVAL` (D4 off-path probe interval), email watchdog threshold).
- [ ] Comment at the D4 `listen()` connection reaffirming WHY `socket_timeout=None` is retained (cross-reference the existing @822-828 rationale) so a future maintainer doesn't "helpfully" add a finite timeout.
- [ ] Comment the B1/B2 atomic-claim rationale at each seam: for B1, why the `claim_message` gate is a SHORT provisional TTL (`CLAIM_TTL_SECONDS`) decoupled from the 2h membership and why it MUST be released on any enqueue exception (a long-lived orphaned gate silently re-drops the message — the Round-3 BLOCKER); for B2, why SETNX/WATCH replaces the Python CAS.

## Success Criteria

- [ ] **A1:** A steer pushed from a second process while a stale worker instance holds a bound session is drained at the next turn boundary (never clobbered). `queued_steering_messages` field + its two methods are deleted; zero references remain outside the migration. The single-consumer invariant of the non-atomic LPOP drain is explicitly tested (two concurrent drainers of one session_id split messages disjointly).
- [ ] **A2:** A simulated resolver/OAuth error leaves the customer email **unseen** and logged; only a definitively-resolved non-customer is `\Seen`-dropped. A *persistent* resolver-unavailable condition (expired OAuth token across the alert threshold) arms the `email:resolver_unavailable` operator alert on the dashboard health surface (matching A3, not log-only) and clears it on the first successful resolve (Concern 2).
- [ ] **A3:** A permanent `IMAP4.error` stops the infinite backoff and raises an operator alert (visible on the dashboard/alert surface); transient errors still back off.
- [ ] **B1:** Two concurrent dispatches of the same `(chat_id, message_id)` result in exactly one enqueue (`SET NX` provisional gate), verified under a simulated config-sync-lag test — on the live path AND across the catchup/reconciler recovery paths (a message claimed on any path is not double-enqueued by a racing recovery scan).
- [ ] **B1 fail-safe (BLOCKER, Round 3):** When `enqueue` raises between claim and enqueue, the claim key is DELETED and the message is NOT permanently dropped — a subsequent reconciler scan re-acquires the gate and re-enqueues (fault-injection test). When the process DIES between claim and enqueue (no delete), the short `CLAIM_TTL_SECONDS` gate self-heals within seconds and the next reconciler scan re-enqueues (no ~1h orphan). The gate TTL is in SECONDS, decoupled from the 2h membership set.
- [ ] **B2:** Two concurrent pending→running claimants result in exactly one `running` transition; the loser gets `StatusConflictError`. The Python re-read+compare is deleted. `register_worker_pid` never refuses or exits on a duplicate pid key: a dead/stale pid (failed `os.kill(pid, 0)` or stale heartbeat) is silently superseded so a launchd-respawned worker is never blocked; only a *confirmed-live* second worker on the same host+role emits a WARNING and supersedes. A test asserts a stale-pid key does NOT block registration (respawn-safety), the worker never flags `os.getpid()` against itself, and cross-host/cross-role pids are not treated as conflicts.
- [ ] **C1:** A crash injected after the child save (parent still `waiting_for_children`) leaves no permanently-stranded parent — the idempotent startup sweep re-finalizes it. The child-independent finalize contract is preserved: a parent-finalize failure never blocks or rolls back the child finalize (no pipeline coupling).
- [ ] **C2:** Under simulated clock skew (reader ≥90s ahead), fresh sessions are NOT flagged stale and `_heal_future_updated_at` does not re-save/reshuffle the index.
- [ ] **C3:** `query.filter()` over an index with a ghost member returns only live records; email subject-coalescing cannot attach to a non-existent session.
- [ ] **C4:** A partial/corrupt `projects.json` read falls back to last-known-good and logs, instead of crash-looping the bridge under launchd.
- [ ] **D1a:** The native-installed `claude` CLI version is pinned via a `PINNED_CLAUDE_VERSION` assertion in `scripts/update/verify.py` (NOT `MANAGED_PACKAGES`); a version mismatch warns (default) or blocks (env-gated), install-method agnostic.
- [ ] **D1b:** A startup contract-check fails loudly (log + refuse/alert) when a scraped TUI marker is absent, WHEN at least one configured role is PTY-transport; a fully-headless fleet (#1842) log-and-skips the check (headless carries no PTY bytes) — a separable PR from the pin.
- [ ] **D2:** A `dev.spawn()` failure after a successful `pm.spawn()` leaves the `pm` pid persisted and reapable; a #1842 headless role (no PTY) persists no pid and is not treated as an orphan; the reaper regex matches the native/PTY `claude` TUI WITHOUT reaping an in-flight headless `claude -p` turn (governed by `_is_stale_print_oneshot`).
- [ ] **D3:** Circuit-breaker writes, bridge emoji/classify tasks, and memory-extraction handlers are awaited/held and log on failure (no lost breaker trips, no GC'd tasks, no silent swallow).
- [ ] **D4:** A dropped notify subscription is detected + resubscribed within `NOTIFY_HEALTHCHECK_INTERVAL` (seconds) by an off-path PUBSUB NUMSUB probe, logged as a WARNING — not the 300s backstop. The `listen()` connection retains `socket_timeout=None` (the rejected finite-timeout design is NOT reintroduced); resubscribe fires only on a confirmed NUMSUB==0.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`): `docs/features/delivery-integrity-hardening.md` exists.

## Team Orchestration

The lead orchestrates; it never builds directly. Each workstream is a builder+validator pair;
most run in parallel (see PR-split). Paste the async + Redis/Popoto rules from
`DOMAIN_FRAMING.md` into each build task (`Domain: async-concurrency` / `Domain: redis-popoto`).

### Team Members

- **Builder (steering-consolidation)** — Name: `steering-builder`; Role: A1; Agent Type: builder; Domain: redis-popoto, async-concurrency; Resume: true
- **Builder (email-intake)** — Name: `email-builder`; Role: A2+A3; Agent Type: builder; Domain: untrusted-input; Resume: true
- **Builder (atomic-claims)** — Name: `claims-builder`; Role: B1+B2; Agent Type: builder; Domain: redis-popoto, async-concurrency; Resume: true
- **Builder (data-integrity)** — Name: `integrity-builder`; Role: C1+C2+C3+C4; Agent Type: builder; Domain: redis-popoto; Resume: true
- **Builder (brittleness)** — Name: `brittleness-builder`; Role: D1+D2+D3+D4; Agent Type: builder; Domain: async-concurrency; Resume: true
- **Validator (resilience)** — Name: `resilience-validator`; Role: verify all Success Criteria + Failure-Path items; Agent Type: validator; Resume: true
- **Documentarian** — Name: `integrity-doc`; Role: feature doc + index + steering/email doc updates; Agent Type: documentarian; Resume: true

### Available Agent Types

Tier 1 (`builder`, `validator`, `code-reviewer`, `test-engineer`, `documentarian`) with
`DOMAIN_FRAMING.md` rules pasted per task. No standing specialist pool.

## Step by Step Tasks

**PR-split proposal (which findings ship together, and ordering):**

| PR | Findings | Files | Independent? | Notes |
|----|----------|-------|--------------|-------|
| PR1 | A1 | session_executor, agent_session, telegram_bridge, health_check, valor_session, migrations | ✅ parallel | Includes Popoto migration; largest deletion. |
| PR2 | A2+A3 | routing, email_bridge, monitoring | ✅ parallel | Email-domain; A2+A3 share files. |
| PR3 | B1 | dedup, dispatch, catchup, reconciler | ✅ parallel | Folds atomic claim into existing dedup; extends the claim to the catchup/reconciler recovery enqueue sites (Concern 1). |
| PR4 | B2 (atomic status claim — correctness) | session_lifecycle (604-648) | ⚠️ coordinate with PR5 | The correctness-critical double-exec fix ONLY. Kept minimal. Edits `session_lifecycle.py`; land before PR5. |
| PR4b | B2-probe (pid observability — optional) | session_health (register_worker_pid) | ✅ parallel | SEPARATED from PR4 (Concern 4): the observability-only, liveness-gated pid probe. Non-correctness; can land after PR4 or be dropped entirely (the status claim is the load-bearing fix). |
| PR5 | C1 | session_lifecycle (221/440-474), worker/__main__ | ⚠️ after PR4 | Shares `session_lifecycle.py` with B2. Idempotent sweep only; preserves child-independent finalize (no coupling). |
| PR6 | C2 | agent_session (973-1037), session_health | ✅ parallel | Independent of PR4/PR5 regions. |
| PR7 | C3 | dedup model, agent_session, indexes | ✅ parallel | |
| PR8 | C4 | routing (134-135) | ✅ parallel | Cheap, high-value; can land first. |
| PR9a | D1a (pin) | verify.py | ✅ parallel | Version-assertion pin, native-installer compatible. NO npm_tools/MANAGED_PACKAGES change. Update-system impact. |
| PR9b | D1b (contract-check) | worker/__main__, pty_driver | ✅ parallel | Marker-drift startup probe; env-gated enforce. Separable from PR9a. |
| PR10 | D2 | pty_pool, session_health (reaper regex) | ⚠️ mild overlap with PR4/PR6 on session_health | Coordinate the `session_health.py` edits. |
| PR11 | D3 | sdk_client, telegram_bridge, memory_extraction | ✅ parallel | |
| PR12 | D4 | agent_session_queue | ✅ parallel | Off-path NUMSUB liveness probe; keeps `socket_timeout=None` (no finite-timeout reintroduction). |

**Ordered constraints:** PR4 → PR5 (shared `session_lifecycle.py`; PR4 is now the status-claim ONLY,
Concern 4). PR4b/PR6/PR10 all touch `session_health.py` in different regions — sequence or rebase to
avoid collisions (PR4 no longer touches `session_health.py` — the pid probe moved to PR4b). Everything
else is parallel-safe. Cheapest high-value first: **PR8 (C4)** and **PR3 (B1)**. PR4b is optional and
can land last or be dropped.

### 1. A1 — Steering consolidation (PR1)
- **Task ID**: build-a1-steering
- **Depends On**: none
- **Validates**: tests/**/test_*steering*.py (REPLACE), new atomic-drain test
- **Assigned To**: steering-builder
- **Agent Type**: builder — Domain: redis-popoto, async-concurrency
- **Parallel**: true
- Repoint `session_executor.py:1675` to `pop_all_steering_messages(session.session_id)`; delete the redundant ListField dual-writes @telegram_bridge:946, session_executor:679/1686. **Do NOT delete `health_check.py:573`** — it is a CLI-harness DELIVERY write (Concern 1); REPOINT it (and collapse the `:573`/`:585` branches) to a single `_repush_messages(session_id, non_abort)` Redis-list RPUSH, and update the `_handle_steering` docstring. Fix resume path valor_session:725-728/916/950.
- Delete `queued_steering_messages` field + methods (agent_session.py:225/2046/2085); remove from agent_session_queue.py:182 serialization.
- Add idempotent Popoto migration (RPUSH residual → drop field) in `scripts/update/migrations.py`.

### 2. A2+A3 — Email intake classification + alert (PR2)
- **Task ID**: build-a2-a3-email
- **Depends On**: none
- **Validates**: tests/**/test_routing*.py, tests/**/test_email*.py (UPDATE/ADD)
- **Assigned To**: email-builder
- **Agent Type**: builder — Domain: untrusted-input
- **Parallel**: true
- `resolve_customer` raises `ResolverUnavailable` on infra error; `_process_inbound_email` leaves unseen + logs on that branch.
- Classify permanent `IMAP4.error`; stop backoff; alert; add email-poll staleness watchdog signal to dashboard health.

### 3. B1 — Atomic per-message claim (PR3)
- **Task ID**: build-b1-claim
- **Depends On**: none
- **Validates**: tests/**/test_dedup.py, tests/**/test_dispatch.py (UPDATE)
- **Assigned To**: claims-builder
- **Agent Type**: builder — Domain: redis-popoto, async-concurrency
- **Parallel**: true
- Add `claim_message` (`SET NX`, `CLAIM_TTL`) to `bridge/dedup.py`; gate `dispatch_telegram_session` + `record_telegram_message_handled` before enqueue. Keep membership set for catchup.
- Extend the claim to the recovery paths (Concern 1): add `claim_message` before the enqueue sites in `catchup.py:~257` and `reconciler.py:~239` (shared claim key, in-line — not via the wrapper, preserving the documented recovery two-step contract); loser skips enqueue but records dedup. Update the `dispatch.py` module docstring to note recovery paths now also claim.

### 4. B2 — Atomic pending→running claim (PR4 — correctness only)
- **Task ID**: build-b2-claim
- **Depends On**: none (but merge before build-c1-finalize)
- **Validates**: tests/**/test_session_lifecycle*.py (UPDATE)
- **Assigned To**: claims-builder
- **Agent Type**: builder — Domain: redis-popoto, async-concurrency
- **Parallel**: true (coordinate merge order with task 5)
- **Kept MINIMAL (Concern 4):** ONLY the correctness-critical atomic status claim. Replace the Python CAS @604-648 with WATCH/MULTI or a SETNX run-claim; delete the compare. This alone is the double-exec fix (exactly one actor per session). Does NOT touch `session_health.py` — the pid probe is split into task 4b.

### 4b. B2-probe — Observability-only pid probe (PR4b — separated, optional)
- **Task ID**: build-b2-pid-probe
- **Depends On**: none (may land after build-b2-claim; independently revertible/droppable)
- **Validates**: tests/**/test_session_health*.py (ADD)
- **Assigned To**: claims-builder
- **Agent Type**: builder — Domain: redis-popoto
- **Parallel**: true
- SEPARATED from PR4 (Concern 4) so the correctness fix stays minimal. For `register_worker_pid` @session_health:2981 add ONLY an observability-only, liveness-gated log-and-supersede probe (scope to host+role, exclude `os.getpid()`, liveness-check the competitor via `os.kill(pid, 0)` + heartbeat freshness, silently supersede a dead/stale pid, WARNING only on a confirmed-live duplicate) — NEVER refuse/`sys.exit` on key presence, so a launchd-respawned worker is never wedged by a dead pid's residual TTL'd key. Dropping this PR entirely is an acceptable fallback (the PR4 status claim is the load-bearing fix).

### 5. C1 — Atomic finalize + sweep (PR5)
- **Task ID**: build-c1-finalize
- **Depends On**: build-b2-claim (shared session_lifecycle.py)
- **Validates**: tests/**/test_*finalize*.py, waiting_for_children tests (UPDATE)
- **Assigned To**: integrity-builder
- **Agent Type**: builder — Domain: redis-popoto
- **Parallel**: false (after PR4)
- PRESERVE the child-independent, best-effort parent finalize @440-474 (do NOT couple the writes); add an idempotent worker-startup sweep that re-invokes `_finalize_parent_sync` for parents stranded in `waiting_for_children` whose children are all terminal.

### 6. C2 — Monotonic freshness (PR6)
- **Task ID**: build-c2-freshness
- **Depends On**: none
- **Validates**: tests/**/test_session_health*.py (UPDATE)
- **Assigned To**: integrity-builder
- **Agent Type**: builder — Domain: redis-popoto
- **Parallel**: true (coordinate session_health.py edits with PR4/PR10)
- Stop re-save in `_heal_future_updated_at`; relative-age staleness.

### 7. C3 — Ghost-member reconciliation (PR7)
- **Task ID**: build-c3-ghosts
- **Depends On**: none
- **Validates**: new ghost-member test
- **Assigned To**: integrity-builder
- **Agent Type**: builder — Domain: redis-popoto
- **Parallel**: true
- Reconcile-on-read (preferred) or member TTL; drop ghost members from `query.filter()`.

### 8. C4 — Guarded config read (PR8 — land early)
- **Task ID**: build-c4-config
- **Depends On**: none
- **Validates**: tests/**/test_routing*.py (config cases)
- **Assigned To**: integrity-builder
- **Agent Type**: builder — Domain: untrusted-input
- **Parallel**: true
- Guarded loader @routing.py:134-135 with atomic last-known-good sidecar; never raise at import.

### 9. D1 — Native-installer version-assertion pin (PR9a) + startup contract-check (PR9b)
- **Task ID**: build-d1-pin
- **Depends On**: none
- **Validates**: version-assertion update-system test (D1a); contract-check unit test (D1b)
- **Assigned To**: brittleness-builder
- **Agent Type**: builder — Domain: async-concurrency
- **Parallel**: true (two separable PRs)
- **D1a (PR9a):** add a `PINNED_CLAUDE_VERSION` version-assertion step to `scripts/update/verify.py`
  comparing `claude --version` (native install at `~/.local/bin/claude` → version-dir symlink) to the
  pin; warn on drift, env-gated block. Do NOT add to `MANAGED_PACKAGES` (OQ3 resolved: native install, not npm).
- **D1b (PR9b):** startup contract-check @`worker/__main__.py:726` asserting `pty_driver.py` markers
  (`IDLE_BAR`@99/`PROMPT_GLYPH`@100/`SPINNER_EVIDENCE_RE`@116) + trust-folder patterns (`startup_parser.py:117-121`)
  still match; env-gated enforce. **Scope to PTY transport (Concern 3):** gate the hard-fail on "at least
  one configured role is `pty`-transport" (per #1842's `pm_transport`/`dev_transport`) — a fully-headless
  fleet is immune to TUI-marker drift, so the check log-and-skips there.

### 10. D2 — Immediate pid record + reaper regex (PR10)
- **Task ID**: build-d2-pids
- **Depends On**: none (coordinate session_health.py with PR4/PR6)
- **Validates**: tests/**/test_pty_pool*.py (UPDATE)
- **Assigned To**: brittleness-builder
- **Agent Type**: builder — Domain: async-concurrency
- **Parallel**: true
- Record pid immediately after each spawn @pty_pool:527-556 (None-aware — a #1842 headless role has no PTY pid; guard `pty is not None and pty._child is not None`); broaden `_CLAUDE_CMDLINE_RE` @session_health:58 to the native/PTY `claude` TUI cmdline WITHOUT reaping in-flight headless `claude -p` turns (already governed by `_is_stale_print_oneshot`@3880).

### 11. D3 — Hold fire-and-forget tasks (PR11)
- **Task ID**: build-d3-tasks
- **Depends On**: none
- **Validates**: caplog assertions for circuit/bridge/memory paths
- **Assigned To**: brittleness-builder
- **Agent Type**: builder — Domain: async-concurrency
- **Parallel**: true
- Await/hold circuit writes @sdk_client:1842/1857/1923; append emoji/classify tasks to `_background_tasks` @telegram_bridge:1617/1637; log in memory_extraction handlers @328/537/560/791/1029.

### 12. D4 — Notify-listener liveness probe (PR12)
- **Task ID**: build-d4-listener
- **Depends On**: none
- **Validates**: tests/**/test_agent_session_queue*.py (UPDATE/ADD)
- **Assigned To**: brittleness-builder
- **Agent Type**: builder — Domain: async-concurrency
- **Parallel**: true
- KEEP `socket_timeout=None` @agent_session_queue:851 (do NOT reintroduce the rejected finite timeout — see Blocker/Technical Approach D4). Add an off-path periodic `PUBSUB NUMSUB` liveness probe (`NOTIFY_HEALTHCHECK_INTERVAL`, provisional ~15s) on a SEPARATE connection; on a confirmed NUMSUB==0 signal the listener to resubscribe + log a WARNING; a transient probe error does not tear down the listener.

### 13. Validate all
- **Task ID**: validate-all
- **Depends On**: all build tasks + document-integrity
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table; confirm every Success Criterion + Failure-Path item; confirm docs exist.

### 14. Documentation
- **Task ID**: document-integrity
- **Depends On**: the build tasks whose surface it documents
- **Assigned To**: integrity-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/delivery-integrity-hardening.md`; update session-steering + email-bridge docs; README index.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| A1: ListField field deleted | `grep -rn "queued_steering_messages = ListField" models/agent_session.py` | exit code 1 |
| A1: no stray field refs outside migration | `grep -rn "queued_steering_messages" agent/ bridge/ models/ tools/ worker/ \| grep -v migrations` | match count == 0 |
| A1: turn-boundary pop uses atomic primitive | `grep -c "pop_all_steering_messages" agent/session_executor.py` | output > 0 |
| A2: resolver-unavailable branch exists | `grep -c "ResolverUnavailable" bridge/routing.py bridge/email_bridge.py` | output > 0 |
| A2: resolver-unavailable operator alert (Concern 2) | `grep -c "resolver_unavailable" bridge/email_bridge.py` | output > 0 |
| A3: permanent-auth classification | `grep -cE "AUTHENTICATIONFAILED\|Invalid credentials\|auth_failed" bridge/email_bridge.py` | output > 0 |
| B1: provisional gate in dedup | `grep -c "def claim_message" bridge/dedup.py` | output > 0 |
| B1: gate has a release/DELETE fail-safe | `grep -c "def release_message_claim" bridge/dedup.py` | output > 0 |
| B1: gate TTL is seconds, decoupled from 2h membership | `grep -c "CLAIM_TTL_SECONDS" bridge/dedup.py` | output > 0 |
| B1: dispatch releases gate on enqueue exception (BLOCKER) | `grep -c "release_message_claim" bridge/dispatch.py` | output > 0 |
| B1: gate gates dispatch | `grep -c "claim_message" bridge/dispatch.py` | output > 0 |
| B1: gate gates + releases on recovery paths | `grep -cE "claim_message\|release_message_claim" bridge/catchup.py bridge/reconciler.py` | output > 0 |
| B2: atomic claim replaces CAS | `grep -cE "watch\|multi\|nx=True" models/session_lifecycle.py` | output > 0 |
| B2: pid probe supersede-only (liveness-gated) | `grep -cE "supersede\|os\.kill\|liveness" agent/session_health.py` | output > 0 |
| B2: pid probe never refuses/exits (anti-criterion) | `sed -n '/def register_worker_pid/,/^def [a-zA-Z_]/p' agent/session_health.py \| grep -cE "sys\.exit\|raise .*Conflict\|refuse"` | output == 0 |
| C1: idempotent finalize sweep on startup | `grep -cE "waiting_for_children" worker/__main__.py` | output > 0 |
| C1: child-independent finalize preserved (no coupling) | `grep -c "Parent finalization failed (non-fatal)" models/session_lifecycle.py` | output > 0 |
| C2: heal no longer re-saves (anti-criterion, function-scoped) | `sed -n '/def _heal_future_updated_at/,/^    def [a-zA-Z_]/p' models/agent_session.py \| grep -c "record.save()"` | output == 0 |
| C4: guarded config read | `grep -cE "last_known_good\|JSONDecodeError" bridge/routing.py` | output > 0 |
| D1a: claude version-assertion pin (native-installer, NOT npm) | `grep -c "PINNED_CLAUDE_VERSION" scripts/update/verify.py` | output > 0 |
| D1a: NOT added to MANAGED_PACKAGES (anti-criterion) | `grep -c "anthropic-ai/claude-code" scripts/update/npm_tools.py` | output == 0 |
| D1b: contract-check present | `grep -cE "contract.check\|IDLE_BAR\|marker" worker/__main__.py` | output > 0 |
| D1b: contract-check scoped to PTY transport (Concern 3) | `grep -cE "transport\|headless" worker/__main__.py` | output > 0 |
| D2: reaper regex broadened | `grep -c "claude" agent/session_health.py` | output > 0 |
| D2: pid record None-aware for headless (Concern 3) | `grep -cE "is not None" agent/granite_container/pty_pool.py` | output > 0 |
| D3: bridge tasks held | `grep -c "_background_tasks.append" bridge/telegram_bridge.py` | output > 0 |
| D4: periodic pubsub health-check (NOT a finite socket_timeout) | `grep -cE "pubsub_numsub\|HEALTHCHECK\|health.check\|resubscribe" agent/agent_session_queue.py` | output > 0 |
| D4: socket_timeout=None preserved (anti-criterion — no finite reintroduction) | `grep -c "socket_timeout=None" agent/agent_session_queue.py` | output > 0 |
| Feature doc exists | `test -f docs/features/delivery-integrity-hardening.md && echo found` | output contains found |
| No #1820 lease smuggled in | `grep -rcE "owner_session_id\|slot_lease\|lease_ttl" models/session_lifecycle.py` | match count == 0 |

Note: the `grep -c claude` row is a presence sanity check, not a strict anti-criterion; the
builder tunes the exact expected count to the final diff. The anti-criteria (expected 0 or, for
`socket_timeout=None`, expected-preserved) are: A1 no-stray-refs; B2 pid-probe-never-refuses (the
`register_worker_pid` function-scoped grep for `sys.exit`/`raise …Conflict`/`refuse` must be 0 —
the probe is supersede-only, so it can never wedge the launchd respawn loop); C2 heal function-scoped
no-save; D1a not-in-MANAGED_PACKAGES; D4 finite-socket_timeout-not-reintroduced (the `socket_timeout=None`
row asserts the deliberate design at `agent_session_queue.py:851` is preserved — see Blocker D4);
and no-#1820-lease. For each expected-0 anti-criterion, demonstrate it FAILS against a
deliberately-violating input first (red-state proof) and paste the FAIL output into the PR
description. The C2 row already returns 1 against the current HEAD (the pre-fix `record.save()`
at `models/agent_session.py:1060` exists), so its red-state is the baseline itself.

## Critique Results

### Round 3 (2026-07-02, FULL critique @HEAD `199bda9a` (revised @HEAD `f8544f0a`) → NEEDS REVISION → revised)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker | Critique | B1 claim-before-enqueue orphans the claim key and re-creates the silent-drop it exists to eliminate: the plan sized `CLAIM_TTL` to the ~1h sync-lag window and gave recovery paths the SAME key. If `enqueue` raises or the process dies between claim and enqueue, the claim key persists ~1h with no session enqueued; catchup/reconciler then hit the orphaned key, `SET NX` fails, they skip ("a peer won" — none did), and the message is silently dropped — recovery defeated by the plan's own shared key. `bridge/dispatch.py:99-106` documents the "enqueue-then-record; a raised enqueue leaves dedup unrecorded so the reconciler retries" contract the claim-before-enqueue broke. | Technical Approach B1, Data Flow B1 (steps 3-5), Risk 2, Race 2, Success Criteria B1, Failure-Path (B1), Test Impact (dispatch/catchup/reconciler fault-injection), Verification (3 new rows), Update System env vars, Open Question 1, Build task 3 | Redefined the claim as a PROVISIONAL concurrency gate: a SHORT TTL in **seconds** (`CLAIM_TTL_SECONDS`, provisional ~60s), sized to cross-actor processing skew and DECOUPLED from the 2h membership set (the durable set — not the gate — covers the sync-lag/replay window). Added `release_message_claim` (DELETE) and wrapped every enqueue site (dispatch + both recovery paths) so ANY enqueue exception DELETEs the gate before re-raising — no orphan on a handled failure; a process death mid-window self-heals via the short TTL and the next reconciler scan re-enqueues. Kept the SHARED gate key across live + recovery (a distinct per-path key would REOPEN the cross-path double-enqueue race); safety comes from the short TTL + delete-on-exception, not from splitting the key. Added a fault-injection test. Risk 2 inverted (the long-TTL orphan IS the hazard). |
| Concern | Critique | A1 — the delete target at `health_check.py:573` is a CLI-harness DELIVERY write, NOT a redundant dual-write; deleting it can LOSE steers. | Technical Approach A1 (health_check bullet), Build task 1, Test Impact (new health_check delivery test) | Re-verified: `_handle_steering` (`agent/health_check.py:490`) LPOPs the Redis list (`:507`) and, when there is no active SDK client (CLI-harness), must RE-DELIVER popped non-abort messages to the turn-boundary inbox — `:573` is that delivery write, not a producer-side dual-write. Changed from DELETE to REPOINT: collapse the `:573`/`:585` branches to a single `_repush_messages(session_id, non_abort)` Redis-list RPUSH (post-A1 the worker reads the Redis list); update the `_handle_steering` docstring. Only the genuine dual-writes (`telegram_bridge.py:946`, `session_executor.py:679/1686`) are deleted. |
| Concern | Critique | A2 — the flagship OAuth-expiry case ends log-only, with no operator alert (unlike sibling A3 which alerts). | Technical Approach A2, Solution A2, Data Flow A2, Success Criteria A2, Failure-Path (A2), Agent Integration, Verification (new A2 row), Build task 2, Update System env vars | Added a threshold-gated operator alert `email:resolver_unavailable` (+ `logger.critical`) on the `ResolverUnavailable` branch, surfaced on the same dashboard health field as A3's `email:auth_failed`, armed only after N consecutive unavailable polls (`EMAIL_RESOLVER_ALERT_AFTER`, provisional 3) and cleared on the first successful resolve — so an expired OAuth token is loud, not log-only, without paging on a transient blip. |
| Concern | Critique | Stale prior art — merged #1842 (per-role transport hedge) stales D1b/D2 anchors and BREAKS D2's premise for `headless` roles that have no pid. | Freshness Check (sibling-PR list + D1/D2/C2 anchor rows + disposition), Technical Approach D1b + D2, Solution D1/D2, Success Criteria D1b/D2, Test Impact (pty_pool), Build tasks 9/10, Verification (2 new scope rows) | Re-verified against HEAD `f8544f0a`: #1842 (PR #1848) + #1688 (PR #1847) moved anchors (`worker/__main__.py:712→726`; `pty_driver.py` markers `95,96,112-115→99,100,116`; pty_pool spawn `523-530→527-556`) and made each role config-selectable `pty` vs `headless`. Adapted D2: a headless role has `pm`/`dev`=`None` (no PTY, no pid) — the per-spawn pid record is None-guarded and a fully-headless pair records an empty pid set (not an orphan); the reaper broadening must not reap in-flight headless `claude -p` turns (already governed by `_is_stale_print_oneshot`@3880). Scoped D1b: the TUI-marker contract-check governs only PTY-transport roles (a headless turn carries no PTY bytes) and log-and-skips a fully-headless fleet, gated on "≥1 role is PTY-transport". |
| Concern | Critique | B2-probe bundling — the B2 observability pid-probe is bundled into the correctness-critical PR4; separate it so PR4 stays minimal (the atomic status claim is the correctness fix). | PR-split table (PR4 split into PR4 + PR4b), Solution B2, Technical Approach B2 (split note), Build tasks (task 4 + new task 4b), Ordered-constraints note, Test Impact (PR4b annotation) | Split PR4 into PR4 (atomic status claim in `session_lifecycle.py` — correctness only, the load-bearing double-exec fix) and PR4b (observability-only pid probe in `session_health.py` — optional, droppable). PR4 no longer touches `session_health.py`, so the session_health region-collision coordination now applies to PR4b/PR6/PR10. |
| Nit | Critique | C2 anchor drift — `_heal_future_updated_at` moved. | Freshness Check (C2 heal row), Technical Approach C2, Verification note | Corrected: `_heal_future_updated_at`@`agent_session.py:1004-1070`, `record.save()`@1060 (drifted from `973-1037`/`:1029`); the C2 verification red-state note now cites `:1060`. |

### Round 2 (2026-07-02, FULL critique @HEAD `3514ed1b` → NEEDS REVISION → revised)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker | Critique | B2 `register_worker_pid` singleton guard "refuse (or log-and-supersede) a second live worker pid" is fleet-hazardous: after an unclean worker exit under launchd `KeepAlive`, the dead pid's TTL'd key still exists, so a refuse-guard blocks the healthy respawned worker for the full TTL window — defeating the self-healing respawn. Both critics also note the guard is largely redundant with the atomic pending→running status claim (`session_lifecycle.py:604-648`), which already guarantees one actor per session. | Solution B2, Data Flow B2, Technical Approach B2, Risk 3b, Test Impact, Success Criteria B2, Verification (2 rows + anti-criteria note), Build task 4, Prior Art #1271 | Dropped "refuse". The atomic status claim is named as the real double-exec fix. `register_worker_pid` keeps ONLY an observability-only, liveness-gated log-and-supersede probe: scope to host+role, exclude `os.getpid()`, liveness-check the competitor via `os.kill(pid, 0)` + heartbeat freshness, silently supersede any dead/stale pid (the exact launchd-respawn case), WARN + supersede only on a confirmed-live duplicate — never `sys.exit`/refuse on key presence. Anti-criterion added: function-scoped grep for `sys.exit`/`raise …Conflict`/`refuse` in `register_worker_pid` must be 0. Dropping the probe entirely is recorded as an acceptable equivalent-correctness fallback. |
| Concern×4 | Critique | Four implementation-detail concerns on the B2 pid probe (relayed as "embed the implementation notes into the plan body"): (1) scope the comparison to host+role so cross-machine/cross-role pids are not conflicts; (2) exclude `os.getpid()` so a worker never flags itself; (3) liveness-check the competitor (`os.kill(pid, 0)` + heartbeat freshness) before treating it as a conflict; (4) never treat key presence alone as a conflict — supersede dead/stale pids silently. | Technical Approach B2 (five liveness-gating bullets), Success Criteria B2, Test Impact (`test_session_health*`), Verification | All four embedded verbatim into the B2 Technical Approach as explicit gating rules and into the Success Criteria + respawn-safety test. NOTE: the round-2 critique run artifact was garbage-collected before this revision, so the four concerns are recorded as relayed by the supervisor; each maps to a concrete gating rule now in the plan body. |
| Nit | Critique | Anchor drift contradicts the plan's "re-verified" Freshness claim: A1 anchors moved (`session_executor.py` `:1584→:1675`, `:2012→:2106`; also `:647→:679`, `:1595→:1686`; `agent_session.py` methods `2015/2054→2046/2085`; `bridge_adapter.py` `:536→:624`). | Freshness Check (table + disposition + post-critique re-verification), Data Flow A1, Technical Approach A1, Race 1, Build task 1 | Re-verified every A1 citation against HEAD `3514ed1b` and corrected all occurrences throughout the plan. B2 CAS region `:604-648` and valor_session `:725-728/916/950` re-confirmed unchanged. |

### Round 1 (2026-07-01, FULL critique → NEEDS REVISION → revised)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| Blocker | Critique | D4 finite `socket_timeout` reverts a documented design (`agent_session_queue.py:822-828`) — prior finite timeout caused spurious socket-timeout exceptions + a 10s reconnect cycle that DROPPED notifications. | Technical Approach D4, Data Flow, Race 5, Risks/Failure-Path, Success Criteria D4, Verification | Rewrote D4: KEEP `socket_timeout=None` on the blocking `listen()` connection; add an OFF-PATH periodic `PUBSUB NUMSUB` liveness probe on a SEPARATE connection; resubscribe + WARNING only on a confirmed NUMSUB==0. Round two can't reproduce the failure because the `listen()` connection is untouched — the probe reads a different connection whether or not a message is in flight. |
| Concern | Critique | B1 claim gates only the dispatch path; `catchup.py`/`reconciler.py` intentionally bypass the wrapper (`dispatch.py:15-18`), leaving the double-exec race open on recovery paths. | Technical Approach B1, Data Flow B1, Race 2, Test Impact, Success Criteria B1, Verification | Extended `claim_message` in-line to both recovery enqueue sites (catchup `:257`, reconciler `:239`) with a shared claim key; loser skips enqueue but records dedup. Kept the documented two-step recovery pairing (not routed through the wrapper); docstring updated. |
| Concern | Critique | C1 all-or-nothing finalize inverts the intentional child-independent-finalize contract (`session_lifecycle.py:439-451` swallows parent-finalize as non-fatal). | Technical Approach C1, Data Flow C1, Race 4, Risk 5, Test Impact, Success Criteria C1, Verification | Withdrew the pipeline coupling. Preserve child-independent best-effort parent finalize; close the crash-window orphan with an idempotent worker-startup sweep re-invoking the already-idempotent `_finalize_parent_sync` (`:719-732`). |
| Concern | Critique | A1 mischaracterizes `pop_all_steering_messages` as atomic; it's a non-atomic LPOP loop safe only under a single-consumer invariant (`steering.py:80-109`). | Data Flow A1, Technical Approach A1, Race 1, Failure-Path, Test Impact, Success Criteria A1 | Corrected all references to the non-atomic-but-single-consumer model; A1 now PRESERVES and TESTS the invariant (two concurrent drainers of one session_id split disjointly). |
| Concern | Critique | D1 OQ3 (npm pin vs native version-assertion) unresolved and conflated with the contract-check probe. | Technical Approach D1, Update System, Data Flow, Solution, OQ3, Success Criteria D1a/D1b, Verification, Prerequisites, Research, PR-split | Resolved: live `claude` is native-installer (`~/.local/bin/claude` → `~/.local/share/claude/versions/2.1.197`), NOT npm — so `MANAGED_PACKAGES` is wrong. Pin via a `PINNED_CLAUDE_VERSION` assertion in `verify.py` (D1a); the marker contract-check (D1b) is split into a separate PR. |
| Nit | Critique | C2 verification `grep -c "record.save()"` doesn't prove the heal stopped re-saving (file-wide; `_heal_future_updated_at` re-saves at `:1029`). | Verification, note below table | Retargeted to a function-scoped `sed`-range grep expecting 0; baseline currently returns 1 (its own red-state proof). |
| Nit | Critique | D1–D4 "brittleness" grouping has a weak shared thesis. | Technical Approach D-group lead-in | One-line acknowledgement added; mitigated by the 12-way PR split, no structural change. |

---

## Open Questions

1. **B1 gate vs membership set — RESOLVED (Round 3).** They COEXIST with distinct lifetimes: the
   `SET NX` gate is a seconds-wide concurrency arbiter (`CLAIM_TTL_SECONDS`, provisional ~60s), and the
   2h `DedupRecord` membership set is the durable catchup-replay dedup (recorded post-enqueue). The gate
   is NOT sized to the sync lag and does NOT subsume membership — the decoupling is what makes the short
   gate TTL safe (a long gate TTL was the Round-3 BLOCKER: it orphaned the key on a mid-window failure
   and silently re-dropped the message). The gate is released on any enqueue exception and self-heals via
   its short TTL on a process death.
2. **B2 mechanism:** Redis `WATCH`/`MULTI` on the status field, or a `SET NX`
   `session:runclaim:{id}` key? Plan leans SETNX (matches existing idiom, simpler); WATCH/MULTI
   if the status field must remain the sole source of truth.
3. **D1 pin mechanism — RESOLVED.** The live CLI is at `~/.local/bin/claude` →
   `~/.local/share/claude/versions/2.1.197` via the **native installer** (verified against HEAD),
   NOT npm. So the npm route (`MANAGED_PACKAGES`) is the wrong mechanism. **Decision:** keep the
   native-installer path and add a `PINNED_CLAUDE_VERSION` version-assertion step to
   `scripts/update/verify.py` (warn-on-drift default, env-gated block), install-method agnostic.
   The pin (D1a) and the marker contract-check (D1b) are split into two separable PRs. See
   Technical Approach D1 + Update System.
4. **PR-split granularity:** Ship all 12 PRs, or bundle the tightly-coupled ones (A2+A3, B1+B2,
   C1–C4, D1–D4) into 4-ish PRs? Plan proposes 12 for reviewability; PM may prefer fewer.
5. **C2 skew source of truth:** Is there a single trusted clock to compute relative age against
   (Redis `TIME`?), or must staleness be purely relative to the record's own monotonic markers?
