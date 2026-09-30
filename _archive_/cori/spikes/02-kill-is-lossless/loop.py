"""A stateless control loop.

Each turn: lock the objective (single-flight), fold every event for it into a
state, ask a deterministic scripted "agent" what to do next, append the
resulting events, commit. Nothing about the objective is kept in process memory
between turns; the fold runs from seq 0 every time.

Effects on the external world are two events around one world write:

    effect.intent {key}   -> world write (separate autocommit connection)
                          -> effect.outcome {key, status}

A kill can land between any two of those. What the loop does with a dangling
intent on restart is the ``--policy``:

    naive         re-run the effect. At-least-once; duplicates possible.
    at_most_once  never re-run; record outcome=unknown. Effects can be lost.
    reconcile     look the key up in the world first; re-run only if absent.

Usage: python loop.py --objective ID --policy P [--step-delay S]
Prints one JSON line per phase to stdout for the chaos harness to time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import psycopg
from psycopg.types.json import Jsonb

DSN = os.environ.get("CORI_SPIKE_DSN", "postgresql://127.0.0.1:5499/cori_spike_kill")

# The scripted objective. Deterministic given the state. Effects carry an
# idempotency key derived from objective id and step so a re-run is detectable.
SCRIPT = [
    ("plan", None),
    ("effect", "write_a"),
    ("think", None),
    ("effect", "write_b"),
    ("delegate", "child-1"),
    ("await_report", "child-1"),
    ("effect", "write_c"),
    ("think", None),
    ("effect", "post_summary"),
    ("effect", "notify"),
    ("done", None),
]

T0 = time.perf_counter()


def log(phase: str, **kw):
    print(json.dumps({"t": round(time.perf_counter() - T0, 4), "phase": phase, **kw}))
    sys.stdout.flush()


def fold(events: list[tuple[str, dict]]) -> dict:
    """Pure projection of the event stream into a state."""
    st = {"step": 0, "dangling": None, "effects": {}, "reports": {}, "done": False}
    for typ, p in events:
        if typ == "step.completed":
            st["step"] = p["step"] + 1
        elif typ == "effect.intent":
            st["dangling"] = p["key"]
        elif typ == "effect.outcome":
            st["dangling"] = None
            st["effects"][p["key"]] = p["status"]
        elif typ == "report.landed":
            st["reports"][p["child"]] = p["summary"]
        elif typ == "objective.done":
            st["done"] = True
    return st


def render(conn, objective: str) -> dict:
    cur = conn.execute(
        "SELECT type, payload FROM es.events WHERE objective_id=%s ORDER BY seq",
        (objective,),
    )
    return fold(cur.fetchall())


def append(conn, objective: str, typ: str, payload: dict | None = None):
    conn.execute(
        "INSERT INTO es.events (objective_id, type, payload) VALUES (%s, %s, %s)",
        (objective, typ, Jsonb(payload or {})),
    )
    log("event", type=typ, **(payload or {}))


def world_write(world_conn, objective: str, key: str, payload: str):
    world_conn.execute(
        "INSERT INTO es.world (objective_id, key, payload) VALUES (%s, %s, %s)",
        (objective, key, payload),
    )


def world_has(world_conn, objective: str, key: str) -> bool:
    cur = world_conn.execute(
        "SELECT 1 FROM es.world WHERE objective_id=%s AND key=%s LIMIT 1",
        (objective, key),
    )
    return cur.fetchone() is not None


def run_effect(conn, world_conn, objective, key, delay):
    """intent -> commit -> world write -> outcome -> commit. Three kill windows."""
    with conn.transaction():
        append(conn, objective, "effect.intent", {"key": key})
    time.sleep(delay)  # window 1: intent recorded, nothing done
    world_write(world_conn, objective, key, f"payload for {key}")
    time.sleep(delay)  # window 2: done in the world, outcome not recorded
    with conn.transaction():
        append(conn, objective, "effect.outcome", {"key": key, "status": "ok"})


def resolve_dangling(conn, world_conn, objective, key, policy, delay):
    if policy == "naive":
        log("dangling", key=key, action="rerun")
        time.sleep(delay)
        world_write(world_conn, objective, key, f"payload for {key}")
        time.sleep(delay)
        with conn.transaction():
            append(conn, objective, "effect.outcome", {"key": key, "status": "ok"})
    elif policy == "at_most_once":
        log("dangling", key=key, action="give_up")
        with conn.transaction():
            append(conn, objective, "effect.outcome", {"key": key, "status": "unknown"})
    elif policy == "reconcile":
        if world_has(world_conn, objective, key):
            log("dangling", key=key, action="recovered")
            with conn.transaction():
                append(
                    conn,
                    objective,
                    "effect.outcome",
                    {"key": key, "status": "recovered"},
                )
        else:
            log("dangling", key=key, action="rerun")
            time.sleep(delay)
            world_write(world_conn, objective, key, f"payload for {key}")
            time.sleep(delay)
            with conn.transaction():
                append(conn, objective, "effect.outcome", {"key": key, "status": "ok"})
    else:
        raise ValueError(policy)


def turn(conn, world_conn, objective, policy, delay) -> bool:
    """One turn. Returns True when the objective is done."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (objective,))
        st = render(conn, objective)
        log("rendered", step=st["step"], dangling=st["dangling"])
    if st["done"]:
        return True
    if st["dangling"]:
        resolve_dangling(conn, world_conn, objective, st["dangling"], policy, delay)
        return False
    kind, arg = SCRIPT[st["step"]]
    step = st["step"]
    if kind in ("plan", "think"):
        time.sleep(delay)
        with conn.transaction():
            append(conn, objective, f"{kind}.made", {"step": step})
            append(conn, objective, "step.completed", {"step": step})
    elif kind == "effect":
        key = f"{objective}:{arg}"
        # The projection, not the step counter, says whether the effect is
        # done. A resolved dangling intent leaves an outcome here and the step
        # must not run the effect again.
        if key not in st["effects"]:
            run_effect(conn, world_conn, objective, key, delay)
        with conn.transaction():
            append(conn, objective, "step.completed", {"step": step})
    elif kind == "delegate":
        with conn.transaction():
            append(conn, objective, "brief.issued", {"child": arg, "step": step})
            append(conn, objective, "step.completed", {"step": step})
    elif kind == "await_report":
        if arg not in st["reports"]:
            # The child "runs" and its report lands as an event of its own.
            time.sleep(delay)
            with conn.transaction():
                append(
                    conn,
                    objective,
                    "report.landed",
                    {"child": arg, "summary": f"{arg} finished"},
                )
            return False
        with conn.transaction():
            append(conn, objective, "step.completed", {"step": step})
    elif kind == "done":
        with conn.transaction():
            append(conn, objective, "objective.done", {"step": step})
            append(conn, objective, "step.completed", {"step": step})
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--objective", required=True)
    ap.add_argument("--policy", default="reconcile")
    ap.add_argument("--step-delay", type=float, default=0.02)
    a = ap.parse_args()
    log("start", pid=os.getpid())
    with psycopg.connect(DSN) as conn, psycopg.connect(DSN, autocommit=True) as world:
        log("connected")
        while not turn(conn, world, a.objective, a.policy, a.step_delay):
            pass
    log("done")


if __name__ == "__main__":
    main()
