# Archived Feature Docs

These documents describe superseded designs. They are kept for historical reference and
are **not** listed in the [feature index](../README.md). Nothing active should link here.

| Document | Superseded by |
|----------|---------------|
| [System Overview](system-overview.md) | [Bridge/Worker Architecture](../bridge-worker-architecture.md), [Headless Session Runner](../headless-session-runner.md) |
| [Telegram Integration](telegram.md) | [Bridge Module Architecture](../bridge-module-architecture.md), [Telegram Messaging](../telegram-messaging.md), [Telegram Inbound Attachments](../telegram-inbound-attachments.md) |

Both predate the bridge/worker separation: they describe a single process that received a
Telegram message and executed the agent turn inline. Today the bridge is I/O only and the
standalone worker is the sole session-execution engine.
