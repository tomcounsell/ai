# Spike 03: Gateway kill and trace

Closes tech-stack §4 (LLM gateway) and spike 3 in §14.

## Question

With a streaming proxy between the agent and the provider, holding the only
provider key and issuing per-Brief bearer tokens: how fast does a revoke stop
an agent, can the whole tool-call sequence be rebuilt from the gateway's own
log, and does the gateway's usage ledger match what the provider reports?

## Method

- `gateway.py`: FastAPI + httpx. In-memory kernel issues `brief_*` tokens with
  a token budget. `POST /v1/messages` authenticates the token, refuses if
  revoked, estimates input with the provider's `count_tokens` endpoint,
  refuses if `used + estimated_input + max_tokens > budget`, forwards with the
  real key, streams the SSE bytes back unchanged while parsing them for usage
  and content blocks, and appends request, response, usage, refusals, and cuts
  to `audit.jsonl`. A revoke flag is checked before every streamed chunk.
- `agent.py`: raw Anthropic SDK streaming loop (Haiku 4.5, `max_retries=0`)
  with three fake tools, pointed at the gateway with only its Brief token.
- `run.py`: three scenarios in one process. (1) clean run, then rebuild the
  tool sequence from the log alone and diff it against the agent's own trace.
  (2) an out-of-band watcher polls the kernel and revokes once call 2 has
  streamed three chunks. (3) a budget of 3,300 tokens, too small for the run.

Run: `./run.sh`. Reads `ANTHROPIC_API_KEY` from `~/src/ai/.env`; the agent
never sees it. Total spend for the three scenarios: about $0.02.

## Numbers

Clean run: 6 model calls, 5 tool calls.

| Measure | Result |
|---|---|
| Tool sequence rebuilt from gateway log == agent's own trace | identical (5 of 5 calls, inputs and results) |
| Provider usage seen by the client vs gateway-parsed usage | 0 tokens difference across 24 usage fields |
| `count_tokens` pre-check estimate vs billed input | exact on all 6 calls (738, 825, 915, 1000, 1088, 1182) |
| `count_tokens` pre-check latency | median 410 ms per call |
| End-to-end call latency through the gateway | median 1,540 ms |

Revoke during call 2's stream:

| Measure | Result |
|---|---|
| In-flight stream cut after revoke | 40.6 ms (at the next upstream chunk) |
| Agent observed the failure after revoke | 42.4 ms (`APIStatusError: permission_error: brief revoked`) |
| Retry with the same token | refused with 403 in 4.8 ms, zero provider calls |
| Usage recorded for the cut call | input 820, output 1 (the final `message_delta` never arrived) |

Budget of 3,300 tokens: 3 calls forwarded (2,664 billed), call 4 refused
because 2,664 + 1,000 estimated input + 300 max_tokens exceeded the budget.
The refusal is conservative by design: it reserves `max_tokens` up front.

## Surprises

1. **The pre-flight budget check is the most expensive thing in the gateway.**
   `count_tokens` is exact, but it is a second round trip to the provider and
   cost 410 ms of a 1,540 ms call. For a budget check that only needs to be
   conservative, the previous call's billed input plus the size of what was
   appended since is enough, and it is free. Use `count_tokens` for the first
   call of a Brief and for a periodic re-anchor, never for every call.
2. **A cut stream loses its output usage.** The provider reports output tokens
   in the final `message_delta`, which never arrives when the gateway
   disconnects. The ledger shows 1 output token for a call that generated
   some unknown number before the cut. The safe rule is to charge the reserved
   `max_tokens` to any call that ends without a `message_delta`, and reconcile
   later against the provider's usage report if that matters for money.
3. **Reconstruction from the log is complete because the harness sends the
   whole conversation on every call.** Each request body contains every prior
   tool_result, so the log has both halves of every tool exchange without the
   agent cooperating. That also means the log grows quadratically with turn
   count; the gateway should store request bodies by content hash and record
   deltas, or the audit log becomes the largest table in the system.
4. **Kill latency is bounded by upstream chunk cadence, not by the gateway.**
   The revoke flag is checked per chunk, so a cut lands within one output
   token's worth of time (tens of ms on Haiku). A non-streaming request would
   not be cut until it finished; the gateway should force `stream: true`
   upstream regardless of what the client asked, which this one does.

## Not covered

- Non-Anthropic wire formats.
- Cache-control injection by the gateway (tech-stack §4 item 6).
- A Brief that opens two streams concurrently.

## Recommendation

**Assumption holds.** Revoke is a sub-50 ms kill against a streaming call and
a sub-5 ms refusal on the next call, and the gateway log alone reconstructs
the tool trace. Two edits for §4: state that the budget pre-check uses the
previous call's usage rather than `count_tokens` per call, and that a call cut
by revoke is charged its reserved `max_tokens`. The "reconciles to provider
usage within 1%" pass criterion for spike 3 holds exactly for completed calls
and is unmeasurable from the stream for cut calls.
