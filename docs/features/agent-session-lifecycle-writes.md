---
tracking: https://github.com/tomcounsell/ai/issues/3270
status: Shipped
---

# AgentSession Lifecycle Writes

## The rule

**A bare `save()` on an `AgentSession` is a lifecycle write.**

`AgentSession.save()` (`models/agent_session.py:1014`) overrides popoto's
`save()` only to stamp `updated_at`; it then delegates to popoto's own
`save()` (`.venv/.../popoto/models/base.py:1136`). When that call is not given
`update_fields`, its `save(update_fields=None)` path
(`.venv/.../popoto/models/base.py:1514`) does:

```python
hset_mapping = encode_popoto_model_obj(self)  # 1
```

— an `HSET` of the **entire encoded instance** — and then runs
`field.on_save()` for every field (`:1567`).

`status` is an `IndexedField` (`models/agent_session.py:167`). So a bare
`save()` from a stale in-memory instance rewrites both the Redis hash *and*
the status index, from whatever `status` value happens to still be sitting on
that instance. It emits no LIFECYCLE log line and no `session_events` entry —
there is no audit trail for this class of write. That silence is what let the
#3270 incident run for days before anyone could see it happening: a routing
helper read a session, mutated one unrelated field, and its bare `save()` was
silently authorized to rewrite the row's lifecycle state.

The consequence generalizes: any code path that reads an `AgentSession` row,
mutates one incidental field, and calls a bare `save()` is doing a full-row
write it almost certainly did not intend.

## The convention: narrow incidental writes

Code that touches an incidental field — not the lifecycle `status` itself —
should call `save(update_fields=[...])`, naming exactly the fields being
written. `update_fields` skips popoto's whole-instance HSET and writes only
the named fields, so a stale snapshot of `status` (or anything else) sitting
on the in-memory instance never reaches Redis.

Two sites on the #3270 incident path were narrowed as the worked examples:

- `agent/output_handler.py::_persist_routing_fields` (~:1478) persists the
  drafter's `context_summary` back onto the session:

  ```python
  session.context_summary = context_summary
  session.save(update_fields=["context_summary", "updated_at"])
  ```

- `agent/output_handler.py::_rtr_emit_event` (~:1518) appends a Read-the-Room
  event to `session.session_events`:

  ```python
  session.session_events = events
  session.save(update_fields=["session_events", "updated_at"])
  ```

Both call sites carry the rule verbatim in their own docstrings, so the
narrowing is not just enforced in one doc — it is legible at the point of use.

The regression test that encodes the rule (not the site count) is
`tests/unit/output_handler/test_output_handler_stale_snapshot.py`,
parametrized over both narrowed writers: a stale in-memory copy of a session
must not clobber a concurrently-written `status` when either writer saves.

## Three load-bearing caveats

### 1. Narrowing reduces the window; it does not close it everywhere

`transition_status` and `finalize_session`
(`models/session_lifecycle.py`) both save **the caller's object, not a fresh
re-read** — this is documented in their own docstrings ("The caller's object
is used for the save (not the re-read), preserving any companion fields set
before this call," `models/session_lifecycle.py:322` for `finalize_session`
and `:759` for `transition_status`), and both call a bare `session.save()`
to commit the transition. A long-held session object still carries whatever
stale snapshot it was constructed with. Narrowing the *incidental* saves
elsewhere reduces how often a stale object reaches a save call at all, but it
does not make a bare lifecycle save itself safe in general — that save is
still a full-row write by design, because a status transition legitimately
needs to persist every field the caller set beforehand.

### 2. The caller-object mirror pattern

When a fresh-reading helper stamps a field via a narrow save, and the caller
holds its own (possibly stale) copy of that same session that will later be
saved in full, the helper must also mirror the new value onto the caller's
object — otherwise the caller's trailing full save resurrects the old value
microseconds later.

`agent/session_health.py::flush_deferred_self_draft_sync` does this for
`response_delivered_at` (~:2879-2886):

```python
_stamp_target = get_authoritative_session(session_id) or source
_stamp_target.response_delivered_at = _stamp_at
_stamp_target.save(update_fields=["response_delivered_at", "updated_at"])
if session is not None and session is not _stamp_target:
    session.response_delivered_at = _stamp_at
```

This follows the pre-existing `extra_context` clear a few lines below it in
the same function (~:2916-2930), which mirrors its own fresh-read-and-clear
onto the caller's `session.extra_context` for the identical reason: both
fields would otherwise be silently reverted by `finalize_session`'s trailing
`session.save()` on its own `session` parameter, which runs immediately after
this flush returns.

### 3. Narrowing is not always correct — judge each site

Some full saves are deliberate and must stay full:

- `scripts/migrate_agent_session_fields.py` (~:112) re-saves every session
  with a bare `session.save()` specifically to rebuild popoto indices after a
  field-name migration. Narrowing this save would defeat its purpose.
- `models/session_lifecycle.py`'s terminal-transition save (`:653`, inside
  `finalize_session`) writes `status` (and everything else the caller set)
  intentionally — a `KEEP (B1/B2/B3, see #2083 ledger)` note at each of the
  three related backfill/save sites explains why the full write is required
  there for popoto's index-swap mechanics.

Narrowing a save changes which fields persist. A site that is accidentally
relying on the side effect of a full write — a companion field set earlier in
the same call stack that only reaches Redis because the save happened to be
unscoped — will silently stop persisting that field if the save is narrowed
without checking. Unit tests will not reliably catch this, because a test
exercising the narrowed function in isolation has no reason to also assert on
the field that quietly stopped saving. Each `AgentSession.save()` call site
needs its own judgement call, not a mechanical narrow-everything sweep.

## Follow-up

This PR narrowed only the two sites on the #3270 incident path. The full
enumeration of remaining live-code `AgentSession` bare-`save()` sites — roughly
35 at the time of writing — is tracked in
[#3272 "AgentSession save() hardening sweep"](https://github.com/tomcounsell/ai/issues/3272),
which fixes them one commit per site on their own review pass so each gets the
judgement call in caveat 3 above rather than a blanket change.

## Related

- [Message Drafter](message-drafter.md) — the drafter module `_persist_routing_fields` and `_rtr_emit_event` support.
- [Session Steering](session-steering.md) — the resume-steer scoping fix that shipped alongside this in #3270.
