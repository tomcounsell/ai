"""Spike 07: does stopping a sandbox kill its compute and keep its disk?
Tech stack §4: a stop is a generation fence, a token revoke, and a compute
stop with the disk retained. This measures the compute half on apple/container.

Each run: boot a sandbox with a bind mount at /work, start a detached loop
writing a counter once a second and a curl holding an HTTP request open
against a server on the Mac, then stop (or kill) the container. Measured:
time until the stop command returns, time until the Mac-side server sees the
connection drop, and time until the counter file stops growing. Then the
container is removed and the mount checked for the counter file.
"""

import http.server
import json
import os
import socketserver
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

IMAGE = "cori-base:3.14"
NETWORK = "cori-hostonly"
HOST_PORT = 8765
RUNS = int(os.environ.get("SPIKE07_RUNS", "20"))


def sh(*args, check=True, timeout=120):
    t0 = time.perf_counter()
    r = subprocess.run(
        ["container", *args], capture_output=True, text=True, timeout=timeout
    )
    ms = (time.perf_counter() - t0) * 1000
    if check and r.returncode:
        raise RuntimeError(f"container {' '.join(args)}\n{r.stderr.strip()}")
    return r, ms


def gateway_ip():
    r, _ = sh("network", "inspect", NETWORK)
    return json.loads(r.stdout)[0]["status"]["ipv4Gateway"]


class SlowServer:
    """Serves /slow by holding the response open for 120 s while probing the
    socket every 50 ms. Records the moment the client side goes away."""

    def __init__(self, ip):
        self.ip = ip
        self.connected_at = None
        self.dropped_at = None
        self.drop_reason = None
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.connected_at = time.perf_counter()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                deadline = time.perf_counter() + 120
                try:
                    while time.perf_counter() < deadline:
                        # A write on a reset or closed socket raises; a stalled
                        # peer would not, so the drop time is the reset time.
                        self.wfile.write(b".")
                        self.wfile.flush()
                        time.sleep(0.05)
                    outer.drop_reason = "timeout"
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    outer.dropped_at = time.perf_counter()
                    outer.drop_reason = type(e).__name__

            def log_message(self, *a):
                pass

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.srv = Server((ip, HOST_PORT), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def reset(self):
        self.connected_at = self.dropped_at = self.drop_reason = None

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def boot(name, work):
    sh("rm", "-f", name, check=False)
    sh(
        "run",
        "-d",
        "--name",
        name,
        "--network",
        NETWORK,
        "--mount",
        f"type=bind,source={work},target=/work",
        IMAGE,
    )
    t0 = time.perf_counter()
    while True:
        r, _ = sh("exec", name, "true", check=False)
        if r.returncode == 0:
            return
        if time.perf_counter() - t0 > 30:
            raise RuntimeError("container never answered exec")


def start_workload(name, gw):
    # Detached loop, its own process group, survives the exec session ending.
    sh(
        "exec",
        name,
        "sh",
        "-c",
        "nohup setsid sh -c 'i=0; while true; do i=$((i+1)); "
        "echo $i >> /work/counter.txt; sleep 1; done' "
        ">/dev/null 2>&1 &",
    )
    sh(
        "exec",
        name,
        "sh",
        "-c",
        f"nohup setsid curl -s --max-time 120 http://{gw}:{HOST_PORT}/slow "
        ">/work/curl.out 2>&1 &",
    )


def counter_lines(work):
    p = work / "counter.txt"
    return len(p.read_text().splitlines()) if p.exists() else 0


def counter_mtime(work):
    p = work / "counter.txt"
    return p.stat().st_mtime if p.exists() else 0.0


def one_run(series, i, gw, server):
    name = f"s07-{series}-{i}"
    work = Path(tempfile.mkdtemp(prefix=f"spike07-{series}-{i}-"))
    boot(name, work)
    server.reset()
    start_workload(name, gw)
    # Let the workload establish: the request must be open and the counter
    # must be ticking before the stop is issued.
    t0 = time.perf_counter()
    while server.connected_at is None or counter_lines(work) < 2:
        if time.perf_counter() - t0 > 15:
            raise RuntimeError("workload did not establish")
        time.sleep(0.1)
    time.sleep(1.0)
    lines_before = counter_lines(work)

    # The stop. Wall clock is the Mac's; mtime on the bind mount is written by
    # the VM through virtiofs, so the counter check is polled from the host.
    t_stop = time.perf_counter()
    t_stop_wall = time.time()
    cmd_error = None
    try:
        if series == "stop":
            sh("stop", "-t", "1", name)
        else:
            sh("kill", name)
    except RuntimeError as e:
        # Seen once in 40: an XPC "Connection interrupted" from the runtime
        # while the container still went down. Recorded, not hidden.
        cmd_error = str(e).splitlines()[-2][:120] if "\n" in str(e) else str(e)[:120]
    cmd_returned = time.perf_counter() - t_stop

    # Watch the counter file for 3 s after the stop command returned.
    last_growth_wall = counter_mtime(work)
    end = time.perf_counter() + 3.0
    while time.perf_counter() < end:
        m = counter_mtime(work)
        if m > last_growth_wall:
            last_growth_wall = m
        time.sleep(0.05)
    counter_stopped_after = max(0.0, last_growth_wall - t_stop_wall)
    lines_after = counter_lines(work)

    # The server side of the open request.
    deadline = time.perf_counter() + 5
    while server.dropped_at is None and time.perf_counter() < deadline:
        time.sleep(0.02)
    request_dropped_after = (
        server.dropped_at - t_stop if server.dropped_at is not None else None
    )

    _, rm_ms = sh("rm", "-f", name)
    retained = (work / "counter.txt").exists() and lines_after >= lines_before
    return {
        "cmd_error": cmd_error,
        "cmd_returned_ms": round(cmd_returned * 1000),
        "request_dropped_ms": (
            round(request_dropped_after * 1000)
            if request_dropped_after is not None
            else None
        ),
        "drop_reason": server.drop_reason,
        "counter_last_write_after_stop_ms": round(counter_stopped_after * 1000),
        "lines_before": lines_before,
        "lines_after": lines_after,
        "retained": retained,
        "rm_ms": round(rm_ms),
    }


def sigterm_probe(gw, server):
    """What SIGTERM to the container's PID 1 does on its own, without the
    runtime tearing the VM down: does the loop keep writing, does the request
    stay open, does the container stay running."""
    name = "s07-sigterm"
    work = Path(tempfile.mkdtemp(prefix="spike07-sigterm-"))
    boot(name, work)
    server.reset()
    start_workload(name, gw)
    t0 = time.perf_counter()
    while server.connected_at is None or counter_lines(work) < 2:
        if time.perf_counter() - t0 > 15:
            raise RuntimeError("workload did not establish")
        time.sleep(0.1)
    procs = (
        "for p in /proc/[0-9]*; do echo $(basename $p) $(cat $p/comm 2>/dev/null); done"
    )
    r0, _ = sh("exec", name, "sh", "-c", procs)
    t_kill = time.perf_counter()
    r, _ = sh("exec", name, "sh", "-c", "kill -TERM 1; echo sent", check=False)
    before = counter_lines(work)
    time.sleep(3.0)
    after = counter_lines(work)
    r1, _ = sh("exec", name, "sh", "-c", procs, check=False)
    dropped = server.dropped_at
    ls, _ = sh("ls", "--format", "json")
    state = next(
        (
            c.get("status")
            for c in json.loads(ls.stdout)
            if c.get("configuration", {}).get("id") == name
        ),
        "gone",
    )
    sh("rm", "-f", name, check=False)
    return {
        "kill_term_1_output": (r.stdout + r.stderr).strip()[:80],
        "counter_lines_before": before,
        "counter_lines_3s_later": after,
        "request_dropped_ms_after_kill": (
            round((dropped - t_kill) * 1000) if dropped is not None else None
        ),
        "processes_before": sorted(r0.stdout.split("\n")[1:]) if r0.stdout else [],
        "processes_3s_later": sorted(r1.stdout.split("\n")[1:]) if r1.stdout else [],
        "container_state_3s_later": (
            state.get("state") if isinstance(state, dict) else state
        ),
    }


def summarize(runs, key):
    vals = [r[key] for r in runs if r[key] is not None]
    if not vals:
        return {"n": 0}
    vals.sort()
    return {
        "n": len(vals),
        "median": round(statistics.median(vals)),
        "p95": vals[min(len(vals) - 1, int(len(vals) * 0.95))],
        "max": vals[-1],
    }


def main():
    gw = gateway_ip()
    # The host bridge exists only while a container is attached, so boot a
    # holder first, then bind the server.
    holder = Path(tempfile.mkdtemp(prefix="spike07-holder-"))
    boot("s07-holder", holder)
    server = SlowServer(gw)
    out = {}
    try:
        out["sigterm_to_pid1"] = sigterm_probe(gw, server)
        print("sigterm", out["sigterm_to_pid1"], file=sys.stderr)
        for series in ("stop", "kill"):
            runs = []
            for i in range(RUNS):
                runs.append(one_run(series, i, gw, server))
                print(series, i, runs[-1], file=sys.stderr)
            out[series] = {
                "runs": RUNS,
                "cmd_returned_ms": summarize(runs, "cmd_returned_ms"),
                "request_dropped_ms": summarize(runs, "request_dropped_ms"),
                "request_drop_reasons": sorted({str(r["drop_reason"]) for r in runs}),
                "counter_last_write_after_stop_ms": summarize(
                    runs, "counter_last_write_after_stop_ms"
                ),
                "counter_grew_after_stop": sum(
                    1 for r in runs if r["lines_after"] > r["lines_before"] + 1
                ),
                "retained_disk": sum(1 for r in runs if r["retained"]),
                "cmd_errors": [r["cmd_error"] for r in runs if r["cmd_error"]],
                "rm_ms": summarize(runs, "rm_ms"),
            }
    finally:
        server.close()
        sh("rm", "-f", "s07-holder", check=False)
    print(json.dumps(out, indent=2))
    ok = all(out[k]["retained_disk"] == RUNS for k in ("stop", "kill"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
