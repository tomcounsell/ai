"""A product room's Sheet (SKILL.md beside this file), through `gws sheets`.

    python3 sheet.py new "<Product> product" --share tom@yuda.me [--share ...]
    python3 sheet.py read SHEET_ID
    python3 sheet.py order SHEET_ID
    python3 sheet.py due SHEET_ID [--today YYYY-MM-DD]

`new` copies the template and shares the copy; `read` prints the Queue as
JSON; `order` sorts the Queue by the ranking rule and numbers the ready
rows, printing each row that moved; `due` lists the blockers to chase and
the shipped items to review today, and every open blocker with the days it
has waited. It prints JSON and changes nothing it
does not name."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime

# "Product sheet template" in valor@yuda.me's Drive.
TEMPLATE = "1aL39ddaSXTGan43epPbDemccTrLynwyhCoYQ5rYOzkE"

QUEUE = ["id", "lane", "rank", "item", "goal", "value", "appetite", "due", "status",
         "blocked by", "issue", "dev thread", "review on"]  # fmt: skip
BLOCKED = ["item", "blocker", "kind", "owner", "asked on", "chase on", "chased", "status"]
# In flight first, then the ranked ready rows, then the rest by how far shaped.
STAGES = ["approved", "building", "ready", "shaped", "shaping", "raw", "shipped", "reviewed"]
BANDS = {"value": ["high", "medium", "low"], "appetite": ["small", "medium", "large"]}


def gws(*args: str, params: dict, body: dict | None = None) -> dict:
    cmd = ["gws", *args, "--params", json.dumps(params)]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    got = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if got.returncode != 0:
        raise SystemExit(f"gws {' '.join(args)} failed: {got.stderr.strip() or got.stdout.strip()}")
    return json.loads(got.stdout or "{}")


def values(sheet: str, tab: str) -> list[list[str]]:
    """The tab's rows under its header, blank rows included."""
    got = gws(
        "sheets", "spreadsheets", "values", "get", params={"spreadsheetId": sheet, "range": f"{tab}!A2:Z"}
    )
    return got.get("values", [])


def rows(found: list[list[str]], columns: list[str]) -> list[dict[str, str]]:
    """Each non-blank row as a dict of `columns`; `_rest` holds any cells
    past them (columns people added), which move with the row."""
    return [
        {**dict(zip(columns, [*r, *[""] * len(columns)])), "_rest": r[len(columns) :]}
        for r in found
        if any(r)
    ]


def _band(row: dict[str, str], name: str) -> int:
    found = row.get(name, "").strip().lower()
    return BANDS[name].index(found) if found in BANDS[name] else len(BANDS[name])


def _key(row: dict[str, str]) -> tuple:
    status = row.get("status", "").strip().lower()
    stage = STAGES.index(status) if status in STAGES else len(STAGES)
    mandatory = row.get("lane", "").strip().lower() == "mandatory"
    # Mandatory work by its date (none last); bets by value, then the smaller appetite.
    due = row.get("due", "").strip() or "9999-12-31"
    return (stage, not mandatory, due if mandatory else "", _band(row, "value"), _band(row, "appetite"))


def ordered(queue: list[dict[str, str]]) -> list[dict[str, str]]:
    """The Queue in ranking order (ties keep their place), each `ready` row
    numbered from 1 and every other row's rank blank."""
    out, n = [], 0
    for row in sorted(queue, key=_key):
        ready = row.get("status", "").strip().lower() == "ready"
        n += ready
        out.append({**row, "rank": str(n) if ready else ""})
    return out


def due(blocked: list[dict[str, str]], queue: list[dict[str, str]], today: date) -> dict[str, list]:
    """The open blockers whose chase date has come, the shipped items
    whose review date has come, and every open blocker with the days since
    it was asked (`waited`, None without a date) and whether its chase has
    come (`chase due`)."""

    def day(text: str) -> date | None:
        try:
            return date.fromisoformat(text.strip())
        except ValueError:
            return None

    def come(text: str) -> bool:
        return (day(text) or date.max) <= today

    open_ = [b for b in blocked if b.get("status", "").strip().lower() != "cleared"]
    return {
        "chase": [b for b in open_ if come(b.get("chase on", ""))],
        "review": [
            r
            for r in queue
            if r.get("status", "").strip().lower() == "shipped" and come(r.get("review on", ""))
        ],
        "open": [
            {
                **b,
                "waited": (today - asked).days if (asked := day(b.get("asked on", ""))) else None,
                "chase due": come(b.get("chase on", "")),
            }
            for b in open_
        ],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new")
    new.add_argument("title")
    new.add_argument("--share", action="append", default=[])
    for name in ("read", "order", "due"):
        sub.add_parser(name).add_argument("sheet")
    sub.choices["due"].add_argument(
        "--today", type=date.fromisoformat, default=datetime.now().astimezone().date()
    )
    args = p.parse_args(argv)

    if args.command == "new":
        made = gws("drive", "files", "copy", params={"fileId": TEMPLATE}, body={"name": args.title})
        for email in args.share:
            gws("drive", "permissions", "create", params={"fileId": made["id"], "sendNotificationEmail": False},
                body={"role": "writer", "type": "user", "emailAddress": email})  # fmt: skip
        out = {
            "id": made["id"],
            "url": f"https://docs.google.com/spreadsheets/d/{made['id']}",
            "shared": args.share,
        }
    elif args.command == "read":
        out = rows(values(args.sheet, "Queue"), QUEUE)
    elif args.command == "order":
        found = values(args.sheet, "Queue")
        before = rows(found, QUEUE)
        after = ordered(before)
        # Every row read is written back, blank ones moved to the end, so no old row survives below.
        # Each row is padded to the full A:Z width read, so a moved row clears what stood there before.
        written = [[*[r[c] for c in QUEUE], *r.get("_rest", [])] for r in after]
        written = [[*w, *[""] * (26 - len(w))] for w in written] + [[""] * 26] * (len(found) - len(after))
        gws("sheets", "spreadsheets", "values", "update",
            params={"spreadsheetId": args.sheet, "range": f"Queue!A2:Z{len(found) + 1}", "valueInputOption": "RAW"},
            body={"values": written})  # fmt: skip
        place = {r["id"]: i for i, r in enumerate(before)}
        out = [{"id": r["id"], "item": r["item"], "rank": r["rank"], "from": place[r["id"]] + 1, "to": i + 1}
               for i, r in enumerate(after) if place[r["id"]] != i]  # fmt: skip
    else:
        out = due(
            rows(values(args.sheet, "Blocked"), BLOCKED), rows(values(args.sheet, "Queue"), QUEUE), args.today
        )
    json.dump(out, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
