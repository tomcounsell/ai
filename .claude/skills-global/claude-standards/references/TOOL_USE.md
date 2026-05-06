# Tool Use Reference

Guidance for designing tool-enabled prompts — where Claude decides when to call your code, you execute it, and hand the result back. Read when building a workflow or agent that needs external data, actions, or structured output beyond what a single prompt can produce. For the prompt-writing techniques that make the tool schema and surrounding prompt effective, see [`PROMPT_ENGINEERING.md`](PROMPT_ENGINEERING.md). For broader integrations where someone else has already authored the tool schemas, see [`MCP.md`](MCP.md).

---

## What tool use is (and isn't)

Tool use gives Claude a set of callable functions it can invoke while answering a prompt. The flow:

1. You send the user's request plus a list of tool schemas.
2. Claude decides whether to answer directly or request a tool call. A tool-call response contains the tool name and arguments.
3. You execute the tool and return the result in a follow-up request that includes the full conversation history.
4. Claude incorporates the result and either answers, or requests another tool call.
5. Loop until the response has no tool-call block (i.e., `stop_reason != "tool_use"`).

Claude does not run your code. Claude emits structured requests to call functions that *you* implement. Tool reliability is a function of your code, your schemas, and your prompt — not something the API guarantees.

---

## Tool schemas

Each tool has three schema pieces:

- **`name`** — treat it like a function name: action-oriented, specific (`get_current_datetime`, not `date`).
- **`description`** — three or four sentences covering what the tool does, when to use it, and what it returns. Claude reads this to decide when to call; a vague description leads to over- or under-calling.
- **`input_schema`** — JSON Schema describing arguments. Types matter; per-argument descriptions matter more than argument names.

The most common failure mode is an under-specified description. "Gets the weather" is not a specification — does it need a city? a zip code? what units? current only or forecasts? Claude infers the answers from the description and argument docs; ambiguity produces wrong or missing calls.

Rule of thumb: if a new engineer couldn't write correct calling code from reading the schema alone, neither can Claude.

---

## Tool functions (the code behind the schema)

A tool function is ordinary code. Three discipline points:

- **Validate inputs early.** Raise on missing, empty, or malformed arguments with a clear error message. Claude sees the error and can retry with corrected inputs — but only if the message says what was wrong.
- **Return structured data, not prose.** JSON-serializable output is more reliable than free text, because Claude reconstructs state instead of re-parsing English.
- **Fail fast on unrecoverable conditions.** Raise and return the result with `is_error=true`. Silent partial success is worse than an explicit failure — Claude will act on whatever you returned.

A tool that raises "`doc_id` must be a non-empty string" is self-teaching. A tool that silently returns `None` is a trap.

---

## The multi-turn tool loop

A single user request can trigger multiple tool calls. "What day is 103 days from today?" invokes both `get_current_datetime` and `add_duration_to_datetime` before the final answer.

```
while True:
    response = claude(messages, tools=schemas)
    messages.append(assistant_message(response.content))  # full multi-block content
    if response.stop_reason != "tool_use":
        break
    tool_results = [run_tool(b) for b in response.content if b.type == "tool_use"]
    messages.append(user_message(tool_results))  # results go in a user-role message
```

Three traps to avoid:

- **Appending only text.** Assistant responses with tool calls are multi-block (text + tool_use). Append the full `content` array, not just the text.
- **Mismatched `tool_use_id`.** Each tool_result must carry the `tool_use_id` from its corresponding tool_use block. With parallel tool calls, those IDs are the only link between request and result.
- **Dropping the tools argument on follow-ups.** Even when you don't expect another tool call, include the tools schema. Forgetting it breaks Claude's ability to request another tool if needed.

---

## Parallel tool execution (batch tool)

Claude can in principle emit multiple tool_use blocks in a single response, but in practice rarely does so without nudging. If several calls are independent (three weather lookups, three document reads), exposing a `batch` tool makes parallel execution reliable:

- The batch tool takes a list of `{tool_name, arguments}` invocations.
- Your implementation iterates the list, runs each inner tool, and returns all results.
- Claude learns to package independent calls into one batch rather than serializing across turns.

The gain is latency — three 500ms calls drop from 1.5s (sequential turns) to ~500ms (batched). The cost is a layer of routing code. Worth it for agents with frequent independent lookups; overkill for a workflow that naturally executes one tool per step.

---

## Structured output via tools

The prefill + stop-sequence trick in `PROMPT_ENGINEERING.md` extracts structured output well for small, well-shaped cases. For higher reliability or more complex schemas, use a tool as the extraction vehicle:

1. Define a tool whose `input_schema` *is* the structure you want extracted.
2. Force the model to call that specific tool with `tool_choice: {type: "tool", name: "your_tool"}`.
3. Read the structured data out of the tool_use block's `input` field. You never actually run the tool.

You're trading simplicity for reliability: the model fills the tool's argument schema, the API validates it as JSON, and you get a typed object. Use when extraction matters more than terseness, or when the schema has nesting, enums, or conditional fields that prompting alone struggles to enforce.

---

## Streaming tool calls

With `stream=True` on a tool-enabled request, Claude emits `input_json_delta` events carrying fragments of the tool's JSON arguments as they're generated. Two modes:

- **Default (validated).** The API buffers chunks until a top-level key/value pair is complete and valid, then ships a burst. Safe, slower.
- **Fine-grained (`fine_grained: true`).** The API forwards chunks immediately as tokens arrive. Faster, but chunks may not yet be valid JSON. Your client must tolerate parse-in-progress states.

Use fine-grained when the UI benefits from early argument display ("searching for: 'quantum'..." before the full query finalizes). Use default when the client just wants the final valid JSON.

---

## Built-in Anthropic tools

Some tools ship with the API. You still write the execution glue for most of them, but you don't author the schema.

- **Text editor** — built-in schema for file operations (view, create, replace, undo). Claude knows the schema; you implement the filesystem side. Useful for agent-style coding assistants without inventing a schema.
- **Web search** — fully hosted. Add the schema to `tools`; Claude performs the search itself and your code does nothing. The response contains the search queries, web_search_result blocks, and citation blocks tying statements to sources. Constrain with `allowed_domains` when source quality matters (medical, legal, academic).
- **Code execution + Files API** — Claude runs Python in an isolated Docker container with no network. Files uploaded via the Files API (returns a file ID) are referenced by ID. Claude can read inputs, run analysis, and generate output files.
- **Computer Use** — a special tool schema that lets Claude request mouse/keyboard/screenshot actions. The execution environment (typically Anthropic's reference Docker container) carries out the actions against a real GUI. You're still the execution layer; Anthropic provides the contract.

Built-ins save schema design but still follow the standard tool-use flow (request → execute → result → loop). They're not a separate system.

---

## When not to reach for tools

Tool use adds latency and complexity. Before adding a tool, ask:

- **Can the prompt answer directly?** If the fact is in training data and doesn't need to be fresh, no tool needed.
- **Is the tool just extracting structured output?** Prefill+stop may be sufficient (see `PROMPT_ENGINEERING.md`).
- **Is this really an agent?** Fixed-sequence pipelines often do better as explicit workflows, not tool-call loops (see `AGENTS_AND_WORKFLOWS.md`).
- **Does an MCP server already do this?** For broad third-party integrations, a maintained MCP server saves you the schema work entirely (see `MCP.md`).

Tools shine when the model needs external data, side effects, or capabilities beyond text — not as a default wrapper for everything the model could already do.
