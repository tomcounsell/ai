---
status: Building
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-12
tracking: https://github.com/tomcounsell/ai/issues/920
last_comment_id:
---

# Consolidate Secrets to ~/Desktop/Valor/.env as Single Source of Truth

## Problem

There is no canonical, documented answer to "where do secrets live?" Developers and agents
reach for `repo/.env` as the obvious location, causing sensitive keys to drift into the
repository directory — which is gitignored but still physically inside the repo checkout.
Meanwhile, `~/Desktop/Valor/.env` (iCloud-synced, machine-private) already holds the
authoritative set of API keys.

**Current behavior:**
- `~/src/ai/.env` is a regular file containing 6 keys: `VOYAGE_API_KEY`, `IMAP_USER`,
  `IMAP_PASSWORD`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_ADDRESS`
- `~/Desktop/Valor/.env` already contains those same 6 keys plus all other secrets
- `scripts/update/env_sync.py` copies `VOYAGE_API_KEY` from vault → repo `.env` on each update
  (a workaround for the split that introduced its own complexity)
- Most entrypoints use a two-pass `load_dotenv` calling both files, but `config/settings.py`
  uses pydantic-settings `env_file=".env"` (reads only the repo file)
- No documentation explains where to put a new secret; developers guess and drift happens

**Desired outcome:**
`~/Desktop/Valor/.env` is the one and only secrets file. `repo/.env` is a symlink to it.
All existing `load_dotenv` calls work unchanged. New secrets go in the vault, not the repo.

## Freshness Check

**Baseline commit:** `39b1c8678fec00cccddae6a6ae6f4d3bd5b27a81`
**Issue filed at:** 2026-04-12T10:42:16Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/update/env_sync.py` — SYNC_KEYS contains `VOYAGE_API_KEY`, copies vault → project — still holds
- `bridge/telegram_bridge.py:44-45` — two-pass `load_dotenv` pattern — still holds
- `config/settings.py:213` — `env_file=".env"` (relative) — still holds; resolves through symlink correctly
- `~/src/ai/.env` — regular file (246 bytes), 6 keys — confirmed present, not yet a symlink
- `~/Desktop/Valor/.env` — vault file — confirmed present with full key set including the 6 drift keys

**Commits on main since issue was filed (touching referenced files):** None

**Active plans in `docs/plans/` overlapping this area:** None found

**Notes:** The drift keys (`VOYAGE_API_KEY` and email vars) are already duplicated in the vault,
so migration requires no key generation — just cleanup and symlinking.

## Prior Art

No prior issues or PRs found related to `.env` consolidation or symlink-based secrets management
in this repository.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: None — all `load_dotenv` and pydantic-settings call sites work unchanged
  through the symlink
- **Coupling**: Decreases — removes the env_sync copy logic and the two-source split
- **Data ownership**: `~/Desktop/Valor/` becomes the sole owner of secrets (as intended)
- **Reversibility**: Fully reversible — delete the symlink, restore the regular `.env` file

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `~/Desktop/Valor/.env` exists | `test -f ~/Desktop/Valor/.env` | Vault file must exist before symlinking |
| Vault contains all repo keys | `grep -c VOYAGE_API_KEY ~/Desktop/Valor/.env` | Verify no keys are lost in migration |

Run all checks: `python scripts/check_prerequisites.py docs/plans/consolidate-secrets-env.md`

## Solution

### Key Elements

- **Symlink**: `~/src/ai/.env` → `~/Desktop/Valor/.env` — one physical file, no code changes needed
- **env_sync.py retirement**: Replace copy logic with symlink verification; module becomes a no-op or is removed
- **Update script**: Add symlink creation/repair step to `scripts/remote-update.sh` and `scripts/update/run.py`
- **Documentation**: Canonical rule written into `CLAUDE.md`, `docs/features/config-architecture.md`, and `.env.example` header

### Flow

`Developer adds new secret` → writes to `~/Desktop/Valor/.env` → symlink means `repo/.env` reflects it immediately → all load_dotenv calls pick it up — no sync step needed

### Technical Approach

1. **Symlink creation** (local machine): Migrate the 6 repo-only keys into the vault, then
   replace `repo/.env` with `ln -sf ~/Desktop/Valor/.env ~/src/ai/.env`

2. **env_sync.py**: Replace the copy-keys logic with a symlink health check:
   - Verify `project_dir/.env` is a symlink pointing to `~/Desktop/Valor/.env`
   - If not (new machine, broken link), create the symlink and log it
   - Remove `SYNC_KEYS` list and all copy logic

3. **remote-update.sh**: Add a symlink repair block (idempotent):
   ```bash
   # Ensure .env → ~/Desktop/Valor/.env
   VAULT_ENV="$HOME/Desktop/Valor/.env"
   REPO_ENV="$PROJECT_DIR/.env"
   if [ -f "$VAULT_ENV" ] && [ ! -L "$REPO_ENV" ]; then
       ln -sf "$VAULT_ENV" "$REPO_ENV"
   fi
   ```

4. **CLAUDE.md**: Add a searchable rule under a new `## Secrets` heading

5. **docs/features/config-architecture.md**: Update the Config Files table; add a Secrets section
   explaining the symlink and iCloud sync strategy

6. **`.env.example` header**: Update comment to say "Documents the contents of `~/Desktop/Valor/.env`"

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `env_sync.py` replacement: verify the symlink-check function logs a warning and continues
  if `~/Desktop/Valor/` does not exist (new machine pre-iCloud-sync scenario)
- [ ] No other exception handlers in scope — this work touches shell scripts and docs, not
  complex exception-handling code

### Empty/Invalid Input Handling
- [ ] Symlink creation in `remote-update.sh`: guard against `VAULT_ENV` being empty before calling `ln`
- [ ] `env_sync.py`: handle missing vault path gracefully (same as current missing-vault behavior)

### Error State Rendering
- [ ] The update script logs a clear warning if vault `.env` is not found (pre-iCloud-sync)
- [ ] No user-visible UI output affected

## Test Impact

- [ ] `tests/unit/test_update_env_sync.py` (if it exists) — UPDATE or DELETE: the copy logic being
  replaced by symlink-check logic will break any tests asserting keys were added/updated in project `.env`

```bash
# Check if test file exists
ls ~/src/ai/tests/ -R | grep env_sync 2>/dev/null || echo "No env_sync tests found"
```

No other existing tests affected — the symlink is transparent to all load_dotenv callers.

## Rabbit Holes

- **Migrating all entrypoints to single-pass load_dotenv**: The two-pass pattern is harmless with
  a symlink (second call is a no-op loading the same file). Removing the second pass from 15+ files
  is unnecessary churn.
- **Encrypting the vault .env**: Out of scope — iCloud encryption and filesystem permissions are
  sufficient for this use case.
- **Moving secrets to a secrets manager (Vault, AWS SSM)**: Valid long-term direction but a
  separate project. This plan establishes a clear interim canonical location.

## Risks

### Risk 1: iCloud sync lag on a new machine
**Impact:** If the vault `.env` hasn't synced yet when `remote-update.sh` runs, the symlink
creation step silently skips. Secrets unavailable until iCloud finishes syncing.
**Mitigation:** The script logs a clear warning. On a fresh machine, the operator runs
`scripts/setup.sh` after iCloud sync completes anyway. This is documented behavior, not silent failure.

### Risk 2: .gitignore doesn't cover symlinks
**Impact:** If git follows the symlink, secrets could be committed.
**Mitigation:** `.gitignore` entry `/.env` covers the symlink entry by name, regardless of whether
it's a regular file or symlink. Verify `.gitignore` still lists `.env` — no change needed.

## Race Conditions

No race conditions identified — all operations are synchronous shell/file operations with no
concurrent access patterns.

## No-Gos (Out of Scope)

- Changing any `load_dotenv` call sites — the symlink makes them all correct
- Adding secrets rotation or audit logging
- Migrating to a dedicated secrets manager
- Modifying pydantic-settings `env_file` path in `config/settings.py`

## Update System

The update system (`scripts/remote-update.sh` and `scripts/update/env_sync.py`) is the **primary
delivery mechanism** for this change across all machines:

- `scripts/remote-update.sh`: add idempotent symlink creation/repair block (runs on every `/update`)
- `scripts/update/env_sync.py`: replace copy-keys logic with symlink health check; remove `SYNC_KEYS`
- `scripts/update/run.py`: update Step 1.6 log message from "Syncing env vars from vault" to
  "Verifying .env symlink"
- No new dependencies; no new config files; no migration steps beyond running `/update` once

## Agent Integration

No agent integration required — this is a configuration/infrastructure change. The agent's
`load_dotenv` calls are unaffected by the symlink. No MCP servers or bridge changes needed.

## Documentation

- [ ] Update `docs/features/config-architecture.md`: correct the Config Files table secrets row;
  add a `### Secrets` subsection explaining the symlink approach and iCloud sync rationale
- [ ] Update `CLAUDE.md`: add `## Secrets` section with the canonical rule:
  "All secrets go in `~/Desktop/Valor/.env`. Never write secrets to `repo/.env`. The repo `.env`
  is a symlink — writing to it writes to the vault."
- [ ] Update `.env.example` header comment: clarify it documents `~/Desktop/Valor/.env` contents,
  not a file to copy into the repo
- [ ] No new feature doc needed — this is a clarification of existing config architecture

## Success Criteria

- [ ] `ls -la ~/src/ai/.env` shows a symlink → `~/Desktop/Valor/.env`
- [ ] `repo/.env` as a regular file no longer exists
- [ ] `scripts/remote-update.sh` contains symlink creation/repair logic
- [ ] `scripts/update/env_sync.py` verifies symlink, no longer copies keys
- [ ] `docs/features/config-architecture.md` states `~/Desktop/Valor/.env` is the secrets file
- [ ] `CLAUDE.md` contains searchable rule: "secrets go in `~/Desktop/Valor/.env`"
- [ ] `.env.example` header references the vault path
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (secrets-consolidation)**
  - Name: secrets-builder
  - Role: Migrate keys, create symlink, update env_sync, update remote-update.sh
  - Agent Type: builder
  - Resume: true

- **Documentarian (config-docs)**
  - Name: docs-writer
  - Role: Update CLAUDE.md, config-architecture.md, .env.example
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Verify all success criteria, symlink integrity, test passage
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Migrate Keys and Create Symlink
- **Task ID**: build-symlink
- **Depends On**: none
- **Validates**: symlink exists at `~/src/ai/.env`
- **Assigned To**: secrets-builder
- **Agent Type**: builder
- **Parallel**: false
- Verify all 6 keys from `repo/.env` are present in `~/Desktop/Valor/.env`
- Remove `~/src/ai/.env` regular file
- Create symlink: `ln -sf ~/Desktop/Valor/.env ~/src/ai/.env`
- Verify symlink resolves correctly

### 2. Update env_sync.py and run.py
- **Task ID**: build-env-sync
- **Depends On**: build-symlink
- **Validates**: `scripts/update/env_sync.py` — no copy logic, symlink verification present; `scripts/update/run.py` — no `env_r.added`/`env_r.updated` references
- **Assigned To**: secrets-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace `sync_env_from_vault()` copy logic with symlink health check
- Remove `SYNC_KEYS` list
- Update `EnvSyncResult` to reflect new behavior: `symlink_ok: bool`, `created: bool`, `error: str | None`
- **CRITICAL (blocker fix)**: Update `scripts/update/run.py`:
  - Line ~119: update `env_sync_result: env_sync.EnvSyncResult | None` type annotation (no change needed, just verify)
  - Lines ~330–331: replace `env_r.added`/`env_r.updated` references with `env_r.symlink_ok`/`env_r.created`
  - Lines ~334–335: update error warning block to use `env_r.error`
  - Update Step 1.6 log message from "Syncing env vars from vault" to "Verifying .env symlink"

### 3. Update remote-update.sh
- **Task ID**: build-update-script
- **Depends On**: none
- **Validates**: `scripts/remote-update.sh` contains symlink repair block
- **Assigned To**: secrets-builder
- **Agent Type**: builder
- **Parallel**: true
- Add idempotent symlink creation block after lockfile setup
- Guard against missing vault `.env` with clear warning log

### 4. Update Documentation
- **Task ID**: update-docs
- **Depends On**: build-symlink
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: true
- Update `docs/features/config-architecture.md` Config Files table and add Secrets subsection
- Update `CLAUDE.md` with `## Secrets` section
- Update `.env.example` header comment

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-env-sync, build-update-script, update-docs
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `ls -la ~/src/ai/.env` shows symlink
- Run `python -m ruff check scripts/update/env_sync.py`
- Verify CLAUDE.md contains secrets rule
- Verify config-architecture.md updated
- Run relevant tests

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| .env is a symlink | `test -L ~/src/ai/.env && echo ok` | output contains ok |
| Symlink target correct | `readlink ~/src/ai/.env` | output contains Desktop/Valor/.env |
| Lint clean | `python -m ruff check scripts/update/env_sync.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/update/env_sync.py` | exit code 0 |
| CLAUDE.md has secrets rule | `grep -c "Desktop/Valor/.env" CLAUDE.md` | output > 0 |
| config-architecture updated | `grep -c "Desktop/Valor" docs/features/config-architecture.md` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Operator, Skeptic | `run.py` `UpdateResult` dataclass + Step 1.6 logging crash if `EnvSyncResult` fields renamed | Task 2 extended | Update `run.py:119` type annotation + lines 330–335 log block to match new `EnvSyncResult(symlink_ok, created, error)` shape |
| CONCERN | Operator, Adversary | `remote-update.sh` sources `.env` at line 16 before symlink repair block; ordering bug on first run | Task 3 updated | Place symlink repair block before `.env` source line |
| CONCERN | Simplifier, User | Double `load_dotenv` calls in 5 files become misleading noise | Task 4 updated | Add one-line comment at each double-load site: `# vault path is now symlink target — second load is intentional no-op` |
| CONCERN | Adversary, Skeptic | `.gitignore` covers `.env` by name; git symlink behavior not verified | Task 5 updated | Run `git check-ignore -v .env` after symlink creation in validator |
| NIT | — | Task 4 missing `Validates:` field | Task 4 updated | Added grep validation command |

---

## Open Questions

None — the approach is well-defined, all affected files identified, and no business trade-offs require supervisor input.
