# Docs Auditor

Unified documentation hygiene substrate that consolidates five disjointed pieces — `feature-docs-audit`, `documentation-audit`, `knowledge-reindex`, `/do-xref-audit`, `/do-docs-audit` — into one module (`reflections/docs_auditor.py`) consumed by two callers: a daily rotation reflection and the `/do-docs` SDLC stage.

## What It Replaces

| Old piece | Type | Status |
|-----------|------|--------|
| `feature-docs-audit` | reflection (daily) | deleted |
| `documentation-audit` | reflection (daily) | deleted |
| `knowledge-reindex` | reflection (daily) | deleted (was broken) |
| `/do-xref-audit` | manual skill | deleted (no replacement) |
| `/do-docs-audit` | manual skill | deleted |
| `scripts/docs_auditor.py` | standalone script | deleted |

## Two Callers

```
                          reflections/docs_auditor.py
                          ┌────────────────────────────┐
docs-auditor (daily) ────►│ run_docs_auditor()         │──► docs-audit/{slug}-{ts} branch + PR
                          │   ↓                        │
                          │ audit(scope_mode='rotation')│
                          └────────────────────────────┘

                          ┌────────────────────────────┐
/do-docs (SDLC stage) ───►│ audit(scope_mode=          │──► commit on current branch
                          │   'pr-changed-files')      │
                          └────────────────────────────┘
```

### Caller A — `docs-auditor` daily rotation reflection

Picks the least-recently-audited primary doc from a Redis hash, expands its
neighborhood (≤20 files via outbound links + inbound refs), runs auto-fix
detectors, applies them, opens a `docs-audit/{slug}-{ts}` branch, posts a
non-draft PR, and notifies the `Dev: Valor` Telegram chat. Errors are
swallowed; the auditor never crashes the worker.

### Caller B — `/do-docs` SDLC stage

The `/do-docs` skill (`.claude/skills/do-docs/SKILL.md`) calls the substrate
with `scope_mode="pr-changed-files"` after sub-agents A–D finish discovery.
The substrate applies auto-fixes to the working tree, commits to the **current
branch** (no new branch), and fires the memory-refresh hook. The skill then
writes the SDLC stage marker and updates the plan frontmatter.

## Detectors

### Auto-fix (safe transforms)

| Detector | What it catches | Mechanism |
|----------|-----------------|-----------|
| Renamed markdown links | `[label](old/path.md)` after a rename | `git log --follow --diff-filter=R` |
| Renamed paths/symbols | `` `old/module.py` `` after a rename | `git log --follow --diff-filter=R` |
| README broken entries | Index entries pointing at deleted files | filesystem check + rename probe |
| Stale-term dictionary | `SessionLog`, `RedisJob`, `session_log`, `redis_job` | `STALE_TERMS` dict at module top |

### File-as-issue (judgment required)

| Detector | What it catches | Action |
|----------|-----------------|--------|
| Deleted target | `` `path.py` `` references with no rename in history | `gh issue create` (deduped) |
| Stub doc | Docs with <5 content lines | `gh issue create` (deduped) |
| Orphan plan | `docs/plans/*.md` lacking a tracking-issue link | `gh issue create` (deduped) |

Issues are deduped by SHA-256 of the title via `docs_audit:issues_filed:{hash}`
Redis keys (30-day TTL).

## Rotation State

Rotation state lives in a **single Redis hash** `docs_audit:last_run`:

| Field | Value |
|-------|-------|
| `{path_slug}` | float Unix timestamp of last audit (repo doc) |
| `vault:{project_key}:{path_slug}` | float Unix timestamp (vault doc) |

Reads use `HGETALL` (single round-trip); writes use `HSET`. A repo with 200
docs produces 200 hash fields under one key, not 200 top-level keys. Audit
state is inspectable via `redis-cli HGETALL docs_audit:last_run`.

Vault docs are picked at half the rate of repo docs (`DEFAULT_VAULT_WEIGHT = 0.5`)
because the vault is read-mostly.

## Locking

```
docs_audit:running:global       — rotation reflection lock (TTL 1h)
docs_audit:sweeper:running      — branch-sweeper lock (TTL 30min)
docs_audit:issues_filed:{hash}  — per-finding dedup (TTL 30d)
docs_audit:last_run             — rotation state hash
docs_audit:last_completed_run_ts        — Phase 2 liveness signal
docs_audit:last_completed_run_summary   — Phase 2 liveness JSON summary
```

All locks use the established SETNX pattern: `r.set(key, "1", nx=True, ex=ttl)`.
The auditor releases its lock in a `try/finally` so the next scheduled run
does not have to wait out the TTL.

## Memory Refresh Hook

`reflections.docs_auditor.refresh_docs_in_memory(touched_paths: list[str]) -> None`
is a public no-op-by-default hook called after fixes are applied and committed,
before PR creation (Caller A) or stage marker write (Caller B). Issue #1249
will replace the body with a real implementation that re-ingests touched docs
into the Memory substrate.

The hook signature is **stable** — call sites in this module will not change
when #1249 lands. The hook is always non-blocking and fire-and-forget;
exceptions are caught and logged so the auditor never fails because the hook
failed.

## Branch Sweeper

`run_docs_branch_sweeper()` runs daily and:
- Deletes `docs-audit/*` remote branches with no PR ever opened, age >7 days
- Closes (`gh pr close --delete-branch`) open `docs-audit/*` PRs older than 14 days

Scope is intentionally narrow — only branches under the `docs-audit/` prefix
are touched. Branches with any review comment, non-bot authorship, or merged
PR are left alone. This sweeper does NOT touch `session/*` branches; that
remains `agent/session_revival.py`'s scope.

## Configuration

Both reflections are registered in `config/reflections.yaml`:

```yaml
- name: docs-auditor
  description: "Unified docs auditor: rotates least-recently-audited primary doc..."
  interval: 86400
  priority: low
  execution_type: function
  callable: "reflections.docs_auditor.run_docs_auditor"
  enabled: true

- name: do-docs-branch-sweeper
  description: "Delete stale docs-audit/* branches >7d..."
  interval: 86400
  priority: low
  execution_type: function
  callable: "reflections.docs_auditor.run_docs_branch_sweeper"
  enabled: true
```

`config/reflections.yaml` is vault-managed (symlink to `~/Desktop/Valor/reflections.yaml`).

## Operational Cheatsheet

```bash
# Inspect rotation state
redis-cli HGETALL docs_audit:last_run

# Phase 2 liveness signal
redis-cli GET docs_audit:last_completed_run_ts
redis-cli GET docs_audit:last_completed_run_summary

# Force-clear the lock if a run hung
redis-cli DEL docs_audit:running:global

# Run /do-docs from a PR
python -c "from reflections.docs_auditor import audit; \
  import json; print(json.dumps(audit(primary_path=None, \
  scope_mode='pr-changed-files', apply_mode='apply', project_key='valor')))"
```

## Tests

Unit tests live in `tests/unit/test_docs_auditor_substrate.py` — covers
`audit()`, rotation reflection, branch sweeper, SETNX lock contention,
neighborhood cap, zero-diff gate, auth probe degradation, memory-refresh
hook, and the `/do-docs` thin-caller contract.

```bash
pytest tests/unit/test_docs_auditor_substrate.py -v
```

## See Also

- [Reflections](reflections.md) — registry and scheduler design
- `.claude/skills/do-docs/SKILL.md` — Caller B skill definition
- `reflections/docs_auditor.py` — substrate source
- Issue #1247 — design and rollout plan
- Issue #1249 — memory refresh hook (forward-compat target)
