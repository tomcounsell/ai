# SDLC Job Playlist (Deprecated)

> **Deprecated**: The playlist feature was deprecated in the SDLC Redesign (#459) and all remaining code was removed in #474. Sequential issue processing is now handled by queuing separate ChatSessions per chat group. See `docs/features/chat-dev-session-architecture.md` for the current architecture.

## Historical Context

The playlist feature (introduced in #450) allowed agents to enqueue multiple GitHub issues for sequential SDLC processing via a Redis-backed playlist. When one issue's pipeline completed, an Observer hook automatically popped the next issue and scheduled it.

This was removed because the ChatSession/DevSession architecture provides a cleaner model: each message creates its own ChatSession, and per-chat-group queues handle serialization naturally without a separate playlist mechanism.

## Tracking

- **Original issue**: [#450](https://github.com/tomcounsell/ai/issues/450)
- **Deprecated in**: [#459](https://github.com/tomcounsell/ai/issues/459) (SDLC Redesign)
- **Code removed in**: [#474](https://github.com/tomcounsell/ai/issues/474)
