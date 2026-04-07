---
status: Planning
type: feature
appetite: Small
owner: valorengels
created: 2026-04-07
tracking: https://github.com/yudame/cuttlefish/issues/221
last_comment_id:
---

# manage.py ep CLI for Agentic Episode Management (Phase 1)

## Problem

Agentic episode management requires verbose Django shell one-liners to inspect or update episode state. There is no ergonomic CLI for the most common Phase 1 operations: checking episode state, updating the description, reading the brief artifact, and running setup.

**Current behavior:** An agent must compose multi-line shell invocations like `manage.py shell -c "from apps.podcast.models import Episode; ep = Episode.objects.get(slug='ep10'); ep.description = '...'; ep.save()"` to do anything useful during Phase 1.

**Desired outcome:** A single `manage.py ep <slug>` command covering show, set, brief, and setup — readable, auditable, and safe to run against production with a visible warning banner.

## Prior Art

- **PR #182**: Remove dead `import_podcast_feed` command — confirmed pattern of removing stale commands; sets precedent for lean command design.
- **PR #106**: Add `generate_descriptions` command — closest reference; shows bulk-field-update pattern using `update_fields`. No slug-addressable show/set pattern exists yet.

No prior issues or PRs attempted a slug-first inspection/mutation CLI for episodes. This is greenfield within the existing command infrastructure.

## Data Flow

### `ep <slug>` (show)
1. **Entry**: slug argument parsed from `sys.argv` via argparse
2. **Lookup**: `Episode.objects.select_related("podcast", "workflow").get(slug=slug)` — raises `CommandError` on miss
3. **Render**: Print title, slug, podcast name, episode_number, status, description (truncated to 120 chars), workflow current_step + status, and artifact title list from `episode.artifacts.values_list("title", flat=True)`
4. **Output**: Formatted to stdout via `self.stdout.write`

### `ep <slug> set field=value ...`
1. **Entry**: slug + one or more `field=value` tokens
2. **Validate**: Check each field name against `Episode._meta.get_fields()` names — reject unknowns with `CommandError` before touching DB
3. **Update**: `setattr(episode, field, value)` for each pair, then `episode.save(update_fields=[...] + ["modified_at"])`
4. **Output**: Print confirmation of each field updated

### `ep <slug> brief`
1. **Entry**: slug + `brief` subcommand
2. **Lookup**: Episode, then `episode.artifacts.get(title="p1-brief")`
3. **Output**: Print artifact content, or clear "p1-brief not found" message if `DoesNotExist`

### `ep <slug> setup`
1. **Entry**: slug + `setup` subcommand
2. **Lookup**: Episode by slug
3. **Delegate**: Call `setup_episode(episode.pk)` from `apps.podcast.services.setup`
4. **Output**: Print artifact title ("p1-brief") and word count from `artifact.word_count`

### Production warning (all subcommands)
- On startup: parse `DATABASE_URL` env var, extract hostname, print `WARNING: PRODUCTION DATABASE ({host})` if host is not localhost/127.0.0.1/::1 and `LOCAL=True` (reusing the same detection logic as `settings/database.py`)

## Architectural Impact

- **New file only**: `apps/podcast/management/commands/ep.py` — no changes to models, services, or other commands
- **New dependencies**: None — imports only Django stdlib, existing models, and `setup_episode`
- **Interface changes**: None
- **Coupling**: Low — the command imports `setup_episode` directly from the service layer, consistent with how `start_episode.py` does it
- **Reversibility**: Trivially reversible — delete the file

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `apps/podcast` app exists | `ls apps/podcast/management/commands/` | Confirms management command directory |

No new env vars or external services required.

## Solution

### Key Elements

- **`ep.py` command**: Single management command file with manual subcommand dispatch — `argv[0]` determines which handler runs (`show`, `set`, `brief`, or `setup`). No argparse subparsers needed at this scale.
- **Slug lookup helper**: `_get_episode(slug)` raises `CommandError` with a clear message on miss — prevents tracebacks.
- **Field validation for `set`**: Check field names against `Episode._meta.fields` before any `setattr` — reject unknowns cleanly.
- **Production banner**: Check `DATABASE_URL` host at the top of `handle()`, print warning to `self.stderr` if remote.

### Flow

`uv run python manage.py ep <slug>` → slug lookup → print summary table

`uv run python manage.py ep <slug> set field=value` → validate field names → update → confirm

`uv run python manage.py ep <slug> brief` → slug lookup → artifact lookup → print content

`uv run python manage.py ep <slug> setup` → slug lookup → `setup_episode(pk)` → print artifact title + word count

### Technical Approach

- **Subcommand dispatch**: Check `options["subcommand"]` (positional arg after slug). Default to `show` when omitted.
- **`add_arguments`**: Two positional args — `slug` (required) and `subcommand` (optional, choices: show/set/brief/setup, default: show). Then `nargs="*"` for `field=value` pairs used by `set`.
- **`set` field parsing**: Split each `field=value` token on the first `=`. Reject tokens without `=`. Validate field against `{f.name for f in Episode._meta.fields}`.
- **Truncation**: Description truncated at 120 chars with `...` suffix in the show output.
- **`select_related`**: Use `episode.workflow` (OneToOne, accessed via related name) to avoid N+1 on show.
- **Style**: Use `self.style.WARNING` / `self.style.SUCCESS` / `self.style.ERROR` for colored output consistent with other commands.
- **Target**: ~80 lines — no helper modules, no new files beyond `ep.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- `Episode.DoesNotExist` → `CommandError` with message `"No episode found with slug '{slug}'"` — tested with unknown slug input
- `EpisodeArtifact.DoesNotExist` in `brief` → print `"p1-brief artifact not found for this episode"`, exit 0 (not an error, just informational)
- Unknown field in `set` → `CommandError` before any DB write — tested

### Empty/Invalid Input Handling
- Missing slug → argparse required field error (automatic)
- `set` with no `field=value` pairs → `CommandError("set requires at least one field=value argument")`
- `field=value` token with no `=` → `CommandError("Invalid argument: '{token}' — expected field=value")`

### Error State Rendering
- All errors use `CommandError` which Django formats to stderr with "Error:" prefix — no silent swallowing
- Production warning goes to `self.stderr` so it's visible even when stdout is captured

## Test Impact

No existing tests are affected — this is a greenfield command. New tests needed:

- `apps/podcast/tests/test_management_commands.py` (create) — unit tests covering all four subcommands and error paths

## Rabbit Holes

- **Interactive prompting**: No `input()` calls — agents can't respond to prompts
- **Rich formatting / tables**: Plain text output is sufficient and more reliable across terminals
- **Phase 2-12 subcommands**: Tracked separately in #220 — do not add them here
- **Per-field `set` flags** (`--description`, `--status`): Explicitly rejected by design; arbitrary `field=value` pairs are the constraint

## Risks

### Risk 1: Field validation allows setting internal/behavior-mixin fields
**Impact:** Agent could corrupt timestamp fields, etc.
**Mitigation:** Restrict the allowed field set to the explicitly editable fields listed in the Episode model (`title`, `slug`, `description`, `status`, `tags`, `show_notes`, `episode_number`). Validate against a whitelist rather than all `_meta.fields`. Document the whitelist in the command.

### Risk 2: Running setup against an episode that already has a workflow
**Impact:** `setup_episode()` uses `update_or_create` — idempotent by design. Low risk.
**Mitigation:** `setup_episode` already handles this gracefully; no additional guard needed. The output should note "Updated" vs "Created" based on what `setup_episode` returns (or log inspection).

## Race Conditions

No race conditions identified — all operations are synchronous, single-request, and single-threaded. The `set` command uses `update_fields` for atomic partial saves.

## No-Gos (Out of Scope)

- Phase 2-12 workflow operations (research triggers, artifact editing, workflow step advancement)
- Interactive mode or prompting
- JSON output format (plain text is sufficient for agentic use)
- Listing all episodes across podcasts
- `delete` subcommand

## Update System

No update system changes required — this feature is a single Python file within the existing Django app structure.

## Agent Integration

No MCP server changes required — the command is invoked directly via `uv run python manage.py ep` in bash tool calls. No `.mcp.json` registration needed.

## Documentation

- [ ] Update `CLAUDE.md` "Management Commands" table to include `ep` with its four subcommands
- [ ] No separate feature doc needed — command is self-documenting via `--help`

## Success Criteria

- [ ] `manage.py ep <slug>` prints: title, slug, podcast, episode_number, status, description (truncated to 120 chars), workflow step/status, and artifact title list
- [ ] `manage.py ep <slug> set description="..."` updates `Episode.description` and prints confirmation
- [ ] `manage.py ep <slug> brief` prints the `p1-brief` artifact content (or a clear "not found" message)
- [ ] `manage.py ep <slug> setup` calls `setup_episode(episode.pk)` and prints artifact title + word count
- [ ] Unknown slugs print a clear error, not a traceback
- [ ] Unknown field names in `set` print a clear error and do not save
- [ ] Production warning banner printed when `DATABASE_URL` host is not localhost
- [ ] `set` rejects tokens missing `=` with a clear error
- [ ] `set` rejects non-whitelisted field names with a clear error
- [ ] Tests pass (`DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_management_commands.py -v`)

## Team Orchestration

### Team Members

- **Builder (ep-command)**
  - Name: ep-builder
  - Role: Implement `apps/podcast/management/commands/ep.py` and tests
  - Agent Type: builder
  - Resume: true

- **Validator (ep-command)**
  - Name: ep-validator
  - Role: Verify implementation against all acceptance criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build ep.py management command
- **Task ID**: build-ep-command
- **Depends On**: none
- **Validates**: `apps/podcast/tests/test_management_commands.py` (create)
- **Assigned To**: ep-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/podcast/management/commands/ep.py` with all four subcommands (show, set, brief, setup)
- Implement production warning banner using `DATABASE_URL` host detection
- Use `select_related("podcast")` and access `episode.workflow` for OneToOne, `episode.artifacts` for artifacts
- Implement field whitelist for `set` subcommand
- Keep total file length to ~80 lines

### 2. Write tests
- **Task ID**: build-tests
- **Depends On**: build-ep-command
- **Validates**: `apps/podcast/tests/test_management_commands.py`
- **Assigned To**: ep-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/podcast/tests/test_management_commands.py`
- Test all four subcommands with valid inputs
- Test unknown slug error path
- Test unknown field error path in `set`
- Test missing `=` token in `set`
- Test `brief` when artifact does not exist
- Use `call_command` from `django.core.management`

### 3. Update CLAUDE.md management commands table
- **Task ID**: update-docs
- **Depends On**: build-tests
- **Assigned To**: ep-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `ep` row to the Management Commands table in `CLAUDE.md`

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: update-docs
- **Assigned To**: ep-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_management_commands.py -v`
- Run `uv run pre-commit run --all-files`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_management_commands.py -v` | exit code 0 |
| Lint clean | `uv run flake8 apps/podcast/management/commands/ep.py --max-line-length=88` | exit code 0 |
| Format clean | `uv run black --check apps/podcast/management/commands/ep.py` | exit code 0 |
| Command help renders | `uv run python manage.py ep --help` | output contains "ep" |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — the issue provides complete acceptance criteria and design constraints. Ready to build.
