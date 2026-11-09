"""Choose and deduplicate the old system's memory for `python -m core memory import`.

One-off (docs/plans/memory-import.md). Reads the JSON lines of one or more
old-memory exports (`scripts/cutover_export_memory.py`) and keeps the
`source` human and agent records whose `superseded_by` is empty, less the
session summaries. Each record's project is the `<name>` of an
`extraction-tg_<name>_...` agent id, else its `project_key`, matched to a
project spec by name for its `repo` and `client`; a record with neither is
dropped and listed. Within a project, records whose content shares at least
0.8 of its words (token Jaccard) collapse into one; `claude -p` then drops,
40 records at a time, those not worth recalling on a future task, and of
the groups sharing 0.4 to 0.8 names one keeper per real duplicate group.
A keeper is an existing record's text, Tom's own when he wrote one.

A record's project is also read from evidence apart from that mapping (docs/
plans/memory-measure.md): the chat its Telegram message came from (the
export's `chat_id`, a group's) among the specs' chats, paused specs (`*.toml.paused`)
included, else the extraction label; where the two disagree the evidence
wins and the report lists the move. `--imported FILE` (repeatable) names
JSON lines of rows already imported on some Mac (`memory_id`, `project`,
`merged_from`): each such record keeps its place, as its group's keeper,
past every filter and the model passes, and gets only its date and its
project corrected; a record an earlier import merged away stays merged.
Each line carries the export's `occurred_at` and `dated_by` when it has
them. Writes OUT/import.jsonl and OUT/report.md, which Valor reviews before
the import. Run from the kernel checkout:
  PYTHONPATH=. .venv/bin/python scripts/memory_import_prep.py EXPORT.jsonl... OUT/ [--imported FILE]...
"""

import json
import random
import re
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.settings import settings
from core.workspace import Spec

SUMMARY = re.compile(r"^In an? \d+-event session")
LABEL = re.compile(r"^extraction-tg_([^_]+)_")
NEAR, CANDIDATE = 0.8, 0.4
BATCH = 40


def words(text: str) -> frozenset[str]:
    return frozenset(re.findall(r"\w+", text.lower()))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def groups(recs: list[dict], low: float, high: float) -> list[list[dict]]:
    """The connected groups of `recs` whose pairs share `low` <= Jaccard <
    `high` of their words, singletons left out."""
    parent = list(range(len(recs)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(recs)):
        for j in range(i + 1, len(recs)):
            if low <= jaccard(recs[i]["words"], recs[j]["words"]) < high:
                parent[root(i)] = root(j)
    found: dict[int, list[dict]] = {}
    for i, r in enumerate(recs):
        found.setdefault(root(i), []).append(r)
    return [g for g in found.values() if len(g) > 1]


def keeper(group: list[dict]) -> dict:
    """A record already imported, else Tom's own when he wrote one, else
    the longest."""
    return min(group, key=lambda r: (not r.get("imported"), r["by"] != "tom", -len(r["text"])))


def ask(prompt: str) -> dict:
    """Claude Code's JSON answer to `prompt`."""
    out = subprocess.run(["claude", "-p", prompt], capture_output=True, text=True, check=True).stdout
    return json.loads(out[out.index("{") : out.rindex("}") + 1])


def worth(batch: list[dict]) -> list[int]:
    """Claude Code's answer for one batch: the 1-based numbers of the
    records not worth recalling on a future task."""
    listing = "\n\n".join(f"[{n}] (by {r['by']})\n{r['text']}" for n, r in enumerate(batch, 1))
    prompt = (
        "These records are an AI engineer's memory of past work and chats. Name the ones NOT worth "
        'recalling on a future task: chatter and acknowledgements ("Continue", "2", "Yeah good '
        'luck with that"), bare references with no fact ("SDLC issue 152"), and notes about the '
        "agent's own sessions or tooling with no lasting fact (\"The response contains no "
        'substantive observations", "Agent session initialized with bypassPermissions", notes on '
        "deferred tools or pending MCP servers). Keep real facts, even short ones or ones about "
        'tools ("Twenty CRM provides native HTTP/SSE MCP server at POST /mcp endpoint"), and '
        "people's instructions, preferences and corrections. Answer with JSON only: "
        '{"drop": [number, ...]}.\n\n' + listing
    )
    return ask(prompt)["drop"]


def judge(group: list[dict]) -> list[list[str]]:
    """Claude Code's answer for one candidate group: the ids of each set of
    records that say the same thing."""
    listing = "\n\n".join(f"[{r['memory_id']}]\n{r['text']}" for r in group)
    prompt = (
        "These memory records may repeat one another. Group the ones that state the same fact "
        "or preference (paraphrases), leaving out any record that says something the others do "
        'not. Answer with JSON only: {"duplicates": [[id, id, ...], ...]}.\n\n' + listing
    )
    return ask(prompt)["duplicates"]


def merge(group: list[dict]) -> dict:
    """The keeper of `group`, every other record merged into it but one
    already imported, which keeps its place."""
    keep = keeper(group)
    gone = [r for r in group if r is not keep and not r.get("imported")]
    keep["merged_from"] += [m for r in gone for m in [r["memory_id"], *r["merged_from"]]]
    keep["merged"] = keep.get("merged", []) + gone
    return keep


def collapse(recs: list[dict]) -> tuple[list[dict], list[dict]]:
    """`recs` with each near-exact group merged: the survivors, and the
    keepers that merged something."""
    merged = [merge(g) for g in groups(recs, NEAR, 2.0)]
    gone = {id(r) for k in merged for r in k["merged"]}
    return [r for r in recs if id(r) not in gone], merged


def sift(recs: list[dict]) -> tuple[list[dict], list[dict]]:
    """`recs` split by Claude Code, `BATCH` at a time, into those worth
    recalling and those not."""
    asked = [r for r in recs if not r.get("imported")]
    batches = [asked[i : i + BATCH] for i in range(0, len(asked), BATCH)]
    with ThreadPoolExecutor(8) as pool:
        answers = list(pool.map(worth, batches))
    gone = {id(b[n - 1]) for b, a in zip(batches, answers, strict=True) for n in a if 0 < n <= len(b)}
    return [r for r in recs if id(r) not in gone], [r for r in recs if id(r) in gone]


def paraphrases(recs: list[dict]) -> tuple[list[dict], list[dict]]:
    """`recs` with the duplicate groups Claude Code names merged, each
    candidate group asked about 20 records at a time."""
    candidates = [
        g[i : i + 20]
        for g in groups(recs, CANDIDATE, NEAR)
        if not all(r.get("imported") for r in g)
        for i in range(0, len(g), 20)
    ]
    with ThreadPoolExecutor(8) as pool:
        answers = list(pool.map(judge, candidates))
    merged = []
    for group, answer in zip(candidates, answers, strict=True):
        by_id = {r["memory_id"]: r for r in group}
        for ids in answer:
            same = [by_id[i] for i in dict.fromkeys(ids) if i in by_id]
            if len(same) > 1:
                merged.append(merge(same))
    gone = {id(r) for k in merged for r in k["merged"]}
    return [r for r in recs if id(r) not in gone], merged


def cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def evidence(r: dict, chats: dict[str, str]) -> str | None:
    """The project the record's own origin names: its Telegram group's spec
    (not a direct chat, whose person may work on several projects), else
    its extraction label."""
    chat = str(r.get("chat_id") or "")
    if chat.startswith("-") and (name := chats.get(f"telegram:{chat}")) is not None:
        return name
    label = LABEL.match(str(r.get("agent_id") or ""))
    return label.group(1) if label else None


def main(argv: list[str]) -> None:
    previous = [argv[i + 1] for i, a in enumerate(argv) if a == "--imported"]
    argv = [a for i, a in enumerate(argv) if a != "--imported" and (i == 0 or argv[i - 1] != "--imported")]
    if len(argv) < 2:
        raise SystemExit(__doc__)
    *exports, out = argv
    seen: dict[str, dict] = {}
    for path in exports:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                seen.setdefault(r["memory_id"], r)
    imported: dict[str, str] = {}
    merged_away: set[str] = set()
    for path in previous:
        for line in Path(path).read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                imported[row["memory_id"]] = row["project"]
                merged_away.update(row.get("merged_from") or ())
    paused = [Spec.load(str(p)) for p in sorted(Path(settings.projects_dir).glob("*.toml.paused"))]
    specs = {s.name: s for s in [*paused, *Spec.all()]}
    chats = {c: s.name for s in specs.values() for c in s.chats}
    by_repo = {s.repo: s.name for s in specs.values()}
    stage: Counter = Counter(read=len(seen))
    dropped, by_project, moved = [], {}, []
    for r in seen.values():
        content = str(r.get("content") or "")
        old = r["memory_id"] in imported
        if r["memory_id"] in merged_away and not old:
            stage["merged by an earlier import"] += 1
        elif not old and r.get("source") not in ("human", "agent"):
            stage["knowledge skipped"] += 1
        elif not old and r.get("superseded_by"):
            stage["superseded skipped"] += 1
        elif not old and SUMMARY.match(content):
            stage["session summaries dropped"] += 1
        else:
            label = LABEL.match(str(r.get("agent_id") or ""))
            name = by_repo.get(
                imported.get(r["memory_id"], ""), label.group(1) if label else r.get("project_key")
            )
            if (found := evidence(r, chats)) is not None and found != name:
                moved.append((r["memory_id"], name, found, content))
                name = found
            if name not in specs:
                dropped.append((r["memory_id"], name, content))
                continue
            by = str(r["agent_id"] or "").lower() if r["source"] == "human" else "valor"
            rec = {
                "memory_id": r["memory_id"],
                "text": content,
                "by": by,
                "merged_from": [],
                "words": words(content),
                "imported": old,
                "dates": {k: r[k] for k in ("occurred_at", "dated_by") if r.get(k)},
            }
            by_project.setdefault(name, []).append(rec)
    stage["no project dropped"] = len(dropped)
    rows, merges, unworthy = [], [], []
    table = [
        "| project | chosen | after near-exact | not worth recalling | after paraphrase | dated | no later than | undated |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, recs in sorted(by_project.items()):
        near, m1 = collapse(recs)
        worthy, gone = sift(near)
        kept, m2 = paraphrases(worthy)
        merges += [m for m in m1 if not any(m is r for r in gone)] + m2
        unworthy += [(name, r) for r in gone]
        bound = sum("no later than" in r["dates"].get("dated_by", "") for r in kept)
        undated = sum(not r["dates"] for r in kept)
        table.append(
            f"| {name} | {len(recs)} | {len(near)} | {len(gone)} | {len(kept)} "
            f"| {len(kept) - bound - undated} | {bound} | {undated} |"
        )
        spec = specs[name]
        rows += [
            {
                "text": r["text"],
                "project": spec.repo,
                "client": spec.client or "",
                "by": r["by"],
                "memory_id": r["memory_id"],
                "merged_from": r["merged_from"],
                **r["dates"],
            }
            for r in kept
        ]
        stage["chosen"] += len(recs)
        stage["after near-exact"] += len(near)
        stage["not worth recalling dropped"] += len(gone)
        stage["after paraphrase"] += len(kept)
    Path(out).mkdir(parents=True, exist_ok=True)
    with open(Path(out) / "import.jsonl", "w") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    report = ["# Old memory, before and after", "", "| stage | records |", "|---|---|"]
    report += [f"| {k} | {v} |" for k, v in stage.items()]
    report += ["", "## Per project", "", *table, "", f"## Dropped, no project ({len(dropped)})", ""]
    report += [f"- `{i}` ({n or 'none'}): {cell(c)}" for i, n, c in dropped]
    report += ["", f"## Moved by the record's own chat or label ({len(moved)})", ""]
    report += [f"- `{i}` {a or 'none'} -> {b}: {cell(c)}" for i, a, b, c in moved]
    report += ["", f"## Dropped, not worth recalling ({len(unworthy)})", ""]
    report += [f"- ({n}, by {r['by']}): {cell(r['text'])}" for n, r in unworthy]
    sample = random.Random(0).sample(merges, min(20, len(merges)))
    report += [
        "",
        f"## Sampled merges ({len(sample)} of {len(merges)})",
        "",
        "| kept | merged |",
        "|---|---|",
    ]
    report += [f"| {cell(k['text'])} | {'<br>'.join(cell(r['text']) for r in k['merged'])} |" for k in sample]
    (Path(out) / "report.md").write_text("\n".join(report) + "\n")
    print(f"{len(rows)} records written to {Path(out) / 'import.jsonl'}; report in {Path(out) / 'report.md'}")


if __name__ == "__main__":
    main(sys.argv[1:])
