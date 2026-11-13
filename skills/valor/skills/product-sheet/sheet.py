"""A product room's Sheet (SKILL.md beside this file), through `gws sheets`.

    python3 sheet.py new "<Product>" --share tom@yuda.me [--share ...]
    python3 sheet.py doc SHEET_ID [--share EMAIL ...]
    python3 sheet.py seed SHEET_ID OWNER/REPO
    python3 sheet.py read SHEET_ID
    python3 sheet.py order SHEET_ID
    python3 sheet.py due SHEET_ID [--today YYYY-MM-DD]
    python3 sheet.py lookup SHEET_ID "<how someone described it, or P-004>"

`new` makes the Drive folder "<Product> product", copies the template into
it as "<Product> product backlog", and does `doc`; `doc` renders `doc.md`
into the companion Google Doc in the Sheet's folder (making the folder, and
moving the Sheet into it, when the Sheet sits loose in My Drive), writes
the Sheet's Columns tab from the Doc's table in place of the old Guide tab,
links the Doc on the Goal tab, and shares the folder; `seed` appends each
open GitHub issue no row links yet as a raw row; `read` prints the Queue as
JSON; `order` sorts the Queue by the ranking rule and numbers the ready
rows, printing each row that moved; `due` lists the blockers to chase and
the shipped items to review today, and every open blocker with the days it
has waited; `lookup` finds the items someone means, from an id or their
own loose description, each with its Pitches and Blocked rows. It prints
JSON and changes nothing it does not name."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

# "Product sheet template" in valor@yuda.me's Drive.
TEMPLATE = "1aL39ddaSXTGan43epPbDemccTrLynwyhCoYQ5rYOzkE"

QUEUE = ["id", "lane", "rank", "item", "goal", "value", "appetite", "due", "status",
         "blocked by", "issue", "dev thread", "review on", "source"]  # fmt: skip
PITCHES = ["id", "item", "job", "problem", "evidence", "solution", "rabbit holes", "no-gos", "acceptance",
           "builder questions", "hook", "readiness"]  # fmt: skip
BLOCKED = ["item", "blocker", "kind", "owner", "asked on", "chase on", "chased", "status"]
# In flight first, then the ranked ready rows, then the rest by how far shaped.
STAGES = ["approved", "building", "ready", "shaped", "shaping", "raw", "shipped", "reviewed"]
BANDS = {"value": ["high", "medium", "low"], "appetite": ["small", "medium", "large"]}


DOC = Path(__file__).with_name("doc.md")
# Marks the companion Doc in its folder, so `doc` finds and replaces it.
DOC_MARK = "appProperties has { key='valor' and value='product-doc' }"
DOC_TYPE = "application/vnd.google-apps.document"
FOLDER_TYPE = "application/vnd.google-apps.folder"
ISSUE_URL = re.compile(r"https://github\.com/[^\s,;]+/issues/\d+")


def gws(*args: str, params: dict, body: dict | None = None, upload: str | None = None) -> dict:
    """`upload` is markdown text, sent as the file's content; gws uploads
    only from its working directory, so it runs in a temporary one."""
    cmd = ["gws", *args, "--params", json.dumps(params)]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    if upload is None:
        got = subprocess.run(cmd, capture_output=True, text=True, check=False)
    else:
        with tempfile.TemporaryDirectory() as here:
            Path(here, "doc.md").write_text(upload)
            cmd += ["--upload", "doc.md", "--upload-content-type", "text/markdown"]
            got = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=here)
    if got.returncode != 0:
        raise SystemExit(f"gws {' '.join(args)} failed: {got.stderr.strip() or got.stdout.strip()}")
    return json.loads(got.stdout or "{}")


def render(product: str, sheet_url: str, folder_url: str) -> str:
    """The companion Doc's markdown for one Sheet."""
    text = DOC.read_text()
    for key, value in {"product": product, "sheet": sheet_url, "folder": folder_url}.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def columns() -> list[list[str]]:
    """The Columns tab: the Doc's one four-cell table, header first."""
    cells = [
        [c.strip() for c in line.strip().strip("|").split("|")]
        for line in DOC.read_text().splitlines()
        if line.startswith("|") and not line.startswith("|---")
    ]
    return [c for c in cells if len(c) == 4]


def product_of(name: str) -> str:
    """ "Acme product backlog" or "Acme product" -> "Acme"."""
    return name.removesuffix(" backlog").removesuffix(" product")


def doc(sheet: str, share: list[str]) -> dict[str, str]:
    """Bring one Sheet's folder, companion Doc, Columns tab and Goal link
    to the template; idempotent."""
    meta = gws("drive", "files", "get", params={"fileId": sheet, "fields": "name,parents"})
    product = product_of(meta["name"])
    folder = meta["parents"][0]
    if folder == gws("drive", "files", "get", params={"fileId": "root", "fields": "id"})["id"]:
        folder = gws("drive", "files", "create", params={"fields": "id"},
                     body={"name": f"{product} product", "mimeType": FOLDER_TYPE})["id"]  # fmt: skip
        gws("drive", "files", "update", params={"fileId": sheet, "addParents": folder, "removeParents": meta["parents"][0]}, body={})  # fmt: skip
    for email in share:
        gws("drive", "permissions", "create", params={"fileId": folder, "sendNotificationEmail": False},
            body={"role": "writer", "type": "user", "emailAddress": email})  # fmt: skip
    sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet}"
    folder_url = f"https://drive.google.com/drive/folders/{folder}"
    found = gws("drive", "files", "list", params={
        "q": f"'{folder}' in parents and mimeType='{DOC_TYPE}' and {DOC_MARK} and trashed=false", "fields": "files(id)"})  # fmt: skip
    text = render(product, sheet_url, folder_url)
    if found.get("files"):
        doc_id = found["files"][0]["id"]
        gws("drive", "files", "update", params={"fileId": doc_id}, body={}, upload=text)
    else:
        doc_id = gws("drive", "files", "create", params={"fields": "id"}, upload=text, body={
            "name": f"{product} product backlog: how to read and work it", "mimeType": DOC_TYPE, "parents": [folder],
            "appProperties": {"valor": "product-doc"}, "description": f"How to read and work the Sheet {sheet_url}"})["id"]  # fmt: skip
    doc_url = f"https://docs.google.com/document/d/{doc_id}"

    tabs = {
        t["properties"]["title"]: t["properties"]["sheetId"]
        for t in gws("sheets", "spreadsheets", "get", params={"spreadsheetId": sheet, "fields": "sheets.properties"})["sheets"]
    }  # fmt: skip
    changes = [] if "Columns" in tabs else [{"addSheet": {"properties": {"title": "Columns", "index": 0, "gridProperties": {"frozenRowCount": 1}}}}]  # fmt: skip
    changes += [{"deleteSheet": {"sheetId": tabs["Guide"]}}] if "Guide" in tabs else []
    if changes:
        gws(
            "sheets",
            "spreadsheets",
            "batchUpdate",
            params={"spreadsheetId": sheet},
            body={"requests": changes},
        )
    gws(
        "sheets",
        "spreadsheets",
        "values",
        "clear",
        params={"spreadsheetId": sheet, "range": "Columns!A:Z"},
        body={},
    )
    gws("sheets", "spreadsheets", "values", "update",
        params={"spreadsheetId": sheet, "range": "Columns!A1", "valueInputOption": "RAW"}, body={"values": columns()})  # fmt: skip
    fields = [r[0] if r else "" for r in gws("sheets", "spreadsheets", "values", "get",
              params={"spreadsheetId": sheet, "range": "Goal!A:A"}).get("values", [])]  # fmt: skip
    at = fields.index("Companion Doc") + 1 if "Companion Doc" in fields else len(fields) + 1
    gws("sheets", "spreadsheets", "values", "update",
        params={"spreadsheetId": sheet, "range": f"Goal!A{at}:B{at}", "valueInputOption": "RAW"},
        body={"values": [["Companion Doc", doc_url]]})  # fmt: skip
    gws("drive", "files", "update", params={"fileId": sheet}, body={"description": f"How to read and work it: {doc_url}"})  # fmt: skip
    return {"sheet": sheet_url, "doc": doc_url, "folder": folder_url, "shared": share}


def seeded(queue: list[list[str]], issues: list[dict]) -> list[list[str]]:
    """A raw Queue row for each open issue no row links, oldest first,
    numbered after the highest id; an issue labelled bug is mandatory."""
    linked = {u for r in queue for cell in r for u in ISSUE_URL.findall(cell)}
    ids = [int(m.group(1)) for r in queue if r and (m := re.fullmatch(r"P-(\d+)", r[0].strip()))]
    n = max(ids, default=0)
    out = []
    for issue in sorted(issues, key=lambda i: i["number"]):
        if issue["url"] in linked:
            continue
        n += 1
        bug = any(label["name"].lower() == "bug" for label in issue.get("labels", []))
        cells = {"id": f"P-{n:03d}", "lane": "mandatory" if bug else "bet", "item": issue["title"],
                 "status": "raw", "issue": issue["url"], "source": "GitHub"}  # fmt: skip
        out.append([cells.get(c, "") for c in QUEUE])
    return out


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


# The pitch text a loose description is matched against, besides the title.
DESCRIBED = ["job", "problem", "evidence", "solution"]
MATCHES = 5
ID = re.compile(r"p-?(\d+)", re.IGNORECASE)
# Words that say nothing about which item is meant (a single letter neither: "what's" is "what", "s").
FILLER = {"a", "an", "and", "are", "as", "at", "be", "bug", "by", "can", "do", "does", "for", "from", "had",
          "has", "have", "i", "in", "is", "it", "item", "its", "me", "my", "of", "on", "one", "or", "our",
          "said", "says", "should", "so", "that", "the", "their", "them", "there", "these", "thing", "this",
          "those", "to", "us", "was", "we", "were", "what", "when", "where", "which", "who", "why", "will",
          "with", "you", "your",
          # and the values asked about, which say nothing about which item
          "appetite", "size", "value", "due", "date", "status", "pitch", "blocker", "blocked", "priority"}  # fmt: skip


def _word(text: str) -> str:
    """A word with its common ending off, so "restarted" meets "restart"."""
    for end, put in (("ies", "y"), ("ing", ""), ("ed", ""), ("s", "")):
        if len(text) > len(end) + 3 and text.endswith(end):
            return text[: -len(end)] + put
    return text


def _words(text: str) -> set[str]:
    return {_word(w) for w in re.findall(r"\w+", text.lower()) if len(w) > 1 and w not in FILLER}


def lookup(query: str, queue: list[dict], pitches: list[dict], blocked: list[dict]) -> list[dict]:
    """The Queue items `query` means, each with its Pitches row (or None)
    and its Blocked rows. An id ("P-4", "p-004") finds that item. Otherwise
    the query is how someone described it: each of its words scores 2 when in
    the item's title, else 1 when in its pitch's job, problem, evidence or
    solution; the best `MATCHES` with any score, highest first (ties in Sheet
    order, which is the ranking), each with its `score`."""

    def number(text: str) -> int | None:
        m = ID.fullmatch(text.strip())
        return int(m.group(1)) if m else None

    pitch = {number(p["id"]): p for p in pitches if number(p["id"]) is not None}

    def full(r: dict) -> dict:
        n = number(r["id"])
        return {**{k: v for k, v in r.items() if k != "_rest"},
                "pitch": {k: v for k, v in pitch[n].items() if k != "_rest"} if n in pitch else None,
                "blocked": [{k: v for k, v in b.items() if k != "_rest"} for b in blocked if number(b["item"]) == n]}  # fmt: skip

    if (n := number(query)) is not None:
        return [full(r) for r in queue if number(r["id"]) == n]
    asked = _words(query)
    scored = []
    for r in queue:
        title = _words(r["item"])
        told = _words(" ".join((pitch.get(number(r["id"])) or {}).get(c, "") for c in DESCRIBED))
        score = sum(2 if w in title else 1 if w in told else 0 for w in asked)
        if score:
            scored.append((score, r))
    scored.sort(key=lambda s: -s[0])  # stable: ties keep the Sheet's order
    return [{**full(r), "score": score} for score, r in scored[:MATCHES]]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new")
    new.add_argument("product")
    new.add_argument("--share", action="append", default=[])
    sub.add_parser("doc").add_argument("sheet")
    sub.choices["doc"].add_argument("--share", action="append", default=[])
    seed = sub.add_parser("seed")
    seed.add_argument("sheet")
    seed.add_argument("repo")
    for name in ("read", "order", "due", "lookup"):
        sub.add_parser(name).add_argument("sheet")
    sub.choices["lookup"].add_argument("query")
    sub.choices["due"].add_argument(
        "--today", type=date.fromisoformat, default=datetime.now().astimezone().date()
    )
    args = p.parse_args(argv)

    if args.command == "new":
        folder = gws("drive", "files", "create", params={"fields": "id"},
                     body={"name": f"{args.product} product", "mimeType": FOLDER_TYPE})["id"]  # fmt: skip
        made = gws("drive", "files", "copy", params={"fileId": TEMPLATE},
                   body={"name": f"{args.product} product backlog", "parents": [folder]})  # fmt: skip
        out = {"id": made["id"], **doc(made["id"], args.share)}
    elif args.command == "doc":
        out = doc(args.sheet, args.share)
    elif args.command == "seed":
        got = subprocess.run(["gh", "issue", "list", "--repo", args.repo, "--state", "open", "--limit", "1000",
                              "--json", "number,title,url,labels"], capture_output=True, text=True, check=True)  # fmt: skip
        new_rows = seeded(values(args.sheet, "Queue"), json.loads(got.stdout))
        if new_rows:
            gws("sheets", "spreadsheets", "values", "append",
                params={"spreadsheetId": args.sheet, "range": "Queue!A1", "valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                body={"values": new_rows})  # fmt: skip
        out = [dict(zip(QUEUE, r)) for r in new_rows]
    elif args.command == "lookup":
        # One read of the three tabs: 0.8 s warm, 2.1 s cold on a real Sheet (2026-11-13), so no cache.
        tabs = {"Queue": QUEUE, "Pitches": PITCHES, "Blocked": BLOCKED}
        got = gws("sheets", "spreadsheets", "values", "batchGet",
                  params={"spreadsheetId": args.sheet, "ranges": [f"{t}!A2:Z" for t in tabs]})  # fmt: skip
        found = [rows(v.get("values", []), c) for v, c in zip(got["valueRanges"], tabs.values())]
        out = {"matches": lookup(args.query, *found)}
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
