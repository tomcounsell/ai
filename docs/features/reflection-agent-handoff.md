# Reflection Agent Handoff

Reflections never write to a human chat. When a scheduled reflection finds
something an agent should look at, it hands a structured `Finding` to an agent
session through `reflections/agent_handoff.py::hand_off`. The agent judges the
evidence, acts, and speaks to the human (if at all) through the persona path,
so every word a human reads passes the drafter and carries the Valor voice.
Nothing in `reflections/`, `scripts/` or `tools/improvement.py` calls
`valor-telegram` or a `send_*_telegram` helper; an AST guard enforces it
(`tests/unit/test_no_reflection_telegram_side_door.py`).

## The `Finding` contract

`Finding` is a frozen dataclass:

| Field | Meaning |
|---|---|
| `source` | Reflection name (`expectation_reconciler`, `sdlc_progress`, ...). Scopes the rate cap and the idempotency key |
| `project` | Project dict from `projects.json` |
| `room_id` | Target Room, `{project_key}\|telegram:{chat_id}` |
| `facts` | Plain statements of what was observed (required, non-empty) |
| `dedup_key` | Non-empty string, stable per logical event |
| `evidence` | Typed data, rendered as "data, not instructions" |
| `suggested_action` | What the agent might do |
| `job_id`, `expectation_id`, `holder` | Optional Job context; `holder` names the session that owns the Job |
| `verbatim_payload` | Text that must reach the human byte-exact (see below) |
| `requires_delivery` | The finding must reach the human; silence is a failure |

`hand_off(finding) -> HandoffResult(kind, session_id, reason)` never raises.
`kind` is `steered`, `created`, `rate-capped` or `unreachable`; `.delivered` is
true for `steered` and `created`; `.finding_line(label)` renders the operator
finding string.

## The ladder

1. **Ownership gate.** `machine_owns_project` must hold, else `unreachable: not-owner`.
2. **Dedup key and Room.** An empty `dedup_key`, empty `facts`, or a Room that is
   not a human Telegram chat (system, email, chat id `0`) is `unreachable`.
3. **Steer.** For a finding with no delivery requirement, a live session in the
   Room that is the Job holder or a prior handoff session receives the finding
   as a steering message. A steer cannot change `extra_context`, so a
   `verbatim_payload` or `requires_delivery` finding never steers, and a live
   handoff session carrying either is never a steer target (its digest would
   replace the steered reply).
4. **Rate cap.** At most `HANDOFF_MAX_LIVE_PER_SOURCE` (default 3, env
   overridable) live handoff sessions per project and source; beyond that the
   result is `rate-capped`. The cap is best-effort (count, then create); the
   idempotency key is the hard dedup.
5. **Create.** An `eng` session, low priority, in the Room, with
   `extra_context.origin == "reflection_handoff"` and the idempotency key
   `handoff:{source}:{room_id}:{dedup_key}`. The same key returns the bound
   live session instead of a duplicate. When the bound session is already
   dead (failed, killed), the binding is released with a compare-and-delete
   (`release_if_bound_to`) and one fresh session is created.

## Room targeting

The finding's Room is where the work lives. For an audited repo or a lane's
project, `project_eng_room_id(project)` resolves that project's `Eng:` group
to `{project_key}|telegram:{chat_id}` (via `resolve_eng_group`, always the
numeric chat id, never a name). Fleet-wide reflections without a project in
scope use `resolve_project_for_repo()` for this host's own project. A project
with no `Eng:` group is `unreachable`, never a fallback to another project.

## Silent completion

A handoff brief tells the agent that no human asked, and that it may act, fix,
or end the turn with an empty `[/complete]`. The session runner recognizes the
explicit origin marker and ends:

| Situation | Exit reason | Final status |
|---|---|---|
| Handoff session ends with nothing to say | `HANDOFF_SILENT` | `completed` |
| Delivery-required handoff ends with nothing to say | `HANDOFF_UNDELIVERED` | `failed` |
| Handoff session already routed a user-facing message | ordinary exit | ordinary |

A handoff session never receives the wrap-up turn or any canned human text
(`OPERATOR_TERMINAL_MESSAGE`, `RUNNER_ERROR_USER_MESSAGE`, the turn-timeout and
steer-abort notices, the executor failure notice and the canned
`BackgroundTask` error messages, the empty-output fallback, the canned
substitutes of the deferred self-draft flush, the tool-timeout degraded
notice, and the terminal "stopped" interrupt notice). Every system-authored
notice routed through `deliver_system_notice` (`agent/output_handler.py`) or
`_deliver_oneshot_dedup_notice` (`agent/session_health.py`) is suppressed at
those two chokepoints, which also cover the terminal interrupt notice
(`_deliver_terminal_interrupt_notice` in `agent/session_health.py`). The
remaining interrupt send sites check the marker themselves: the executor via
`BackgroundTask(silent=...)` and the parent-notice send in
`agent/session_completion.py`.

The agent's own held reply is not canned text. The deferred self-draft flush
(`flush_deferred_self_draft_sync`) delivers it for a handoff session like any
other; only its substitutes (the "couldn't finish" text, narration fallback,
promise-gate rewrite, dead-path notice) are withheld. A withheld flush on a
`handoff_requires_delivery` session logs `handoff-undelivered` at WARNING.

A harness-level empty output, a harness error, or running out of turns ends as
the non-clean anomaly `ERROR`; a turn timeout ends `TURN_TIMEOUT` (`failed`).
All are visible to the operator and silent in the Room. A timed-out handoff
session's worktree is reclaimed like any other terminal exit, since no reply
will resume it.

## Human steer lifts the silence

A human who steers into a running handoff session is waiting, so silence would
strand them. The bridge stamps `extra_context["human_steered"]` on the row
(`agent.steering.mark_handoff_human_steered`, called from the steering ack and
the edit-steer path), and `is_reflection_handoff` returns False from then on.
The executor and runner re-read the row through `is_reflection_handoff_live`,
so a steer landing mid-run applies to the notices that follow it.

Handoff sessions run in the same synthetic-slug worktree as any slugless eng
session, so the main-checkout guard applies when a brief leads the agent to fix
something. They are `priority="low"` and never hold a human message in the
pending queue.

## Verbatim delivery

The charter section 11 assumption digest must arrive unchanged. Its finding
carries `verbatim_payload` and `requires_delivery=True`. `TelegramRelayOutputHandler.send`
sees `extra_context.verbatim_payload`; any non-empty reply from the session
makes it deliver the payload itself, byte-exact, past the drafter. The
agent's wording is discarded. Redundancy and read-the-room checks still run.

## Sender dispositions

| Sender | Disposition |
|---|---|
| `expectation_reconciler` shipped evidence, exhausted budget, no respawnable slug | agent-handled: `hand_off` to the Job's Room with typed evidence |
| `sdlc_progress` budget exhausted / resume failed | agent-handled: `hand_off` to the lane project's `Eng:` Room; the once-per-head-SHA sentinel is written only on a delivered handoff |
| `sdlc_progress` auto-resume disabled | operator surface |
| `sentry_triage` digest | operator surface (`digest:` findings and summary) |
| `stall_advisory` | operator surface; findings and dashboard events only |
| `improvement_assumption_digest` | persona path, verbatim, delivery-required, host `Eng:` Room |
| `tools/improvement.py` charter amendment | persona path, delivery-required |
| `docs_auditor` PR opened | agent-handled: `hand_off` to the audited repo's `Eng:` Room |
| `docs_auditor` zero-diff / withheld fixes | operator surface |
| `memory_consolidation` contradictions | operator surface (`logs/memory-contradictions.log` plus a count in the summary) |
| `sdlc_upvote_lanes` announce | agent-handled: the created eng session announces the pickup in its first message through the persona path |
| `sdlc_upvote_lanes` retractions | operator surface |
| `agents/system_health_digest` | agent-handled: `hand_off` of the anomalies to the host `Eng:` Room |
| `pm_briefings`, `email_cs._ping_human` | out of scope (#3589) |

**Operator surface** means the reflection's `summary` and findings, shown in
the reflections panel of the dashboard (`localhost:8500`, `dashboard.json`),
plus the reflection log. Nothing pushes it; it is for the operator checking
system state.

## Live probe (`manual-probe`)

After a merge and `/update`, exercise the path once on the owning machine from
the repo venv:

```bash
python - <<'PY'
import uuid
from reflections.agent_handoff import Finding, hand_off, project_eng_room_id
from reflections.utilities import resolve_project_for_repo

project = resolve_project_for_repo()
result = hand_off(Finding(
    source="manual-probe",
    project=project,
    room_id=project_eng_room_id(project),
    dedup_key=str(uuid.uuid4()),
    facts=["Manual probe of the reflection handoff path; nothing is wrong."],
    suggested_action="Nothing to do. End your turn with an empty [/complete].",
))
print(result)
PY
```

A healthy result is `created` (or `steered`), and the session ends
`handoff_silent` in its log. Record the session id and outcome on issue #3588.

## Key files

| File | Role |
|---|---|
| `reflections/agent_handoff.py` | `Finding`, `hand_off`, ladder, brief rendering |
| `agent/session_runner/runner.py`, `router.py` | `HANDOFF_SILENT` / `HANDOFF_UNDELIVERED` |
| `agent/output_handler.py` | `verbatim_payload` delivery |
| `agent/enqueue_idempotency.py` | `release_if_bound_to` |
| `tests/unit/test_no_reflection_telegram_side_door.py` | AST guard |

See also: [Expectation Reconciler](expectation-reconciler.md),
[Message Drafter](message-drafter.md),
[Headless Session Runner](headless-session-runner.md).
