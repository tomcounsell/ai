# Feature Documentation Index

Completed feature documentation for the Valor AI system. Each document describes an implemented feature, its design decisions, and how it works.

## Features

| Feature | Description | Status |
|---------|-------------|--------|
| [Bridge Message Query](bridge-message-query.md) | CLI tool to fetch Telegram message history via file-based IPC with running bridge | Shipped |
| [Bridge Module Architecture](bridge-module-architecture.md) | Sub-module organization of the Telegram bridge for maintainability | Shipped |
| [Bridge Response Improvements](bridge-response-improvements.md) | Enhancements to how the Telegram bridge formats and delivers responses | Shipped |
| [Bridge Self-Healing](bridge-self-healing.md) | Automatic crash recovery with session lock cleanup, watchdog, and escalation | Shipped |
| [Bridge Workflow Gaps](bridge-workflow-gaps.md) | Auto-continue for status updates, output classification, and session log snapshots | Shipped |
| [Build Session Reliability](build-session-reliability.md) | Logging propagation, commit-on-exit, worktree isolation, health monitoring | Shipped |
| [Classification](classification.md) | Auto-classification of messages as bug/feature/chore with immutability and reclassify skill | Shipped |
| [Coaching Loop](coaching-loop.md) | Merged classifier-coach with LLM-generated coaching, tiered fallback, and error crash guard | Shipped |
| [Code Impact Finder](code-impact-finder.md) | Semantic search for blast radius analysis during /do-plan | Shipped |
| [Daydream Reactivation](daydream-reactivation.md) | Autonomous 10-step daily maintenance: cleanup, log analysis, session quality, LLM reflection, institutional memory | Shipped |
| [Do Test](do-test.md) | Intelligent test orchestration with parallel dispatch, changed-file detection, and structured reporting | Shipped |
| [Documentation Lifecycle](documentation-lifecycle.md) | Automated validation and migration system for plan documentation tasks | Shipped |
| [Google Calendar Integration](google-calendar-integration.md) | Work session logging as Google Calendar events with segment rounding | Shipped |
| [Hooks & Session Logging](hooks-session-logging.md) | Claude Code hooks for session event capture and structured logging | Shipped |
| [Image Vision Support](image-vision.md) | Ollama LLaVA image descriptions for visual content in Telegram | Shipped |
| [Job Health Monitor](job-health-monitor.md) | Detects and recovers stuck running jobs in the queue | Shipped |
| [Link Content Summarization](link-summarization.md) | Auto-fetch and summarize shared links via Perplexity API | Shipped |
| [Message Pipeline](message-pipeline.md) | Deferred enrichment pipeline for fast message acknowledgment and zero-loss restarts | Shipped |
| [Plan Prerequisites Validation](plan-prerequisites.md) | Declare and validate environment requirements before plan execution | Shipped |
| [Popoto Redis Expansion](popoto-redis-expansion.md) | Migration from JSONL/JSON file state to Redis for atomicity and queries | Shipped |
| [Reaction Semantics](reaction-semantics.md) | Emoji reaction protocol for message delivery feedback and silent loss prevention | Shipped |
| [Remote Update](remote-update.md) | Telegram command and cron for remote system updates across machines | Shipped |
| [Review Workflow Screenshots](review-workflow-screenshots.md) | Screenshot capture during review for visual validation | Shipped |
| [Scale Job Queue (Popoto + Worktrees)](scale-job-queue-with-popoto-and-worktrees.md) | Redis persistence and git worktrees for parallel build execution | Shipped |
| [SDK Modernization](sdk-modernization.md) | Upgrade to SDK v0.1.35 with programmatic agents, expanded hooks, and cost budgeting | Shipped |
| [Semantic Doc Impact Finder](semantic-doc-impact-finder.md) | Two-stage semantic search (embedding recall + LLM reranking) for finding docs affected by code changes | Shipped |
| [Session Isolation](session-isolation.md) | Two-tier task list scoping and git worktrees for parallel session isolation | Shipped |
| [Session Watchdog](session-watchdog.md) | Active session monitoring with proper cleanup and state management | Shipped |
| [Steering Queue](steering-queue.md) | Mid-execution course correction via Telegram reply threads | Shipped |
| [Telegram History & Links](telegram-history.md) | Searchable message history and link compilation from Telegram | Shipped |
| [Telegram Messaging](telegram-messaging.md) | Unified interface for reading and sending Telegram messages via `valor-telegram` CLI | Shipped |
| [YouTube Transcription](youtube-transcription.md) | Auto-transcribe YouTube videos shared in messages for Claude context | Shipped |

## Adding New Entries

When shipping a new feature, add a row to the table above. Format:

```markdown
| [Feature Name](filename.md) | One-line description | Shipped |
```

Keep entries sorted alphabetically by feature name.
