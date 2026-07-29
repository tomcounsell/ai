---
name: repo-commands
description: Exact invocations for this repo's scripts and CLIs — valor-service (bridge/worker/email), valor-session and the agent-session scheduler, sdlc-tool, the pytest wrappers, memory search, doctor, reflections, analytics, TTS/video/ingest, and computer-use. Use when you need the precise command, flag, or subcommand for a repo tool rather than guessing.
---

# Repo Command Reference

Full command table for this repository. Extracted from `CLAUDE.md` so it loads on demand
instead of sitting in context every session.


| Command | Description |
|---------|-------------|
| `./scripts/start_bridge.sh` | Start Telegram bridge |
| `./scripts/valor-service.sh status` | Check bridge status |
| `./scripts/valor-service.sh restart` | Restart bridge, watchdog, and worker after code changes |
| `./scripts/valor-service.sh worker-start` | Start standalone worker service (also re-enables launchd auto-respawn) |
| `./scripts/valor-service.sh worker-stop` | Transient stop — `bootout` only; launchd's `KeepAlive=true` may relaunch |
| `./scripts/valor-service.sh worker-restart` | Restart standalone worker |
| `./scripts/valor-service.sh worker-status` | Check worker service status |
| `./scripts/valor-service.sh worker-disable` | Stop the worker **and** disable launchd auto-respawn (stays down until `worker-enable`/`worker-start`) |
| `./scripts/valor-service.sh worker-enable` | Re-enable launchd auto-respawn (does NOT start the worker; pair with `worker-start`) |
| `./scripts/valor-service.sh email-start` | Start the email bridge (IMAP polling) |
| `./scripts/valor-service.sh email-stop` | Stop the email bridge |
| `./scripts/valor-service.sh email-restart` | Restart the email bridge |
| `./scripts/valor-service.sh email-status` | Check email bridge status, IMAP last-poll age, and SMTP relay heartbeat |
| `./scripts/valor-service.sh email-disable` | Stop the email bridge **and** disable launchd auto-respawn (stays down until `email-enable`/`email-start`) |
| `./scripts/valor-service.sh email-enable` | Re-enable launchd auto-respawn for the email bridge (does NOT start it; pair with `email-start`) |
| `./scripts/valor-service.sh email-dead-letter list` | List failed SMTP sends in dead-letter queue |
| `./scripts/valor-service.sh email-dead-letter replay --all` | Replay all dead-lettered emails |
| `./scripts/install_email_bridge.sh` | Install launchd plist for boot-time email bridge (machine-gated, idempotent; opt-in) |
| `tail -f logs/bridge.log` | Stream bridge logs |
| `pytest tests/` | Run all tests (parallel by default — `-n auto --dist=loadfile` from `pyproject.toml`). **Prefer `scripts/pytest-clean.sh` over bare `pytest`** — the wrapper reaps xdist workers on exit; without it, interrupted runs leave orphan workers consuming memory (see xdist reaper note in `pyproject.toml`). |
| `pytest tests/unit/` | Run unit tests only (~40s parallel) |
| `pytest tests/unit/ -n0` | Force serial unit run (e.g. for debugging) |
| `pytest tests/integration/` | Run integration tests only (~125s parallel) |
| `scripts/pytest-clean.sh <pytest-args>` | Run pytest with automatic xdist worker reaping (drop-in for `pytest`). Full-suite runs also take a machine-global advisory lock (a `/tmp` path keyed to the repo's git common dir, shared across all worktrees) so a second concurrent full-suite run — including one from another worktree — waits instead of oversubscribing cores — see `docs/features/full-suite-pytest-lock.md`. Disable with `PYTEST_SUITE_LOCK=0`. |
| `scripts/reap-xdist.sh` | Kill any orphan xdist workers on the system (one-shot reaper, idempotent) |
| `pytest -m sdlc` | Run tests for a specific feature (see `tests/README.md`) |
| `python -m ruff format . && python -m ruff check .` | Format and lint |
| `python -m ui.app` | Start web UI server on localhost:8500 |
| `curl -s localhost:8500/dashboard.json` | Check the dashboard — full system state as JSON (sessions, health, reflections, machine) |
| `curl -s localhost:8500/memories/metrics.json` | Corpus-wide memory ingest-quality metrics as JSON (act rate, junk rate, ingest volume, histograms); optional `?project_key=`/`?min_evidence=`. See `docs/features/memory-telemetry.md`. |
| `python -m tools.memory_eval.snapshot` | Snapshot current memory-corpus telemetry to `docs/baselines/memory-telemetry-baseline.{json,md}`; refuses to overwrite existing artifacts unless `--force` is passed |
| `tail -f logs/worker.log` | Stream worker logs |
| `python -m reflections --dry-run` | Load the reflection registry, print status, exit 0 (validates the out-of-process scheduler entry) |
| `./scripts/install_reflection_worker.sh` | Install/reload the reflection-scheduler subprocess (`com.valor.reflection-worker`; worker-role gated, self-skips + removes stale plist elsewhere) |
| `tail -f logs/reflection_worker.log` | Stream reflection-scheduler subprocess logs (`python -m reflections`) |
| `sdlc-tool stage-query --issue-number {N}` | Query SDLC pipeline state for an issue (cwd-independent — see `docs/features/sdlc-tool-resolver.md`) |
| `sdlc-tool verdict get --stage CRITIQUE --issue-number {N}` | Read the recorded critique verdict for an issue (also: `--stage REVIEW`) |
| `sdlc-tool verdict finalize --pr {N} --issue-number {N} --verdict APPROVED --blockers 0 --tech-debt 0 --run-id {ID}` | Atomically record the REVIEW verdict + `REVIEW_CONTEXT head_sha=` trailer + `completed` stage marker with fail-closed named-error readback (see `docs/features/sdlc-verdict-fail-closed-persistence.md`) |
| `sdlc-tool verdict selfcheck --pr {N} --issue-number {N}` | Read-only probe: verdict present, trailer matches PR head, marker completed — the `/do-sdlc` supervisor gates advance-past-REVIEW on `ok:true` |
| `python scripts/sdlc_reflection.py` | Run SDLC reflection manually |
| `python scripts/sdlc_reflection.py --dry-run` | Preview SDLC reflection without writing |
| `python scripts/sdlc_reflection.py --days 14` | Run with larger lookback window |
| `./scripts/install_sdlc_reflection.sh` | Install SDLC reflection launchd schedule |
| `tail -f logs/sdlc_reflection.log` | Stream SDLC reflection logs |
| `python scripts/autoexperiment.py --target observer --iterations 50` | Run autoexperiment on observer prompt |
| `python scripts/autoexperiment.py --target summarizer --dry-run` | Dry-run autoexperiment on the message drafter (target name is historical) |
| `python scripts/autoexperiment.py --list-targets` | List autoexperiment targets |
| `./scripts/install_autoexperiment.sh` | Install autoexperiment nightly schedule |
| `./scripts/install_nightly_tests.sh` | Install nightly regression test launchd schedule (bridge-role gated; auto-installed by `/update` on bridge machines, self-skips + removes stale plist elsewhere) |
| `python scripts/nightly_regression_tests.py --dry-run` | Preview nightly test run without Telegram |
| `tail -f logs/nightly_tests.log` | Stream nightly test logs |
| `tail -f logs/nightly_tests_error.log` | Stream nightly test error log (startup crashes) |
| `python -m tools.analytics export --days 30` | Export analytics metrics as JSON |
| `python -m tools.analytics summary` | Print human-readable analytics summary |
| `python -m tools.analytics rollup` | Run analytics daily rollup manually |
| `python -m tools.agent_session_scheduler status` | Show queue status (pending, running, killed counts) |
| `python -m tools.agent_session_scheduler list --status killed,abandoned` | List sessions filtered by status |
| `python -m tools.agent_session_scheduler kill --agent-session-id <ID>` | Kill a running or pending session by ID |
| `python -m tools.agent_session_scheduler kill --session-id <ID>` | Kill a session by session ID |
| `python -m tools.agent_session_scheduler kill --all` | Kill all running and pending sessions |
| `python -m tools.agent_session_scheduler cleanup --age 30 --dry-run` | Preview stale session cleanup |
| `python -m tools.agent_session_scheduler cleanup --age 30` | Delete stale killed/abandoned/failed sessions |
| `python -m tools.valor_session list` | List all sessions |
| `python -m tools.valor_session status --id <ID>` | Show session status and pending steering messages |
| `python -m tools.valor_session status --full-message --id <ID>` | Show full initial prompt (no 100-char truncation) |
| `python -m tools.valor_session inspect --id <ID>` | Dump all raw Popoto fields for a session (debugging) |
| `python -m tools.valor_session children --id <ID>` | List all child sessions spawned by a parent session |
| `python -m tools.valor_session steer --id <ID> --message "..."` | Inject a steering message into a running session |
| `python -m tools.valor_session kill --id <ID>` | Kill a session |
| `python -m tools.valor_session kill --all` | Kill all running sessions |
| `python -m tools.valor_session create --role eng --message "..."` | Create and enqueue a new Eng session. `project_key` determines the repo via `projects.json`; there is no working-directory override flag. Precedence: `--project-key` > `--parent` inheritance > cwd match (raises on no match). Warns to stderr if no worker is running. |
| `python -m tools.valor_session resume --id <ID> --message "..."` | Resume a completed, killed, or failed session (hard-PATCH path; accepts session_id or agent_session_id) |
| `python -m tools.valor_session release --pr <N>` | Clear retain_for_resume after PR merge/close |
| `python -m tools.valor_session telemetry --id <ID>` | Show session telemetry timeline (turn events, token usage, status transitions) |
| `valor-session crash-signatures` | Show crash signatures in the library (project-scoped) |
| `valor-session crash-policy list` | Show derived auto-resume policy entries |
| `valor-session-archive status` | Show the SQLite secondary-store (`data/session_archive.db`) freshness: row count, last export age, health |
| `valor-session-archive restore --dry-run` | Report the empty-Redis restore guard decision (would it restore/skip/resume, and how many rows) without writing anything — read-only; export and live restore run automatically via the worker |
| `python -m tools.memory_search search "query"` | Search memories by query |
| `python -m tools.memory_search search "query" --category correction` | Search filtered by category |
| `python -m tools.memory_search search "query" --tag redis` | Search filtered by tag |
| `python -m tools.memory_search save "content"` | Save a new memory |
| `python -m tools.memory_search inspect --id <ID>` | Inspect a specific memory |
| `python -m tools.memory_search inspect --stats` | Show memory statistics |
| `python -m tools.memory_search forget --id <ID> --confirm` | Delete a memory |
| `python -m tools.memory_search status` | Check memory system health (Redis, counts, superseded ratio) |
| `python -m tools.memory_search status --json` | Memory health as machine-readable JSON |
| `python -m tools.memory_search status --deep` | Memory health with Redis-side `orphan_index_count`, disk-side `disk_orphan_count`, and per-category confidence |
| `python -m tools.doctor` | Run all environment and health checks |
| `python -m tools.doctor --quick` | Skip slow checks (Telegram session, model verification) |
| `python -m tools.doctor --quality` | Include code quality checks (ruff, pytest) |
| `python -m tools.doctor --json` | Output health check results as JSON |
| `python -m tools.doctor --install-hook` | Install git pre-push hook running doctor --quick |
| `valor-youtube-search "query"` | Search YouTube for videos by query |
| `valor-youtube-search --limit N "query"` | Search YouTube with limited results |
| `valor-youtube-transcribe <url>` | Transcribe a YouTube video (captions-first, Whisper fallback). Prefer this over `WebFetch` for YouTube URLs — YouTube serves anti-bot HTML to non-browser fetchers. |
| `valor-youtube-transcribe --json <url>` | Same as above, emit raw `process_youtube_url` dict as JSON |
| `valor-youtube-transcribe --summary-only <url>` | Emit only the GPT-4o-mini summary (or full transcript with a note if none) |
| `valor-video-watch <url> ["question"]` | Visual grounding for a YouTube or X/Twitter video: yt-dlp download, ffmpeg scene-change frame extraction (deduped), Whisper transcript, and Grok X-native context/fallback for X. Prints frame JPEG paths (`t=MM:SS`) to `Read` image-by-image. Use when the answer is on-screen, not in the audio. See `docs/features/video-watch-visual-grounding.md`. |
| `valor-video-watch --json <url>` | Same, emitting the raw `watch_video` result dict as JSON |
| `valor-tts --text "Hello." --output /tmp/out.ogg` | Synthesize text to OGG/Opus (Kokoro local primary, OpenAI tts-1 fallback). See `docs/features/tts.md`. |
| `valor-tts --text "Hello." --output /tmp/out.ogg --voice af_bella` | Synthesize with a specific voice (catalog in `tools/tts/README.md`) |
| `valor-tts --text "Hello." --output /tmp/out.ogg --force-cloud` | Force the cloud (OpenAI tts-1) backend even if Kokoro is available |
| `valor-deck-video deck.md` | Render a narrated MP4 of a Marp deck (per-slide `<!-- narration: ... -->`, voiceover via valor-tts, slides held for each clip's duration). See `docs/features/narrated-deck-video.md`. |
| `valor-ingest <path-or-url>` | Convert a PDF/DOCX/PPTX/XLSX/HTML/image/YouTube URL into a `.md` sidecar the knowledge indexer picks up (see `docs/features/markitdown-ingestion.md`) |
| `valor-ingest --scan ~/work-vault/` | Backfill every convertible binary file in the vault recursively (audio formats deliberately excluded) |
| `valor-computer bootstrap` | Readiness preflight (`GET /v1/bootstrap`); run once per session before the first action. Exit 0 when ready, 78 when permissions ungranted (`instructions.ready == false`) or bcu unavailable — relay `instructions.user` and stop |
| `valor-computer list_apps` | List all visible macOS apps (requires bcu opt-in via `/setup`; macOS-only — exits 78 on other OSes) |
| `valor-computer list_windows <app>` | List open windows for an app (name, bundle ID, or query); window IDs are strings |
| `valor-computer click <window> --x N --y N` | Click coordinates in a native window without moving the user's cursor |
| `valor-computer type_text <window> "text"` | Type text into a native app window via Accessibility API |
| `valor-computer screenshot <window> --output /tmp/out.png` | Capture a native window screenshot via get_window_state imageMode (see `docs/features/computer-use.md`) |

