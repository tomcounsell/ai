# Changelog

All notable changes to Valor are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) v1.1.0. Versioning uses date-based milestones (the project has no formal semver releases yet). Entries are maintained manually — PRs for notable features should include a new entry under `[Unreleased]`.

---

## [Unreleased]

---

## [0.11.0] - 2026-04-15

### Added
- **Terminal emoji upgrade:** semantic `find_best_emoji` lookup for terminal reactions, replacing hardcoded emoji sets with LLM-judged `EmojiResult` with lazy cache (#992)
- **YouTube search tool:** `valor-youtube-search` CLI via yt-dlp for video lookup from any agent or skill (#988)
- **WebSearch in /do-plan:** automated web research phase (Phase 0.7) fetches current best-practice context before plan drafting (#982)
- **Worker health check at enqueue:** `valor-session create` and `valor-session status` warn when no worker is consuming the queue (#983)
- **Memory status subcommand:** `python -m tools.memory_search status` reports Redis health, record counts, and superseded ratio (#970)
- **Memory consolidation reflection:** nightly LLM-based semantic dedup merges near-duplicate memory records via Haiku; original records preserved with `superseded_by` links (#959)
- **Reflections package extraction:** replaced 3086-line monolith with 18-unit `reflections/` package; each unit independently testable and schedulable (#967)

### Fixed
- Startup recovery no longer hijacks local CLI sessions — discriminates by `session_id` instead of blind `session[0]` (#989)
- SDLC pipeline continuation race in `_handle_dev_session_completion` — ordering invariant enforced (#990)
- Harness session continuity via `--resume` to prevent context overflow (#981)

---

## [0.10.0] - 2026-04-13

### Added
- **Email bridge:** IMAP/SMTP secondary transport receives and sends email as a first-class channel alongside Telegram (#908)
- **Email personas:** domain wildcard routing and customer-service persona for email sessions
- **Sentry integration:** `sentry-cli` installed via `/update`, opt-in reflection, and release tracking wired to PRs (#916)
- **SDLC stage model selection:** Opus for Plan/Critique/Review, Sonnet for Build/Test/Patch; hard-PATCH builder session resume via `--resume` flag (#909)
- **CLI harness unification:** all session types (PM, Dev, Teammate) migrated to the `claude -p` CLI harness; SDK execution path removed (#912)
- **Session inspect/children/--full-message:** `valor-session inspect`, `children`, and `--full-message` subcommands for deep session debugging (#930)
- **Per-stage SDLC addenda:** `docs/sdlc/` repo-specific addenda read by SDLC skills at runtime; reflection agent updates them post-merge (#932)
- **PM child fan-out:** PM sessions can spawn multiple Dev sessions for multi-issue SDLC prompts (#903)
- **Chunked document retrieval:** fine-grained semantic search over documents split into overlapping chunks (#864)

### Changed
- All session types unified under CLI harness — SDK session execution removed

### Fixed
- Session isolation bypass: dev sessions now always get worktree isolation regardless of PM creation path (#888)
- PM session read-only Bash allowlist enforced to prevent silent mutations (#883)

---

## [0.9.0] - 2026-04-09

### Added
- **Sustainable self-healing:** circuit-gated queue governance with flood backoff and dynamic catchup (#842)
- **Project-keyed worker serialization:** worker processes one session per project at a time, preventing cross-project interference (#832)
- **Analytics system:** unified metrics collection with daily rollup and dashboard integration (#895)
- **Worker hibernation:** sessions pause mid-execution on API failures and drip-resume on recovery (#844)
- **PM session read-only Bash allowlist:** restricts PM sessions to safe shell operations only (#883)
- **AI semantic evaluator in /do-build:** acceptance criteria evaluated by LLM after deterministic checks pass (#807)

### Fixed
- Summarizer fallback: agent self-summary via session steering when summarizer is unavailable (#892)
- Deterministic reply-to root cache and completed session resume (#922)

---

## [0.8.0] - 2026-03-25

### Added
- **Subconscious memory:** persistent long-term memory with bloom-filter recall, post-session extraction, and `<thought>` injection via Claude Code hooks (#515)
- **Claude Code memory hooks:** UserPromptSubmit ingest, PostToolUse recall with file-based sliding window, Stop extracts observations (#525)
- **Autoexperiment:** nightly autonomous prompt optimization framework for observer/summarizer targets via LLM self-evaluation (#411)

### Changed
- Memory recall uses multi-query decomposition to split large keyword sets into retrieval clusters for broader coverage

---

## [0.7.0] - 2026-03-17

### Added
- **Test suite organization:** feature markers, e2e test layer, and `tests/README.md` index with coverage map (#431)
- **Pipeline graph:** directed cyclic graph for SDLC stage transitions with first-class failure cycles (#314)

---

## [0.6.0] - 2026-02-26

### Added
- **Unified AgentSession model:** single Popoto-backed model for all session types (PM, Dev, Teammate) with bullet-point summarizer (#180)
- **Stage-aware auto-continue:** SDLC sessions auto-continue until the stage is complete or blocked on a genuine open question (#185)
- **Session steering:** externalize guidance injection via `queued_steering_messages`; any process can steer a running session (#749)
- **Daydream v2:** autonomous bug-fixing via plan-build-PR cycles with multi-repo support (#148)

---

## [0.5.0] - 2026-02-13

### Added
- **SDK modernization:** migrated core agent execution to Claude Agent SDK with tool-use and multi-turn support
- **Telegram messaging:** full send/receive pipeline with session context surviving across conversations
- **Link summarization:** URL content fetch and LLM summarization with Redis caching

---

## [0.4.0] - 2026-01-31

### Added
- **SDLC pipeline:** Plan → Critique → Build → Test → Review → Docs → Merge skill chain with failure cycles
- **`/do-plan` skill:** Shape Up-style feature planning with structured frontmatter, team orchestration, and critique rounds
- **`/do-build` skill:** worktree-isolated plan execution with builder/validator agent teams
- **Worktree isolation:** per-feature git worktrees for parallel work without collisions
- **Session management:** `valor-session` CLI for creating, listing, inspecting, steering, and killing sessions

---

## [0.3.0] - 2026-01-20

### Added
- **YouTube video transcription:** `yt-dlp` + Whisper pipeline for extracting transcripts from YouTube URLs
- **Google Workspace integration:** Gmail, Calendar, Docs, Sheets via `gws` CLI and MCP servers

---

## [0.2.0] - 2025-11-28

### Added
- **Dashboard:** web UI on `localhost:8500` showing live sessions, system health, reflections, and machine state
- **Self-healing:** watchdog service with crash tracking, 5-level escalation, and automatic recovery

---

## [0.1.0] - 2024-08-13

### Added
- Initial project bootstrap: Telegram bridge, standalone worker, Redis-backed session queue (Popoto ORM), and agent harness scaffolding
