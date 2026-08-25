# SDLC Sequential Issue Processing

Sequential issue processing is handled by queuing separate PM sessions per chat
group. Each message creates its own Eng session, and per-chat-group queues
serialize processing. See [Eng Session Architecture](eng-session-architecture.md)
for the current architecture.
