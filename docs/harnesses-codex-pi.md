# Further harnesses: Codex and Pi

Part of [harnesses.md](harnesses.md).

A second harness conforms to the same port. Its wrapper builds argv, env,
and cwd from the base URL, Brief, and turn id; parses a result carrying
`text`, `is_error`, and a session id; resumes by that id; runs under the same
sandbox profile with the same `VALOR_TURN` mark; and honors the signal
channel, which needs nothing harness-specific beyond writing files.

The gateway meters two formats: Anthropic's Messages API, and OpenAI's
Responses API at `<gateway>/t/<token>/openai/v1` (architecture.md, Metered
spending). A harness whose provider speaks another format needs a route that
meters it before its wrapper can exist. Nothing about Codex or Pi has been
run here.
