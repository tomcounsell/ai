---
status: docs_complete
type: feature
appetite: Small
owner: Valor
created: 2026-05-22
tracking: https://github.com/tomcounsell/ai/issues/1410
last_comment_id:
revision_applied: true
---

# Teammate session: code-write hard lock + capable prompt

## Problem

Today the "teammate" `session_type` claims to be a read-mostly conversational role but enforces nothing: `agent/teammate_handler.py::build_teammate_instructions()` is prose that tells the model "Do NOT write files outside `~/work-vault/`, do NOT spawn sub-agents, do NOT modify code." The Claude SDK runs in `bypassPermissions`, and `agent/hooks/pre_tool_use.py` only branches on `SESSION_TYPE=pm`. A teammate session is functionally a Dev session wearing a polite costume.

The user wants the opposite shape for Cyndra Dev (and every other teammate-routed group):

**Current behavior:**
- Cyndra Dev chat lands on the `teammate` persona, which prose-prohibits modifying code, running scripts, restarting services, or doing anything operational.
- A motivated/forgetful model can absolutely write to `agent/sdk_client.py` from a teammate session today — nothing stops it.
- Even when the model follows the prompt, it can't help with legitimate ops work (deploys, restarts, password resets) because the prompt says no.

**Desired outcome:**
- Teammate sessions can do real operational work: run scripts, restart services, edit docs, update `.claude/` skills, write to the knowledge base, reset passwords, etc.
- ONE hard rule, enforced in code (not prose): writes to source code paths require spawning a Dev session.
- The block message is a useful redirect, not a wall: it includes the exact `valor-session create` command the teammate should suggest to the human.
- Bash stays open. Audit log captures every teammate Bash call so misuse is visible after the fact.

## Freshness Check

**Baseline commit:** `a7d8144510713e8dc540a008225e478e759917bf` (main at plan time)
**Issue filed at:** N/A — plan originated from in-session conversation, tracking issue created at Phase 2.5.
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/hooks/pre_tool_use.py:97-114` — `_is_pm_session()` + `_is_pm_allowed_write()` exist as described; pattern to mirror is intact.
- `agent/hooks/pre_tool_use.py:386-494` — `pre_tool_use_hook` handles Write/Edit/Bash; PM enforcement branches at lines 436 and 480.
- `agent/teammate_handler.py:19-100` — `build_teammate_instructions()` returns the prose constraint string; no parameters, no per-project branching.
- No `config/personas/teammate.md` file exists; `_load_persona_overlay_with_log("teammate", ...)` falls back to whatever its default behavior is. Teammate behavior is driven by `build_teammate_instructions()`, not a persona overlay file.

**Cited sibling issues/PRs re-checked:** N/A — no sibling issues referenced.

**Commits on main since baseline (touching pre_tool_use.py or teammate_handler.py):** None at plan time.

**Active plans in `docs/plans/` overlapping this area:** None — quick scan of plan filenames shows no other in-flight work on teammate or pre_tool_use.

## Prior Art

Searched closed issues and merged PRs for related teammate/persona/permission work:

- **Issue #1268** (closed 2026-05-08): Composed persona system — single (persona × access-level × channel) builder. *Relevance: this plan modifies behavior at the same layer (per-`session_type` enforcement). The composed-persona work doesn't touch `pre_tool_use.py` enforcement, so no conflict.*
- **Issue #955** (closed 2026-05-19): customer-service persona fix — TEAMMATE read-only override. *Relevance: customer-service inherits teammate-style constraints via prompt. This plan does NOT loosen customer-service — only teammate. We need to make sure customer-service still gets the prose-level restriction it expects.*
- **Issue #648** (closed 2026-04-03): Added TEAMMATE as a first-class session type. *Relevance: this is the foundation. `SESSION_TYPE=teammate` env var was added here; our enforcement piggybacks on it.*
- **Issue #827** (closed 2026-04-09): Bug where PM sessions got teammate read-only restriction. *Relevance: confirms there has been historical confusion between PM and teammate restrictions. Our new teammate enforcement must NOT bleed into PM sessions (already guarded by separate `_is_pm_session()`/`_is_teammate_session()` checks).*
- **PR #1333** (merged 2026-05-09): teammate delivery-review prompt fix. *Relevance: shows `build_teammate_instructions()` is a live, frequently-tuned surface. Our rewrite must preserve the delivery-review prose (tool-call contract) verbatim.*

No prior attempts have added code-level enforcement for teammate sessions. This is greenfield enforcement, not a re-attempt.

## Architectural Impact

- **New dependencies:** None. Pure additions to `pre_tool_use.py` + rewrite of `build_teammate_instructions()`.
- **Interface changes:** None at module-public level. New private helpers `_is_teammate_session()` and `_teammate_is_allowed_write()` in `pre_tool_use.py`.
- **Coupling:** Adds a code-level dependency from `pre_tool_use.py` onto the `SESSION_TYPE=teammate` env var contract (same contract PM already uses).
- **Data ownership:** Unchanged.
- **Reversibility:** High. Revert the two files and ship. No data migration, no state changes.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (standard code review on PR)

## Prerequisites

No prerequisites — this work modifies in-tree Python files and adds tests. No new secrets, services, or external dependencies.

## Solution

### Key Elements

- **`_is_teammate_session()`** in `pre_tool_use.py`: env-var check, mirrors `_is_pm_session()`.
- **`TEAMMATE_ALLOWED_WRITE_PATHS`**: universal allowlist constant (recursive prefixes + top-level filenames + the vault root).
- **`_teammate_is_allowed_write(file_path)`**: path-prefix check (no symlink resolution; see Risks). Returns True iff the path is on the allowlist.
- **Teammate branch in `pre_tool_use_hook`**: blocks Write/Edit/MultiEdit to disallowed paths with a block message that names the redirect (`valor-session create --role dev ...`).
- **Teammate Bash audit log**: `logger.info("[teammate-audit] ...", ...)` on every Bash invocation when `SESSION_TYPE=teammate`. Bash is NOT blocked — it's logged.
- **Rewritten `build_teammate_instructions()`**: drops the "do not modify code / do not spawn sub-agents" prose; adds an "if you need to change code, spawn a Dev session via this command" block. Preserves the delivery-review prose (PR #1333) verbatim.

### Flow

User asks teammate to do something operational:
- **`restart the email bridge`** → Bash `./scripts/valor-service.sh email-restart` → allowed, logged in audit trail → done.
- **`update the README to mention the new flag`** → Edit `README.md` → allowed (top-level `*.md`) → done.
- **`fix the bug in agent/sdk_client.py`** → Edit `agent/sdk_client.py` → blocked with: *"Code paths require a Dev session. Want me to spawn one? `valor-session create --role dev --slug <slug> --message <task>`"* → teammate suggests this to the human → human confirms → teammate runs the CLI to spawn a Dev session.
- **`save this note to the vault`** → Write `~/work-vault/notes/foo.md` → allowed (vault root prefix) → done.

### Technical Approach

**Universal allowlist** — see the algorithm block below for the canonical constants. Conceptually:

- **Anchored top-level dirs** (anywhere under project root, but only as `parts[0]`): `docs`, `.claude`, `.github`, `wiki`, `skills`
- **Top-level filenames** (exactly at project root, depth=1): README.md, CHANGELOG.md, CLAUDE.md, AGENTS.md, GEMINI.md, OPENCLAW.md, SWARM.md, PLAN.md, TODO.md, ROADMAP.md, CONTRIBUTING.md, SECURITY.md, MAINTENANCE.md, DEPLOYMENT.md, INSTRUCTIONS.md, LICENSE, NOTICE, CNAME, .gitignore, .gitattributes, .editorconfig
- **Top-level extensions** (depth=1 only): `.md` (catches PHASE_*, MODERNIZATION_*, etc.)
- **Absolute prefixes**: `~/work-vault/` (knowledge base)

**`_teammate_is_allowed_write(file_path)` algorithm:**

The algorithm has TWO passes — a normalization pass (catches path-traversal via `..`) and a realpath pass (catches symlink escape). Both passes must agree the path is allowed.

```python
def _teammate_is_allowed_write(file_path: str) -> bool:
    if not file_path:
        return False

    # Resolve project root from cwd (matches the cwd contract the worker
    # establishes when spawning the session — same contract PM relies on).
    project_root = os.path.realpath(os.getcwd())

    # PASS 1 — normalize input to defeat `..` traversal.
    normalized = os.path.normpath(file_path)
    if not _path_on_allowlist(normalized, project_root):
        return False

    # PASS 2 — realpath to defeat symlink escape.
    # Use realpath on the parent directory + basename so we don't follow a
    # symlink that doesn't yet exist (Write creates files), but DO follow any
    # symlink in the parent chain (which is the actual escape vector).
    try:
        parent = os.path.realpath(os.path.dirname(os.path.abspath(normalized)))
        resolved = os.path.join(parent, os.path.basename(normalized))
    except OSError:
        return False  # Can't resolve → default-deny
    if not _path_on_allowlist(resolved, project_root):
        return False

    return True


def _path_on_allowlist(path: str, project_root: str) -> bool:
    """Check whether `path` is on the teammate write allowlist.

    `path` may be relative or absolute. `project_root` is the project's
    real (symlink-resolved) working directory.
    """
    # Absolute prefixes (vault) — match BEFORE rebasing to project root.
    abs_path = os.path.abspath(path)
    for prefix in TEAMMATE_ALLOWED_ABSOLUTE_PREFIXES:
        if abs_path.startswith(prefix):
            return True

    # Rebase to project-root-relative for the directory/top-level checks.
    # If the path is outside project_root, fall through to deny (vault was
    # handled above; everything else outside the project is off-limits).
    try:
        rel = os.path.relpath(abs_path, project_root)
    except ValueError:
        return False  # Different drives (Windows); shouldn't happen on macOS.
    if rel.startswith(".."):
        return False  # Outside project root.

    rel_posix = rel.replace("\\", "/")
    parts = rel_posix.split("/")
    first = parts[0] if parts else ""

    # Directory prefix check — ANCHORED to parts[0], not substring.
    # parts[0] must equal one of the allowed top-level dir names.
    # NOTE: require len(parts) > 1 so a bare file literally named `docs`
    # (or `.claude`, `wiki`, etc.) at project root does NOT match the
    # directory rule. Bare top-level files go through the explicit
    # filename/extension allowlist below.
    if len(parts) > 1 and first in TEAMMATE_ALLOWED_DIR_NAMES_AT_ROOT:
        return True

    # Top-level file check — exactly ONE part means top-level file.
    if len(parts) == 1:
        if first in TEAMMATE_ALLOWED_TOPLEVEL_NAMES:
            return True
        # Top-level *.md (covers CHANGELOG, PHASE_*, MODERNIZATION_*, etc.)
        if any(first.endswith(ext) for ext in TEAMMATE_ALLOWED_TOPLEVEL_EXTENSIONS):
            return True

    return False
```

Constants change accordingly (replacing the earlier "directory prefix substrings" with anchored dir names):

```python
TEAMMATE_ALLOWED_DIR_NAMES_AT_ROOT: frozenset[str] = frozenset({
    "docs", ".claude", ".github", "wiki", "skills",
})
TEAMMATE_ALLOWED_TOPLEVEL_NAMES: frozenset[str] = frozenset({
    "README.md", "CHANGELOG.md", "CLAUDE.md", "AGENTS.md", "GEMINI.md",
    "OPENCLAW.md", "SWARM.md", "PLAN.md", "TODO.md", "ROADMAP.md",
    "CONTRIBUTING.md", "SECURITY.md", "MAINTENANCE.md", "DEPLOYMENT.md",
    "INSTRUCTIONS.md", "LICENSE", "NOTICE", "CNAME",
    ".gitignore", ".gitattributes", ".editorconfig",
})
TEAMMATE_ALLOWED_TOPLEVEL_EXTENSIONS: tuple[str, ...] = (".md",)
TEAMMATE_ALLOWED_ABSOLUTE_PREFIXES: tuple[str, ...] = (
    os.path.expanduser("~/work-vault/"),
)
```

**Why two passes:**
- Pass 1 (normpath): catches `docs/../agent/foo.py` — the syntactic escape.
- Pass 2 (realpath): catches `ln -s ../agent docs/escape && write docs/escape/sdk_client.py` — the symlink escape. The substring-match would see `/docs/` in the input and allow; the OS write would follow the symlink and land in `agent/sdk_client.py`. Realpath resolution after normpath fixes this.

**Anchoring to `parts[0]` instead of substring** also fixes positional promiscuity: `agent/docs_handler/foo.py` no longer matches the `docs/` rule by accident.

**Block message** when a teammate hits a disallowed path:

```
Blocked: teammate sessions cannot write to '<path>'. This path looks like source
code, which requires a Dev session. To proceed:

  valor-session create --role dev --slug <slug> --message "<task description>"

Suggest this to the human first and wait for explicit confirmation before
spawning the Dev session. Teammates may write to: docs/, .claude/, .github/,
wiki/, skills/, top-level *.md and meta files, and ~/work-vault/.
```

**Hook wiring** in `pre_tool_use_hook`:

- Extend the Write/Edit branch (line ~424) to also accept `MultiEdit` (currently a gap — see Risks).
- After the sensitive-path check and after the PM-session check, add:
  ```python
  if _is_teammate_session() and not _teammate_is_allowed_write(file_path):
      return {"decision": "block", "reason": <block_message>}
  ```
- Bash branch: after the sensitive-file check and PM check, add audit logging (NOT a block). **The audit call MUST be wrapped in try/except** — failure to log must never block the user's command (matches the liveness-writer pattern at lines 402-408):
  ```python
  if _is_teammate_session():
      try:
          truncated = (command or "")[:500]
          logger.info(f"[teammate-audit] bash command={truncated!r}")
      except Exception as _audit_err:
          logger.debug("[pre_tool_use] teammate audit log failed (non-fatal): %s", _audit_err)
  ```

**Audit log destination — decision: `logger.info` in `pre_tool_use.py`.**
- *Why not Redis:* extra infra, connection-fail modes, would need a new queue/list with no consumer yet.
- *Why not a separate file:* duplicates log routing that worker.log already handles.
- *Why `logger.info`:* the hook already logs PM blocks at WARNING; teammate audits at INFO with a `[teammate-audit]` tag are trivially greppable in `logs/worker.log` and survive log rotation. If volume becomes an issue or we want structured audit, we can pipe to Redis later — but that's YAGNI today.

**Symlink resolution policy — decision: prefix-check the input string, do NOT resolve symlinks.**
- *Reasoning:* the LLM passes a path it intends to write to. If it passes `repo/.env`, that's caught by `SENSITIVE_PATHS` regardless. If it passes a symlink-into-vault path inside the repo, the LLM clearly intends to edit the vault — and `~/work-vault/` is on the allowlist anyway. Resolving symlinks adds disk I/O per write and creates a TOCTOU window (symlink target can change between resolve and write). The string-prefix check is faster, deterministic, and matches PM's existing approach.
- *Confirmed:* this repo's `.env` was historically symlinked to `~/Desktop/Valor/.env` (not `~/work-vault/`), so the .env-into-vault concern raised in scoping turns out to be moot — `.env` is in `SENSITIVE_PATHS` and would be blocked for all session types regardless.

**Teammate prompt rewrite** in `build_teammate_instructions()`:

Keep:
- IDENTITY section
- CONVERSATIONAL RULES section
- RESEARCH FIRST section (chat history search)
- DELIVERY REVIEW section (verbatim — tool-call contract from PR #1333)

Drop:
- "Do NOT write files outside ~/work-vault/, create branches, run tests, or modify code"
- "Do NOT use the Agent tool to spawn sub-agents"
- "If the question requires actual work (fixes, changes, deployments), say so and suggest the user request it explicitly"

Add (replacing the dropped block):
- "TOOL POSTURE: You have full read/write/Bash access. The pre_tool_use hook enforces ONE rule: writes to source code (anything outside `docs/`, `.claude/`, `.github/`, `wiki/`, `skills/`, top-level meta files, and `~/work-vault/`) are blocked. If you hit a block, suggest spawning a Dev session to the human via: `valor-session create --role dev --slug <slug> --message <task>`. Don't spawn it unilaterally — get human confirmation first."
- "OPERATIONAL WORK ENCOURAGED: running scripts, restarting services, querying state, updating docs, resetting credentials via documented tools — all in scope. Be useful."
- "WHEN BLOCKED: Do NOT apologize or treat the block as a permanent stop. The block is a routing decision, not a refusal. Your job on a block is to (1) restate what the human asked for in concrete terms, (2) propose the exact `valor-session create --role dev --slug <slug> --message <task>` command you'd run, (3) wait for the human's go-ahead. The block message itself contains the command template — surface it to the human, don't swallow it."

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks introduced.
- [ ] The audit log call is wrapped in try/except (fire-and-forget pattern matching the existing liveness write at line 402-408) — if it raises, the hook must still complete normally.

### Empty/Invalid Input Handling
- [ ] `_teammate_is_allowed_write("")` returns False — defensive default-deny.
- [ ] `_teammate_is_allowed_write(None)` returns False — covered by the empty check.
- [ ] Path with backslashes (`docs\foo.md`) is normalized to forward slashes before matching.
- [ ] Path with `..` components (e.g., `docs/../agent/foo.py`) — the substring match accepts `/docs/` even though the resolved path is `agent/foo.py`. **This is a path-traversal escape.** Mitigation: normalize via `os.path.normpath` before the prefix check (test the normalized path, not the raw input).

### Error State Rendering
- [ ] When the hook blocks, the `reason` string is propagated to the LLM by the SDK (existing infra). The block message must be self-contained — include the `valor-session create` command verbatim so the LLM can copy/paste it.

## Test Impact

- [ ] `tests/unit/test_pm_session_permissions.py` — UPDATE: keep all existing PM assertions intact. Add a sibling test class `TestTeammateWriteRestriction` with cases mirroring `TestPMBashRestriction`'s structure (allow/deny matrix for representative paths).
- [ ] `tests/unit/test_qa_handler.py` — UPDATE: the existing tests assert specific substrings from the old prose (`"Do NOT write files"`, `"Do NOT use the Agent tool"`). After rewriting `build_teammate_instructions()`, these assertions will fail. Replace with assertions on the NEW prose markers (`"valor-session create --role dev"`, `"OPERATIONAL WORK ENCOURAGED"`, and the preserved DELIVERY REVIEW substrings).
- [ ] New file: `tests/unit/test_teammate_write_restriction.py` — REPLACE/CREATE: dedicated test module for `_teammate_is_allowed_write()` covering the full allow/deny matrix:
  - **Allow:** `docs/foo.md`, `docs/features/x.md`, `.claude/skills/y.md`, `.github/workflows/z.yml`, `wiki/Home.md`, `skills/custom.md`, `README.md`, `CHANGELOG.md`, `LICENSE`, `.gitignore`, `~/work-vault/notes/n.md`, `~/work-vault/AI Valor Engels System/foo.md`
  - **Deny — code paths:** `agent/sdk_client.py`, `bridge/telegram_bridge.py`, `worker/__main__.py`, `tools/foo.py`, `tests/unit/x.py`, `apps/web/page.tsx`, `packages/core/index.ts`
  - **Deny — positional promiscuity:** `agent/docs_handler/foo.py` (must NOT match `/docs/` rule), `tools/wiki_scraper.py` (must NOT match `/wiki/` rule), `agent/skills_router.py` (must NOT match `/skills/` rule)
  - **Deny — path traversal:** `docs/../agent/foo.py`, `.claude/../bridge/x.py`, `docs/sub/../../agent/y.py`
  - **Deny — symlink escape:** Set up a tmp dir with `ln -s ../agent docs/escape`, attempt write to `docs/escape/sdk_client.py`, assert denied via realpath check. Use `tmp_path` fixture and `monkeypatch` cwd.
  - **Deny — top-level non-allowlist file:** `pyproject.toml`, `package.json`, `Makefile`, `Dockerfile`, `manage.py`
  - **Deny — bare top-level dir-name file:** `docs` (no extension, at project root) must be DENIED even though `parts[0]="docs"`. Covered by the `len(parts) > 1` guard. Likewise `.claude`, `wiki`, `skills` as bare files.
  - **Deny — nested non-allowlist:** `apps/api/README.md` (top-level *.md rule does NOT extend to nested READMEs)
  - **Deny — outside project root:** `/tmp/foo.md`, `/etc/passwd`
  - **Deny — empty/invalid:** `""`, `None` (via type guard)
  - **MultiEdit case:** same allow/deny matrix exercised through the `MultiEdit` tool_name branch of `pre_tool_use_hook`.
- [ ] `tests/unit/test_qa_nudge_cap.py` — no change expected (tests `TEAMMATE_MAX_NUDGE_COUNT` only).
- [ ] `tests/unit/test_steering_mechanism.py` — no change expected (imports `TEAMMATE_MAX_NUDGE_COUNT` only).

## Rabbit Holes

- **Don't try to parse Bash for code-path writes.** `sed -i agent/foo.py` will slip through the Write/Edit guard. We accept this — Bash audit log catches it after the fact. Trying to lint arbitrary Bash for path-mutation intent is brittle (cp, mv, tee, sed, awk, redirection, heredocs, `git apply`...) and we will lose the arms race. **Note:** the symlink-escape via `ln -s` followed by Write is closed because pass 2 of the allowlist check uses `os.path.realpath` — but `ln -s ../agent docs/escape && sed -i 's/x/y/' docs/escape/sdk_client.py` is still a Bash-route escape we're accepting via audit-only.
- **Don't add per-project allowlist extensions** (e.g., `projects.<key>.teammate.writable_paths`). The user explicitly chose general-over-tailored. Edge cases (Hugo content sites, Django templates) take the dev-session redirect.
- **Don't introduce a new `session_type="ops"` or `"sysadmin"`.** Teammate already exists; we're just making it honest. A new session type adds routing complexity for no enforcement benefit.
- **Don't resolve symlinks.** Tempting because "what if the LLM writes through a symlink into a code path?" — but the LLM passes the path it sees, the SDK writes through whatever Python's `open()` does, and TOCTOU makes resolution unreliable anyway. Trust the input string + the `SENSITIVE_PATHS` net.
- **Don't refactor PM's `_is_pm_allowed_write` to share code with teammate's.** PM's allowlist is `docs/` only; teammate's is a much wider set. Sharing would force a parameter explosion. Keep them as parallel helpers.

## Risks

### Risk 1: MultiEdit is currently un-handled in `pre_tool_use_hook`
**Impact:** PM session can already bypass write restrictions via MultiEdit (existing latent bug). Teammate would inherit the same gap.
**Mitigation:** Extend the `tool_name in ("Write", "Edit")` check to `tool_name in ("Write", "Edit", "MultiEdit")` for both PM and teammate enforcement. Add a regression test for MultiEdit. This is in-scope for this plan since the teammate enforcement needs it; PM also benefits.

### Risk 2: Path-traversal escape (`docs/../agent/foo.py`)
**Impact:** Substring-match for `/docs/` would allow this write, leaking through the allowlist.
**Mitigation:** `os.path.normpath` is applied as pass 1 of the allowlist check, BEFORE matching. Test case in `test_teammate_write_restriction.py` covers this explicitly with multiple traversal variants.

### Risk 2b: Symlink escape (`ln -s ../agent docs/escape`)
**Impact:** Surfaced during plan critique. A teammate can create a symlink via Bash (`ln -s ../agent docs/escape`) then issue `Write("docs/escape/sdk_client.py", ...)`. The substring check sees `/docs/` and would allow it; the OS write follows the symlink and lands in `agent/sdk_client.py`.
**Mitigation:** Pass 2 of the allowlist check uses `os.path.realpath` on the parent directory (which resolves any symlinks in the chain), then re-checks the resolved path against the allowlist. Both passes must agree the path is allowed. Test case in `test_teammate_write_restriction.py` exercises this with a real symlink in a `tmp_path` fixture. **Note:** this closes the Write/Edit/MultiEdit route; the Bash route (`sed -i docs/escape/sdk_client.py`) remains audit-only, as documented in Rabbit Holes.

### Risk 3: Teammate session running in a non-project working directory
**Impact:** If a teammate session runs with cwd outside any project (e.g., user's home), relative paths like `docs/foo.md` could resolve to unexpected places. The allowlist doesn't validate that the path is actually inside a project root.
**Mitigation:** Accept the risk for now. Teammate sessions are spawned by the worker with the project's `working_directory` as cwd (same as PM and Dev). If this assumption breaks, we'd see PM enforcement break too — they share the cwd contract. Document the assumption in a comment near the constants.

### Risk 4: Audit log volume
**Impact:** Teammate sessions doing a lot of Bash work could flood worker.log with `[teammate-audit]` lines.
**Mitigation:** Truncate command to 500 chars. Log rotation is already in place. If volume is a problem in practice, migrate to a Redis stream — but YAGNI until we see it bite.

### Risk 5: customer-service persona regression — VERIFIED MOOT
**Status:** Investigated during plan critique. Not a real risk.
**Finding:** `build_teammate_instructions()` is only called from `agent/sdk_client.py:3405` (the teammate-mode injection path). The customer-service branch at `agent/sdk_client.py:3672` loads `config/personas/customer-service.md` directly via `_load_persona_overlay_with_log("customer-service", fallback="teammate", ...)` — the `fallback="teammate"` arg refers to falling back to the *persona overlay file*, not to `build_teammate_instructions()`. Customer-service prose constraints live in `config/personas/customer-service.md` (verified: contains its own "you do not write or modify code in this mode" line), entirely independent of this plan's surface.
**No mitigation needed** — leaving entry in plan as evidence the question was asked and resolved.

## Race Conditions

No race conditions identified — the hook is synchronous, runs in-process in the Claude harness, has no shared mutable state, and reads the `SESSION_TYPE` env var which is set once at session start.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #TODO] Migrate PM allowlist to share infrastructure with teammate allowlist. *Not now — keep them parallel; refactor only when a third role appears.* (Will file as a chore issue if/when needed.)
- [SEPARATE-SLUG #TODO] Move teammate audit log to Redis stream for structured observability. *Not now — `logger.info` is sufficient, YAGNI.* (Will file when log-grep ergonomics actually hurt.)
- [SEPARATE-SLUG #TODO] Per-project allowlist extensions in `projects.json`. *Explicitly rejected by user during scoping — general solution preferred.* (Will not file; this is a "no" not a "later".)
- [EXTERNAL] Update the Cyndra Dev Telegram group's pinned message / channel topic to reflect the new teammate capabilities. *Owner action — bot can't pin in groups it doesn't admin.*

## Update System

No update system changes required. This is a pure in-tree code change — no new dependencies, no new config files, no migration steps. The `/update` skill will pick it up via `git pull` on each machine and the standard `valor-service.sh restart` (which the update flow already runs) reloads the worker with the new hook code.

## Agent Integration

No new MCP server or bridge changes required.

- The enforcement runs inside the agent's `pre_tool_use_hook`, which is loaded by the Claude harness for every session — same wiring path PM enforcement uses today.
- `build_teammate_instructions()` is consumed by the existing PM-session dispatch path (see `agent/teammate_handler.py` callers); no changes to caller signatures.
- No new CLI entry point in `pyproject.toml` — the `valor-session create --role dev` CLI referenced in the block message already exists.
- Integration test: a worker-level test that spawns a teammate session and verifies the env var (`SESSION_TYPE=teammate`) is set when the hook fires. Already covered indirectly by existing teammate tests; a new explicit assertion will be added in `test_teammate_write_restriction.py`.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/teammate-session-permissions.md` documenting:
  - The hard rule (code paths require Dev session)
  - The universal allowlist (with rationale for each entry)
  - The audit log location and grep pattern
  - How to spawn a Dev session from a teammate session
  - The dropped-prose, added-prose summary for `build_teammate_instructions()`
- [ ] Add entry to `docs/features/README.md` index table.

### Cross-Reference Updates
- [ ] Update `docs/features/pm-dev-session-architecture.md` to note that teammate sessions now have code-level write enforcement (currently it describes teammate as "conversational, Teammate persona" without enforcement detail).
- [ ] Update `CLAUDE.md` "Session Types" bullet for teammate if it claims read-only behavior (audit during build — short edit if needed).

### Inline Documentation
- [ ] Add a module docstring section to `pre_tool_use.py` (alongside the existing "PM Bash enforcement" docstring) explaining teammate enforcement and the audit log.
- [ ] Add comments at each new helper explaining the allowlist rationale and the explicit decisions on symlink resolution + path-traversal handling.

## Success Criteria

- [ ] `_is_teammate_session()` and `_teammate_is_allowed_write()` implemented and exported from `agent/hooks/pre_tool_use.py`.
- [ ] `pre_tool_use_hook` blocks Write/Edit/MultiEdit to disallowed paths when `SESSION_TYPE=teammate`, with the redirect message containing the literal string `valor-session create --role dev`.
- [ ] `pre_tool_use_hook` does NOT block Bash for teammate sessions but logs every Bash command with `[teammate-audit]` tag.
- [ ] `build_teammate_instructions()` no longer contains the prose strings `"Do NOT write files"` or `"Do NOT use the Agent tool"`; DOES contain `"valor-session create --role dev"` and the preserved DELIVERY REVIEW block verbatim.
- [ ] `tests/unit/test_teammate_write_restriction.py` covers the full matrix from Test Impact — allow cases, deny cases, positional-promiscuity cases (`agent/docs_handler/x.py` denied), path-traversal cases (`docs/../agent/x.py` denied), **symlink-escape case** (`ln -s ../agent docs/escape`; write to `docs/escape/sdk_client.py` denied via realpath), top-level-only restrictions (nested README.md denied), out-of-project-root cases, MultiEdit parity with Write/Edit.
- [ ] `build_teammate_instructions()` output contains the literal string `"WHEN BLOCKED"` and the redirect command `"valor-session create --role dev"` — tested in updated `test_qa_handler.py`.
- [ ] `tests/unit/test_qa_handler.py` updated to assert new prose markers; passes.
- [ ] `tests/unit/test_pm_session_permissions.py` still passes (no regression in PM enforcement).
- [ ] PM session MultiEdit regression test added — confirms PM allowlist now also gates MultiEdit.
- [ ] `pytest tests/unit/` passes.
- [ ] `python -m ruff check .` and `python -m ruff format --check .` pass.
- [ ] Documentation created and indexed.
- [ ] Manual smoke: spawn a teammate session via worker, observe a write-block on `agent/foo.py` and a successful write on `docs/foo.md`. Audit log line appears in `logs/worker.log` for any Bash run.

## Team Orchestration

### Team Members

- **Builder (enforcement)**
  - Name: `teammate-enforce-builder`
  - Role: Add helpers + hook branches in `pre_tool_use.py`, including MultiEdit gap fix for PM
  - Agent Type: builder
  - Resume: true

- **Builder (prompt)**
  - Name: `teammate-prompt-builder`
  - Role: Rewrite `build_teammate_instructions()` (drop restrictive prose, add operational + redirect prose, preserve delivery-review verbatim)
  - Agent Type: builder
  - Resume: true

- **Test writer**
  - Name: `teammate-test-writer`
  - Role: Create `test_teammate_write_restriction.py`, update `test_qa_handler.py`, add MultiEdit regression to `test_pm_session_permissions.py`
  - Agent Type: test-writer
  - Resume: true

- **Documentarian**
  - Name: `teammate-docs`
  - Role: Create `docs/features/teammate-session-permissions.md`, update index, cross-reference `pm-dev-session-architecture.md`, audit `CLAUDE.md`
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `teammate-validator`
  - Role: Verify all success criteria, run lint/test, smoke-test a real teammate session
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build enforcement helpers + hook branches
- **Task ID**: build-enforcement
- **Depends On**: none
- **Validates**: `tests/unit/test_teammate_write_restriction.py`, `tests/unit/test_pm_session_permissions.py` (MultiEdit regression)
- **Assigned To**: teammate-enforce-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_is_teammate_session()` helper in `agent/hooks/pre_tool_use.py`.
- Add `TEAMMATE_ALLOWED_WRITE_DIR_PREFIXES`, `TEAMMATE_ALLOWED_TOPLEVEL_NAMES`, `TEAMMATE_ALLOWED_TOPLEVEL_EXTENSIONS`, `TEAMMATE_ALLOWED_ABSOLUTE_PREFIXES` constants.
- Add `_teammate_is_allowed_write(file_path)` with normpath-based path-traversal defense.
- Extend `tool_name` check to include `MultiEdit` for both PM and teammate enforcement.
- Add teammate-block branch in Write/Edit/MultiEdit path with the redirect block message.
- Add teammate Bash audit logging (`logger.info` with `[teammate-audit]` tag, fire-and-forget try/except).
- Update module docstring to document teammate enforcement.

### 2. Rewrite teammate prompt
- **Task ID**: build-prompt
- **Depends On**: none
- **Validates**: `tests/unit/test_qa_handler.py`
- **Assigned To**: teammate-prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite `build_teammate_instructions()` in `agent/teammate_handler.py`:
  - Drop: "Do NOT write files outside ~/work-vault/...", "Do NOT use the Agent tool...", "If the question requires actual work...".
  - Keep verbatim: IDENTITY, CONVERSATIONAL RULES, RESEARCH FIRST, DELIVERY REVIEW sections.
  - Add: TOOL POSTURE block (full read/write/Bash, hook enforces one rule, redirect command).
  - Add: OPERATIONAL WORK ENCOURAGED block.
- Confirm `agent/sdk_client.py:3665-3677` customer-service branch uses `config/personas/customer-service.md` and is NOT affected by this rewrite.

### 3. Write tests
- **Task ID**: write-tests
- **Depends On**: none (can write in parallel with build tasks)
- **Validates**: itself + locks behavior contract for builders
- **Assigned To**: teammate-test-writer
- **Agent Type**: test-writer
- **Parallel**: true
- Create `tests/unit/test_teammate_write_restriction.py` covering full allow/deny matrix from Success Criteria, including MultiEdit and path-traversal cases.
- Update `tests/unit/test_qa_handler.py` to assert on new prose markers (`"valor-session create --role dev"`, `"OPERATIONAL WORK ENCOURAGED"`) and preserved DELIVERY REVIEW substrings.
- Add MultiEdit regression case to `tests/unit/test_pm_session_permissions.py` ensuring PM allowlist now gates MultiEdit identically to Write/Edit.

### 4. Validate enforcement build
- **Task ID**: validate-enforcement
- **Depends On**: build-enforcement, write-tests
- **Assigned To**: teammate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_teammate_write_restriction.py tests/unit/test_pm_session_permissions.py -v`.
- Confirm all new test cases pass.
- Confirm no regression in PM tests.

### 5. Validate prompt build
- **Task ID**: validate-prompt
- **Depends On**: build-prompt, write-tests
- **Assigned To**: teammate-validator
- **Agent Type**: validator
- **Parallel**: true (with validate-enforcement)
- Run `pytest tests/unit/test_qa_handler.py -v`.
- Manually inspect the new `build_teammate_instructions()` output for completeness (DELIVERY REVIEW preserved verbatim, redirect command present).

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-enforcement, validate-prompt
- **Assigned To**: teammate-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/teammate-session-permissions.md`.
- Add entry to `docs/features/README.md`.
- Update `docs/features/pm-dev-session-architecture.md` teammate section.
- Audit `CLAUDE.md` Session Types bullet; edit if it claims read-only.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: teammate-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full `pytest tests/unit/` and `pytest tests/integration/`.
- Run `python -m ruff check .` and `python -m ruff format --check .`.
- Smoke test: spawn a teammate session via `valor-session create --role teammate ...` (or equivalent worker path), attempt a write to `agent/foo.py` (expect block), attempt a write to `docs/foo.md` (expect success), run a Bash command (expect audit log line in `logs/worker.log`).
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_teammate_write_restriction.py tests/unit/test_qa_handler.py tests/unit/test_pm_session_permissions.py -v` | exit code 0 |
| Full unit suite | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Block message present | `grep -l 'valor-session create --role dev' agent/hooks/pre_tool_use.py agent/teammate_handler.py` | output contains both files |
| Old prose removed | `grep -c 'Do NOT use the Agent tool' agent/teammate_handler.py` | output > 0 returns 0 (i.e., grep finds no matches → exit code 1) |
| Audit tag present | `grep -c 'teammate-audit' agent/hooks/pre_tool_use.py` | output > 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — all design points were resolved during scoping conversation. The plan reflects the user's stated decisions on allowlist boundary (option A), no per-project knobs, Bash open + audit, and explicit prompt rewrite with Dev-session redirect. Ready for `/do-plan-critique` before build.
