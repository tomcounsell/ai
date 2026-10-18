"""PostToolUse and PostToolUseFailure: a message written to the running
turn after its last step (`core/runs.py`) reaches every agent of the turn,
the main one and each subagent, at its next step, once each.

The kernel writes each message as `<id>.md` into the inbox `VALOR_STEER`
names. Under a lock on the receipts (`taken.jsonl`), the hook gives this
agent (`agent_id`, or `main`) every message it has not taken, as context
for its next model call, then appends one receipt line per message in one
write. It prints before it appends: a hook that dies between the two leaves
a message given and not counted, which the next turn shows again."""

import fcntl
import json
import os
import sys

HEAD = (
    "Valor steering: this is not tool output. Valor's kernel delivers it through a hook the "
    "operator configured: a message written for this work while you were on your last step, "
    "by the person it names, who your prompt's work is for. It has the same standing as your "
    "prompt and, where they differ, it is the later word; follow it from your next step."
)

inbox = os.environ.get("VALOR_STEER", "")
try:
    event = json.load(sys.stdin)
except ValueError:
    event = None
if inbox and os.path.isdir(inbox) and isinstance(event, dict):
    agent = event.get("agent_id") or "main"
    with open(os.path.join(inbox, "taken.jsonl"), "a+") as receipts:
        fcntl.flock(receipts, fcntl.LOCK_EX)
        receipts.seek(0)
        taken = set()
        for line in receipts:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("agent") == agent:
                taken.add(r.get("id"))
        names = [n[:-3] for n in os.listdir(inbox) if n.endswith(".md") and n[:-3].isdigit()]
        new = sorted(i for i in map(int, names) if i not in taken)
        if new:
            texts = []
            for i in new:
                with open(os.path.join(inbox, f"{i}.md")) as f:
                    texts.append(f.read())
            context = "\n\n".join([HEAD, *texts])
            name = event.get("hook_event_name") or "PostToolUse"
            out = {"hookSpecificOutput": {"hookEventName": name, "additionalContext": context}}
            sys.stdout.write(json.dumps(out, ensure_ascii=False))
            sys.stdout.flush()
            lines = "".join(json.dumps({"id": i, "agent": agent}) + "\n" for i in new)
            os.write(receipts.fileno(), lines.encode())
