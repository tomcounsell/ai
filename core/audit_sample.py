"""The audit sample: Tom's own labels on candidates the blind verifier
judged, the list he reads them from, and the verifier's calibration.

Serves "Independent checks" (`docs/mission.md`) and the human audit sample
of `docs/architecture.md` ("Calibration and autonomy"). It is measurement,
not a check or a gate: nothing reads a label or a score to hold, refuse,
reorder, or redirect any task or effect, nothing sends the list to Tom, and
no turn's Brief carries any of it.

A label is one `audit.labelled` row on the `audit` stream, never on the
task's own: `{task_id, candidate_sha, label, note, provenance}`, the label
`pass` or `changes`, given by reading the work without the verdict. A label
is real when its `role_played` is false, whatever `by` names; a stand-in's
label is only counted. The latest real label on a candidate is the one in
force; every row stays in the ledger.

The verdicts scored are the session `review.decided` rows, the ones with a
`reviewer_verdict` (a `kernel` or hand-recorded review has none). The
reviewer's own answer is scored, never the verdict the kernel computed from
it, so the governance outcome never enters a figure.

The list (`sample`) is a weighted random order (Efraimidis and Spirakis):
each candidate's key is `u^(1/w)`, sorted high first. `u` is hashed from the
task id and the ledger id of the candidate's first session review, which
the kernel assigns, so a turn cannot steer its candidate down the list by
varying its commit. `w` (`WEIGHTS`) is Valor's reading of
`docs/architecture.md`, "weighted toward work that left the workspace and
every `act`, with a smaller share of failures": 4 when a done merge carried
the candidate, else 2 when its latest session review is `pass`, else 1.
Each line shows the work and how to read it, never the verdict.

The scores (`scores`) are stratified estimates. A verdict's stratum is its
`reviewer_verdict` and its candidate's `w`, so every verdict in a stratum
had the same chance of being labelled; each labelled verdict weighs
`N_s / n_s`. A figure is printed only when every stratum it sums over has a
label; otherwise it is "not estimable" and names the uncovered strata.
"""

import hashlib
import math
from collections import Counter, defaultdict
from typing import Any

from psycopg.rows import dict_row

from core import ledger, tasks

STREAM = "audit"
LABELS = ("pass", "changes")
WEIGHTS = {"merged": 4, "latest review pass": 2, "otherwise": 1}
REAL = "tom"  # the source of every real label, whoever `by` names
ASSUMPTION = (
    "The figures estimate every verdict in the ledger when the candidates are labelled in list "
    "order; skipping candidates by choice is a selection the weights cannot undo. The rates and "
    "the Brier score are ratio estimates: consistent, not exactly unbiased at small n. A candidate "
    "labelled while unmerged and merged afterwards counts in its merged stratum."
)


class NotFound(LookupError):
    """No verifier's review of that candidate: nothing to label."""


async def reviewed(conn, task_id: str, sha: str) -> bool:
    """Whether the task's ledger holds a session review of the candidate."""
    row = await (
        await conn.execute(
            "SELECT 1 FROM events WHERE task_id = %s AND type = 'review.decided' "
            "AND payload->'candidate'->>'sha' = %s AND payload->>'reviewer_verdict' IS NOT NULL LIMIT 1",
            (task_id, sha),
        )
    ).fetchone()
    return row is not None


async def record(
    conn,
    task_id: str,
    sha: str,
    label: str,
    *,
    note: str | None = None,
    by: str = "tom",
    via: str = "the command line",
    role_played: bool = False,
) -> dict[str, Any]:
    """Append one `audit.labelled` row. Only the command line calls it, after
    its lookup (`reviewed`) found the candidate."""
    if label not in LABELS:
        raise ValueError(f"a label is one of {', '.join(LABELS)}")
    payload = {
        "task_id": task_id,
        "candidate_sha": sha,
        "label": label,
        "note": note,
        "provenance": ledger.provenance(by, via, role_played),
    }
    event_id = await ledger.append(conn, STREAM, "audit.labelled", payload)
    return {"event_id": event_id, **payload}


async def labels(conn) -> list[dict[str, Any]]:
    """Every label, oldest first: `{task_id, candidate_sha, label, source,
    provenance, event_id}`. `source` is `tom` for a real label and `stand-in`
    for a role-played one."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, payload FROM events WHERE task_id = %s AND type = 'audit.labelled' ORDER BY id",
            (STREAM,),
        )
        rows = await cur.fetchall()
    out = []
    for r in rows:
        p = r["payload"]
        prov = p.get("provenance") or {}
        out.append(
            {
                "task_id": p["task_id"],
                "candidate_sha": p["candidate_sha"],
                "label": p["label"],
                "source": "stand-in" if prov.get("role_played") else REAL,
                "provenance": prov,
                "event_id": r["id"],
            }
        )
    return out


def in_force(found: list[dict[str, Any]], source: str = REAL) -> dict[tuple[str, str], dict[str, Any]]:
    """The latest label of `source` per candidate, by row id."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for lab in sorted(found, key=lambda x: x["event_id"]):
        if lab["source"] == source:
            latest[(lab["task_id"], lab["candidate_sha"])] = lab
    return latest


async def _verdicts(conn) -> list[dict[str, Any]]:
    """Every session review, oldest first."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT id, task_id, payload FROM events WHERE type = 'review.decided' "
            "AND payload->>'reviewer_verdict' IS NOT NULL ORDER BY id"
        )
        rows = await cur.fetchall()
    return [
        {
            "event_id": r["id"],
            "task_id": r["task_id"],
            "sha": r["payload"]["candidate"]["sha"],
            "model": r["payload"].get("model") or "unknown",
            "verdict": r["payload"]["reviewer_verdict"],
            "predicted_failure": r["payload"].get("predicted_failure"),
        }
        for r in rows
    ]


async def _merged(conn) -> set[tuple[str, str]]:
    """The candidates a done merge carried: a merge's held row names its
    candidate, and an outcome `done` for the same effect is the merge."""
    rows = await (
        await conn.execute(
            "SELECT h.task_id, h.payload->'payload'->'candidate'->>'sha' FROM events h "
            "WHERE h.type = 'effect.held' AND h.payload->>'action_type' = 'merge' AND EXISTS ("
            "SELECT 1 FROM events o WHERE o.type = 'effect.outcome' AND o.task_id = h.task_id "
            "AND o.payload->>'effect_id' = h.payload->>'effect_id' AND o.payload->>'kind' = 'done')"
        )
    ).fetchall()
    return {(r[0], r[1]) for r in rows}


def weight(merged: bool, latest: str) -> int:
    if merged:
        return WEIGHTS["merged"]
    return WEIGHTS["latest review pass"] if latest == "pass" else WEIGHTS["otherwise"]


def key(task_id: str, event_id: int, w: int) -> float:
    """A candidate's place on the list: `u^(1/w)`, `u` the first 64 bits of
    `sha256(task_id:event_id)` over 2^64."""
    u = int(hashlib.sha256(f"{task_id}:{event_id}".encode()).hexdigest()[:16], 16) / 2**64
    return u ** (1 / w)


def _candidates(verdicts: list[dict[str, Any]], merged: set[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """Per candidate: its first session review's id and its weight."""
    out: dict[tuple[str, str], dict] = {}
    for v in verdicts:  # oldest first
        c = out.setdefault((v["task_id"], v["sha"]), {"first": v["event_id"]})
        c["latest"] = v["verdict"]
    for k, c in out.items():
        c["w"] = weight(k in merged, c["latest"])
    return out


async def sample(conn) -> list[dict[str, Any]]:
    """The candidates with no real label, in list order. Each carries only
    the task id, the instruction, the base and candidate sha, and the git
    command that shows the work: no verdict, finding, forecast, or merge
    state."""
    candidates = _candidates(await _verdicts(conn), await _merged(conn))
    done = in_force(await labels(conn))
    ordered = sorted(
        (k for k in candidates if k not in done),
        key=lambda k: key(k[0], candidates[k]["first"], candidates[k]["w"]),
        reverse=True,
    )
    briefs: dict[str, tasks.Brief] = {}
    out = []
    for task_id, sha in ordered:
        if task_id not in briefs:
            briefs[task_id] = await tasks.brief(conn, task_id)
        b = briefs[task_id]
        repo = b.mirror or b.workspace or "."
        out.append(
            {
                "task_id": task_id,
                "instruction": b.instruction,
                "base_sha": b.base_sha,
                "sha": sha,
                "read": f"git -C {repo} diff {b.base_sha} {sha}"
                if b.base_sha
                else f"git -C {repo} show {sha}",
            }
        )
    return out


def _brier_value(x: Any) -> float | None:
    """A forecast the Brier term may use: a number, not a bool, in 0 to 1."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not 0 <= x <= 1:
        return None
    return float(x)


def _stratum_name(s: tuple[str, int]) -> str:
    return f"{s[0]}, {s[1]}"


def figures(
    verdicts: list[dict[str, Any]],
    candidates: dict[tuple[str, str], dict],
    labelled: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    """One model's figures over one source's labels in force."""
    strata = [(v, w) for v in LABELS for w in sorted(set(WEIGHTS.values()), reverse=True)]
    big_n: Counter = Counter()
    small_n: Counter = Counter()
    scored = []
    for v in verdicts:
        k = (v["task_id"], v["sha"])
        s = (v["verdict"], candidates[k]["w"])
        big_n[s] += 1
        if k in labelled:
            small_n[s] += 1
            scored.append({**v, "stratum": s, "label": labelled[k]["label"]})
    for v in scored:
        v["weight"] = big_n[v["stratum"]] / small_n[v["stratum"]]

    def uncovered(which: str | None) -> list[str]:
        return [
            _stratum_name(s)
            for s in strata
            if (which is None or s[0] == which) and big_n[s] > 0 and small_n[s] == 0
        ]

    def rate(which: str | None, den, num) -> dict[str, Any]:
        among = [v for v in scored if den(v)]
        missing = uncovered(which)
        out: dict[str, Any] = {"n": len(among), "value": None, "not_estimable": missing}
        if not missing and among:
            out["value"] = sum(v["weight"] for v in among if num(v)) / sum(v["weight"] for v in among)
        return out

    brier_terms, skipped, absent = [], 0, 0
    for v in scored:
        pf = v["predicted_failure"]
        if pf is None:
            absent += 1
        elif (x := _brier_value(pf)) is None:
            skipped += 1
        else:
            brier_terms.append((v["weight"], (x - (1.0 if v["label"] == "changes" else 0.0)) ** 2))
    brier: dict[str, Any] = {
        "n": len(brier_terms),
        "value": None,
        "skipped": skipped,
        "absent": absent,
        "not_estimable": uncovered(None),
    }
    if not brier["not_estimable"] and brier_terms:
        brier["value"] = sum(w * e for w, e in brier_terms) / sum(w for w, _ in brier_terms)
    return {
        "verdicts": len(scored),
        "candidates": len({(v["task_id"], v["sha"]) for v in scored}),
        "strata": [{"verdict": s[0], "w": s[1], "N": big_n[s], "n": small_n[s]} for s in strata],
        "confusion": {
            f"{a}/{b}": sum(1 for v in scored if v["verdict"] == a and v["label"] == b)
            for a in LABELS
            for b in LABELS
        },
        "pass_labelled_changes": rate(
            "pass", lambda v: v["verdict"] == "pass", lambda v: v["label"] == "changes"
        ),
        "changes_labelled_pass": rate(
            "changes", lambda v: v["verdict"] == "changes", lambda v: v["label"] == "pass"
        ),
        "false_accept": rate(None, lambda v: v["label"] == "changes", lambda v: v["verdict"] == "pass"),
        "false_reject": rate(None, lambda v: v["label"] == "pass", lambda v: v["verdict"] == "changes"),
        "brier": brier,
    }


async def scores(conn) -> dict[str, Any]:
    """The verifier's calibration per source and per model, with the list
    weights, the count of stand-in labels, and the real labels in force per
    `by`."""
    verdicts = await _verdicts(conn)
    candidates = _candidates(verdicts, await _merged(conn))
    found = await labels(conn)
    real = in_force(found)
    by_model: dict[str, list] = defaultdict(list)
    for v in verdicts:
        by_model[v["model"]].append(v)
    return {
        "weights": dict(WEIGHTS),
        "assumption": ASSUMPTION,
        "stand_in_labels": sum(1 for lab in found if lab["source"] == "stand-in"),
        "labels_by": dict(Counter(lab["provenance"].get("by") for lab in real.values())),
        "sources": {REAL: {m: figures(vs, candidates, real) for m, vs in sorted(by_model.items())}},
    }


def _figure(name: str, f: dict[str, Any], digits: int = 3) -> str:
    if f["not_estimable"]:
        return f"{name}: not estimable (n {f['n']}; no label in stratum {'; '.join(f['not_estimable'])})"
    if f["value"] is None:
        return f"{name}: none labelled (n 0)"
    return f"{name}: {f['value']:.{digits}f} (n {f['n']})"


def render_scores(s: dict[str, Any]) -> str:
    """The scores as the text `audit scores` prints and the page shows."""
    w = s["weights"]
    lines = [
        "# The verifier's calibration",
        "",
        (
            f"List weights: merged {w['merged']}, latest review pass {w['latest review pass']}, "
            f"otherwise {w['otherwise']}."
        ),
        s["assumption"],
        f"Stand-in labels left out: {s['stand_in_labels']}.",
        "Real labels in force per by: "
        + (", ".join(f"{b} {n}" for b, n in sorted(s["labels_by"].items(), key=str)) or "none")
        + ".",
    ]
    for source, models in s["sources"].items():
        for model, f in models.items():
            lines += [
                "",
                f"## {model}, labels from {source}",
                f"Labelled verdicts {f['verdicts']}, labelled candidates {f['candidates']}.",
                "Strata (verdict, w): "
                + "; ".join(f"({x['verdict']}, {x['w']}) N {x['N']} n {x['n']}" for x in f["strata"]),
                "Confusion, verifier/label: " + ", ".join(f"{k} {v}" for k, v in f["confusion"].items()),
                _figure("Of verifier pass, labelled changes", f["pass_labelled_changes"]),
                _figure("Of verifier changes, labelled pass", f["changes_labelled_pass"]),
                _figure("False accepts", f["false_accept"]),
                _figure("False rejects", f["false_reject"]),
                _figure("Brier score", f["brier"])
                + f"; forecasts skipped {f['brier']['skipped']}, absent {f['brier']['absent']}",
            ]
    if not any(s["sources"].values()):
        lines += ["", "No verifier's review is in the ledger."]
    return "\n".join(lines)


def render_list(items: list[dict[str, Any]]) -> str:
    """The list as the text `audit` prints."""
    if not items:
        return "Nothing to label: every reviewed candidate has a label from Tom."
    lines = []
    for c in items:
        lines += [
            f"{c['task_id']}  {' '.join((c['instruction'] or '').split())}",
            f"    base {c['base_sha']}  candidate {c['sha']}",
            f"    {c['read']}",
        ]
    return "\n".join(lines)
