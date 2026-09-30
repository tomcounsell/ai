"""Chaos harness for spike 02.

For each policy: run the loop once unkilled to get the reference final state,
then N trials that SIGKILL the loop at a uniformly random point in its
lifetime, restart it, and compare the final projected state and the world.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import statistics
import subprocess
import sys
import time
import uuid

import psycopg

DSN = os.environ.get("CORI_SPIKE_DSN", "postgresql://127.0.0.1:5499/cori_spike_kill")
HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, HERE)
from loop import fold  # noqa: E402


def spawn(objective, policy, delay):
    return subprocess.Popen(
        [
            sys.executable,
            os.path.join(HERE, "loop.py"),
            "--objective",
            objective,
            "--policy",
            policy,
            "--step-delay",
            str(delay),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "CORI_SPIKE_DSN": DSN},
    )


def parse_lines(out: str):
    return [json.loads(l) for l in out.splitlines() if l.startswith("{")]


def final_state(conn, objective):
    cur = conn.execute(
        "SELECT type, payload FROM es.events WHERE objective_id=%s ORDER BY seq",
        (objective,),
    )
    events = cur.fetchall()
    st = fold(events)
    cur = conn.execute(
        "SELECT key, count(*) FROM es.world WHERE objective_id=%s GROUP BY key",
        (objective,),
    )
    world = {k.split(":", 1)[1]: int(n) for k, n in cur.fetchall()}
    # Normalise keys so runs with different objective ids compare equal.
    st["effects"] = {
        k.split(":", 1)[1]: ("ok" if v == "recovered" else v)
        for k, v in st["effects"].items()
    }
    return st, world, len(events)


def run_unkilled(conn, policy, delay):
    obj = f"ref-{uuid.uuid4().hex[:8]}"
    t0 = time.perf_counter()
    p = spawn(obj, policy, delay)
    out, err = p.communicate(timeout=60)
    assert p.returncode == 0, err
    wall = time.perf_counter() - t0
    st, world, n = final_state(conn, obj)
    return st, world, n, wall


def trial(conn, policy, delay, ref_wall, rng, kills=1):
    obj = f"k-{uuid.uuid4().hex[:8]}"
    killed = False
    for _ in range(kills):
        kill_at = rng.uniform(0.0, ref_wall)
        p = spawn(obj, policy, delay)
        time.sleep(kill_at)
        if p.poll() is None:
            os.kill(p.pid, signal.SIGKILL)
            killed = True
        p.communicate()
        if not killed:
            break
    # Restart and let it finish.
    t_restart = time.perf_counter()
    p2 = spawn(obj, policy, delay)
    out, err = p2.communicate(timeout=60)
    wall2 = time.perf_counter() - t_restart
    assert p2.returncode == 0, err
    lines = parse_lines(out)
    first_event = next((l["t"] for l in lines if l["phase"] == "event"), None)
    rendered = next((l for l in lines if l["phase"] == "rendered"), None)
    dangling = next((l for l in lines if l["phase"] == "dangling"), None)
    st, world, n = final_state(conn, obj)
    return {
        "killed": killed,
        "kill_at": kill_at,
        "resumed_from_step": rendered["step"] if rendered else None,
        "dangling": dangling["action"] if dangling else None,
        "time_to_first_event": first_event,
        "restart_wall": wall2,
        "state": st,
        "world": world,
        "n_events": n,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--step-delay", type=float, default=0.02)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--kills", type=int, default=1, help="SIGKILLs per trial")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    summary = {}
    with psycopg.connect(DSN, autocommit=True) as conn:
        for policy in ("reconcile", "naive", "at_most_once"):
            ref_st, ref_world, ref_n, ref_wall = run_unkilled(
                conn, policy, a.step_delay
            )
            print(
                f"\n== policy={policy}  reference: {ref_n} events, "
                f"world={ref_world}, wall={ref_wall:.2f}s"
            )
            rows = []
            for i in range(a.trials):
                rows.append(
                    trial(conn, policy, a.step_delay, ref_wall, rng, kills=a.kills)
                )
            killed = [r for r in rows if r["killed"]]
            conv_state = sum(1 for r in killed if r["state"] == ref_st)
            conv_world = sum(1 for r in killed if r["world"] == ref_world)
            dup = sum(1 for r in killed if any(n > 1 for n in r["world"].values()))
            lost = sum(1 for r in killed if any(k not in r["world"] for k in ref_world))
            unknown = sum(
                1 for r in killed if "unknown" in r["state"]["effects"].values()
            )
            dangling = [r["dangling"] for r in killed if r["dangling"]]
            ttfe = [
                r["time_to_first_event"] for r in killed if r["time_to_first_event"]
            ]
            print(
                f"trials={a.trials} kills_per_trial={a.kills} killed_in_flight={len(killed)} "
                f"(the rest finished before the kill landed)"
            )
            print(
                f"  event-log state converged: {conv_state}/{len(killed)}   "
                f"world converged: {conv_world}/{len(killed)}"
            )
            print(
                f"  kills that landed between intent and outcome: {len(dangling)} "
                f"-> actions {dict((x, dangling.count(x)) for x in set(dangling))}"
            )
            print(
                f"  runs with a duplicated world effect: {dup}   "
                f"runs with a lost world effect: {lost}   "
                f"runs with outcome=unknown in state: {unknown}"
            )
            if ttfe:
                print(
                    f"  time from restart to first new event: "
                    f"median={statistics.median(ttfe)*1000:.0f}ms "
                    f"max={max(ttfe)*1000:.0f}ms (includes interpreter start + connect + render)"
                )
            steps = [r["resumed_from_step"] for r in killed]
            print(f"  resumed-from-step histogram: {sorted(steps)}")
            summary[policy] = {
                "killed": len(killed),
                "state_converged": conv_state,
                "world_converged": conv_world,
                "dangling": len(dangling),
                "dup": dup,
                "lost": lost,
                "unknown": unknown,
                "ttfe_median_ms": statistics.median(ttfe) * 1000 if ttfe else None,
            }
    print("\nSUMMARY", json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
