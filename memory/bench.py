"""Scoring recall on dated questions (docs/plans/memory-measure.md).

A question names the day it is asked (`asked_on`), the ledger, project and
client of the task that asks it, its words (`query`), the records that
answer it (`gold`, `<ledger>/<key>` refs), and for client confinement the
records that must never reach it (`forbidden`). Its `type` is one of
long-term, episodic, seasonal-calendar, seasonal-resurfacing, confinement.

`load` saves the records of one or more stores into a scratch store, each
record's `ledger_id` renumbered by its time (an imported record's
`occurred_at`, else its row's time), so recall's own ledger-id bound
(`recall.candidates`) admits only the records from before a question. An
imported record with no date ranks before every question, as it precedes
every later task in live recall; `score` scores each question with and
without those records, since one written after an event would otherwise
answer a question about before it. Keys are kept, so gold refs name the
same records as in the live stores. `fixture` writes the same records and
questions for popoto's own benchmark harness (`--dataset dated`), which
compares BM25 alone with hybrid retrieval.
"""

import bisect
from datetime import UTC, datetime
from typing import Any

from memory.recall import LIMIT, brief, candidates, ranked
from memory.records import Confined, Record, Standard, ref, use

TYPES = ("long-term", "episodic", "seasonal-calendar", "seasonal-resurfacing", "confinement")


def read(dsn: str) -> dict[str, list]:
    """Every record, standard and confinement in the store at `dsn`, as
    plain data."""
    use(dsn)
    return {
        "records": [
            {
                "ledger": r.ledger,
                "key": r.key,
                "client": r.client or "",
                "project": r.project,
                "ledger_id": int(r.ledger_id),
                "origin": r.origin,
                "text": r.text or "",
                "meta": dict(r.meta or {}),
            }
            for r in Record.query.all()
        ],
        "standards": sorted({s.record for s in Standard.query.all()}),
        "confined": sorted({c.record for c in Confined.query.all()}),
    }


def when(record: dict[str, Any]) -> float | None:
    """The record's time in epoch seconds; None for an undated import."""
    meta = record["meta"]
    if meta.get("occurred_at"):
        return datetime.fromisoformat(meta["occurred_at"]).timestamp()
    if record["origin"] == "imported":
        return None
    return float(meta.get("ledger_at") or 0)


def load(dsn: str, stores: list[dict[str, list]]) -> list[float | None]:
    """Save every store's records into the empty scratch store at `dsn`,
    ranked by time; returns the time of each rank, undated ones first."""
    use(dsn)
    records = [r for s in stores for r in s["records"]]
    records.sort(key=lambda r: (when(r) is not None, when(r) or 0.0, r["ledger"], r["ledger_id"], r["key"]))
    for rank, r in enumerate(records, 1):
        Record.create(**{**r, "ledger_id": rank})
    for name in sorted({n for s in stores for n in s["standards"]}):
        Standard.create(record=name)
    for name in sorted({n for s in stores for n in s["confined"]}):
        Confined.create(record=name)
    return [when(r) for r in records]


def score(dsn: str, times: list[float | None], questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Each question replayed against the scratch store as it stood on its
    `asked_on`, with and without undated records: whether a gold record
    is in the top `LIMIT`, whether it reached the Brief, and whether any
    forbidden record, or any record of another project and client that is
    not a standard of working, reached the Brief."""
    backend = use(dsn)
    undated = sum(t is None for t in times)
    dated = [t for t in times if t is not None]
    standards = {s.record for s in Standard.query.all()}
    results = []
    for q in questions:
        asked = datetime.fromisoformat(q["asked_on"]).replace(tzinfo=UTC).timestamp()
        cutoff = undated + bisect.bisect_left(dated, asked) + 1
        found = candidates(
            ledger=q["ledger"], project=q["project"], client=q["client"], cutoff=cutoff, started_at=asked
        )
        row = {"id": q["id"], "type": q["type"]}
        for arm, pool in (
            ("with_undated", found),
            ("dated_only", {k: r for k, r in found.items() if when(_plain(r)) is not None}),
        ):
            top = [ref(r.ledger, r.key) for r in ranked(backend, pool, q["query"], LIMIT)]
            chosen = [r for r, _ in brief(backend, pool, q["query"])]
            refs = {ref(r.ledger, r.key) for r in chosen}
            gold = set(q.get("gold") or ())
            leaked = refs & set(q.get("forbidden") or ()) | {
                ref(r.ledger, r.key)
                for r in chosen
                if r.project != q["project"]
                and (r.client or "") != q["client"]
                and ref(r.ledger, r.key) not in standards
            }
            row[arm] = {
                "at10": bool(gold & set(top)) if gold else None,
                "brief": bool(gold & refs) if gold else None,
                "leaked": sorted(leaked),
                "undated_in_top": sum(
                    when(_plain(pool[k])) is None for k in pool if ref(pool[k].ledger, pool[k].key) in top
                ),
            }
        results.append(row)
    return {"questions": results, "by_type": summary(results)}


def _plain(r: Record) -> dict[str, Any]:
    return {"origin": r.origin, "meta": r.meta or {}}


def summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per type and arm: questions, recall@10 and the Brief rate over the
    questions with gold, leaks, and undated records in the top ten."""
    out: dict[str, Any] = {}
    for t in TYPES:
        rows = [r for r in results if r["type"] == t]
        if not rows:
            continue
        out[t] = {"n": len(rows)}
        for arm in ("with_undated", "dated_only"):
            scored = [r[arm] for r in rows if r[arm]["at10"] is not None]
            out[t][arm] = {
                "recall_at_10": round(sum(s["at10"] for s in scored) / len(scored), 3) if scored else None,
                "in_brief": round(sum(s["brief"] for s in scored) / len(scored), 3) if scored else None,
                "leaks": sum(bool(r[arm]["leaked"]) for r in rows),
                "undated_in_top10": sum(r[arm]["undated_in_top"] for r in rows),
            }
    return out


def fixture(stores: list[dict[str, list]], questions: dict[str, Any]) -> dict[str, Any]:
    """The same questions and records as popoto's `dated` benchmark fixture
    (tests/benchmarks/datasets/dated.py): a record's project is its ledger
    and project, as recall's candidates are, and its time is `when`'s."""
    wanted = {(q["ledger"], q["project"]) for q in questions["questions"]}
    return {
        "version": questions.get("version", ""),
        "records": [
            {
                "id": ref(r["ledger"], r["key"]),
                "text": r["text"],
                "project": f"{r['ledger']}|{r['project']}",
                "at": None if when(r) is None else datetime.fromtimestamp(when(r), UTC).isoformat(),
            }
            for s in stores
            for r in s["records"]
            if (r["ledger"], r["project"]) in wanted and r["origin"] != "correction"
        ],
        "questions": [
            {**q, "project": f"{q['ledger']}|{q['project']}"} for q in questions["questions"] if q.get("gold")
        ],
    }
