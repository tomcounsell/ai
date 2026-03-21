# SDLC Job Playlist (Deprecated)

> **Deprecated**: The playlist feature was removed in the SDLC Redesign (#459). Messages now start and end with ChatSessions — sequential issue processing is handled by queuing separate ChatSessions per chat group. See `docs/features/chat-dev-session-architecture.md` for the current architecture.

## Historical Context

The playlist feature (introduced in #450) allowed agents to enqueue multiple GitHub issues for sequential SDLC processing via a Redis-backed playlist. When one issue's pipeline completed, an Observer hook (`_playlist_hook` in `agent/job_queue.py`) automatically popped the next issue and scheduled it.

This was removed because the ChatSession/DevSession architecture provides a cleaner model: each message creates its own ChatSession, and per-chat-group queues handle serialization naturally without a separate playlist mechanism.

## Tracking

- **Original issue**: [#450](https://github.com/tomcounsell/ai/issues/450)
- **Removed in**: [#459](https://github.com/tomcounsell/ai/issues/459) (SDLC Redesign)
