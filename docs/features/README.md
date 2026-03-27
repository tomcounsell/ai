# Feature Documentation Index

Completed feature documentation for the Valor AI system. Each document describes an implemented feature, its design decisions, and how it works.

## Features

| Feature | Description | Status |
|---------|-------------|--------|
| [Adding Reflection Tasks](adding-reflection-tasks.md) | Developer guide with copy-paste template for adding new reflection steps | Shipped |
| [Agent Definition Fallback](agent-definition-fallback.md) | Graceful degradation when agent definition markdown files are missing — logs warning and continues with fallback prompt instead of crashing | Shipped |
| [Agent Session Model](agent-session-model.md) | Unified lifecycle model merging RedisJob + SessionLog for agent work tracking | Shipped |
| [Autoexperiment](autoexperiment.md) | Autonomous overnight prompt optimization using ultra-cheap LLMs via OpenRouter | Shipped |
| [Bridge Message Query](bridge-message-query.md) | CLI tool to fetch Telegram message history via file-based IPC with running bridge | Shipped |
| [Bridge Module Architecture](bridge-module-architecture.md) | Sub-module organization of the Telegram bridge for maintainability | Shipped |
| [Bridge Resilience](bridge-resilience.md) | Circuit breaker, unified recovery loop, degraded mode, structured logging | Shipped |
| [Bridge Response Improvements](bridge-response-improvements.md) | Enhancements to how the Telegram bridge formats and delivers responses | Shipped |
| [Bridge Self-Healing](bridge-self-healing.md) | Automatic crash recovery with session lock cleanup, watchdog, escalation, flood-backoff persistence, and dynamic catchup lookback | Shipped |
| [Bridge Workflow Gaps](bridge-workflow-gaps.md) | Auto-continue for status updates, output classification, and session log snapshots | Shipped |
| [Build Output Verification](build-output-verification.md) | Three-layer verification gates preventing /do-build from silently completing with no code changes | Shipped |
| [Build Session Reliability](build-session-reliability.md) | Logging propagation, commit-on-exit, worktree isolation, health monitoring | Shipped |
| [Chat Dev Session Architecture](chat-dev-session-architecture.md) | ChatSession/DevSession split — session type discriminator splitting orchestration from execution | Shipped |
| [ChatSession Q&A Mode](chatsession-qa-mode.md) | Haiku-based intent classifier routing informational queries to direct ChatSession response without DevSession spawn | Shipped |
| [Classification](classification.md) | Auto-classification of messages as bug/feature/chore with immutability and reclassify skill | Shipped |
| [Claude Code Memory](claude-code-memory.md) | Hook-based memory integration for Claude Code CLI sessions: prompt ingestion, tool-call recall with sliding window, deja vu signals, post-session extraction, AgentSession lifecycle tracking, and post-merge learning | Shipped |
| [Code Impact Finder](code-impact-finder.md) | Semantic search for blast radius analysis during /do-plan | Shipped |
| [Completion Tracking](completion-tracking.md) | Branch-based work tracking and completion token system | Archived |
| [Config Architecture](config-architecture.md) | Unified config system: Pydantic Settings, path constants, zero hardcoded paths | Shipped |
| [Config-Driven Chat Mode](config-driven-chat-mode.md) | Per-group persona field in projects.json controlling session type, classifier bypass, and passive listener behavior | Shipped |
| [Context Fidelity Modes](context-fidelity-modes.md) | Right-sized context compression (full/compact/minimal/steering) for sub-agent dispatch | Shipped |
| [Correlation IDs](correlation-ids.md) | End-to-end request tracing with shared correlation_id from Telegram receipt to response delivery | Shipped |
| [Deep Plan Analysis](deep-plan-analysis.md) | Prior Art, Data Flow, Failure Analysis, and Architectural Impact investigation sections in /do-plan | Shipped |
| [Deployment](deployment.md) | Multi-instance deployment configuration with per-machine project routing | Shipped |
| [Design Review](do-design-review.md) | Review web UI against 10 premium design criteria with severity ratings | Shipped |
| [Do Test](do-test.md) | Intelligent test orchestration with parallel dispatch, changed-file detection, structured reporting, and pytest plugin configuration | Shipped |
| [do-patch Skill](do-patch-skill.md) | Targeted fix skill for test failures and review blockers; called automatically by do-build | Shipped |
| [Documentation Audit](documentation-audit.md) | Weekly LLM-powered audit of docs/ accuracy against codebase; KEEP / UPDATE / DELETE verdicts, directory and filename enforcement | Shipped |
| [Documentation Lifecycle](documentation-lifecycle.md) | Automated validation and migration system for plan documentation tasks | Shipped |
| [Enforce REVIEW/DOCS Stages](enforce-review-docs-stages.md) | Hard delivery gates in Observer preventing SDLC pipeline from skipping REVIEW and DOCS stages | Shipped |
| [Enhanced Planning](enhanced-planning.md) | Spike Resolution (Phase 1.5), RFC Review (Phase 2.8), Infrastructure Documentation, and task validation fields for /do-plan | Shipped |
| [Features README Sort Check](features-readme-sort-check.md) | PostToolUse hook enforcing alphabetical sort order in the feature index table with auto-fix | Shipped |
| [Git State Guard](git-state-guard.md) | Detects and resolves dirty git state (merges, rebases, cherry-picks) before SDLC branch operations | Shipped |
| [Goal Gates](goal-gates.md) | Deterministic enforcement gates preventing SDLC pipeline from silently skipping stages | Shipped |
| [Google Calendar Integration](google-calendar-integration.md) | Work session logging as Google Calendar events with segment rounding | Shipped |
| [Hooks & Session Logging](hooks-session-logging.md) | Claude Code hooks for session event capture and structured logging | Shipped |
| [Image Vision Support](image-vision.md) | Ollama LLaVA image descriptions for visual content in Telegram | Shipped |
| [Intake Classifier](intake-classifier.md) | Haiku-powered message intent triage (interjection/new_work/acknowledgment) for bridge routing | Shipped |
| [Issue Poller](issue-poller.md) | Automatic SDLC kickoff: polls GitHub issues, dedup via Claude Haiku, auto-creates draft plans | Shipped |
| [Job Dependency Tracking](job-dependency-tracking.md) | Sibling dependency graph, branch-session mapping, checkpoint/restore, PM controls, session observability | Shipped |
| [Job Health Monitor](job-health-monitor.md) | Detects and recovers stuck running jobs in the queue | Shipped |
| [Job Hierarchy](job-scheduling.md#parent-child-job-hierarchy) | Parent-child job decomposition with completion propagation, progress tracking, and orphan/stuck parent self-healing | Shipped |
| [Job Queue Reliability](job-queue.md) | KeyField index fixes, Event-based drain guard, delete-and-recreate pattern, orphan recovery | Shipped |
| [Job Self-Scheduling](job-scheduling.md) | Agent-initiated queue operations: schedule SDLC jobs, deferred execution, 4-tier priority, queue manipulation | Shipped |
| [Link Content Summarization](link-summarization.md) | Auto-fetch and summarize shared links via Perplexity API | Shipped |
| [Lint Auto-Fix](lint-auto-fix.md) | Automatic lint/format fixing via pre-commit hook and PostToolUse hook, eliminating agent churn loops | Shipped |
| [Memory Search Tool](memory-search-tool.md) | Direct interface for searching, saving, inspecting, and forgetting memories from the Memory model | Shipped |
| [Message Pipeline](message-pipeline.md) | Deferred enrichment pipeline for fast message acknowledgment and zero-loss restarts | Shipped |
| [Mid-Session Steering](mid-session-steering.md) | End-to-end steering flow for injecting reply-to messages into running agent sessions | Shipped |
| [Observer Agent](observer-agent.md) | Deterministic SDLC steer/deliver router — no LLM calls, rules-based pipeline progression | Shipped |
| [OOP Audit](do-oop-audit.md) | Prompt-only audit skill scanning Python classes for 14 structural anti-patterns with framework detection and severity grouping | Shipped |
| [Operational Logging](operational-logging.md) | Consistent INFO-level prefix-tagged logging at every decision point for end-to-end message tracing | Shipped |
| [Personas](personas.md) | Configurable persona system: base + overlay files replacing monolithic SOUL.md for developer, PM, and teammate roles | Shipped |
| [Pipeline Graph](pipeline-graph.md) | Canonical directed graph for SDLC stage routing with cycle support for test-failure and review-feedback loops | Shipped |
| [Pipeline State Machine](pipeline-state-machine.md) | Programmatic stage tracking replacing inference-based detection with direct state recording at transition points | Shipped |
| [Plan Completion Gate](plan-completion-gate.md) | Deterministic validation preventing plan completion and PR merge while checkboxes remain unchecked | Shipped |
| [Plan Prerequisites Validation](plan-prerequisites.md) | Declare and validate environment requirements before plan execution | Shipped |
| [PM Channels](pm-channels.md) | Project manager mode routing Telegram groups to work-vault folders with SDLC bypass | Shipped |
| [PM SDLC Decision Rules](pm-sdlc-decision-rules.md) | PM outcome parsing (success/partial/fail), auto-merge on clean gates, annotate-rather-than-skip for review findings | Shipped |
| [PM Telegram Tool](pm-telegram-tool.md) | ChatSession composes and sends its own Telegram messages via Redis IPC, with summarizer as fallback | Shipped |
| [PM Voice Refinement](pm-voice-refinement.md) | Naturalized SDLC language, crash message pool, sentence-aware truncation, milestone-selective emoji for PM output | Shipped |
| [Popoto Redis Expansion](popoto-redis-expansion.md) | Migration from JSONL/JSON file state to Redis for atomicity and queries | Shipped |
| [Race Condition Analysis](race-condition-analysis.md) | Structured concurrency analysis section in plan template with soft validator for async code | Shipped |
| [Reaction Semantics](reaction-semantics.md) | Emoji reaction protocol for message delivery feedback and silent loss prevention | Shipped |
| [Redis Model Relationships](redis-models.md) | Cross-references between Popoto models, project_key on all models, enrichment metadata ownership on TelegramMessage | Shipped |
| [Reflections](reflections.md) | Unified reflection scheduler with declarative YAML registry, Redis state tracking, skip-if-running guard; subsumes health check, orphan recovery, branch cleanup, and 14-unit daily maintenance pipeline | Shipped |
| [Reflections Dashboard](reflections-dashboard.md) | Web dashboard for monitoring reflection scheduler execution, run history, and ignore patterns at `/reflections/` | Shipped |
| [Remote Update](remote-update.md) | Telegram command and cron for remote system updates across machines | Shipped |
| [Review Workflow Screenshots](review-workflow-screenshots.md) | Screenshot capture during review for visual validation | Shipped |
| [Scale Job Queue (Popoto + Worktrees)](scale-job-queue-with-popoto-and-worktrees.md) | Redis persistence and git worktrees for parallel build execution | Shipped |
| [SDK Modernization](sdk-modernization.md) | Upgrade to SDK v0.1.35 with programmatic agents and expanded hooks | Shipped |
| [SDLC Critique Stage](sdlc-critique-stage.md) | Automated plan validation between PLAN and BUILD stages with parallel war-room critics and inline source file verification | Shipped |
| [SDLC Enforcement](sdlc-enforcement.md) | Quality gates for code sessions: user-level hooks, pipeline stage model, settings merger, cross-repo enforcement | Shipped |
| [SDLC Observer](sdlc-observer.md) | Web dashboard for real-time SDLC pipeline tracking with stage indicators, event timelines, and artifact links at `/sdlc/` | Shipped |
| [SDLC Pipeline Integrity](sdlc-pipeline-integrity.md) | Session continuation hardening, deterministic URL construction, merge guard hook, and MERGE pipeline stage | Shipped |
| [SDLC Stage Handoff](sdlc-stage-handoff.md) | Structured GitHub issue comments for cross-stage context relay -- each stage posts findings on completion and reads prior stage context on start | Shipped |
| [SDLC-First Routing](sdlc-first-routing.md) | Automatic work request classification (Ollama/Haiku) and orchestrator routing for SDLC vs conversational requests, with cross-repo `gh` resolution via `GH_REPO` env var | Shipped |
| [Semantic Doc Impact Finder](semantic-doc-impact-finder.md) | Two-stage semantic search (embedding recall + LLM reranking) for finding docs affected by code changes | Shipped |
| [Semantic Session Routing](semantic-session-routing.md) | Semantic matching of unthreaded messages to active sessions with declared expectations via structured summarizer output | Shipped |
| [Session Isolation](session-isolation.md) | Two-tier task list scoping and git worktrees for parallel session isolation | Shipped |
| [Session Lifecycle Diagnostics](session-lifecycle-diagnostics.md) | Structured LIFECYCLE logging at every state transition with stall detection and CLI status report | Shipped |
| [Session Management](session-management.md) | Reply-chain root resolution ensuring all replies in a thread map to one canonical session_id | Shipped |
| [Session Tagging](session-tagging.md) | Auto-tagging and CRUD for session categorization based on activity, classification, and transcript patterns | Shipped |
| [Session Transcripts](session-transcripts.md) | Append-only session transcript files with AgentSession Redis model for metadata | Shipped |
| [Session Watchdog](session-watchdog.md) | Active session monitoring with proper cleanup and state management | Shipped |
| [Session Watchdog Reliability](session-watchdog-reliability.md) | Type guards, activity-based stall detection, observer circuit breaker with escalating backoff | Shipped |
| [Skill Context Injection](skill-context-injection.md) | Pre-resolved SDLC_* env vars from AgentSession into Claude Code subprocesses, plus /do-pr-review sub-skill decomposition | Shipped |
| [Skills Audit](do-skills-audit.md) | Deterministic validation of all SKILL.md files with 12 rules and Anthropic best practices sync | Shipped |
| [Skills Dependency Map](skills-dependency-map.md) | Visual map of skill-to-skill, skill-to-agent, and sub-file relationships for cleanup planning | Shipped |
| [Skills Reorganization](skills-reorganization.md) | Canonical SKILL.md template, progressive disclosure, command consolidation, hardlink scoping | Shipped |
| [Stall Retry](stall-retry.md) | Automatic retry of stalled agent sessions with exponential backoff, process cleanup, and Telegram notification on final failure | Shipped |
| [Steering Queue](steering-queue.md) | Mid-execution course correction via Telegram reply threads and parent-child ChatSession-to-DevSession steering | Shipped |
| [Structured Logging & Telemetry](structured-logging-telemetry.md) | Redis-backed telemetry counters, structured log lines, and health check integration for Observer Agent observability | Shipped |
| [Subconscious Memory](subconscious-memory.md) | Automatic and intentional memory: human messages, categorized agent observations, and intentional saves stored as Memory records, surfaced as `<thought>` blocks via PostToolUse hook | Shipped |
| [Summarizer Format](summarizer-format.md) | Structured bullet-point output with SDLC stage progress and markdown links for Telegram delivery | Shipped |
| [SuperWhisper Transcription](superwhisper-transcription.md) | Dual-backend audio transcription with local SuperWhisper primary and OpenAI Whisper API fallback | Shipped |
| [System Overview](system-overview.md) | High-level architecture and design principles | Archived |
| [Task List Isolation Experiment](task-list-isolation.md) | Experiment results validating CLAUDE_CODE_TASK_LIST_ID behavior | Archived |
| [Telegram History & Links](telegram-history.md) | Searchable message history and link compilation from Telegram | Shipped |
| [Telegram Messaging](telegram-messaging.md) | Unified interface for reading and sending Telegram messages via `valor-telegram` CLI | Shipped |
| [Telegram PM Guide](telegram-pm-guide.md) | PM-facing guide for Telegram interaction patterns, session resumption, and pipeline signals | Shipped |
| [Test Baseline Verification](test-baseline-verification.md) | Verified classification of test failures as regressions vs pre-existing by running failing tests against main | Shipped |
| [Test Coverage Standards](test-coverage-standards.md) | Standards and tooling for preventing silent failure classes: exception swallowing, empty output loops, coupled tests, missing error rendering, silent builds | Shipped |
| [Test Reliability: Flaky Filter](test-reliability-flaky-filter.md) | Branch-side retry for flaky tests, deterministic junitxml baseline parsing, and completeness validation for test classification | Shipped |
| [Tools Standard](tools-standard.md) | Tool compliance standard, audit checks, and remediation results for the tools/ directory | Shipped |
| [Trace & Verify Protocol](trace-and-verify.md) | Data-driven root cause analysis replacing narrative-only 5 Whys with forward verification | Shipped |
| [UTC Timestamps](utc-timestamps.md) | All timestamps normalized to tz-aware UTC with `bridge/utc.py` utilities; local conversion only at display boundary | Shipped |
| [Web Dashboard](web-dashboard.md) | Session table with SDLC stage pills, project metadata popovers, history-based stage inference, and configurable retention via DASHBOARD_RETENTION_HOURS | Shipped |
| [Web UI](web-ui.md) | Localhost FastAPI web application at port 8500 serving observability dashboards with HTMX interactivity and dark theme | Shipped |
| [Workspace Safety Invariants](workspace-safety-invariants.md) | Pre-launch validation of agent working directories with CWD existence, path containment, and slug sanitization | Shipped |
| [Worktree SDK Compatibility Experiment](worktree-sdk-compatibility.md) | Experiment results for Claude Agent SDK compatibility with git worktrees | Archived |
| [xfail Hygiene](xfail-hygiene.md) | Three-layer xfail hygiene system preventing stale test markers after bug fixes land | Shipped |
| [YouTube Transcription](youtube-transcription.md) | Auto-transcribe YouTube videos shared in messages for Claude context | Shipped |

## Adding New Entries

When shipping a new feature, add a row to the table above. Format:

```markdown
| [Feature Name](filename.md) | One-line description | Shipped |
```

Keep entries sorted alphabetically by feature name.
