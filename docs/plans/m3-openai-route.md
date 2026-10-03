---
tracking: none
slug: m3-openai-route
type: build
status: planned; revised after critique round 1, awaiting round 2
critique_rounds: 2
review_rounds: 2
---

# 3a: the gateway's OpenAI route

Task 3a of milestone 3 of [valor-rebuild.md](valor-rebuild.md). It gives
the gateway a second upstream, OpenAI's Responses API, metered by the same
rule as the Anthropic route: the kernel's own key on the way out, a
worst-case estimate before the call, the provider's reported usage priced
after it. Pi on GPT-6.1 (task 3b, [m3-pi-harness.md](m3-pi-harness.md))
spends through this route and no other.

Built on the rebuild branch from the commit this plan lands on, in its own
worktree, test database, and ports. It merges after 1.5, with the rest of
milestone 3.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`, as valor-rebuild.md sets for the
OpenAI route. The task changes the kernel's spend path and its credential
handling. A mistake either sends the kernel's OpenAI key somewhere it was
not meant to go, lets a turn spend on that key without a row, or records
less than OpenAI bills.

## The Done items it closes

From milestone 3, "Pi runs on GPT-6.1 on OpenAI, metered by a new route in
the gateway". 3a closes the route half; 3b closes the Pi half.

- **A metered OpenAI call.** A turn that posts to
  `/t/<token>/openai/v1/responses` reaches OpenAI with the kernel's key,
  and the ledger holds `gateway.opened` with `route: "openai"` and the
  worst-case estimate, then `gateway.charged` with OpenAI's returned
  usage, the tier and tool calls it reported, the request id, and a charge
  equal to that usage priced by the table. Evidence: the replayed cases
  with the charges worked out by hand in the test, and one live call
  (`VALOR_LIVE=1`) whose returned usage the build report prices by hand
  beside the recorded charge, with the request id. If an OpenAI admin key
  is in the vault at build time, the report also gives the Usage API's
  one-minute bucket for that key and model beside the charge; if not,
  that comparison is a rollout step for Tom.
- **The key never leaves the kernel.** No ledger row, error body, response
  header, or the gateway's captured stderr holds the key or any part of
  it. Evidence: a test that serves every path, error, and cut case with a
  recognizable fake key and searches the ledger rows, every response body
  and header the turn received, and the stderr the test captured from
  the gateway.
- **The key goes only to the listed paths.** Evidence: a test per refused
  path (chat completions, files, uploads, batches, assistants, realtime,
  audio, images, embeddings, fine tuning, a stored response's retrieval)
  showing a 403 and no request at the upstream.

## Threat model

- A turn controls the request: its path, body, headers, and any key it
  puts in them. It does not control the gateway's process, the kernel's
  key directory (every turn profile denies it), or the price tables.
- What it might do: send the kernel's key to a path that spends without a
  meter (batches, files, fine tuning); hide a call's true size (a
  `previous_response_id` that brings stored context OpenAI bills as
  input); use a tool that bills per call; cut the stream before the usage
  arrives; send its own key so the call bills another account and the row
  lies; route billing to another project with an `OpenAI-Project` header;
  or read the key back from an error body or an echoed header.
- What holds: the credential is set only on a listed path, after the
  turn's own credential and organization headers are dropped; the meter
  charges the worst case whenever the usage did not arrive whole; a
  fee-bearing tool is charged per call from the response; and a request
  whose cost the table cannot price (an unpriced model, tier, or
  fee-bearing tool) gets a 400.
- **Accepted: stored responses on a shared key.** A `previous_response_id`
  continues any response stored under the key, so a turn that learns
  another conversation's id can read its context. On a key shared with
  Tom's own work or other tools, that reach covers them too. The plan
  recommends a project key used only by the kernel (Questions for Tom),
  which narrows the reach to the kernel's own turns; on a shared key the
  reach is accepted as stated. 3b records whether Pi's client sends
  `store: false`.
- **Accepted: fees after a cut.** A background response, or a streamed one
  whose connection is cut, can go on generating at OpenAI. Its tokens are
  bounded by `max_output_tokens` and covered by the worst case; its tool
  calls after the cut are not seen. The charge counts the fee-bearing calls
  seen, plus the request's `max_tool_calls` times the highest listed fee
  when the request sets it; without `max_tool_calls` the unseen calls are
  not charged.
- Out of reach of this task: a turn that holds an OpenAI key of its own
  and calls `api.openai.com` directly. The turn profile allows outbound
  connections beyond loopback, and nothing in a workspace holds such a
  key; the vault and the kernel key directory are both denied to every
  turn.

## Design

### The route (`core/gateway.py`)

The gateway keeps one handler, `/t/{token}/{tail:.*}`, with the tail read
undecoded and checked by `safe_tail` as it arrives. A tail that starts
with `openai/` belongs to the OpenAI route; every other tail belongs to the
Anthropic route, unchanged, still recorded as `route: "gateway"`.

- **Upstream.** `settings.openai_upstream`, default `https://api.openai.com`,
  overridden by `VALOR_OPENAI_UPSTREAM` (tests point it at a local
  replaying upstream). The `openai/` prefix is stripped before forwarding.
- **Listed paths.** `OPENAI_CREDENTIALED_PATHS = ("v1/responses",
  "v1/models")`, plus `v1/models/<id>`, checked as `credentialed` checks
  `CREDENTIALED_PATHS`. `POST v1/responses` is metered; the model listing
  is forwarded with the key and charged nothing. Any other OpenAI path is
  a 403, with nothing sent upstream. A tail with a percent escape, a dot
  segment, or a doubled slash is refused by `safe_tail` with a 400, as on
  the Anthropic route.
- **Headers.** The turn's `authorization`, `x-api-key`,
  `openai-organization`, and `openai-project` are dropped. The gateway sets
  `authorization: Bearer <key>`, the key read from the kernel key
  directory by an `OpenAIKey` credential (`core/gateway.py`, beside
  `ClaudeLogin`).
- **No OpenAI credential.** A gateway built without one answers every
  OpenAI path with a 403 and sends nothing. The Anthropic route without a
  credential forwards the turn's own; the OpenAI route does not, because
  an OpenAI call that bills a key of the turn's own is a call the kernel
  cannot see on any invoice of its own.
- **A 401 invalidates only its route's credential.** The handler's
  `invalidate()` on a 401 is called on the credential of the route that
  answered, so an OpenAI 401 never forces a Keychain read of the Claude
  login.
- **Response headers.** `DROP_RESPONSE` applies as on the Anthropic route.
  OpenAI's `openai-organization` and `openai-project` response headers are
  dropped too, so a turn learns nothing of the account.

### Prices (`core/settings.py`, `core/spending.py`)

- `OPENAI_PRICES` in `core/settings.py`, beside `PRICES` and kept apart
  from it as `JUDGEMENT_PRICES` is, so the Anthropic route never prices an
  OpenAI model. An `OpenAIPrice` per model id prefix, with the pricing
  page's URL in a comment and a `checked` date: input, cached input, and
  output per million tokens; a rate per service tier the page lists; the
  long-context threshold and its input and output rates; and the model's
  context window and maximum output.
- `OPENAI_TOOL_FEES` beside it: one line per fee-bearing tool type the
  page prices per call (the build reads `web_search` and `file_search`
  from the page), each with its unit and `checked` date. A tool whose fee
  unit the response does not let the gateway count (a code interpreter
  container billed per session) gets no line, so a request using it is
  unpriced. Token-only tools (function, custom, MCP, computer use, shell,
  image generation, whose cost is in the usage) need no line.
- `openai_prices(model)` matches the longest prefix, as `prices()` does,
  and returns None for an unknown model, which the gateway answers with
  the same 400 as an unpriced Anthropic model, before any row is written
  or anything goes upstream.
- `openai_cost(usage, tier, tool_calls, prices)`: at the rates of the tier
  the response reports, input minus cached input at the input rate, cached
  input at the cached rate, output at the output rate; above the
  long-context threshold, the long-context rates for input and output
  both; plus each fee-bearing tool's count times its fee. In OpenAI's
  usage, `input_tokens` includes `input_tokens_details.cached_tokens` and
  `output_tokens` includes `output_tokens_details.reasoning_tokens`, so
  neither is added twice. Rounded up per field, as `cost` is.
- `openai_worst_case(body, prices)`: the estimated input (the existing
  `estimate_input`, 3 bytes per token, or the model's context window when
  the body carries `previous_response_id` or `conversation`) at the
  highest input rate any tier or the long-context rate lists, plus
  `max_output_tokens` (or the model's maximum output when absent) at the
  highest output rate, plus `max_tool_calls` times the highest fee among
  the request's fee-bearing tools when it sets both.

### What the meter cannot price

A request gets a 400 naming what has no price, before any row is written
and with nothing sent, when the table cannot give its cost. This is the
rule that refuses an unpriced model, applied to the two other parts of a
Responses body that carry their own prices:

- `service_tier` naming a tier the entry does not price (`auto` and an
  absent tier are priced: they open at the highest tier and charge at the
  tier the response reports);
- a tool whose type is neither token-only nor in `OPENAI_TOOL_FEES`.

`background: true` is metered like any call: a streamed background
response feeds the meter as it streams; a non-streamed one returns queued
with `usage: null` and is charged the worst case.

### The meter (`core/gateway.py`)

`OpenAIMeter` beside `Meter`, fed the same bytes:

- **Streamed.** Server-sent events. `response.completed`,
  `response.incomplete`, and `response.failed` carry `response.usage`,
  `response.service_tier`, and `response.output`; the first of them seen
  with a usage object closes the call. `response.output_item.added` items
  of a fee-bearing `*_call` type are counted as they stream, so a cut call
  is charged the calls seen.
- **Not streamed.** A JSON body with `usage` at the top level.
- **`usage: null` is no usage.** A queued or in-progress response carries
  it; the meter treats it as absent, and the call takes the worst-case
  branch.
- **The charge, by `_close`'s cases:**
  - never sent: 0;
  - usage arrived: `openai_cost(usage, tier, tool_calls)`;
  - a 4xx or 5xx before any output event: 0, as on the Anthropic route;
  - cut, or no usage, after the call reached OpenAI: the worst case.
    OpenAI reports input only at the end, so there is no reported input
    to charge in place of the estimate, unlike the Anthropic route's
    `message_start`.
- `gateway.opened` carries `route: "openai"`, the model, and the estimate.
  It is written before the request goes upstream, so the request id
  (`x-request-id`) goes in `gateway.charged`'s detail, with the reported
  tier and the tool call counts.

### The key (`core/credentials.py`, `core/settings.py`, `core/__main__.py`)

- `settings.openai_keyfile`, in the kernel key directory beside
  `judgement_keyfile` (`~/.config/valor-kernel/`), mode 600, denied to
  every turn and check profile by `kernel_paths()`.
- `python -m core openai-key [--name NAME]` copies `NAME` (default
  `OPENAI_API_KEY`) from the vault `.env` with `copy_keys`, and prints
  `written`, `kept`, or `missing`, never a value. It is the
  `judgement-keys` command with one name.
- `_run_task` builds `Gateway(credential=ClaudeLogin(),
  openai_credential=OpenAIKey())`. A missing key file is not a refusal to
  run: the OpenAI route answers 403 and every Claude Code turn runs as
  before. A turn that needs the route fails at its first call with the
  gateway's reason in its stderr tail.

## Tech debt absorbed

- **The Anthropic route undercounts web search.** `spending.cost` ignores
  `usage.server_tool_use.web_search_requests`, which Anthropic bills per
  search. `Price` gains the page's per-search fee and `cost` charges it,
  so both routes price per-call tools the same way. A replayed Anthropic
  response with two searches is charged them, worked out by hand.
- `docs/harnesses.md` says the gateway speaks only the Anthropic format.
  The build rewrites that passage to describe both routes.
- The gateway's module docstring names one upstream. It names both.

## Left out

- Chat Completions, the Assistants API, Realtime, batches, files, and fine
  tuning. Each is a 403 until a task prices it.
- Code interpreter containers, until the gateway can count their unit.
- OpenAI models other than the GPT-6.1 id in the table. Adding one is a
  table row and its replayed cases.
- Codex and every harness other than Pi (milestone 3 leaves them out).
- A budget, a cap, or a stop on OpenAI spend. Spend is metered only.

## Tests

`tests/test_gateway_openai.py`, real Postgres, a real local upstream
replaying recorded Responses traffic from `tests/fixtures/openai/`. Every
test carries `pytest.mark.spend(usd=0)` except the live one.

Recordings: `tests/fixtures/openai/record.py` makes, against the real API
under `VALOR_LIVE=1`, one streamed text reply, one streamed reply with a
function call and reasoning tokens, one with cached input, one
`response.incomplete` (output limit hit), one `response.failed`, one
non-streamed reply, one with a `web_search` call, one background reply
streamed and one not, and one 400. The key and request ids are scrubbed
before writing. Cases the API cannot be made to produce cheaply (input
above the long-context threshold, a priority tier) are edits of a
recording, with the edit named in the fixture. The replaying upstream
serves them byte for byte and records every request it received.

Charges, each worked out by hand in the test from the recording's usage
and the table:

- a streamed reply charges input, output, and nothing cached;
- a reply with 1,024 cached tokens charges those at the cached rate and
  the rest of the input at the full rate, and the total is less than the
  same usage priced uncached;
- reasoning tokens are counted once, inside `output_tokens`;
- input above the long-context threshold charges the long-context rates
  on input and on output;
- a request with no tier whose response reports `priority` charges the
  priority rates; one that reports `default` charges the default rates;
- a reply with one `web_search_call` charges its tokens plus one search
  fee;
- `response.incomplete` and `response.failed` charge their usage;
- a non-streamed reply charges `usage`;
- a non-streamed background reply (`status: queued`, `usage: null`)
  charges the worst case and raises nothing;
- a streamed background reply charges its usage at completion;
- a 400 from the upstream before any output charges 0;
- a stream cut after `response.created` charges the worst case;
- a stream cut after a `web_search_call` item and before
  `response.completed` charges the worst case plus that one fee;
- a turn stopped mid-stream (the NOTIFY path, as in
  `tests/test_gateway_meter.py`) charges the worst case and the row closes;
- a body with no `max_output_tokens` takes the table's maximum output in
  its estimate;
- a `previous_response_id` takes the model's context window as input in
  its estimate;
- the request id is in `gateway.charged`, not `gateway.opened`.

Refusals, each showing nothing reached the upstream:

- an unpriced model (400);
- an unpriced `service_tier` (400);
- a `code_interpreter` tool and an unknown tool type (400); a `function`,
  an `mcp`, and a `web_search` tool are forwarded;
- `v1/chat/completions`, `v1/files`, `v1/batches`, `v1/responses/<id>`
  (retrieval), `v1/embeddings`, `v1/realtime` (403);
- `openai/v1/responses%2f..`, `openai//v1/responses`, and
  `openai/v1/./responses` (400 from `safe_tail`);
- a gateway with no OpenAI credential (403 on every OpenAI path; the
  Anthropic route still answers).

Headers and the key:

- the turn's `authorization: Bearer sk-turn` reaches the upstream as the
  kernel's key and never as `sk-turn`;
- `openai-organization` and `openai-project` from the turn never reach the
  upstream; the upstream's `openai-organization` response header never
  reaches the turn;
- an upstream 401 on the OpenAI route invalidates `OpenAIKey` and leaves
  `ClaudeLogin`'s cached token and read time untouched;
- a fake key with a recognizable body is absent from the ledger rows,
  every response body and header the turn received, and the gateway's
  captured stderr, in every case above, including an upstream 401 whose
  body echoes the key's last four characters (the replaying upstream
  sends one);
- `python -m core openai-key` against a scratch vault and key file writes
  mode 600, reports `written`, then `kept`, and with the name absent,
  `missing`; `--name` copies another name; its output holds no value.

Live (`VALOR_LIVE=1`, `pytest.mark.spend(usd=0.05)`): one streamed
GPT-6.1 reply through the gateway; the test prints the returned usage,
the recorded charge, and the request id, and the build report prices the
usage by hand beside the charge.

The Anthropic route's tests run unchanged and pass, with the web search
case added.

## Files it changes

- `core/gateway.py`: the route split, `OpenAIKey`, `OpenAIMeter`, the
  listed paths, the header drops, the per-route 401 invalidation.
- `core/settings.py`: `OpenAIPrice`, `OPENAI_PRICES`, `OPENAI_TOOL_FEES`,
  `Price`'s web search fee, `openai_upstream`, `openai_keyfile`.
- `core/spending.py`: `openai_prices`, `openai_cost`, `openai_worst_case`;
  the web search fee in `cost`.
- `core/credentials.py`: the OpenAI key name.
- `core/__main__.py`: the `openai-key` command; the gateway built with
  both credentials.
- `tests/test_gateway_openai.py`, `tests/test_gateway_meter.py` (the web
  search case), `tests/fixtures/openai/` (recordings and `record.py`),
  and the replaying upstream if the existing one does not serve both
  formats.
- `docs/architecture.md` (the gateway section and the turn record table),
  `docs/harnesses.md` (the gateway passage), `core/README.md` if it lists
  the gateway's paths.

3b changes `core/settings.py` and `core/__main__.py` too, and its
`reviewer_openai` seat takes the GPT-6.1 id this task pins. The build that
merges second rebases onto the first.

## Expected spend, as information

The recordings and the live test together make about a dozen GPT-6.1
calls of a few hundred tokens each and one web search, well under a
dollar. The suite's replayed tests spend nothing.

## Rollout

1. Tom: the key the kernel spends on is in the vault `.env` (see
   Questions for Tom). This is a credential step.
2. The build session, after merge: `python -m core openai-key` (with
   `--name` if Tom names a kernel-only key), and report `written` or
   `kept`. It prints no value.
3. One live call through the merged gateway, its returned usage priced by
   hand beside the charge, with the request id.
4. If no OpenAI admin key was in the vault at build time: Tom, or the
   build session once he puts one there, reads the Usage API's one-minute
   bucket for that key and model at the live call's minute and compares it
   with the charge.

## Questions for Tom

- **Which key.** Recommended: a project key in OpenAI used only by the
  kernel, put in the vault under a name of Tom's choosing, so stored
  responses a turn can reach are the kernel's own and the Usage API shows
  the kernel's spend alone. Assumed if Tom does not make one: the
  `OPENAI_API_KEY` already in the vault, with the shared-key reach stated
  in the threat model accepted.

## Decided by default

- **The model id comes from OpenAI.** The build takes GPT-6.1's id from
  the pricing page and confirms it in `v1/models` through the gateway (the
  page lists `gpt-6.1-sol`), and pins that id in `OPENAI_PRICES` and in
  3b's seat.
- **One route, prefixed by `openai/`.** The turn's base URL for OpenAI is
  `<gateway>/t/<token>/openai/v1`. A second listener on another port would
  need a second sandbox parameter and a second profile line for nothing
  the prefix does not already give.
- **Responses only.** Pi's `openai-responses` API is the one Pi uses for
  GPT models, and Responses reports cached and reasoning tokens in one
  place. Chat Completions stays refused until something needs it.
- **Per-call tool fees are priced lines.** A tool is unpriced only when no
  line prices it, which is the unpriced-model rule; token-only tools pass.
- **Background responses are metered, not refused.** The worst case covers
  a queued or cut one.
- **A cut stream charges the worst case.** OpenAI sends input usage only
  at the end, so no smaller figure is known to be at least the bill.
- **The tier charged is the one OpenAI reports.** With no tier or `auto`,
  the project's default decides the bill, and the response says which.
- **A missing key does not stop a run.** Only turns that use the route
  fail, and they fail with the gateway's reason.
- **Prices are copied by hand with a checked date**, as the Anthropic table
  is, since the page has no machine-readable form the kernel trusts.

## Record

- Critique round 1: revise. Every finding accepted.
  1. Background responses metered, not refused; per-call tool fees are
     lines in `OPENAI_TOOL_FEES` counted from `*_call` items; a tool with
     no line gets the 400; token-only tools pass; the blanket allowlist
     deleted.
  2. Evidence is OpenAI's returned usage priced by hand, with the request
     id on `gateway.charged`; the Usage API bucket comparison at build
     time if an admin key is in the vault, otherwise a rollout step for
     Tom.
  3. The charge uses the tier the response reports; the estimate opens at
     the highest tier.
  4. Long-context rates apply to input and output; a replayed case above
     the threshold.
  5. The request id moved to `gateway.charged`.
  6. A 401 invalidates only its route's credential; a test that
     `ClaudeLogin` is untouched.
  7. `usage: null` is no usage; a replayed queued case.
  8. A kernel-only project key recommended; the shared-key reach of
     `previous_response_id` stated as accepted in the threat model; 3b
     records `store`.
  9. `OPENAI_PRICES` lives in `core/settings.py` beside `PRICES`.
  10. The id is taken from the pricing page and `v1/models`, under
      Decided by default.
  11. The Anthropic web search undercount fixed in `cost` as tech debt
      absorbed, with a test.
  12. The searched places named (ledger rows, response bodies and headers,
      captured stderr); the Anthropic route keeps `route: "gateway"`; the
      no-credential 403 explained in one line.
