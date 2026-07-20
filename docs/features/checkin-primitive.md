# Check-in Primitive

**Issue:** [#2139](https://github.com/tomcounsell/ai/issues/2139)
**Status:** Shipped

## What it does

`python -m tools.agent_session_scheduler checkin` lets a running session register a
**one-shot future check-in**: an arbitrary prompt that runs at a chosen time T as a real Eng
session and delivers its result back to the originating chat. The call returns a citable
`schedule_id` that the [promise gate](promise-gate.md) accepts as a verifiable
autonomous-delivery reference.

This is the *fulfillment* half of the promise gate. The gate already blocked unevidenced
forward promises ("I'll report back when it lands") unless the draft cited a scheduled-delivery
mechanism — but no runtime primitive existed for a session to *create* one. `checkin` closes
that gap: a session that legitimately cannot finish its work in-turn can schedule a real
follow-up and cite it, instead of blocking in-turn or going silent.

## Usage

```bash
# Relative delay
python -m tools.agent_session_scheduler checkin \
  --prompt "Check whether the research job finished; if so, summarize and post to the chat" \
  --in 30m

# Absolute time
python -m tools.agent_session_scheduler checkin \
  --prompt "Report the deploy status" \
  --at 2026-07-21T09:00:00Z

# Explicit destination chat + priority
python -m tools.agent_session_scheduler checkin \
  --prompt "Poll job X" --in 2h --chat-id 179144806 --priority high
```

Flags:

| Flag | Required | Meaning |
|------|----------|---------|
| `--prompt` | yes | Arbitrary prompt run when the check-in fires |
| `--at <ISO>` / `--in <dur>` | exactly one | Absolute ISO-8601 instant, or relative `<N>{s\|m\|h\|d}` (e.g. `30m`, `2h`, `1d`) |
| `--chat-id` | no | Destination chat (default: originating chat from the `CHAT_ID` env) |
| `--priority` | no | `urgent\|high\|normal\|low` (default `normal`) |
| `--project` | no | Project key (default: `PROJECT_KEY` env or `valor`) |

## Output and promise-gate contract

```json
{
  "status": "scheduled",
  "agent_session_id": "...",
  "session_id": "checkin-<hex>",
  "schedule_id": "<32-char hex>",
  "citation": "schedule_id=<hex>",
  "chat_id": "179144806",
  "priority": "normal",
  "scheduled_at": "2026-07-20T12:30:00+00:00"
}
```

Paste the `citation` (`schedule_id=<hex>`) into the message that promises a follow-up. The
`schedule_id` is a plain hex token so it matches the promise gate's
`_SCHEDULED_DELIVERY_PATTERNS` (`schedule_id[=:]?\s*[a-f0-9-]{6,}`), and the gate ALLOWs the
otherwise-blocked forward promise.

## How it fires

`checkin` reuses the durable future-fire substrate that already powers `schedule --after`:

1. It creates an `AgentSession` (`session_type=eng`, `status=pending`, `scheduled_at=T`,
   `chat_id=<originating chat>`, `message_text=<prompt>`).
2. `agent/session_pickup.py` skips the session while `scheduled_at` is in the future, then
   makes it eligible once T passes; the worker pops it and runs the prompt as a normal Eng
   session whose output routes to `chat_id`.
3. A companion `Reflection` row (`output_sink="telegram:<chat_id>"`, `at:<ISO>`,
   `auto_delete_after_run=True`) is registered for dashboard visibility only. It is **not** in
   the YAML registry, so `ReflectionScheduler` never ticks it — execution is driven solely by
   the scheduled `AgentSession`. There is no double-fire.

## Time-based-with-reschedule (the deterministic default)

The primitive is one-shot by design. "Wake at T, check, reschedule if not done" is emergent,
not a code feature: the woken session inspects the work and, if it is still incomplete, calls
`checkin` again for a later T. This keeps the primitive deterministic and avoids new session
states, completion-triggered wakeups, parent-attached children (#1633), and nudge-loop content
inspection (#1058).

## Abuse limits

- **Depth cap** — `MAX_SCHEDULING_DEPTH` (3): a deeply self-scheduled session cannot keep
  scheduling further work. Same guard `schedule` uses.
- **Rate limit** — `MAX_SCHEDULED_PER_HOUR` (30) per project: the shared per-hour counter
  counts check-ins (session-id prefix `checkin-`) alongside parent-attached children.
- **Lead-time cap** — `CHECKIN_MAX_LEAD_SECONDS` (default 7 days, env-overridable,
  provisional/tunable): a check-in cannot be scheduled beyond this horizon.
- Past `--at` times are rejected.

## Related

- [Promise Gate](promise-gate.md) — the detection half; its recovery template now names this
  primitive as the sanctioned deferral path.
- [Reflections](reflections.md) — the `at:` schedule grammar and agent-reflection engine.
