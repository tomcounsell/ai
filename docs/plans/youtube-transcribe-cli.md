---
status: Ready
type: feature
appetite: Small
owner: Valor Engels
created: 2026-05-10
tracking: https://github.com/tomcounsell/ai/issues/1371
last_comment_id:
revision_applied: true
---

# YouTube Transcribe CLI + Agent Guidance

## Problem

Agents working outside the bridge enrichment path (local Claude Code sessions, manually-invoked dev work, ad-hoc investigation) reach for `WebFetch` when given a YouTube URL and get blocked — YouTube serves anti-bot HTML to non-browser fetchers. The rich enrichment tool that already solves this (`process_youtube_url` at `tools/link_analysis/__init__.py:376`) has no CLI surface and no mention in CLAUDE.md, so the agent has no obvious way to discover or invoke it from a shell.

**Current behavior:**
Agent encounters a YouTube URL outside a bridge-enriched Telegram message, reaches for `WebFetch`, gets blocked, and has no documented alternative. May try and fail repeatedly on the same URL.

**Desired outcome:**
A `valor-youtube-transcribe` CLI exists wrapping `process_youtube_url`. CLAUDE.md explicitly steers the agent away from `WebFetch` for YouTube URLs. One verified investigation result confirms whether enrichment ran cleanly for the original triggering message.

## Freshness Check

**Baseline commit:** f908de1227013a65eb9e59d40f281e6a09b9c4f8 (main)
**Issue filed at:** 2026-05-10T06:58:14Z (today)
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/link_analysis/__init__.py:376` — `async def process_youtube_url(url: str) -> dict` — still holds at the cited line.
- `bridge/enrichment.py:126` — `from tools.link_analysis import process_youtube_urls_in_text` — verified at line 126.
- `pyproject.toml:85` — `valor-ingest = "tools.valor_ingest:main"` — confirmed; `valor-youtube-search` registration is at line 84.
- `tools/youtube_search/cli.py` — structural template still in place; argparse + `youtube_search_sync` shape carries over.

**Cited sibling issues/PRs re-checked:** None cited in the issue body.

**Commits on main since issue was filed (touching referenced files):** None — issue filed today, no intervening commits.

**Active plans in `docs/plans/` overlapping this area:** None — no active plans touch `tools/link_analysis/` or `bridge/enrichment.py` YouTube path.

**Notes:** All file:line references are stable. Proceeding with no plan-side adjustments.

## Prior Art

- **PR #988**: feat: YouTube search tool via yt-dlp — added `valor-youtube-search` and the `tools/youtube_search/cli.py` template that this plan mirrors structurally. Successful reference implementation.
- **PR #736**: fix: caption-first YouTube transcription + enrichment suppression fix — established the captions-first / Whisper-fallback design inside `process_youtube_url`. The function this plan wraps is the result of #736's hardening.
- **PR #1167**: feat(#1161): markitdown integration for knowledge pipeline — established `valor-ingest`'s separate, simpler YouTube path (markdown sidecar output). Confirms the two paths are deliberately distinct contracts; this plan does not touch that path.

## Research

No external research needed — the work wraps an existing internal function with a thin argparse layer. No new dependencies, APIs, or ecosystem patterns. `process_youtube_url` is already battle-tested via the bridge enrichment path.

## Data Flow

1. **Entry point**: User (or agent) runs `valor-youtube-transcribe https://youtu.be/abc123` in a shell.
2. **CLI parser**: `tools/link_analysis/cli.py:main()` parses argparse args (positional `url`, optional `--json` and `--summary-only`).
3. **Async wrapper**: `asyncio.run(process_youtube_url(url))` invokes the existing async function at `tools/link_analysis/__init__.py:376`.
4. **Captions-first path**: `process_youtube_url` calls `youtube-transcript-api` to fetch captions; on failure, falls back to Whisper audio transcription.
5. **Optional summarization**: Long transcripts (>2000 chars) get GPT-4o-mini summarization stored under `summary` key.
6. **Output**: CLI formats the dict into human-readable text (default) or raw JSON (`--json`); writes to stdout. Exit 0 on `success: True`, exit 1 on failure with error message on stderr.

## Architectural Impact

- **New dependencies**: None. `process_youtube_url` already exists; the CLI is a wrapper.
- **Interface changes**: Adds one new entry to `pyproject.toml [project.scripts]`. No existing function signatures change.
- **Coupling**: Decreases coupling. Currently `process_youtube_url` is reachable only through `bridge/enrichment.py`. Adding a CLI surface gives shell-level callers (and agents via Bash) a sanctioned access path.
- **Data ownership**: No change.
- **Reversibility**: Trivial to undo. Delete the CLI file, remove the `pyproject.toml` line, revert the CLAUDE.md edit.

## Appetite

**Size:** Small

**Team:** Solo dev (one builder + one validator)

**Interactions:**
- PM check-ins: 0 (scope is unambiguous)
- Review rounds: 1 (standard PR review)

The work is mechanical (CLI mirroring existing template) plus a documentation edit plus a read-only log investigation. The bottleneck is the bridge-log investigation, which is bounded by available log retention.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `process_youtube_url` is importable | `python -c "from tools.link_analysis import process_youtube_url; print('ok')"` | The wrapped function must exist |
| `pyproject.toml` writable | `test -w pyproject.toml && echo ok` | CLI registration target |
| `logs/bridge.log` exists | `test -f logs/bridge.log && echo ok` | Required for investigation step (read-only) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/youtube-transcribe-cli.md`

## Solution

### Key Elements

- **`tools/link_analysis/cli.py`**: New file. Async-aware `main()` mirroring `tools/youtube_search/cli.py`. Wraps `process_youtube_url` via `asyncio.run`.
- **`pyproject.toml`**: Add `valor-youtube-transcribe = "tools.link_analysis.cli:main"` to `[project.scripts]` next to the existing `valor-youtube-search` line.
- **`CLAUDE.md`**: Add Quick Commands row near `valor-youtube-search`; add a one-line steering note: prefer the dedicated tool over `WebFetch` for YouTube URLs.
- **Bridge-log investigation**: Read-only grep of `logs/bridge.log` for the original triggering session's YouTube enrichment behavior. Result becomes a comment on issue #1371 (or a follow-up bug issue if a defect surfaces).

### Flow

Agent encounters YouTube URL in shell → runs `valor-youtube-transcribe URL` → reads transcript or summary on stdout → continues with the conversational task without hitting WebFetch.

For `--json`: agent runs `valor-youtube-transcribe --json URL` → parses dict → uses fields programmatically.

### Technical Approach

- **Async handling**: `process_youtube_url` is `async`. Use `asyncio.run(process_youtube_url(url))` in `main()`. The `youtube_search` CLI uses a sync wrapper (`youtube_search_sync`); we don't need a sync mirror — direct `asyncio.run` is the simplest path.
- **Output formatting**:
  - Default: Human-readable. Print `Title: ...`, `Duration: ...`, then either the summary (if present) or the full transcript.
  - `--summary-only`: If `summary` is non-empty, print only that. Otherwise fall back to the full transcript with a note.
  - `--json`: `print(json.dumps(result, indent=2))`. Raw dict from `process_youtube_url`.
- **Exit codes**: Exit 0 if `result["success"]` is True. Exit 1 on failure; print `result["error"]` to stderr.
- **No new dependencies**: All imports come from the existing `tools.link_analysis` module.
- **Investigation procedure**: `grep -n "process_youtube_url\|YouTube" logs/bridge.log | grep -B2 -A5 "tg__179144806"` (or equivalent) within ±5 minutes of the triggering message timestamp. Determine: did enrichment run? Did a guard suppress it (live-stream check, length check, exception path)?

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `process_youtube_url` already returns `{"success": False, "error": ...}` on its own internal exceptions. The CLI must surface that error to stderr and exit non-zero. Test asserts both behaviors.
- [ ] No new `except Exception: pass` blocks introduced.

### Empty/Invalid Input Handling
- [ ] Empty string URL → argparse rejects empty positional or `process_youtube_url` returns `success: False, error: "Not a valid YouTube URL"`. Test asserts exit code 1 and stderr contains the error.
- [ ] Non-YouTube URL (e.g., `https://example.com`) → same path; `extract_youtube_id` returns None; CLI exits 1.
- [ ] Whitespace-only URL → argparse rejects.

### Error State Rendering
- [ ] Live-stream URL → `process_youtube_url` returns the "Cannot transcribe live streams" error. CLI prints the error to stderr and exits 1.
- [ ] Video-too-long URL → returns "Video too long" error. CLI surfaces it.
- [ ] Network failure simulation: exception inside `process_youtube_url` → returned as `error` field; CLI exits 1.

## Test Impact

- [ ] `tests/unit/test_link_analysis.py` (if it exists) — UPDATE: add CLI smoke tests using a known short caption-bearing video; mock `process_youtube_url` to return canned dicts and assert CLI argparse + output formatting + exit codes. If no such file exists, create `tests/unit/test_link_analysis_cli.py`.
- [ ] No existing CLI tests in `tools/link_analysis/` — this is greenfield CLI surface, additive only.
- [ ] `pyproject.toml [project.scripts]` is not directly tested; the integration test below covers entry-point registration.
- [ ] `tests/integration/test_cli_entry_points.py` (if it exists) — UPDATE: add a row for `valor-youtube-transcribe --help` exit-code-0 smoke test to confirm console_scripts registration works after `pip install -e .`. If not present, add a small integration test invoking the CLI as a subprocess.
- [ ] **Live async-chain integration test (C2):** Add a `@pytest.mark.integration` test in `tests/integration/test_link_analysis_cli.py` (create if absent) that runs `subprocess.run(["valor-youtube-transcribe", "<short-captioned-vid-url>"], capture_output=True, timeout=60)` and asserts `proc.returncode == 0` and `len(proc.stdout) > 0`. Do NOT mock `process_youtube_url` here — the whole point is to catch the `asyncio.run` × `asyncio.get_event_loop()` interaction at `tools/link_analysis/__init__.py:447` (deprecated in 3.12+). Also add a deprecation-warning smoke check: invoke under `python -W error::DeprecationWarning -m tools.link_analysis.cli <url>` once during validate-cli; a non-zero exit signals the deprecation has become an error and the wrapper needs adjustment.

If neither test file pre-exists, create them. The build phase will resolve which.

## Rabbit Holes

- **Refactoring `process_youtube_url`**: explicitly out of scope. The function is battle-tested; the CLI just wraps it.
- **Unifying with `valor-ingest`'s YouTube path**: tempting (DRY) but wrong — the contracts differ (sidecar markdown file vs stdout transcript). Forced unification would harm both callers.
- **Adding new CLI flags beyond `--json` / `--summary-only`**: scope creep. Resist `--language`, `--format=srt`, `--save-to-file`, etc. unless explicitly requested.
- **Expanding the investigation into a general bridge-log audit**: stay scoped to the one triggering session. If a pattern surfaces, file a separate issue.
- **Adding a `valor-youtube` umbrella with subcommands**: out of scope. Two flat CLIs (`valor-youtube-search`, `valor-youtube-transcribe`) match the existing pattern.

## Risks

### Risk 1: `process_youtube_url`'s async-loop interaction with `asyncio.run`
**Impact:** If `process_youtube_url` internally uses `asyncio.get_event_loop()` in a way that conflicts with a fresh `asyncio.run`, the CLI would fail at startup.
**Mitigation:** Validate at build time by running the CLI against a known short caption-bearing video. The existing bridge enrichment path already calls `process_youtube_url` from inside an async context; the CLI calls it from a fresh event loop, which is a strictly simpler scenario.

### Risk 2: Long videos taking > 2 minutes to transcribe
**Impact:** Agent invocations could time out on long videos using Whisper fallback.
**Mitigation:** `process_youtube_url` already enforces `MAX_VIDEO_DURATION` and returns an error early. CLI surfaces the error correctly. For agent UX, document in CLAUDE.md that captions-first is fast (<5s) and Whisper fallback can be slow.

### Risk 3: Investigation finds a real bridge bug
**Impact:** Could expand scope mid-build.
**Mitigation:** Plan explicitly says: file a separate bug issue and link it from the comment on #1371. Do NOT expand this plan to fix the bridge bug.

## Race Conditions

No race conditions identified — the CLI is a single-shot synchronous-from-caller-perspective tool. `asyncio.run` creates a fresh event loop per invocation; no shared state across invocations.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1371] The bridge-log investigation result either confirms "enrichment ran cleanly" (resolved as a comment on #1371) OR surfaces a defect that becomes its own issue. Either outcome is bounded by this plan; the latter does not extend the plan.

If the bridge-log investigation surfaces a defect, **file the new issue immediately** and reference it from the comment on #1371. Do not patch the defect inside this plan.

## Update System

No update system changes required — adding a new console script entry to `pyproject.toml` is picked up automatically by the standard install path (`pip install -e .` re-creates the entry-point shim). The `/update` skill already runs the install step.

## Agent Integration

The new CLI is invoked via the agent's Bash tool — no MCP server, no `.mcp.json` change, no bridge import. The CLAUDE.md edit is the agent-discovery surface:

- New row in the Quick Commands table near `valor-youtube-search`.
- Tooling-section steering note: "For YouTube URLs, call `valor-youtube-transcribe` (or `process_youtube_url` from Python) — never `WebFetch`. YouTube serves anti-bot HTML to non-browser fetchers."

Integration test verifies the agent path: a subprocess invocation of `valor-youtube-transcribe --help` should exit 0 (proves console_scripts registration succeeded after `pip install -e .`).

## Documentation

### Feature Documentation
- [ ] Update `docs/features/youtube-transcription.md` — add a section on the new CLI surface alongside the existing bridge-enrichment description.
- [ ] No new entry in `docs/features/README.md` index needed — the feature already has a row; we are extending it.

### External Documentation Site
- [ ] Not applicable — this repo does not publish external docs.

### Inline Documentation
- [ ] Module docstring in `tools/link_analysis/cli.py` mirroring the `youtube_search/cli.py` style.
- [ ] No docstring updates needed in `process_youtube_url` itself — its docstring is already accurate.

## Success Criteria

- [ ] `valor-youtube-transcribe https://youtube.com/watch?v=...` returns transcript or summary on stdout, exit code 0.
- [ ] `valor-youtube-transcribe --json <url>` returns the raw `process_youtube_url` dict as valid JSON.
- [ ] `valor-youtube-transcribe --summary-only <url>` returns just the summary (or full transcript with note if no summary).
- [ ] Invalid URL exits 1 with error message on stderr.
- [ ] CLAUDE.md Quick Commands table lists the new CLI; tooling section explicitly steers away from `WebFetch` for YouTube.
- [ ] `docs/features/youtube-transcription.md` documents the CLI surface.
- [ ] Bridge-log investigation result captured as a comment on issue #1371 (either "enrichment ran cleanly" or a follow-up defect issue link).
- [ ] No churn on `valor-ingest`'s separate YouTube path — `git diff` shows zero changes to `tools/valor_ingest.py` or its tests.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] CLI is invocable as a registered console script after `pip install -e .` (smoke test: `valor-youtube-transcribe --help` exits 0).

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools.

### Team Members

- **Builder (cli)**
  - Name: cli-builder
  - Role: Create `tools/link_analysis/cli.py`, register in `pyproject.toml`, edit `CLAUDE.md`.
  - Agent Type: builder
  - Resume: true

- **Builder (investigation)**
  - Name: bridge-log-investigator
  - Role: Read-only grep of `logs/bridge.log` for session `tg__179144806_9742`; capture findings as a comment on issue #1371. If a defect is found, file a new issue.
  - Agent Type: builder
  - Resume: true

- **Validator (cli)**
  - Name: cli-validator
  - Role: Run CLI against a known short caption-bearing YouTube video; verify all three output modes; verify error path on invalid URL; verify console_scripts registration.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: feature-doc-updater
  - Role: Update `docs/features/youtube-transcription.md` with the new CLI section.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 defaults (builder, validator, documentarian) are sufficient. No specialists needed.

## Step by Step Tasks

### 1. Create the CLI module
- **Task ID**: build-cli
- **Depends On**: none
- **Validates**: `tests/unit/test_link_analysis_cli.py` (create), CLI smoke test
- **Informed By**: Prior Art #988 (template at `tools/youtube_search/cli.py`)
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/link_analysis/cli.py` with argparse-based `main()` mirroring `tools/youtube_search/cli.py` structure.
- Implement positional `url`, optional `--json`, `--summary-only` flags.
- Wrap `process_youtube_url` with `asyncio.run`.
- Format output: human-readable default; raw JSON via `--json`; summary-only via `--summary-only`.
- Exit 0 on success, 1 on failure with error to stderr.

### 2. Register console script
- **Task ID**: build-pyproject
- **Depends On**: build-cli
- **Validates**: `valor-youtube-transcribe --help` exits 0 after reinstall
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `valor-youtube-transcribe = "tools.link_analysis.cli:main"` to `[project.scripts]` in `pyproject.toml` right after the existing `valor-youtube-search` line.
- Reinstall: `pip install -e .` (or equivalent in the repo's standard env).

### 3. Edit CLAUDE.md
- **Task ID**: build-claude-md
- **Depends On**: build-pyproject
- **Validates**: grep confirms new CLI row and steering note
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a Quick Commands table row for `valor-youtube-transcribe` near the existing `valor-youtube-search` row.
- Add a one-line steering note: prefer the dedicated tool over `WebFetch` for YouTube URLs (anti-bot HTML).

### 4. Write CLI tests
- **Task ID**: build-tests
- **Depends On**: build-cli
- **Validates**: `pytest tests/unit/test_link_analysis_cli.py -q` exits 0
- **Assigned To**: cli-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tests/unit/test_link_analysis_cli.py`.
- Mock `process_youtube_url` to return canned dicts (success, error, live-stream, too-long, summary-present, summary-absent).
- Assert CLI exit codes, stdout contents, stderr contents, JSON validity.
- **Pin the no-summary note string (C3):** In `tools/link_analysis/cli.py`, define `NO_SUMMARY_NOTE = "# No summary available; full transcript below"` as a module constant. Add a unit-test case that mocks `process_youtube_url` to return `{"success": True, "transcript": "short", "summary": None}`, runs the CLI with `--summary-only`, and asserts `stdout.startswith(NO_SUMMARY_NOTE)`. Without pinning, future edits will silently drift the contract.

### 5. Bridge log investigation
- **Task ID**: investigate-bridge-log
- **Depends On**: none
- **Validates**: comment posted on issue #1371
- **Assigned To**: bridge-log-investigator
- **Agent Type**: builder
- **Parallel**: true
- Grep `logs/bridge.log` AND all rotated siblings (`logs/bridge.log.[0-9]*`) for `process_youtube_url` invocations near session `tg__179144806_9742`. Use: `grep -E "process_youtube_url|tg__179144806_9742" logs/bridge.log logs/bridge.log.[0-9]*` (shell-glob expansion).
- Determine: did enrichment run? Did a guard suppress it?
- **Log-rotation fallback (C1):** If zero matches across all rotations, "logs rotated out of retention" is a valid finding. Post a comment to #1371 stating: "Bridge logs for session tg__179144806_9742 have rotated out of retention; cannot verify enrichment behavior for the original triggering message" — this satisfies the "investigation result captured" success criterion without a synthetic conclusion. Do NOT block the build on missing log evidence.
- Post findings as a comment on issue #1371. If a defect is found, file a new bug issue and reference it from the comment.

### 6. Validate CLI end-to-end
- **Task ID**: validate-cli
- **Depends On**: build-cli, build-pyproject, build-tests
- **Assigned To**: cli-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `valor-youtube-transcribe https://youtu.be/<known-short-caption-video>` and assert non-empty transcript.
- Run `valor-youtube-transcribe --json <same-url>` and assert valid JSON with expected keys.
- Run `valor-youtube-transcribe --summary-only <same-url>` and assert short output.
- Run `valor-youtube-transcribe https://example.com` and assert exit code 1 with stderr error.
- Run `valor-youtube-transcribe --help` and assert exit code 0.
- Run unit tests: `pytest tests/unit/test_link_analysis_cli.py -q`.

### 7. Update feature docs
- **Task ID**: document-feature
- **Depends On**: validate-cli
- **Assigned To**: feature-doc-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/youtube-transcription.md` with a new CLI section: synopsis, flags, examples, exit codes, distinction from `valor-ingest`'s YouTube path.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-cli, document-feature, investigate-bridge-log, build-claude-md
- **Assigned To**: cli-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -m ruff format --check .` and `python -m ruff check .`.
- Run full test suite: `pytest tests/unit/test_link_analysis_cli.py -q` (and broader suite per `/do-test`).
- Verify no diff to `tools/valor_ingest.py`.
- Verify CLAUDE.md edits present.
- Verify investigation comment on #1371 exists.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| CLI module exists | `test -f tools/link_analysis/cli.py && echo ok` | output contains ok |
| Console script registered | `grep -F 'valor-youtube-transcribe = "tools.link_analysis.cli:main"' pyproject.toml` | exit code 0 |
| CLI help works | `valor-youtube-transcribe --help` | exit code 0 |
| CLI rejects invalid URL | `valor-youtube-transcribe https://example.com` | exit code 1 |
| CLAUDE.md updated | `grep -F 'valor-youtube-transcribe' CLAUDE.md` | exit code 0 |
| Anti-WebFetch steering | `grep -i 'WebFetch.*YouTube\|YouTube.*WebFetch\|never WebFetch' CLAUDE.md` | exit code 0 |
| valor-ingest untouched | `git diff --quiet main -- tools/valor_ingest.py` | exit code 0 (zero diff) |
| Unit tests pass | `pytest tests/unit/test_link_analysis_cli.py -q` | exit code 0 |
| Lint clean | `python -m ruff check tools/link_analysis/cli.py tests/unit/test_link_analysis_cli.py` | exit code 0 |
| Format clean | `python -m ruff format --check tools/link_analysis/cli.py tests/unit/test_link_analysis_cli.py` | exit code 0 |
| Investigation comment | `gh issue view 1371 --comments \| grep -i 'enrichment\|investigation\|process_youtube_url'` | exit code 0 |

## Critique Results

**Verdict:** READY TO BUILD (with concerns)
**Findings:** 4 (0 blockers, 3 concerns, 1 nit)

### Concerns

**C1: Bridge log for session `tg__179144806_9742` may be rotated out**
- Location: Step 5 (investigate-bridge-log), Verification table row "Investigation comment"
- Finding: `logs/bridge.log` currently has 12k lines and 5 rotated siblings (`bridge.log.1`..`.5`); a `grep tg__179144806` across the active log returns zero matches. The triggering message likely lives in a rotated file or is gone entirely.
- Suggestion: Have the investigation task `grep` across `logs/bridge.log*` (all rotations), and document a clean exit path: "logs rotated, evidence unavailable" is a valid finding and should still close the task with a comment on #1371.
- Implementation Note: Use `grep -E "process_youtube_url|tg__179144806_9742" logs/bridge.log logs/bridge.log.[0-9]*` (glob expanded by shell). If zero matches across all rotations, post a comment stating "Bridge logs for session tg__179144806_9742 have rotated out of retention; cannot verify enrichment behavior for the original triggering message" — this satisfies success-criterion "investigation result captured" without forcing a synthetic conclusion.

**C2: `asyncio.run` inside `process_youtube_url` uses `asyncio.get_event_loop()`**
- Location: Solution / Technical Approach, Risk 1
- Finding: At `tools/link_analysis/__init__.py:447`, `process_youtube_url` calls `loop = asyncio.get_event_loop()`. Inside an `asyncio.run()` context this works (returns the running loop), but `asyncio.get_event_loop()` is deprecated for use outside a running loop in Python 3.12+ and may emit DeprecationWarning. The CLI's `asyncio.run(process_youtube_url(...))` is the correct shape (creates a fresh loop), so this is not a runtime breakage — but the test plan must actually exercise the live function once, not only the mocked path.
- Suggestion: Add an integration smoke test that runs the CLI against a real short captioned video to prove the async chain works end-to-end. Risk 1's mitigation already says this; the Test Impact section should explicitly require it (currently only the validator step does).
- Implementation Note: In `tests/integration/test_cli_entry_points.py` (or new `tests/integration/test_link_analysis_cli.py`), use `subprocess.run(["valor-youtube-transcribe", "https://youtu.be/<short-captioned-vid>"], capture_output=True, timeout=60)` and assert `proc.returncode == 0` and `len(proc.stdout) > 0`. Mark with `@pytest.mark.integration` so it can be skipped offline. Do NOT mock `process_youtube_url` here — the whole point is to catch the asyncio.run × get_event_loop interaction.

**C3: `--summary-only` fallback behavior is documented in Resolved Decisions but not in the success criteria**
- Location: Success Criteria (line 205), Resolved Decisions #3
- Finding: Resolved Decision #3 says `--summary-only` falls back to "full transcript prefixed with a one-line note" when no summary exists. Success Criteria line 205 says "returns just the summary (or full transcript with note if no summary)". The success criterion is fine, but the test plan in Step 4 should assert the specific note format ("# No summary available; full transcript below") so the contract is testable.
- Suggestion: In `tests/unit/test_link_analysis_cli.py`, add a case that mocks `process_youtube_url` to return `{"success": True, "transcript": "short", "summary": None}` and asserts stdout starts with the agreed note prefix.
- Implementation Note: Pin the exact note string in the test and in the CLI module — e.g., `NO_SUMMARY_NOTE = "# No summary available; full transcript below"`. Without pinning, future edits will silently drift the contract.

### Nits

**N1: Verification row for "valor-ingest untouched" uses brittle git diff comparison**
- Location: Verification table, line 350
- Finding: `git diff main -- tools/valor_ingest.py | wc -l` evaluated as `output > -1` is always true; this check passes vacuously.
- Suggestion: Change expected to `output == 0` and the command to `git diff main -- tools/valor_ingest.py` then assert empty output, or use `git diff --quiet main -- tools/valor_ingest.py` and check exit code 0.

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and non-empty |
| Task numbering | PASS | 1-8 sequential |
| Dependencies valid | PASS | All `Depends On` references resolve |
| File paths exist | PASS | All cited source paths verified; new files (cli.py, tests) intentionally absent |
| Prerequisites met | PARTIAL | `process_youtube_url` importable; `pyproject.toml` writable; `logs/bridge.log` exists but session `tg__179144806_9742` already rotated out — see C1 |
| Cross-references | PASS | Success criteria map to tasks; no-gos / rabbit holes do not appear in the task list |

---

## Resolved Decisions

The following questions were resolved during finalization:

1. **Investigation timing**: Run in parallel with CLI build — the two are independent.
2. **Test video**: Validator picks any short caption-bearing public video and documents the URL inline in the test file. No preferred URL is mandated; reproducibility comes from the documented choice.
3. **`--summary-only` fallback**: When no summary exists (transcript < 2000 chars), print the full transcript prefixed with a one-line note (e.g., `# No summary available; full transcript below`) and exit 0. Empty output would be confusing.
