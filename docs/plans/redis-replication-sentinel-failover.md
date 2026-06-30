---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-06-30
tracking: https://github.com/tomcounsell/ai/issues/1827
last_comment_id:
---

# Redis Replication + Sentinel for Primary-Loss Failover

## Problem

All durable state — every `AgentSession`, `Memory`, the bloom filter, and the
Telegram/email history cache — lives in Popoto, and Popoto is backed by a single
Redis host. PR #1824 (#1814) added the AOF floor: at most 1 second of write loss
on a hard crash. But AOF only bounds loss *within a running Redis process on one
host*. If that host dies (hardware failure, disk loss, power, network partition),
the entire system is down and the data-dir may be unrecoverable. There is no
replica and no failover.

**Current behavior:** Single Redis host = single point of failure. Loss of the
host loses the system. Recovery is manual: stand up a new Redis, hope the AOF/RDB
files survived, restore by hand.

**Desired outcome:** A replica on a second host holds a live copy of all state.
Sentinel monitors both and promotes the replica automatically when the primary is
lost. The application reconnects to the new primary without a code change and with
bounded interruption. The failover model, the topology, and the `/update`
propagation are documented in `docs/features/redis-durability.md` (today that
doc lists Fix #5 only as a deferred "TBD" line).

## Freshness Check

**Baseline commit:** 4a66f506d245e4892440bec0973c65d527e413b4
**Issue filed at:** 2026-06-30T05:37:09Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `config/settings.py:142` — issue claims `REDIS_URL` is the single env-driven knob. Confirmed: `RedisSettings.url`, default `redis://localhost:6379/0`, env `REDIS_URL`. Still holds.
- `docs/features/redis-durability.md` — issue claims it is "roadmap-only" for Fix #5. Confirmed: the doc exists (created by #1814) and lists Fix #5 in its "Deferred Durability Work" table with issue "TBD". Still holds.
- `docs/plans/completed/redis-durability-hardening.md` — cited reference exists.

**Cited sibling issues/PRs re-checked:**
- #1814 — CLOSED, shipped via PR #1824 (AOF + resilient client + eviction). This issue is its deferred Fix #5.
- #1818 — OPEN tracking issue ("[Tracking] Resilience hardening"). This work is a child.
- PR #1824 — merged; established `scripts/update/redis_persistence.py` + `run.py` Step 3.13 as the Redis-config propagation seam this plan extends.

**Commits on main since issue was filed (touching referenced files):** none (issue filed and planned same day, 2026-06-30).

**Active plans in `docs/plans/` overlapping this area:** `redis-popoto-migration.md` (unrelated — Popoto data-model migration, not HA). No overlap.

**Notes:** Recon (in the issue body) revised the issue's "infra/config only" premise — see Prior Art / Research / Technical Approach.

## Prior Art

- **Issue #1814 / PR #1824** — "Redis durability hardening." Shipped the AOF floor, a resilient Popoto client (`config/redis_bootstrap.py`), and `maxmemory-policy noeviction`. Explicitly **deferred Fix #5 (replication + Sentinel) to a separate issue** — this one. The PR also built the per-machine Redis-config propagation pattern (`scripts/update/redis_persistence.py` called at `run.py` Step 3.13) that this plan reuses for replica/Sentinel config.
- No prior **failed** attempts at replication/Sentinel — this is the first pass at host-level failover. No "Why Previous Fixes Failed" section needed.

## Research

**Queries used:**
- `redis-py Sentinel client master_for failover vs HAProxy VIP stable address best practice 2025`
- `redis replication sentinel minimum 3 sentinels quorum two host setup production guidance`

**Key findings:**
- **HAProxy/VIP + Sentinel is the modern best-practice pattern** ([redis.io Sentinel docs](https://redis.io/docs/latest/operate/oss_and_stack/management/sentinel/), [blog.poespas.me 2025](https://blog.poespas.me/posts/2025/03/06/redis-cluster-haproxy-sentinel-failover/)). Sentinel orchestrates *promotion* but does **not** route client traffic. A stable endpoint in front (HAProxy as a TCP balancer, or a keepalived VIP) gives the application one fixed address that always points at the current master — so no application code changes. This directly informs the **Option A** recommendation below: keep `REDIS_URL` a fixed address, let infra repoint it.
- **Sentinel-aware clients (`redis.sentinel.Sentinel(...).master_for(svc)`)** are the alternative ("smart client"). They remove the extra HAProxy hop but require application code on every connection path — this is **Option B**, and it contradicts the issue's "infra/config only" framing because we have 18 distinct connection points (see Technical Approach).
- **Quorum / host-count constraints** ([redis.io](https://redis.io/docs/latest/operate/oss_and_stack/management/sentinel/), [oneuptime](https://oneuptime.com/blog/post/2026-03-31-redis-sentinel-quorum-configuration/view)): production needs **≥3 Sentinels with quorum=2** on **3 independent machines**; never co-locate a Sentinel with the master it watches. A strict **two-host** topology (the issue's literal acceptance bullet) technically works with quorum=2 but is fragile — it gives no protection against split-brain and a single host loss can take out both a Redis node and a Sentinel. This is a real constraint, captured as a Risk and an Open Question.

## Architectural Impact

- **New dependencies:** none in Python. New *operational* components: a second Redis host (replica), 3 Sentinel processes, and — under the recommended Option A — a stable-address layer (HAProxy or keepalived VIP).
- **Interface changes:** none to Python interfaces under Option A. `REDIS_URL` stays a single address string; its *value* changes (points at the VIP/HAProxy instead of `localhost`).
- **Coupling:** Option A keeps the app fully decoupled from Sentinel (the infra hides failover). Option B would couple 18 connection sites to the Sentinel protocol.
- **Data ownership:** unchanged. Redis remains the owner; the replica is a read-through copy promoted on failover.
- **Reversibility:** high. Tearing down the replica/Sentinel/HAProxy and pointing `REDIS_URL` back at a single host fully reverts. No data-model or schema change.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM (architecture decision A-vs-B), code reviewer (the /update step + any bootstrap change)

**Interactions:**
- PM check-ins: 1-2 (lock the A-vs-B architecture decision before build; confirm the host topology)
- Review rounds: 1 (config templates + /update propagation step + docs)

The coding surface is small (config templates, one /update step, docs, optional doctor check). The bottleneck is the architecture decision and the genuinely external act of provisioning a second host.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `redis-cli` on PATH | `command -v redis-cli` | Apply/verify replica + Sentinel config |
| `REDIS_URL` set | `python -c "from config.settings import settings; print(settings.redis.url)"` | The single address knob this plan repoints |
| Second host reachable | `python -c "print('operator-supplied second host — see EXTERNAL No-Go')"` | Host-level failover requires a real second machine (not programmatically checkable; operator-supplied) |

Run via `python scripts/check_prerequisites.py docs/plans/redis-replication-sentinel-failover.md`.

## Solution

### Key Elements

- **Replica config artifact** — a checked-in `replicaof`-style directive set (and a `redis-replica.conf` template) so a second host can be brought up as a live replica of the primary deterministically.
- **Sentinel config artifact** — a `sentinel.conf` template (`sentinel monitor`, `down-after-milliseconds`, `failover-timeout`, `parallel-syncs`, quorum) plus guidance on the ≥3-Sentinel / 3-machine layout.
- **Stable-address layer (Option A, recommended)** — an HAProxy or keepalived VIP template that exposes one fixed endpoint; `REDIS_URL` points at it so promotion is transparent to all 18 connection sites.
- **`/update` propagation step** — a module analogous to `scripts/update/redis_persistence.py` that, on a host tagged as a Redis node, applies/verifies the replica + Sentinel directives idempotently (non-fatal, like the existing step).
- **Doctor check extension** — assert replication health (`INFO replication` → `role`, `connected_slaves`/`master_link_status`) and Sentinel reachability, so drift is caught independently of `/update`.
- **Documentation** — fill in the Fix #5 section of `docs/features/redis-durability.md`: topology diagram, failover model, manual + automatic failover runbook, promotion verification.

### Flow

Primary Redis healthy → `REDIS_URL` → stable address (VIP/HAProxy) → primary
→ **primary host dies** → Sentinel quorum declares O-DOWN → Sentinel promotes
replica → stable address repoints to new master → app reconnects (retry policy
from #1814 already in place) → service resumes on the promoted host.

### Technical Approach

**The central decision (Option A vs Option B).** Recon found that `REDIS_URL`
being env-driven is *necessary but not sufficient* for transparent failover.
Two client-construction paths both bind to a **fixed host/port derived from
`REDIS_URL`**:

1. `config/redis_bootstrap.py:106` parses `REDIS_URL` into fixed `host`/`port`/
   `db`/`password` and hands them to `popoto.redis_db.set_REDIS_DB_settings(...)`.
   This is the seam for *all* Popoto traffic (sessions, memories, bloom, history).
2. **16 files** call `redis.Redis.from_url(REDIS_URL)` directly, bypassing Popoto:
   `agent/output_handler.py`, `agent/session_completion.py`, `bridge/liveness.py`,
   `bridge/dedup.py`, `bridge/telegram_relay.py`, `bridge/email_bridge.py`,
   `bridge/email_relay.py`, `bridge/routing.py`, `bridge/email_dead_letter.py`,
   `tools/send_message.py`, `tools/send_telegram.py`, `tools/react_with_emoji.py`,
   `tools/valor_email.py`, `tools/valor_telegram.py`, `tools/email_history/__init__.py`,
   `monitoring/bridge_watchdog.py`.

Classic Sentinel promotion gives the new master a **different IP/port**. None of
these 18 sites are Sentinel-aware, so they'd keep dialing the dead master.

- **Option A — stable-address front (RECOMMENDED, matches "infra/config only").**
  Keep `REDIS_URL` a fixed address (keepalived VIP or HAProxy TCP frontend) that
  Sentinel-driven tooling repoints to the current master. **Zero Python change.**
  Deliverables are entirely config templates + the /update step + docs. This is
  the 2026 best practice (see Research).
- **Option B — Sentinel-aware client seam.** Teach `redis_bootstrap.py` to
  discover the master via `redis.sentinel.Sentinel(...)` and provide a shared
  helper the 16 raw `from_url` sites adopt. Removes the HAProxy hop but touches
  app code on every connection path and contradicts the issue framing. Larger
  blast radius, larger test surface.

This plan is **written for Option A** and lists Option B as the documented
alternative. The A-vs-B lock is the top Open Question for PM sign-off before build.

**Topology.** Recommend a **3-Sentinel / 3-machine** layout (per Research) even
though the issue says "second host": primary host, replica host, and a third
lightweight witness running the tie-breaking Sentinel. If only two hosts are
truly available, document the quorum=2 two-host fallback and its split-brain
caveat explicitly rather than shipping it silently.

**Propagation seam.** Mirror `scripts/update/redis_persistence.py`: a new
`scripts/update/redis_replication.py::apply_redis_replication()` invoked from a
new step in `run.py`, role-gated (only runs on hosts tagged as Redis nodes — most
machines are clients and skip non-fatally). It verifies replica/Sentinel directives
are present and applies them idempotently via `redis-cli CONFIG`. Non-fatal,
matching the existing step's contract.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `apply_redis_replication()` must follow the existing `apply_redis_persistence()` contract: never raise, return a `*Result` dataclass with `action ∈ {applied, skipped, failed}`. Add a test asserting it returns `skipped` (not raises) when `redis-cli` is absent and `failed` (logged WARNING) when the node is unreachable.
- [ ] The doctor replication check must degrade to an actionable failure message, never crash `python -m tools.doctor`, when Sentinel/replica is unreachable.

### Empty/Invalid Input Handling
- [ ] Verify behavior when `REDIS_URL` is empty/whitespace — `RedisSettings.validate_url` already falls back to the default; assert the replication step treats "no Redis configured" as `skipped`, not an error.
- [ ] Verify the role-gate: a machine NOT tagged as a Redis node must skip the step cleanly (no partial CONFIG SET).

### Error State Rendering
- [ ] The /update step must surface a loud, operator-actionable WARNING (mirroring the stub-`redis.conf` warning path) when it can apply Sentinel config at runtime but cannot persist it — so the operator knows to restart with the config file.
- [ ] The doctor check's failure message must point at the runbook section in `docs/features/redis-durability.md`.

## Test Impact

- [ ] `tests/` — search for tests asserting on `scripts/update/run.py` step ordering/count; if a test enumerates update steps, UPDATE it to include the new replication step. (Recon found the persistence step at `run.py` Step 3.13; a sibling step is additive.)
- [ ] New unit tests for `scripts/update/redis_replication.py` — NEW file, modeled on the existing `redis_persistence` tests (locate via `grep -rl redis_persistence tests/`). Not a modification of existing tests.
- [ ] `tools/doctor` tests — if a test asserts the set of doctor checks, UPDATE to include the new `redis-replication-health` check.

No existing *behavioral* tests are expected to break — Option A adds config artifacts, one additive /update step, and docs without changing any existing Python interface or the meaning of `REDIS_URL`. The disposition above is precautionary for the two enumeration-style tests that could exist.

## Rabbit Holes

- **Building a custom failover daemon.** Sentinel already does promotion. Do not reinvent health-checking or election. Configure Sentinel; don't write one.
- **Rewriting all 18 connection sites to be Sentinel-aware (Option B) by default.** That is a large, risky refactor that contradicts "infra/config only." Only pursue if PM explicitly picks Option B.
- **Multi-datacenter / WAN replication, TLS between nodes, ACL hardening.** Real concerns, but separate scope from "stand up a replica + Sentinel for host-loss failover." Note and move on.
- **Automating the second-host provisioning end-to-end.** The agent cannot provision a machine. Provide the config artifacts and a runbook; the host itself is operator-supplied.
- **Read-scaling / splitting reads to the replica.** Tempting once a replica exists, but it's a performance feature, not failover. Out of scope.

## Risks

### Risk 1: Two-host topology has no split-brain protection
**Impact:** A network partition between two co-equal hosts can promote the replica while the old primary still accepts writes → divergent data, lost writes on reconvergence.
**Mitigation:** Recommend the 3-machine / 3-Sentinel layout (witness host) per Research. If only two hosts exist, document quorum=2 limits explicitly and require operator acknowledgement; never ship the two-host config as if it were fully HA.

### Risk 2: The 16 raw `from_url(REDIS_URL)` sites silently keep the old address after promotion (Option A regression vector)
**Impact:** If the stable-address layer is misconfigured, these sites pin to the dead master and the system stays down despite a healthy promoted replica.
**Mitigation:** Option A routes *all* connections through the fixed VIP/HAProxy — verify with an integration/manual test that, after a forced failover, a fresh `redis.Redis.from_url(REDIS_URL)` connects to the promoted master. Add this to the runbook's verification steps.

### Risk 3: /update step applies config but can't persist it
**Impact:** Sentinel/replica settings active for the session but lost on Redis restart (same failure mode the AOF stub path handles).
**Mitigation:** Reuse the existing stub-`redis.conf` + loud-WARNING pattern from `redis_persistence.py`; doctor check catches drift.

## Race Conditions

### Race 1: Application reconnect during the promotion window
**Location:** All 18 connection sites + `config/redis_bootstrap.py` retry policy.
**Trigger:** Primary dies; for the `down-after-milliseconds` + `failover-timeout` window there is no writable master.
**Data prerequisite:** The replica must have received the writes (async replication → a small window of unreplicated writes can be lost; this is the documented RPO, bounded further by AOF on the replica).
**State prerequisite:** Sentinel quorum reached and promotion complete before clients retry-succeed.
**Mitigation:** The resilient client from #1814 (`Retry(ExponentialBackoff(cap=10, base=1), retries=3)`, `health_check_interval=30`) already retries through transient `ConnectionError`/`TimeoutError`. Document the expected interruption window (RTO) and unreplicated-write window (RPO) in the runbook; no new locking needed.

## No-Gos (Out of Scope)

- [EXTERNAL] **Provisioning the physical/virtual second (and witness) host(s).** The agent cannot allocate machines or configure inter-host networking/firewalls (ports 6379/26379). This plan delivers the config artifacts, the /update propagation, and the runbook; the operator brings the hosts.
- [EXTERNAL] **Pointing production `REDIS_URL` at the VIP/HAProxy and cutting over live traffic.** A real human-gated operational change on running infrastructure; this plan documents the procedure and provides the templates.
- [SEPARATE-SLUG #1827-followup-B] Adopting Sentinel-aware "smart clients" across all 18 connection sites (Option B) is documented as the alternative but is **not** built unless PM selects it; if selected it replaces Option A in this same plan rather than being deferred. *(If PM picks Option A and later wants Option B, file a dedicated issue.)*
- Fix #2 (SQLite secondary store) and Fix #4 (async Redis off the event loop) — separate deferred items in the same #1814 table, tracked independently in `docs/features/redis-durability.md`. Not this issue.

## Update System

This is the core of the work. The `/update` skill must propagate the replica +
Sentinel configuration to Redis-node machines, mirroring the existing AOF step.

- **New module:** `scripts/update/redis_replication.py::apply_redis_replication()` —
  idempotent, non-fatal, returns a `RedisReplicationResult` dataclass (same shape
  and contract as `RedisPersistenceResult`). Role-gated so client-only machines
  skip cleanly.
- **New step in `scripts/update/run.py`** adjacent to the existing Step 3.13
  (Redis durability), with the same logging/warning/result-collection treatment.
- **Config artifacts checked into the repo** (templates applied by the step and by
  the runbook): replica directives, `sentinel.conf` template, and — for Option A —
  the HAProxy/keepalived template.
- **No new Python package dependencies** (uses `redis-cli` + stdlib, like the
  persistence step). The only propagated change beyond config is the `REDIS_URL`
  *value* on each machine, which already flows through the generic `.env` vault
  sync (`scripts/update/env_sync.py`) — `.env.example:184` carries the placeholder.

## Agent Integration

No agent integration required. This is infrastructure + `/update` + documentation.
There is no new agent-facing capability, no MCP tool, and no new CLI entry point —
the agent does not invoke failover; Sentinel does it automatically and the operator
runs the documented manual-failover commands. The bridge/worker connect to Redis
exactly as today (via `REDIS_URL`); only the address's *target* changes (an infra
concern), and the existing resilient-client retry path (#1814) already handles
reconnection. The `redis-replication-health` doctor check is reachable via the
existing `python -m tools.doctor` surface — no new wiring.

## Documentation

### Feature Documentation
- [ ] Fill in the **Fix #5** section of `docs/features/redis-durability.md` (replace the "TBD" line in the Deferred Durability Work table with a full section): topology diagram, replication model (async, RPO/RTO), Sentinel quorum config, and the failover model under the chosen option.
- [ ] Add an **Operational Runbook** subsection there: how to bring up a replica, how to bring up Sentinel, how to verify replication (`INFO replication`), manual failover (`SENTINEL failover <name>` / `CKQUORUM`), and post-failover verification that `REDIS_URL` connects to the promoted master.
- [ ] Update the **Update System Integration** subsection of that doc to describe the new `redis_replication` step.
- [ ] Confirm `docs/features/README.md` index entry for redis-durability is still accurate (it already exists from #1814).

### Inline Documentation
- [ ] Module docstring on `scripts/update/redis_replication.py` matching the style of `redis_persistence.py`.
- [ ] Comment the role-gate logic (why most machines skip).

## Success Criteria

- [ ] Architecture decision (Option A vs B) recorded in the plan and the doc, with PM sign-off.
- [ ] Replica + Sentinel **config artifacts** checked into the repo (replica directives, `sentinel.conf` template, and Option-A stable-address template).
- [ ] `scripts/update/redis_replication.py` exists, is invoked from `run.py`, is idempotent + non-fatal + role-gated, and has unit tests modeled on the persistence-step tests.
- [ ] `python -m tools.doctor` includes a `redis-replication-health` check that asserts `role`/`master_link_status` and degrades gracefully when unreachable.
- [ ] `docs/features/redis-durability.md` Fix #5 section is complete with topology, failover model, and runbook; the deferred-table "TBD" is replaced with #1827.
- [ ] Documented failover procedure verified at least once in a staging/two-host bring-up (manual `SENTINEL failover`, confirm `REDIS_URL` reaches the promoted master) — evidence captured in the runbook or PR.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms `run.py` references `redis_replication` (Agent-Integration/wiring assertion).

## Verification

| # | Criterion | Check | Type |
|---|-----------|-------|------|
| 1 | Propagation module exists and is wired | `grep -q redis_replication scripts/update/run.py` | positive |
| 2 | Module is non-fatal/idempotent | unit test asserts `skipped` when `redis-cli` absent, `failed` (no raise) when node down | positive |
| 3 | Doctor check present | `python -m tools.doctor --json` lists `redis-replication-health` | positive |
| 4 | Doc Fix #5 filled in | `grep -q "Fix #5" docs/features/redis-durability.md` AND no "TBD" on that row | positive |
| 5 | No Option-B refactor shipped unless selected | `git grep -n "redis.sentinel" -- agent/ bridge/ tools/` returns nothing (anti-criterion for the [SEPARATE-SLUG] No-Go under Option A) | negative |
| 6 | No new Python dependency added | `git diff main -- pyproject.toml` shows no new runtime dep for redis HA | negative |

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (update-propagation)**
  - Name: `redis-ha-builder`
  - Role: Create `scripts/update/redis_replication.py`, wire it into `run.py`, add config templates, extend the doctor check.
  - Agent Type: builder
  - Domain: redis (paste Redis/Popoto rules from `DOMAIN_FRAMING.md`)
  - Resume: true

- **Documentarian (failover-model)**
  - Name: `redis-ha-doc`
  - Role: Fill in the Fix #5 section + runbook in `docs/features/redis-durability.md`.
  - Agent Type: documentarian
  - Resume: true

- **Validator (ha-config)**
  - Name: `redis-ha-validator`
  - Role: Verify the /update step is non-fatal/idempotent/role-gated, the doctor check degrades gracefully, and the Verification table rows pass.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Lock the architecture decision (A vs B)
- **Task ID**: decide-architecture
- **Depends On**: none
- Confirm Option A (stable-address front) vs Option B (Sentinel-aware clients) with PM. Record the decision in the plan + doc. All downstream tasks assume Option A unless overridden.

### 2. Author config artifacts
- **Task ID**: build-config-artifacts
- **Depends On**: decide-architecture
- Add the replica directive template, `sentinel.conf` template (quorum=2, ≥3 sentinels guidance), and the Option-A stable-address (HAProxy/keepalived) template to the repo.

### 3. Build the /update propagation step
- **Task ID**: build-update-step
- **Depends On**: build-config-artifacts
- Create `scripts/update/redis_replication.py` (non-fatal, idempotent, role-gated, `RedisReplicationResult` dataclass modeled on `RedisPersistenceResult`). Wire a new step into `scripts/update/run.py` adjacent to Step 3.13. Add unit tests modeled on the persistence-step tests.

### 4. Extend the doctor check
- **Task ID**: build-doctor-check
- **Depends On**: build-config-artifacts
- Add `redis-replication-health` to `python -m tools.doctor`: assert `role`/`master_link_status` via `INFO replication`, degrade gracefully when unreachable.

### 5. Document the failover model + runbook
- **Task ID**: build-docs
- **Depends On**: decide-architecture
- Fill in `docs/features/redis-durability.md` Fix #5 section: topology, replication model (RPO/RTO), Sentinel config, manual + automatic failover runbook, post-failover `REDIS_URL` verification. Replace the deferred-table "TBD" with #1827.

### 6. Validate
- **Task ID**: validate-all
- **Depends On**: build-update-step, build-doctor-check, build-docs
- Run the Verification table; confirm non-fatal/idempotent/role-gated behavior and graceful doctor degradation.

## Open Questions

1. **Option A vs Option B — which architecture?** Recommendation: **Option A**
   (stable-address VIP/HAProxy front, zero Python change, matches "infra/config
   only" and 2026 best practice). Option B (Sentinel-aware clients across 18 sites)
   is larger and contradicts the issue framing. Confirm A before build.
2. **Host topology — two hosts or three?** The issue says "second host," but real
   HA needs ≥3 Sentinels on 3 independent machines (witness host for tie-break).
   Do we provision a third lightweight witness, or accept the documented two-host
   quorum=2 fallback with its split-brain caveat?
3. **Where does the stable-address layer live?** keepalived VIP (needs L2 adjacency
   between hosts) vs HAProxy TCP frontend (works across subnets, adds a hop). Which
   fits the actual deployment network?
4. **Is a staging/second host available now** to verify the failover procedure end
   to end, or does this plan ship the artifacts + runbook and defer the live
   verification to the operator cutover (the [EXTERNAL] No-Go)?
