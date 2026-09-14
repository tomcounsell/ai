"""The control journal: the single authority for what an improvement case is doing.

``ImprovementCase.state`` and ``.revision`` (``models/improvement_case.py``)
are a *projection* — a queryable mirror of a decision, never the decision
itself. This package owns the decision: a per-case head and a bounded journal
in Redis, advanced only through one Lua script per effect (``journal.py``),
fenced by a case-scoped lease (``lease.py``) and, for research sessions, by a
durable dispatch intent's own state (``intents.py``). The projection is
written only by ``projection.apply`` after the journal has already accepted a
transition.

**Namespace.** Every key lives under ``improve:{project_key}:...`` (see
``keys.py`` for the exact layout, Decision 2 of
``docs/plans/improvement-controller-lane-3-control-journal-fenced-dispatch.md``).
``keys.assert_control_key`` refuses any key outside that prefix, so a typo
cannot point a script at an unrelated namespace (in particular, never at
``lease:session:*``, which belongs to #3220).

**Private alias, not a raw Popoto client.** Every module in this package binds
its own Redis handle by calling ``utils.redis_client.text_redis()`` through a
private alias (``journal._control_redis()``). No module here imports
``POPOTO_REDIS_DB`` or anything from ``popoto.redis_db`` directly, and none of
these keys are Popoto-managed rows — they are plain Redis structures (hashes,
lists, sets, strings) that Popoto's ORM never touches. That split matters
operationally: ``.claude/hooks/validators/validate_no_raw_redis_delete.py`` is
a **text heuristic** over Bash commands, not an AST check, so a builder's own
manual verification (a stray ``python -c "...improve:...delete..."``) can trip
it. Keep every compare-and-delete exercised from pytest files, never from an
inline shell one-liner, and the guard never fires on this package's own
namespace by construction (it doesn't touch a Popoto key at all).

**Reason codes.** Every effect script returns one of a closed vocabulary
instead of raising: ``SCHEMA_MISMATCH``, ``PAUSED``, ``STALE_GENERATION``,
``REVISION_MISMATCH``, ``SLOT_EXHAUSTED``, ``BUDGET_EXHAUSTED``,
``INTENT_STATE``, ``UNAVAILABLE`` come from the Lua scripts themselves;
``INVALID_ARGUMENT`` and ``NOT_A_RESEARCH_SESSION`` are returned by the
Python layer before any Redis call. A connection error at the package
boundary becomes ``UNAVAILABLE`` — no caller of this package ever sees a raw
``redis.exceptions`` type, and no caller falls back to reading the
projection instead.

**The #3220 hand-off.** ``lease.py`` defines ``LeaseProtocol``, matching the
three calls #3220's production session-execution lease
(``models/redis_lease.py``, not yet built) declares. Until it lands, this
package uses ``CaseLease``, an interim implementation confined to the
``improve:`` prefix. ``tests/unit/test_improvement_control_lease.py::
test_interim_lease_retired_when_redis_lease_exists`` fails the whole suite
the moment ``models/redis_lease.py`` exists — that is the signal for #3220's
builder to delete ``CaseLease``, repoint ``default_lease()`` at the real
module, and delete that retirement test along with this lane's now-vacuous
"no second general lease" Verification row.
"""

from __future__ import annotations
