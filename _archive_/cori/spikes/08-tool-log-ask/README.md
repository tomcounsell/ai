# Spike 08: tool log and ask

Closes prereqs item 17. Tech stack §4 (three records) and §5 (five tools, `ask`, `answer()`).

## Question

Can the smallest PydanticAI loop have all five tools bridged to a real
apple/container sandbox and the stub gateway from spike 03, write a tool
log before and after every invocation whose hashes match the artifacts on
the retained disk, and block on `ask` in a way the gateway can see: zero
model calls while the worker waits, a resume after `answer()`, and a clean
terminal event on abort?

## Method

- `sandbox.py`: create, exec, read, write, destroy over the `container` CLI.
  read and write go through `exec -i` (`cat` and `cat >`), so a file written
  by the tool is owned by the sandbox user and later `bash` calls can touch
  it. Names start with `s08-`.
- `loop.py`: `Worker` holds the sandbox, the tool log, the trace events, and
  the pending questions. read, write, edit, and bash are plain functions
  that log `tool.start` (input and its hash) before the port call and
  `tool.end` (exit status, hashes of stdout, stderr, and the artifact read
  back from the sandbox) after. `ask` logs a start, emits a `question` trace
  event, and awaits a Future; `answer()` resolves it; `abort()` cancels the
  run task and the loop writes one `terminal` event with `outcome: aborted`.
  The agent is `pydantic_ai.Agent` with `output_type=Report`, pointed at the
  gateway by base URL with only its Brief token, driven through
  `agent.iter()` so every model request streams and spike 03's stream cut
  still applies.
- `run.py`: starts the spike 03 gateway with the key from the Keychain,
  issues a Brief token with a 60,000-token budget, and runs two scenarios
  on Haiku 4.5. The task: write `/work/greeting.txt` as `Hello, <name>!`
  for a name the worker does not have, then `wc -c` it and report.

Run: `./run.sh` (from the root project, since it needs pydantic-ai; it
imports spike 03's gateway by path). Spend: about $0.01.

## Numbers

Scenario A, ask and answer:

| Measure | Result |
|---|---|
| Question raised | "What is your first name?" after 1 model call |
| Gateway requests during a 3 s hold while the worker waited | 0 |
| Gateway requests between the question event and `answer()` | 0 |
| Gateway requests for the run | 3 (one to ask, one to act, one to report) |
| Tool invocations, in start order | ask, write, bash |
| Every `tool.start` has a `tool.end` with the same seq | yes |
| `artifact_sha256` in the write's `tool.end` versus the file on the retained disk | equal |
| Greeting on the retained disk | `Hello, Tom!` |
| Terminal event | `outcome: report`, Report validated, artifacts `["/work/greeting.txt"]` |

Scenario B, abort during the wait:

| Measure | Result |
|---|---|
| Events after the question | one, `terminal` with `outcome: aborted` |
| Tool log records after the abort | `terminal` only |
| Gateway requests after the abort for that Brief | 0 |
| Files on the retained disk | none (the abort came before any write) |

Per-tool cost through the CLI: write 1,086 ms (an `exec -i` to write plus
an `exec` to hash the artifact), bash 949 ms. Spike 06 measured a bare
`exec true` at 65 to 110 ms; `exec -i` with stdin and a `sh -c` wrapper is
several times that. The three logs are in this directory:
`tool-log-ask.jsonl`, `tool-log-abort.jsonl`, `gateway.jsonl`.

## Surprises

1. **The model called write and bash in the same turn and PydanticAI ran
   them concurrently.** Both `tool.start` records precede either `tool.end`,
   and bash finished first. A reader of the tool log pairs records by `seq`,
   never by position, and the Verifier's "what happened" is the set of
   `tool.end` records, not the order of the lines. This also means a `bash`
   that reads a file the same turn's `write` produces can race it; the loop
   should serialize tool calls per Brief, or the worker prompt should say
   one tool call per turn where order matters.
2. **`exec -i` costs about half a second.** Reading a file back to hash it
   doubles a write's cost. When the worktree is a bind mount the kernel
   owns, the adapter can hash the host side of the mount for free; that is
   the right implementation of `artifact_sha256`, and this spike did it the
   slow way to keep every tool on the port.
3. **An aborted `ask` leaves an open `tool.start`.** The terminal event
   closes the run, and the acceptance check is satisfied, but a log reader
   that expects every start to have an end will see one dangling. Either
   the abort path writes a `tool.end` with `exit_status: aborted`, or the
   reader treats a `terminal` record as closing every open invocation. The
   second is simpler and is what the Verifier plan should assume.
4. **Nothing in PydanticAI needed changing** to make `ask` block. A tool
   that awaits a Future holds the whole run, the graph does not advance,
   and the gateway sees nothing. Cancellation propagates through the
   `agent.iter()` context manager cleanly.

## Recommendation

Holds. The loop is a few hundred lines and every property in the item held
on the first run. Carry into the worker plan: serialize tool calls per
Brief (or accept the interleaving and pair by seq), hash artifacts on the
host side of the mount, and let a `terminal` record close open invocations.
