# Bridge Prompt-Injection Inspection

A pre-execution screen for **untrusted bridge input** (Telegram messages,
emails). It sits between "a stranger typed this" and "the full-access agent acts
on it" — the one place fully untrusted text enters a session that can run Bash,
edit files, and call `gh`.

## Posture: detection, not blocking

The inspector **never blocks, drops, or delays-to-death a message**. On a
suspected-injection verdict it attaches a provenance/risk banner that the agent
sees; the agent decides how to treat the flagged content. The failure costs are
asymmetric — a false positive just prepends a banner the agent can reason past;
a false block silently drops a legitimate message — so the default biases
entirely toward annotate. A future graduated response (stricter handling for
high-stakes tool calls) can layer on once the live flag-rate is observed.

## How it works

1. **Intake seam.** At the raw-intake point in `bridge/telegram_bridge.py`
   (after sender/project resolution, before the steer/resume/new decision) and
   `bridge/email_bridge.py::_process_inbound_email` (after subject/body/from
   unpack, before the coalesce decision), the bridge calls
   `bridge.injection_inspection.inspect_untrusted_input(...)`. This is a
   standalone pre-step — it does **not** touch the dispatch-decision logic.
2. **Pre-gate (`should_inspect`).** Stateless. Skips the LLM call for the
   dominant traffic — a **trusted** sender (whitelisted DM contact / exact named
   email contact) continuing a conversation with no URLs. Inspects when the
   source is **untrusted** (domain-wildcard email, group, unknown) OR the text
   carries a URL, provided it clears `INJECTION_INSPECT_MIN_CHARS`.
3. **Classifier.** A non-harness LLM call via PydanticAI
   (`agent/llm/wrapper.run_typed`, `MODEL_FAST`) returns a structured
   `{risk, reason}` verdict. No keyword/regex matcher. The call is hard-bounded
   by `INJECTION_INSPECT_TIMEOUT_S` so a slow provider cannot stall intake.
4. **Annotation.** On a flagged verdict the banner is stashed in the dispatch/
   enqueue `extra_context` under `injection_risk_banner` (persists on the
   `AgentSession`). `agent/session_executor.py` prepends it to the turn input
   once, covering every transport and dispatch path uniformly.
5. **Banner framing.** The banner is prepended first and declares everything
   after a `SCREEN DELIMITER` as untrusted DATA, not instructions.
   Spoof-resistance is by ordering: an attacker-authored fake "this message is
   safe" line necessarily lands *after* the delimiter, inside the zone the
   banner already marked untrusted.

## Fail open, loudly

The entire inspector body is wrapped in one broad `except Exception`. Any error
(pre-gate, `run_typed` raising `LLMCallError`/`ValueError`, a counter incr) lets
the message through **un-annotated**, logs a WARNING, and increments
`{project_key}:injection-inspector:errors`. A security control that takes down
the bridge when it breaks is worse than the gap it covers.

## Observability

Three project-scoped Redis counters, surfaced on the dashboard/`/health` worker
block (mirroring the tool-budget counters):

- `{project_key}:injection-inspector:inspected` — LLM classifier ran.
- `{project_key}:injection-inspector:flagged` — judged a suspected injection.
- `{project_key}:injection-inspector:errors` — inspector failed open.

`flagged / inspected` is the flag rate; a future graduated-response decision
would read it.

## Configuration (provisional, env-overridable)

Following the raw-`os.environ` module-constant precedent in
`agent/tool_budget.py` (deliberately NOT `config/settings.py`, so no
`.env.example` entry is required):

| Env var | Default | Meaning |
|---------|---------|---------|
| `INJECTION_INSPECTOR_ENABLED` | `true` | Kill-switch. Off = no inspection/banner/LLM call. |
| `INJECTION_INSPECT_MIN_CHARS` | `40` | Skip trivially short text. |
| `INJECTION_INSPECT_MAX_CHARS` | `20000` | Truncate before the LLM to bound cost. |
| `INJECTION_INSPECT_TIMEOUT_S` | `6` | Hard cap on the classifier call. |

## Scope and deferred surfaces

MVP covers the two bridge **text** surfaces (Telegram + email body/subject).
Explicitly deferred to follow-ups:

- **Web-fetch / file-ingest content** (`tools/web/fetch.py`, knowledge watcher)
  — a different seam (`PreToolUse`).
- **Telegram edit-handler path** and **live steering messages** — secondary
  surfaces on distinct code paths; the initial message is screened, edits and
  live steering messages are not. Annotating them requires a different attach
  mechanism.

## Known tradeoff: per-message cost on busy monitored groups

The screen runs at the **raw-intake seam**, deliberately upstream of the
should-respond / dispatch decision so it stays collision-free with the
intake-decision extraction. A consequence: an owned **group**
message that clears the pre-gate triggers a (6s-capped, `MODEL_FAST`) inspection
even when the bridge ultimately ignores it. For low-volume monitored groups this
is negligible; for a busy group it is recurring cost. Mitigations:
`INJECTION_INSPECT_MIN_CHARS`, and the `INJECTION_INSPECTOR_ENABLED` kill-switch.
Gating inspection on "will actually dispatch" requires the dispatch decision to
be an importable function, so the inspector runs at the raw-intake seam instead.
