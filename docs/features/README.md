# Feature Documentation Index

Completed feature documentation for the Valor AI system. Each document describes an implemented feature, its design decisions, and how it works.

## Features

| Feature | Description | Status |
|---------|-------------|--------|
| [Agent Session Model](agent-session-model.md) | Unified lifecycle model merging RedisJob + SessionLog for agent work tracking | Shipped |
| [Bridge Message Query](bridge-message-query.md) | CLI tool to fetch Telegram message history via file-based IPC with running bridge | Shipped |
| [Bridge Module Architecture](bridge-module-architecture.md) | Sub-module organization of the Telegram bridge for maintainability | Shipped |
| [Bridge Response Improvements](bridge-response-improvements.md) | Enhancements to how the Telegram bridge formats and delivers responses | Shipped |
| [Bridge Self-Healing](bridge-self-healing.md) | Automatic crash recovery with session lock cleanup, watchdog, and escalation | Shipped |
| [Bridge Workflow Gaps](bridge-workflow-gaps.md) | Auto-continue for status updates, output classification, and session log snapshots | Shipped |
| [Build Output Verification](build-output-verification.md) | Three-layer verification gates preventing /do-build from silently completing with no code changes | Shipped |
| [Build Session Reliability](build-session-reliability.md) | Logging propagation, commit-on-exit, worktree isolation, health monitoring | Shipped |
| [Classification](classification.md) | Auto-classification of messages as bug/feature/chore with immutability and reclassify skill | Shipped |
| [Coaching Loop](coaching-loop.md) | Merged classifier-coach with LLM-generated coaching, tiered fallback, error crash guard, open question gate, and stage-aware auto-continue for SDLC jobs | Shipped |
| [Code Impact Finder](code-impact-finder.md) | Semantic search for blast radius analysis during /do-plan | Shipped |
| [Completion Tracking](completion-tracking.md) | Branch-based work tracking and completion token system | Archived |
| [Correlation IDs](correlation-ids.md) | End-to-end request tracing with shared correlation_id from Telegram receipt to response delivery | Shipped |
| [Deep Plan Analysis](deep-plan-analysis.md) | Prior Art, Data Flow, Failure Analysis, and Architectural Impact investigation sections in /do-plan | Shipped |
| [Design Review](do-design-review.md) | Review web UI against 10 premium design criteria with severity ratings | Shipped |
| [Do Test](do-test.md) | Intelligent test orchestration with parallel dispatch, changed-file detection, structured reporting, and pytest plugin configuration | Shipped |
| [do-patch Skill](do-patch-skill.md) | Targeted fix skill for test failures and review blockers; called automatically by do-build | Shipped |
| [Documentation Audit](documentation-audit.md) | Weekly LLM-powered audit of docs/ accuracy against codebase; KEEP / UPDATE / DELETE verdicts, directory and filename enforcement | Shipped |
| [Documentation Lifecycle](documentation-lifecycle.md) | Automated validation and migration system for plan documentation tasks | Shipped |
| [Features README Sort Check](features-readme-sort-check.md) | PostToolUse hook enforcing alphabetical sort order in the feature index table with auto-fix | Shipped |
| [Git State Guard](git-state-guard.md) | Detects and resolves dirty git state (merges, rebases, cherry-picks) before SDLC branch operations | Shipped |
| [Google Calendar Integration](google-calendar-integration.md) | Work session logging as Google Calendar events with segment rounding | Shipped |
| [Hooks & Session Logging](hooks-session-logging.md) | Claude Code hooks for session event capture and structured logging | Shipped |
| [Image Vision Support](image-vision.md) | Ollama LLaVA image descriptions for visual content in Telegram | Shipped |
| [Job Health Monitor](job-health-monitor.md) | Detects and recovers stuck running jobs in the queue | Shipped |
| [Link Content Summarization](link-summarization.md) | Auto-fetch and summarize shared links via Perplexity API | Shipped |
| [Message Pipeline](message-pipeline.md) | Deferred enrichment pipeline for fast message acknowledgment and zero-loss restarts | Shipped |
| [Mid-Session Steering](mid-session-steering.md) | End-to-end steering flow for injecting reply-to messages into running agent sessions | Shipped |
| [Observer Agent](observer-agent.md) | Stage-aware SDLC steerer replacing classifier/coach/routing chain with unified Sonnet agent | Shipped |
| [Operational Logging](operational-logging.md) | Consistent INFO-level prefix-tagged logging at every decision point for end-to-end message tracing | Shipped |
| [Plan Prerequisites Validation](plan-prerequisites.md) | Declare and validate environment requirements before plan execution | Shipped |
| [Popoto Redis Expansion](popoto-redis-expansion.md) | Migration from JSONL/JSON file state to Redis for atomicity and queries | Shipped |
| [Race Condition Analysis](race-condition-analysis.md) | Structured concurrency analysis section in plan template with soft validator for async code | Shipped |
| [Reaction Semantics](reaction-semantics.md) | Emoji reaction protocol for message delivery feedback and silent loss prevention | Shipped |
| [Reflections](reflections.md) | Autonomous 14-step daily maintenance: cleanup, log analysis, session quality, LLM reflection, auto-fix PRs, multi-repo support, institutional memory, Telegram notifications | Shipped |
| [Remote Update](remote-update.md) | Telegram command and cron for remote system updates across machines | Shipped |
| [Review Workflow Screenshots](review-workflow-screenshots.md) | Screenshot capture during review for visual validation | Shipped |
| [Scale Job Queue (Popoto + Worktrees)](scale-job-queue-with-popoto-and-worktrees.md) | Redis persistence and git worktrees for parallel build execution | Shipped |
| [SDK Modernization](sdk-modernization.md) | Upgrade to SDK v0.1.35 with programmatic agents, expanded hooks, and cost budgeting | Shipped |
| [SDLC Enforcement](sdlc-enforcement.md) | Quality gates for code sessions: user-level hooks, pipeline stage model, settings merger, cross-repo enforcement | Shipped |
| [SDLC-First Routing](sdlc-first-routing.md) | Automatic work request classification (Ollama/Haiku) and orchestrator routing for SDLC vs conversational requests | Shipped |
| [Semantic Doc Impact Finder](semantic-doc-impact-finder.md) | Two-stage semantic search (embedding recall + LLM reranking) for finding docs affected by code changes | Shipped |
| [Semantic Session Routing](semantic-session-routing.md) | Semantic matching of unthreaded messages to active sessions with declared expectations via structured summarizer output | Shipped |
| [Session Isolation](session-isolation.md) | Two-tier task list scoping and git worktrees for parallel session isolation | Shipped |
| [Session Lifecycle Diagnostics](session-lifecycle-diagnostics.md) | Structured LIFECYCLE logging at every state transition with stall detection and CLI status report | Shipped |
| [Session Tagging](session-tagging.md) | Auto-tagging and CRUD for session categorization based on activity, classification, and transcript patterns | Shipped |
| [Session Transcripts](session-transcripts.md) | Append-only session transcript files with AgentSession Redis model for metadata | Shipped |
| [Session Watchdog](session-watchdog.md) | Active session monitoring with proper cleanup and state management | Shipped |
| [Skills Audit](do-skills-audit.md) | Deterministic validation of all SKILL.md files with 12 rules and Anthropic best practices sync | Shipped |
| [Skills Dependency Map](skills-dependency-map.md) | Visual map of skill-to-skill, skill-to-agent, and sub-file relationships for cleanup planning | Shipped |
| [Skills Reorganization](skills-reorganization.md) | Canonical SKILL.md template, progressive disclosure, command consolidation, hardlink scoping | Shipped |
| [Stall Retry](stall-retry.md) | Automatic retry of stalled agent sessions with exponential backoff, process cleanup, and Telegram notification on final failure | Shipped |
| [Steering Queue](steering-queue.md) | Mid-execution course correction via Telegram reply threads | Shipped |
| [Summarizer Format](summarizer-format.md) | Structured bullet-point output with SDLC stage progress and markdown links for Telegram delivery | Shipped |
| [System Overview](system-overview.md) | High-level architecture and design principles | Archived |
| [Task List Isolation Experiment](task-list-isolation.md) | Experiment results validating CLAUDE_CODE_TASK_LIST_ID behavior | Archived |
| [Telegram History & Links](telegram-history.md) | Searchable message history and link compilation from Telegram | Shipped |
| [Telegram Messaging](telegram-messaging.md) | Unified interface for reading and sending Telegram messages via `valor-telegram` CLI | Shipped |
| [Test Coverage Standards](test-coverage-standards.md) | Standards and tooling for preventing silent failure classes: exception swallowing, empty output loops, coupled tests, missing error rendering, silent builds | Shipped |
| [Trace & Verify Protocol](trace-and-verify.md) | Data-driven root cause analysis replacing narrative-only 5 Whys with forward verification | Shipped |
| [Worktree SDK Compatibility Experiment](worktree-sdk-compatibility.md) | Experiment results for Claude Agent SDK compatibility with git worktrees | Archived |
| [YouTube Transcription](youtube-transcription.md) | Auto-transcribe YouTube videos shared in messages for Claude context | Shipped |

## Adding New Entries

When shipping a new feature, add a row to the table above. Format:

```markdown
| [Feature Name](filename.md) | One-line description | Shipped |
```

Keep entries sorted alphabetically by feature name.
