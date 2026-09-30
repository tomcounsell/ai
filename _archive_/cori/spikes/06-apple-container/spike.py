"""Spike 06: apple/container as the one sandbox adapter. Tech stack §6.

Proves the six port operations (create, exec, read, write, snapshot,
destroy) against the CLI, runs the three profiles, and checks that network
restriction is enforced from outside the VM. Prints the numbers.

Needs: `container system start`, image cori-base:3.14 (infra/sandbox), and
two networks: `container network create --internal cori-hostonly` and
`container network create cori-egress`.
"""

import http.server
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

IMAGE = "cori-base:3.14"
HOSTONLY = "cori-hostonly"
EGRESS = "cori-egress"
HOST_PORT = 8765


def sh(*args, check=True, timeout=120):
    t0 = time.perf_counter()
    r = subprocess.run(
        ["container", *args], capture_output=True, text=True, timeout=timeout
    )
    ms = (time.perf_counter() - t0) * 1000
    if check and r.returncode:
        raise RuntimeError(f"container {' '.join(args)}\n{r.stderr.strip()}")
    return r, ms


def gateway_ip(network):
    r, _ = sh("network", "inspect", network)
    return json.loads(r.stdout)[0]["status"]["ipv4Gateway"]


def create(name, network, *extra):
    """Boot: `run -d` returns when the VM is up; then poll exec until it answers."""
    sh("rm", "-f", name, check=False)
    _, boot_ms = sh("run", "-d", "--name", name, "--network", network, *extra, IMAGE)
    t0 = time.perf_counter()
    while True:
        r, _ = sh("exec", name, "true", check=False)
        if r.returncode == 0:
            break
        if time.perf_counter() - t0 > 30:
            raise RuntimeError("container never answered exec")
    return boot_ms, (time.perf_counter() - t0) * 1000


def exec_(name, *cmd):
    r, ms = sh("exec", name, *cmd, check=False)
    return r.returncode, r.stdout.strip(), ms


def curl(name, url):
    code, out, _ = exec_(
        name, "curl", "-s", "-m", "5", "-o", "/dev/null", "-w", "%{http_code}", url
    )
    return f"http {out}" if code == 0 else f"blocked (curl exit {code})"


def serve_on_host(ip):
    """A tiny HTTP server on the vmnet gateway address: the 'one host'."""

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer((ip, HOST_PORT), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    out = {}
    tmp = Path(tempfile.mkdtemp(prefix="spike06-"))
    space = tmp / "space"
    space.mkdir()
    (space / "artifact.txt").write_text("the space's data\n")

    # 1. create + exec round trip on the host-only network
    boot_ms, ready_ms = create("s06-exec", HOSTONLY)
    rtts = [exec_("s06-exec", "true")[2] for _ in range(20)]
    out["boot_ms"] = round(boot_ms)
    out["first_exec_after_boot_ms"] = round(ready_ms)
    out["exec_rtt_ms_median"] = round(statistics.median(rtts))
    out["exec_rtt_ms_p95"] = round(sorted(rtts)[int(len(rtts) * 0.95) - 1])
    _, tools, _ = exec_(
        "s06-exec", "sh", "-c", "python3 --version; uv --version; git --version"
    )
    out["tools"] = tools.splitlines()

    # 2. write and read through the boundary (container cp), not through exec
    (tmp / "in.txt").write_text("written from the host\n")
    _, write_ms = sh("cp", str(tmp / "in.txt"), "s06-exec:/work/in.txt")
    exec_("s06-exec", "sh", "-c", "tr a-z A-Z < /work/in.txt > /work/out.txt")
    _, read_ms = sh("cp", "s06-exec:/work/out.txt", str(tmp / "out.txt"))
    out["write_ms"] = round(write_ms)
    out["read_ms"] = round(read_ms)
    out["read_back"] = (tmp / "out.txt").read_text().strip()

    # 3. snapshot: the container filesystem as a tar the kernel can keep
    _, snap_ms = sh("export", "-o", str(tmp / "snap.tar"), "s06-exec")
    out["snapshot_ms"] = round(snap_ms)
    out["snapshot_mb"] = round((tmp / "snap.tar").stat().st_size / 1e6, 1)

    # 4. destroy
    # `sleep infinity` ignores SIGTERM, so the default 5 s grace is paid in
    # full; the adapter sends SIGKILL and waits one second at most.
    _, stop_ms = sh("stop", "-t", "1", "s06-exec")
    _, rm_ms = sh("rm", "s06-exec")
    out["destroy_ms"] = round(stop_ms + rm_ms)
    r, _ = sh("ls", "-a", "--format", "json")
    out["destroyed_gone"] = "s06-exec" not in r.stdout

    # 5. network restriction, enforced by the vmnet configuration outside the VM
    # The host's bridge interface exists only while a container is attached,
    # so boot first and bind the host server after.
    gw = gateway_ip(HOSTONLY)
    create("s06-net-hostonly", HOSTONLY)
    create("s06-net-egress", EGRESS)
    srv = serve_on_host(gw)
    try:
        # Right after a destroy on the same network the bridge is rebuilt and
        # the first probe has been seen to time out; count the attempts.
        for attempt in range(1, 4):
            host = curl("s06-net-hostonly", f"http://{gw}:{HOST_PORT}/")
            if host.startswith("http"):
                break
            time.sleep(2)
        out["network"] = {
            "hostonly_to_the_one_host": f"{host} (attempt {attempt})",
            "hostonly_to_pypi": curl("s06-net-hostonly", "https://pypi.org/simple/"),
            "hostonly_to_public_ip": curl("s06-net-hostonly", "http://1.1.1.1/"),
            "egress_to_pypi": curl("s06-net-egress", "https://pypi.org/simple/"),
        }
    finally:
        srv.shutdown()
        sh("rm", "-f", "s06-net-hostonly", "s06-net-egress", check=False)

    # 6. the three profiles
    ro = f"type=bind,source={space},target=/space,readonly"
    rw = f"type=bind,source={space},target=/work"
    # scratch: read-only slice of the space, no network beyond the host
    create("s06-scratch", HOSTONLY, "--mount", ro)
    code, msg, _ = exec_("s06-scratch", "sh", "-c", "echo x > /space/new.txt 2>&1")
    out["scratch_write_to_space_refused"] = code != 0
    _, seen, _ = exec_("s06-scratch", "cat", "/space/artifact.txt")
    out["scratch_reads_space"] = seen == "the space's data"
    exec_("s06-scratch", "sh", "-c", "echo prototype > /tmp/proto.txt")
    sh("rm", "-f", "s06-scratch")

    # worktree: writable, keyed by node id, host sees the write
    create("s06-worktree-node1", HOSTONLY, "--mount", rw)
    exec_("s06-worktree-node1", "sh", "-c", "echo 'from the executor' > /work/edit.txt")
    out["worktree_write_visible_on_host"] = (
        space / "edit.txt"
    ).read_text().strip() == ("from the executor")
    sh("rm", "-f", "s06-worktree-node1")

    # verify: a fresh VM, never the executor's; read-only artifact, no network
    create("s06-verify", HOSTONLY, "--mount", ro)
    code, _, _ = exec_("s06-verify", "test", "-e", "/tmp/proto.txt")
    out["verify_is_fresh_vm"] = code != 0
    _, seen, _ = exec_("s06-verify", "cat", "/space/edit.txt")
    out["verify_reads_artifact"] = seen == "from the executor"
    code, _, _ = exec_("s06-verify", "sh", "-c", "echo x > /space/verdict.txt")
    out["verify_write_refused"] = code != 0
    sh("rm", "-f", "s06-verify")

    print(json.dumps(out, indent=2))
    return 0 if all(v for k, v in out.items() if isinstance(v, bool)) else 1


if __name__ == "__main__":
    sys.exit(main())
