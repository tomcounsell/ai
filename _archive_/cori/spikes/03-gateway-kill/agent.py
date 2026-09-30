"""A small raw-SDK agent loop with three fake tools, pointed at the gateway.
It holds only a Brief token, never the provider key."""

from __future__ import annotations

import json
import time

import anthropic

MODEL = "claude-haiku-4-5-20251001"

FILES = {
    "notes/a.txt": "Alpha: the budget for Q3 is 1200.",
    "notes/b.txt": "Beta: the deadline moved to Friday.",
    "notes/c.txt": "Gamma: verifier seat is the previous generation.",
}

TOOLS = [
    {
        "name": "list_files",
        "description": "List the files in the notes directory.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "read_file",
        "description": "Read one file by path.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "write_note",
        "description": "Write the final summary note.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
    },
]

SYSTEM = (
    "You are a careful assistant. Use the tools one at a time: first list_files, "
    "then read_file on every file listed, one call per turn, then write_note with a "
    "one-sentence summary of all three, then say done. Keep text replies under 15 words."
)


def run_tool(name: str, inp: dict) -> str:
    if name == "list_files":
        return json.dumps(sorted(FILES))
    if name == "read_file":
        return FILES.get(inp.get("path", ""), "no such file")
    if name == "write_note":
        return "written"
    return "unknown tool"


async def run_agent(base_url: str, token: str, max_calls: int = 12) -> dict:
    """Returns the agent's own trace: tool calls, usage per call, and how it ended."""
    client = anthropic.AsyncAnthropic(base_url=base_url, api_key=token, max_retries=0)
    messages: list[dict] = [{"role": "user", "content": "Summarise the notes."}]
    trace = {"tool_calls": [], "usage": [], "ended": None, "error_at": None, "calls": 0}
    for _ in range(max_calls):
        trace["calls"] += 1
        try:
            async with client.messages.stream(
                model=MODEL,
                max_tokens=300,
                system=SYSTEM,
                tools=TOOLS,
                messages=messages,
            ) as s:
                msg = await s.get_final_message()
        except Exception as e:  # noqa: BLE001 - we want the raw failure mode
            trace["ended"] = f"error: {type(e).__name__}: {str(e)[:120]}"
            trace["error_at"] = time.perf_counter()
            return trace
        trace["usage"].append(msg.usage.model_dump())
        messages.append(
            {"role": "assistant", "content": [b.model_dump() for b in msg.content]}
        )
        tool_uses = [b for b in msg.content if b.type == "tool_use"]
        if msg.stop_reason != "tool_use" or not tool_uses:
            trace["ended"] = f"stop_reason={msg.stop_reason}"
            return trace
        results = []
        for tu in tool_uses:
            out = run_tool(tu.name, tu.input)
            trace["tool_calls"].append((tu.name, tu.input, out))
            results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": out}
            )
        messages.append({"role": "user", "content": results})
    trace["ended"] = "max_calls"
    return trace
