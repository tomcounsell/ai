"""Spike 08: tool log and ask, against a real container and spike 03's gateway.

Scenario A: a task that cannot finish without asking. The supervisor watches
the trace, sees the question, checks the gateway log stays silent while the
worker waits, answers, and the run finishes with a Report. Then the tool
log's hashes are checked against the artifacts on the retained disk.

Scenario B: the same task, aborted during the wait. The run ends with one
terminal event and nothing after it in either log.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE), str(HERE.parent / "03-gateway-kill")]
os.environ["CORI_AUDIT_LOG"] = str(HERE / "gateway.jsonl")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from gateway import AUDIT_PATH, Kernel, build_app  # noqa: E402

from loop import Report, ToolLog, Worker, build_agent, sha  # noqa: E402
from sandbox import Sandbox  # noqa: E402

PORT = 8799
GATEWAY = f"http://127.0.0.1:{PORT}"
MODEL = "claude-haiku-4-5-20251001"
TASK = (
    "Create /work/greeting.txt containing exactly one line: 'Hello, <name>!' "
    "where <name> is the person's first name. You do not know the name. "
    "Then run `wc -c /work/greeting.txt` with bash and put its output in the "
    "report summary. List the file in artifacts."
)


def keychain(name: str) -> str:
    return subprocess.run(
        ["security", "find-generic-password", "-s", "cori", "-a", name, "-w"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def gateway_requests() -> list[dict]:
    if not os.path.exists(AUDIT_PATH):
        return []
    with open(AUDIT_PATH) as f:
        recs = [json.loads(line) for line in f]
    return [r for r in recs if r["event"] == "request"]


async def wait_for(pred, timeout=120.0, every=0.05):
    t0 = time.perf_counter()
    while not pred():
        if time.perf_counter() - t0 > timeout:
            raise TimeoutError
        await asyncio.sleep(every)


async def issue(http: httpx.AsyncClient, brief_id: str) -> str:
    r = await http.post(
        f"{GATEWAY}/kernel/issue", json={"brief_id": brief_id, "token_budget": 60_000}
    )
    return r.json()["token"]


def tool_log_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


async def scenario_ask(http: httpx.AsyncClient, out: dict) -> None:
    brief = "brief-ask"
    token = await issue(http, brief)
    mount = Path(tempfile.mkdtemp(prefix="s08-ask-"))
    log_path = HERE / "tool-log-ask.jsonl"
    log_path.unlink(missing_ok=True)
    sb = Sandbox.create(mount)
    try:
        worker = Worker(brief, sb, ToolLog(log_path, brief))
        agent = build_agent(GATEWAY, token, MODEL)
        run = asyncio.create_task(worker.run(agent, TASK))

        # supervisor side: wait for the question
        await wait_for(lambda: any(e["kind"] == "question" for e in worker.events))
        q = next(e for e in worker.events if e["kind"] == "question")
        t_question = time.time()
        calls_at_question = len(gateway_requests())
        await asyncio.sleep(3.0)  # the worker is blocked; the gateway must stay silent
        calls_during_wait = len(gateway_requests()) - calls_at_question
        t_answer = time.time()
        await worker.answer(q["question_id"], "Tom")

        report = await asyncio.wait_for(run, 120)
        assert isinstance(report, Report), report
        reqs = gateway_requests()
        between = [r for r in reqs if t_question <= r["ts"] <= t_answer]

        # tool log hashes versus the artifacts on the retained disk
        recs = tool_log_records(log_path)
        ends = [r for r in recs if r["event"] == "tool.end" and r.get("artifact")]
        checks = []
        for r in ends:
            host_path = mount / Path(r["artifact"]).relative_to("/work")
            checks.append(
                (r["artifact"], r["artifact_sha256"] == sha(host_path.read_bytes()))
            )
        starts = [r for r in recs if r["event"] == "tool.start"]
        paired = all(
            any(e["event"] == "tool.end" and e["seq"] == s["seq"] for e in recs)
            for s in starts
        )
        out["ask"] = {
            "question": q["text"],
            "gateway_calls_before_question": calls_at_question,
            "gateway_calls_during_3s_wait": calls_during_wait,
            "gateway_calls_between_question_and_answer": len(between),
            "gateway_calls_total": len(reqs),
            "tool_invocations": len(starts),
            "every_start_has_end": paired,
            "tools_in_order": [s["tool"] for s in starts],
            "artifact_hashes_match_retained_disk": checks,
            "greeting": (mount / "greeting.txt").read_text().strip(),
            "report": report.model_dump(),
            "terminal": worker.events[-1]["kind"] == "terminal"
            and worker.events[-1]["outcome"] == "report",
        }
    finally:
        sb.destroy()


async def scenario_abort(http: httpx.AsyncClient, out: dict) -> None:
    brief = "brief-abort"
    token = await issue(http, brief)
    mount = Path(tempfile.mkdtemp(prefix="s08-abort-"))
    log_path = HERE / "tool-log-abort.jsonl"
    log_path.unlink(missing_ok=True)
    sb = Sandbox.create(mount)
    try:
        worker = Worker(brief, sb, ToolLog(log_path, brief))
        agent = build_agent(GATEWAY, token, MODEL)
        run = asyncio.create_task(worker.run(agent, TASK))
        await wait_for(lambda: any(e["kind"] == "question" for e in worker.events))
        calls_at_question = len([r for r in gateway_requests() if r["brief"] == brief])
        t_abort = time.time()
        await worker.abort()
        result = await asyncio.wait_for(run, 30)
        await asyncio.sleep(2.0)  # anything that leaks would show up here
        recs = tool_log_records(log_path)
        after = [r for r in recs if r["t"] > t_abort]
        reqs_after = [
            r for r in gateway_requests() if r["brief"] == brief and r["ts"] > t_abort
        ]
        out["abort"] = {
            "result": result,
            "events_after_question": [e["kind"] for e in worker.events[-2:]],
            "last_event": worker.events[-1],
            "tool_log_records_after_abort": [r["event"] for r in after],
            "gateway_calls_before_abort": calls_at_question,
            "gateway_calls_after_abort": len(reqs_after),
            "sandbox_files": sorted(p.name for p in mount.iterdir()),
        }
    finally:
        sb.destroy()


async def main() -> int:
    if os.path.exists(AUDIT_PATH):
        os.remove(AUDIT_PATH)
    kernel = Kernel(keychain("anthropic_api_key"))
    server = uvicorn.Server(
        uvicorn.Config(
            build_app(kernel), host="127.0.0.1", port=PORT, log_level="warning"
        )
    )
    server_task = asyncio.create_task(server.serve())
    out: dict = {}
    async with httpx.AsyncClient(timeout=60) as http:
        await wait_for(lambda: server.started, 10)
        await scenario_ask(http, out)
        await scenario_abort(http, out)
    server.should_exit = True
    await server_task
    print(json.dumps(out, indent=2, default=str))
    a, b = out["ask"], out["abort"]
    ok = (
        a["gateway_calls_between_question_and_answer"] == 0
        and a["gateway_calls_during_3s_wait"] == 0
        and a["every_start_has_end"]
        and all(m for _, m in a["artifact_hashes_match_retained_disk"])
        and a["terminal"]
        and b["result"] is None
        and b["last_event"]["kind"] == "terminal"
        and b["last_event"]["outcome"] == "aborted"
        and b["tool_log_records_after_abort"] == ["terminal"]
        and b["gateway_calls_after_abort"] == 0
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
