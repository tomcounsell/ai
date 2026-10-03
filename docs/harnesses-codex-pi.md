# Further harnesses: Codex and Pi

Part of [harnesses.md](harnesses.md).

A second harness conforms to the same port. Its wrapper builds argv, env,
and cwd from the base URL, Brief, and turn id; parses a result carrying
`text`, `is_error`, and a session id; resumes by that id; runs under the same
sandbox profile with the same `VALOR_TURN` mark; and honors the signal
channel, which needs nothing harness-specific beyond writing files.

**Gap.** The gateway speaks the Anthropic Messages wire format. A harness
whose provider speaks another format needs a gateway route that meters that
format before its wrapper can exist, since a turn that bypasses the gateway
spends outside the meter. The first use is the review seat: an Opus-class
model from another vendor reviewing through its own harness. Nothing about
Codex or Pi has been run here.
