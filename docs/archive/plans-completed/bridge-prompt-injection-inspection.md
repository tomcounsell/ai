# Plan: Pre-execution prompt-injection inspection on untrusted bridge input (#1630)

## Problem

Untrusted external text (Telegram messages, emails — including senders matched
only by an `email.domains` wildcard) reaches our full-access agent's execution
context with **no injection screen** between "a stranger typed this" and "the
agent acts on it." We run in YOLO/full-access mode (Bash, file edits, `gh`), so
an unscreened injection surface is the highest-severity of the six PAI-analysis
findings.

This plan ships an **MVP inspector for the two bridge text surfaces** (Telegram
+ email). Web-fetch (`tools/web/fetch.py`) and file-ingest are explicitly
deferred to a follow-up (issue #1630 Open Question 5) — they are a different
seam (`PreToolUse` / knowledge watcher) and would double the blast radius.

## Design decisions (the load-bearing calls)

### 1. Posture: flag-and-annotate, never block

The inspector **never blocks or drops a message**. On a suspected-injection
verdict it attaches a distinct provenance/risk banner that reaches the agent's
context; the agent decides how to treat the flagged content.

**Why:** A classifier that hard-blocks on false positives makes the bridge
unusable and gets disabled within a week. The failure costs are asymmetric: a
**false positive** just prepends a risk banner the agent can reason past (near-
zero cost); a **false block** silently drops a legitimate message from the CEO
(catastrophic). So the default posture biases entirely toward annotate. A
future graduated response (e.g. stricter handling for high-stakes tool calls)
can layer on once the flag-rate distribution is observed — deliberately out of
scope here.

### 2. Detection: LLM-based via PydanticAI, not regex

An LLM judgment call through the repo's PydanticAI wrapper
(`agent/llm/wrapper.py::run_typed`, `MODEL_FAST`), returning a structured
verdict. **No keyword/regex matcher** — a regex list for "ignore previous
instructions" is trivially bypassed and creates false confidence, and it
violates the repo principle of intelligent systems over rigid patterns. Per the
two-transport rule, this non-harness LLM call goes through PydanticAI
(model-agnostic), never the `claude -p` harness.

### 3. Insertion point: standalone pre-enqueue step at the raw-intake seam

The inspector is called as a **standalone pre-step at the raw-intake seam**,
strictly upstream of the steer/resume/new dispatch decision:

- **Telegram:** `bridge/telegram_bridge.py` after sender/project resolution
  (~line 1259), before any routing/steering branch.
- **Email:** `bridge/email_bridge.py::_process_inbound_email` after
  subject/body/from-address unpack + project resolution (~line 1305), before the
  In-Reply-To / subject-coalescing decision (~1354); the banner is folded into
  the `extra_context` dict built at ~1435. **Note (corrected after critique):**
  the `#1630` tokens at `email_bridge.py:1456/1462/1467` are *deferral comments*
  sitting inside live `attachments_unrecoverable` logic — NOT a landing zone.
  Do not replace them.

**Collision avoidance (#2159 / #2160):** #2159 extracts the *dispatch decision*
(steer/resume/new) into `bridge/intake_decision.py`; #2160 converges email onto
it. The inspector is a separate call **before** that decision and is **NOT**
wired into `bridge/dispatch.py` or the `1544-2509` decision block. As long as it
stays at the raw-intake seams, it does not touch the code either issue moves.
This is stated so a future reader (or #2159's author) knows the inspector is an
intake preamble, not part of the routing decision — if #2159/#2160 later unify
intake, the two inspector call sites are trivial to converge but do not block
that refactor.

### 4. Provenance-aware: trust tier gates inspection depth

`projects.json` already encodes trust structure. We derive a trust tier at the
seam (there is no single pre-existing trust-tier signal, so we re-query the
resolution maps):

- **TRUSTED** — whitelisted Telegram DM contact (`sender_id in DM_WHITELIST`) or
  a named email contact (exact `from_addr` in `EMAIL_TO_PROJECT`).
- **UNTRUSTED** — domain-wildcard email match (`EMAIL_DOMAIN_TO_PROJECT` hit
  with no exact-contact hit), or a group/unknown sender.

A message from a whitelisted DM is a different risk class than one from a
domain-wildcard email match, and the pre-gate treats them differently.

### 5. Cost & latency: a pre-gate bounds LLM calls, and the call is time-boxed

This sits on the inbound hot path, so most messages must **skip the LLM call
entirely**. `should_inspect(trusted, has_urls, text)` returns True only when:

- the source is **UNTRUSTED** (not a whitelisted DM / named email contact), OR
- the text carries **URLs** (cheap presence check, not a matcher),

**and** `len(text) >= MIN_CHARS` (very short = nothing to hide; very long is
truncated to `MAX_CHARS` before the LLM to bound cost).

Consequence: a whitelisted DM continuing a conversation with no URL — the
dominant traffic (Valor's own messages) — triggers **zero** LLM calls. (No
"first message of session" criterion: that would require a session lookup at the
raw-intake seam, which we deliberately avoid — trust-tier + URL presence is
sufficient and keeps the seam stateless.)

When the LLM IS called, it is hard-bounded by `INJECTION_INSPECT_TIMEOUT_S`
(default 6s) passed as `run_typed(..., sdk_timeout=..., hard_timeout=...)` —
**not** the 35s `anthropic_hard_s` default, which would be unacceptable on the
intake path. The call is awaited before enqueue (the banner must be attached to
the record at creation); the tight timeout + pre-gate bound the worst-case added
latency for the rare inspected message, and normal traffic adds 0ms.

### 6. Fail open, loudly

If anything in the inspector raises — `should_inspect`, trust derivation,
`run_typed` (which raises `LLMCallError`, incl. wrapping internal timeouts, or
`ValueError` on empty prompt), banner construction, or a counter incr — the
message **still gets through** un-annotated, a WARNING is logged, and
`{project_key}:injection-inspector:errors` is incremented. The **entire**
inspector body is wrapped in one broad `except Exception` (mirroring
`agent/tool_budget.py`, which wraps everything, not named types) so nothing can
propagate into the Telethon handler and crash/stall the bridge. A security
control that takes down the bridge when it breaks is worse than the gap.

### 7. Banner seam: prepended by the executor from `extra_context`, framed as untrusted-data delimiter

**Corrected after critique.** `build_harness_turn_input`
(`agent/session_runner/harness/claude.py`) takes **no** `extra_context`
parameter and its caller passes none, so a banner stashed in `extra_context`
would never render there. Instead:

- The seam stashes the banner string into the dispatch/enqueue
  `extra_context` (Telegram: the `extra_context_overrides` dict; email: the
  `extra_context` dict) under key `injection_risk_banner`. This **persists on the
  `AgentSession` record** (the same mechanism the `reply_chain_hydrated` flag
  already uses — `session_executor.py:1713` reads `session.extra_context`).
- **`agent/session_executor.py`** (once, right after `enriched_text` is
  finalized, ~line 1745, before `_turn_input`): if
  `session.extra_context["injection_risk_banner"]` is present, **prepend** it to
  `enriched_text`. One edit, in executor code (neither #2159 nor #2160 owns it),
  covering **all** transports and both Telegram dispatch sites uniformly because
  they all produce an `AgentSession` with persisted `extra_context`.

The banner is framed as a **screen delimiter**: it explicitly states that
everything after the delimiter is untrusted external DATA, not instructions.
Spoof-resistance comes from ordering, not a header field: the bridge's banner is
always first; any attacker-authored fake "this message is safe" line necessarily
appears *after* the delimiter, i.e. inside the zone the banner already marked
untrusted.

### 8. Observability

Three project-scoped Redis counters, mirroring the tool-budget pattern and
surfaced on the dashboard via `ui/app.py::_sum_project_counter`:
`injection-inspector:inspected`, `:flagged`, `:errors`. These make the
flag-rate and false-positive-suspicion distribution observable — the data a
future graduated-response decision would need (same instrument-then-decide
discipline as #1886).

## File-by-file changes

1. **`bridge/injection_inspection.py`** (new): module constants (raw
   `os.environ`, tool_budget precedent), `TrustTier` enum, `InspectionVerdict`
   dataclass, `_InjectionJudgment` Pydantic output model, `should_inspect()`
   pre-gate, `async inspect_untrusted_input(...)`, `build_risk_banner()`, and
   fail-quiet counter helpers.
2. **`bridge/telegram_bridge.py`** (~1259): derive `trusted` from
   `sender_id in DM_WHITELIST`; `has_urls` from a cheap presence check on the
   text; `await inspect_untrusted_input(...)`; build `_injection_ctx =
   {"injection_risk_banner": banner}` if flagged. Then fold `_injection_ctx`
   into **both** dispatch sites' override dicts via `{**(existing or {}),
   **_injection_ctx}`: the fresh-message `extra_overrides` (init at 2430, set at
   2477) and the completed-resume `_completed_extra_overrides` (1978/1980). These
   are additive dict merges, not edits to the steer/resume/new decision logic.
3. **`bridge/email_bridge.py`** (~1305): derive `trusted` by
   `from_addr.lower() in EMAIL_TO_PROJECT` (exact named contact) vs a
   domain-only hit; inspect subject+body; on flagged set
   `extra_context["injection_risk_banner"] = banner` in the dict built at ~1435.
4. **`agent/session_executor.py`** (~1745, after `enriched_text` finalized):
   prepend `session.extra_context["injection_risk_banner"]` to `enriched_text`
   when present. This is the ONE seam that makes the agent see the banner
   (`build_harness_turn_input` cannot — it has no extra_context param).
5. **`ui/app.py`**: surface the three counters
   (`injection-inspector:{inspected,flagged,errors}`) on the worker health block.
6. **`.env.example`** + config: document the new env switches/thresholds.
7. **`docs/features/bridge-prompt-injection-inspection.md`** (new): feature doc.

## Env constants (provisional / tunable)

- `INJECTION_INSPECTOR_ENABLED` (default `true`) — kill-switch. Never blocks;
  disabling only turns off the inspection + banner.
- `INJECTION_INSPECT_MIN_CHARS` (default `40`) — skip trivially short text.
- `INJECTION_INSPECT_MAX_CHARS` (default `20000`) — truncate before the LLM to
  bound cost.
- `INJECTION_INSPECT_TIMEOUT_S` (default `6`) — hard time-box on the `run_typed`
  call, well under the 35s `anthropic_hard_s` default, so a slow provider can't
  stall the intake path.

All carry a `# Provisional, tune after observing real rates` comment.

## Test plan (targeted, via `scripts/pytest-clean.sh`)

`tests/unit/test_injection_inspection.py`:
- `should_inspect()` matrix: TRUSTED continuing convo + no URL → skip; UNTRUSTED
  → inspect; URL-bearing trusted → inspect; first-message → inspect; too-short →
  skip.
- Verdict flow with `run_typed` **mocked** (no live LLM): risk="suspected" →
  `flagged=True` + banner; risk="none" → not flagged.
- Fail-open: `run_typed` raising → `inspected=False`, WARNING logged,
  `:errors` counter incremented, message passes.
- Counter increments (`:inspected`, `:flagged`) via a fake Redis double.
- `build_risk_banner()` produces a distinct header, and is `None` when not
  flagged.
- Disabled switch → no LLM call, no banner.

## Acceptance criteria

- [ ] A domain-wildcard-email / unknown-sender message carrying injection text is
      flagged and reaches the agent with an `INJECTION_RISK:` header; the message
      is **not** blocked.
- [ ] A whitelisted-DM continuing message with no URL triggers **zero** LLM calls.
- [ ] Inspector error/timeout → message passes un-annotated, WARNING + `:errors`
      counter; the bridge never crashes or stalls on inspection.
- [ ] The banner is a distinct header line, not concatenated into user text.
- [ ] Counters surface on the dashboard.
- [ ] No edit to `bridge/dispatch.py` and no change to the steer/resume/new
      decision *logic*; the only touches inside the 1544-2509 range are additive
      dict merges of the banner into the existing override dicts at the two
      dispatch call sites (merge-trivial with #2159). Banner rendering lives in
      `session_executor.py`, which neither issue owns.
- [ ] Both Telegram dispatch paths (fresh-message 2509 and completed-resume
      1985) carry the banner when flagged.
- [ ] Targeted tests pass; ruff clean.

## Rollout

Ships enabled (posture is annotate-only, so enabling is low-risk) with an env
kill-switch. The flag-rate/error counters make the live distribution observable;
a future issue can use that data to decide on a graduated response — not now.
