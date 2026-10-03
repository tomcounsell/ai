---
tracking: none
slug: m3-openai-route
type: build
status: planned
critique_rounds: 2
review_rounds: 2
---

# 3a: the gateway's OpenAI route

Task 3a of milestone 3 of [valor-rebuild.md](valor-rebuild.md). It gives
the gateway a second upstream, OpenAI's Responses API, metered by the same
rule as the Anthropic route: the kernel's own key on the way out, a
worst-case estimate before the call, the invoice amount after it. Pi on
GPT-6.1 (task 3b, [m3-pi-harness.md](m3-pi-harness.md)) spends through this
route and no other.

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
  worst-case estimate, then a charge equal to the usage OpenAI returned
  times the table's prices. Evidence: the test suite's replayed cases with
  the charges worked out by hand in the test, and one live call
  (`VALOR_LIVE=1`) whose recorded charge matches OpenAI's usage page for
  that request id, both numbers in the build report.
- **The key never leaves the kernel.** No ledger row, log line, error body,
  or turn-visible file holds the key or any part of it. Evidence: a test
  that serves every path, error, and cut case with a recognizable fake key
  and searches the ledger, the gateway's log, and every response body for
  it.
- **The key goes only to the listed paths.** Evidence: a test per refused
  path (chat completions, files, uploads, batches, assistants, realtime,
  audio, images, embeddings, fine tuning, a stored response's retrieval)
  showing a 403 and no request at the upstream.

## Threat model

- A turn controls the request: its path, body, headers, and any key it
  puts in them. It does not control the gateway's process, the kernel's
  key directory (every turn profile denies it), or the price table.
- What it might do: send the kernel's key to a path that spends without a
  meter (batches, files, background responses, hosted tools); hide a
  call's true size (a `previous_response_id` that brings stored context
  OpenAI bills as input); cut the stream before the usage arrives; send
  its own key so the call bills another account and the row lies; route
  billing to another project with an `OpenAI-Project` header; or read the
  key back from an error body or an echoed header.
- What holds: the credential is set only on a listed path, after the
  turn's own credential and organization headers are dropped; the meter
  charges the worst case whenever the usage did not arrive whole; and a
  request whose cost the table cannot price is refused, the same rule
  that refuses an unpriced Anthropic model.
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
Anthropic route, unchanged.

- **Upstream.** `settings.openai_upstream`, default `https://api.openai.com`,
  overridden by `VALOR_OPENAI_UPSTREAM` (tests point it at a local
  replaying upstream). The `openai/` prefix is stripped before forwarding.
- **Listed paths.** `OPENAI_CREDENTIALED_PATHS = ("v1/responses",
  "v1/models")`, plus `v1/models/<id>`. `POST v1/responses` is metered;
  the model listing is forwarded with the key and charged nothing. Any
  other OpenAI path is a 403, with nothing sent upstream. A tail with a
  percent escape, a dot segment, or a doubled slash is refused by
  `safe_tail` with a 400, as on the Anthropic route.
- **Headers.** The turn's `authorization`, `x-api-key`,
  `openai-organization`, and `openai-project` are dropped. The gateway sets
  `authorization: Bearer <key>`, the key read from the kernel key
  directory by an `OpenAIKey` credential (`core/gateway.py`, beside
  `ClaudeLogin`). A gateway built without an OpenAI credential answers
  every OpenAI path with a 403 and sends nothing.
- **Response headers.** `DROP_RESPONSE` applies as on the Anthropic route.
  OpenAI's `openai-organization` and `openai-project` response headers are
  dropped too, so a turn learns nothing of the account.

### Prices (`core/spending.py`)

- `OPENAI_PRICES`, apart from the Anthropic `PRICES`, with its own
  `checked` date: the day the build read OpenAI's pricing page. One entry
  per model id prefix, with input, cached input, and output prices per
  million tokens, and every surcharge the page lists for the model (a
  long-context rate above a token threshold; a service tier priced apart).
  GPT-6.1 is the one model priced at merge; the build copies its numbers
  from the page and names the page's URL in a comment beside `checked`.
- `openai_prices(model)` matches the longest prefix, as `prices()` does,
  and returns None for an unknown model, which the gateway answers with
  the same 400 as an unpriced Anthropic model, before any row is written
  or anything goes upstream.
- `openai_cost(usage, prices)`: input minus cached input at the input
  rate, cached input at the cached rate, output at the output rate, with
  the long-context rate applied when the page's threshold is passed. In
  OpenAI's usage, `input_tokens` includes
  `input_tokens_details.cached_tokens` and `output_tokens` includes
  `output_tokens_details.reasoning_tokens`, so neither is added twice.
- `openai_worst_case(body, prices)`: the estimated input (the existing
  `estimate_input`, 3 bytes per token) at the highest input rate the entry
  lists, plus `max_output_tokens` at the highest output rate, or the
  model's maximum output from the table when the body sets none.

### What the meter cannot price

A request is answered with a 400 naming what has no price, before any
row is written and with nothing sent, when the table cannot give its
cost. This is the
rule that refuses an unpriced model, applied to the parts of a Responses
body that carry their own charges:

- `tools` holding any hosted tool (`web_search`, `file_search`,
  `code_interpreter`, `computer_use`, `image_generation`, `mcp`, or any
  type other than `function` and `custom`);
- `background: true`, whose response is billed after the connection ends
  and never streams usage to the gateway;
- `service_tier` naming a tier the entry does not price.

A `previous_response_id` or `conversation` brings context the body does
not show. It is not refused: its worst case takes the model's full context
window as input, and the charge reads the input usage OpenAI returns.

### The meter (`core/gateway.py`)

`OpenAIMeter` beside `Meter`, fed the same bytes:

- **Streamed.** Server-sent events. `response.completed`,
  `response.incomplete`, and `response.failed` carry `response.usage`; the
  first of them seen closes the call with that usage. An `error` event with
  no usage leaves the call open to the cut rule.
- **Not streamed.** A JSON body with `usage` at the top level.
- **The charge, by `_close`'s cases:**
  - never sent: 0;
  - usage arrived: `openai_cost(usage)`;
  - a 4xx or 5xx before any output event: 0, as on the Anthropic route;
  - cut after `response.created` with no usage: the worst case. OpenAI
    reports input only at the end, so there is no reported input to
    charge in place of the estimate, unlike the Anthropic route's
    `message_start`.
- `gateway.opened` carries `route: "openai"`, the model, the estimate, and
  the request id once the upstream sends `x-request-id`.

### The key (`core/credentials.py`, `core/settings.py`, `core/__main__.py`)

- `settings.openai_keyfile`, in the kernel key directory beside
  `judgement_keyfile` (`~/.config/valor-kernel/`), mode 600, denied to
  every turn and check profile by `kernel_paths()`.
- `python -m core openai-key` copies `OPENAI_API_KEY` from the vault
  `.env` with `copy_keys`, and prints `written`, `kept`, or `missing`,
  never a value. It is the `judgement-keys` command with one name.
- `_run_task` builds `Gateway(credential=ClaudeLogin(),
  openai_credential=OpenAIKey())`. A missing key file is not a refusal to
  run: the OpenAI route answers 403 and every Claude Code turn runs as
  before. A turn that needs the route fails at its first call with the
  gateway's reason in its stderr tail.

## Tech debt absorbed

- `docs/harnesses.md` says the gateway speaks only the Anthropic format.
  The build rewrites that passage to describe both routes.
- The gateway's module docstring names one upstream. It names both.

## Left out

- Chat Completions, the Assistants API, Realtime, batches, files, and every
  hosted tool. Each is a 403 or an unpriced 400 until a task prices it.
- OpenAI models other than GPT-6.1 in the table. Adding one is a table row
  and its replayed cases.
- Codex and every harness other than Pi (milestone 3 leaves them out).
- A budget, a cap, or a stop on OpenAI spend. Spend is metered only.

## Tests

`tests/test_gateway_openai.py`, real Postgres, a real local upstream
replaying recorded Responses traffic from `tests/fixtures/openai/`. Every
test carries `pytest.mark.spend(usd=0)` except the live one.

Recordings: `tests/fixtures/openai/record.py` makes one streamed text
reply, one streamed reply with a function call and reasoning tokens, one
with cached input, one `response.incomplete` (output limit hit), one
`response.failed`, one non-streamed reply, and one 400, against the real
API under `VALOR_LIVE=1`, writing the bodies with the key and the request
ids scrubbed. The replaying upstream serves them byte for byte and records
every request it received.

Charges, each worked out by hand in the test from the recording's usage
and the table:

- a streamed reply charges input, output, and nothing cached;
- a reply with 1,024 cached tokens charges those at the cached rate and
  the rest of the input at the full rate, and the total is less than the
  same usage priced uncached;
- reasoning tokens are counted once, inside `output_tokens`;
- `response.incomplete` and `response.failed` charge their usage;
- a non-streamed reply charges `usage`;
- a 400 from the upstream before any output charges 0;
- a stream cut after `response.created` charges the worst case;
- a stream cut after `response.output_text.delta` and before
  `response.completed` charges the worst case;
- a turn stopped mid-stream (the NOTIFY path, as in
  `tests/test_gateway_meter.py`) charges the worst case and the row closes;
- a body with no `max_output_tokens` takes the table's maximum output in
  its estimate;
- a `previous_response_id` takes the model's context window as input in
  its estimate.

Refusals, each showing nothing reached the upstream:

- an unpriced model (400);
- each hosted tool type, one case per name, and an unknown tool type (400);
- `background: true` (400);
- an unpriced `service_tier` (400);
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
- a fake key with a recognizable body is absent from the ledger, the
  gateway's log, and every response body in every case above, including
  an upstream 401 whose body echoes the key's last four characters
  (the replaying upstream sends one);
- `python -m core openai-key` against a scratch vault and key file writes
  mode 600, reports `written`, then `kept`, and with the name absent,
  `missing`; its output holds no value.

Live (`VALOR_LIVE=1`, `pytest.mark.spend(usd=0.05)`): one streamed
GPT-6.1 reply through the gateway; the test prints the recorded charge and
the request id, and the build report puts them beside the usage page's
figure for that id.

The Anthropic route's tests run unchanged and pass.

## Files it changes

- `core/gateway.py`: the route split, `OpenAIKey`, `OpenAIMeter`, the
  listed paths, the header drops.
- `core/spending.py`: `OPENAI_PRICES`, `openai_prices`, `openai_cost`,
  `openai_worst_case`.
- `core/settings.py`: `openai_upstream`, `openai_keyfile`.
- `core/credentials.py`: the OpenAI key name.
- `core/__main__.py`: the `openai-key` command; the gateway built with
  both credentials.
- `tests/test_gateway_openai.py`, `tests/fixtures/openai/` (recordings
  and `record.py`), and the replaying upstream if the existing one does
  not serve both formats.
- `docs/architecture.md` (the gateway section and the turn record table),
  `docs/harnesses.md` (the gateway passage), `core/README.md` if it lists
  the gateway's paths.

3b changes `core/settings.py` and `core/__main__.py` too. The build that
merges second rebases onto the first.

## Expected spend, as information

The recordings and the live test together make about ten GPT-6.1 calls of
a few hundred tokens each, well under a dollar. The suite's replayed tests
spend nothing.

## Rollout

1. Tom: put `OPENAI_API_KEY` in the vault `.env` if the one there is not
   the key he wants the kernel to spend on. This is a credential step.
2. The build session, after merge: `python -m core openai-key` on the
   machine, and report `written` or `kept`. It runs the command only
   against the machine's key directory, which is a credential step Tom
   has asked for by approving this plan; it prints no value.
3. One live call through the merged gateway, its charge and request id
   reported beside OpenAI's usage page.

## Questions for Tom

- **Which key.** The kernel spends on the `OPENAI_API_KEY` in the vault
  `.env` (one is there). Assumed: yes, that key, copied to the kernel key
  directory by `python -m core openai-key`.
- **GPT-6.1's model id.** Assumed: `gpt-6.1` as OpenAI's API names it,
  priced from OpenAI's pricing page on the build day.

## Decided by default

- **One route, prefixed by `openai/`.** The turn's base URL for OpenAI is
  `<gateway>/t/<token>/openai/v1`. A second listener on another port would
  need a second sandbox parameter and a second profile line for nothing
  the prefix does not already give.
- **Responses only.** Pi's `openai-responses` API is the one Pi uses for
  GPT models, and Responses reports cached and reasoning tokens in one
  place. Chat Completions stays refused until something needs it.
- **Hosted tools, background mode, and unpriced tiers are unpriced.** The
  gateway already refuses what it cannot price; these are the Responses
  body's unpriced parts. Workspace turns have no web tools on either
  harness, so nothing loses a capability.
- **A cut stream charges the worst case.** OpenAI sends input usage only
  at the end, so no smaller figure is known to be at least the bill.
- **A missing key does not stop a run.** Only turns that use the route
  fail, and they fail with the gateway's reason.
- **Prices are copied by hand with a checked date**, as the Anthropic table
  is, since the page has no machine-readable form the kernel trusts.
