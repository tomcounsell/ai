# Direct-API Reply Handling

**Issue:** [#3570](https://github.com/tomcounsell/ai/issues/3570) · **Plan:** `docs/plans/sonnet-5-5-model-id-migration.md`

Four tools call the Anthropic Messages API, or OpenRouter chat completions, directly: `tools/test_judge`, `tools/image_tagging`, `tools/documentation`, and `tools/image_analysis`. None of them reads `content[0]`. All extract the reply through `tools/llm_reply.py`, which encodes the reply shapes Claude Sonnet 5.5 produces. Calls that go through `agent/llm/run_typed` are covered by [Non-Harness LLM Wrapper](nonharness-llm-wrapper.md), not this doc.

## The `tools/llm_reply.py` contract

| Function | Input | Behavior |
|----------|-------|----------|
| `anthropic_text(response, max_tokens=None)` | An `anthropic` SDK `Message` or the raw JSON dict | Joins the `text` of every `type == "text"` block. Thinking and other blocks are skipped. Returns `""` when there are no text blocks. |
| `openrouter_text(result, max_tokens=None)` | An OpenRouter chat-completion dict | Returns `choices[0].message.content`. The separate `message.reasoning` field is ignored. Returns `""` for empty `choices` or `None` content. |
| `parse_last_json(text)` | Reply text | Returns the last complete JSON object or array in the text. Raises `ValueError` when none is present. |

### Text blocks only

A Sonnet 5.5 reply can open with a thinking block, so the answer is not necessarily the first block. `anthropic_text` reads `text` blocks and nothing else, from both SDK objects and raw dicts.

### Truncation is failure

A reply cut off at the output-token limit is never returned as a partial answer. `anthropic_text` raises `ReplyTruncated` when `stop_reason == "max_tokens"`, and `openrouter_text` raises it when `finish_reason == "length"`. The `max_tokens` argument only makes the message diagnosable. Each tool catches `ReplyTruncated` and surfaces it as its own `{"error": ...}` result.

### The last JSON value wins

The model may reason in prose and end with the JSON, so `parse_last_json` returns the last complete JSON value, not the first. Only code-fence marker lines (a line holding just a fence such as `json`-tagged triple backticks) are stripped; fence-like text inside a JSON string value survives. Nested values and trailing prose are both handled. `test_judge` and `image_tagging` parse with it.

## Per-tool request settings

| Tool | Thinking | Effort | `max_tokens` |
|------|----------|--------|--------------|
| `test_judge` | `adaptive` | `medium` | 8192 |
| `image_tagging` | `adaptive` | `low` | 4096 |
| `documentation` | `between_tools` | none | 8192 |
| `image_analysis` | `between_tools` | none | 4096 |

- `max_tokens` covers thinking plus the reply, so it is sized above the reply alone.
- `between_tools` allows thinking only between tool calls, which none of these single-shot requests make, so it effectively turns thinking off for the Anthropic path.
- The OpenRouter path sends the same `max_tokens` and no reasoning parameters. OpenRouter leaves thinking on by default, which is why the shared limit carries the headroom.
- `test_judge` uses a 120 s request timeout on both paths.

## Parameters Sonnet 5.5 does not accept

- `thinking: {"type": "disabled"}` and `budget_tokens` are rejected. Only `adaptive` and `between_tools` thinking are sent.
- A forced `tool_choice` is rejected with a 400. The `run_typed` Anthropic leg and the `tools/email_cs/agents.py` forced-tool-choice call therefore run on Haiku only. The `email_cs` call site carries a comment stating what moving it off Haiku would take. The leg's path to Sonnet 5.5 is tracked in [#3578](https://github.com/tomcounsell/ai/issues/3578).

## Tests

- `tests/unit/test_llm_reply.py` covers the helper: reasoning prose before the JSON, a leading thinking block in dict and SDK-object forms, both truncation signals, fenced JSON, nested JSON, and no JSON.
- `tests/unit/test_direct_api_truncation.py` covers truncation surfacing as each tool's error result.
