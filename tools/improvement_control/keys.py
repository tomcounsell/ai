"""Key layout for the control journal namespace (Decision 2).

Every builder function returns a key under ``improve:{project_key}:...``.
:func:`assert_control_key` is the one guard every accessor in this package
calls before touching Redis, so a typo cannot address a key outside this
namespace (never ``lease:session:*``, #3220's key, in particular).

Schema (see Decision 2 of the plan for the authoritative field-by-field
description; this is a compact index, not a restatement)::

    improve:{project}:_ns:schema                 string  schema version "1"
    improve:{project}:_ns:pause                  hash    namespace-wide pause
    improve:{project}:_ns:slots                  hash    unit-1 reservations
    improve:{project}:budget:unit2:{day_key}     hash    unit-2 day window
    improve:{project}:budget:unit2:res:{res_id}  hash    one unit-2 reservation
    improve:{project}:{case}:head                hash    the case's revision head
    improve:{project}:{case}:journal             list    bounded JSON journal
    improve:{project}:{case}:intents             set     per-case intent index
    improve:{project}:{case}:intent:{action_id}  hash    one dispatch intent
    improve:{project}:{case}:lease               hash    the case lease
    improve:{project}:{case}:lease:gen           string  monotonic generation

Money is stored in integer cents throughout the unit-2 keys.
"""

from __future__ import annotations

#: Schema version written once per namespace; every script checks it.
SCHEMA_VERSION = 1

#: Prefix every control key must start with. Enforced by :func:`assert_control_key`.
_PREFIX = "improve:"


def assert_control_key(key: str) -> str:
    """Raise if ``key`` is not one of this namespace's own keys.

    Returns the key unchanged so it can be used inline:
    ``client.get(assert_control_key(schema_key(pk)))``.
    """
    if not isinstance(key, str) or not key.startswith(_PREFIX):
        raise ValueError(f"control key must start with {_PREFIX!r}, got {key!r}")
    return key


def schema_key(project_key: str) -> str:
    return assert_control_key(f"improve:{project_key}:_ns:schema")


def pause_key(project_key: str) -> str:
    return assert_control_key(f"improve:{project_key}:_ns:pause")


def slots_key(project_key: str) -> str:
    return assert_control_key(f"improve:{project_key}:_ns:slots")


def unit2_window_key(project_key: str, day_key: str) -> str:
    return assert_control_key(f"improve:{project_key}:budget:unit2:{day_key}")


def unit2_reservation_key(project_key: str, reservation_id: str) -> str:
    return assert_control_key(f"improve:{project_key}:budget:unit2:res:{reservation_id}")


def head_key(project_key: str, case_id: str) -> str:
    return assert_control_key(f"improve:{project_key}:{case_id}:head")


def journal_key(project_key: str, case_id: str) -> str:
    return assert_control_key(f"improve:{project_key}:{case_id}:journal")


def intents_set_key(project_key: str, case_id: str) -> str:
    return assert_control_key(f"improve:{project_key}:{case_id}:intents")


def intent_key(project_key: str, case_id: str, action_id: str) -> str:
    return assert_control_key(f"improve:{project_key}:{case_id}:intent:{action_id}")


def lease_key(project_key: str, case_id: str) -> str:
    return assert_control_key(f"improve:{project_key}:{case_id}:lease")


def lease_gen_key(project_key: str, case_id: str) -> str:
    return assert_control_key(f"improve:{project_key}:{case_id}:lease:gen")
