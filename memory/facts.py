"""What is true about a project over time (docs/memory.md, Facts; issue
#3684): dated facts that supersede one another, the entities they are
about under every name each has had, typed links between them, and how far
each source was read.

Each model stores the project (its spec's `repo`) and client, as `Record`
does, and the ledger its row came from. A fact, entity or link is named
across the store by `<ledger>/<row id>`, its ledger row's. Every row is
one unit, taken by `take` from plain data as `ingest.save` hands it over,
so a rebuild from the ledger restores the same facts, intervals, chains
and links, and a unit taken again writes nothing twice.

`prepare` turns one write a turn asked for into the rows to append, or
refuses it as bad input, reading what is stored; it writes nothing.
`read` answers what was true at a date, a fact's history, what a fact or
entity links to, and the sources. Popoto's validity intervals are
half-open: as of the instant a fact is superseded, its successor holds and
it does not.
"""

import math
from datetime import UTC, datetime
from typing import Any

import popoto
from popoto.fields.constants import Defaults
from popoto.fields.supersession import SupersessionProtocol

from memory.recall import quote
from memory.records import ref, split, use

# The rows this module takes (`core/memory.py` adds them to its TYPES).
TYPES = (
    "fact.added",
    "fact.superseded",
    "fact.confirmed",
    "entity.added",
    "entity.aliased",
    "link.added",
    "source.read",
)
# How far `--related` walks by default, and at most.
DEPTH, MOST_DEPTH = 2, 6


class Fact(popoto.Model):
    """One claim about the project, true from `since` until a successor
    supersedes it. `source` cites where it came from (`commit:<sha>`,
    `telegram:<chat>/<message>`, `<ledger>/<row>`); `reported` and
    `evidence` hold a claim that has both, and `why_differs` the reason."""

    key = popoto.KeyField()
    ledger = popoto.KeyField()
    project = popoto.KeyField()
    client = popoto.KeyField()
    kind = popoto.StringField()
    claim = popoto.StringField()
    reported = popoto.StringField(null=True)
    evidence = popoto.StringField(null=True)
    why_differs = popoto.StringField(null=True)
    source = popoto.StringField()
    source_at = popoto.FloatField()
    since = popoto.FloatField()
    supersedes = popoto.StringField(null=True)
    reason = popoto.StringField(null=True)
    about = popoto.ListField(null=True)
    validity = popoto.ValidityField()

    class Meta:
        backend = "postgres"


class Confirmation(popoto.Model):
    """A person's answer confirming a fact: `answer` names its ledger row
    (`<ledger>/<id>`). A fact with none is inferred."""

    fact = popoto.KeyField()
    answer = popoto.StringField()
    by = popoto.StringField()
    at = popoto.FloatField()

    class Meta:
        backend = "postgres"


class Entity(popoto.Model):
    """A person, decision, event or concept of the project."""

    key = popoto.KeyField()
    ledger = popoto.KeyField()
    project = popoto.KeyField()
    client = popoto.KeyField()
    kind = popoto.StringField()
    name = popoto.StringField()

    class Meta:
        backend = "postgres"


class Alias(popoto.Model):
    """One name an entity has had, case and spaces folded: unique per
    project and client."""

    alias = popoto.KeyField()
    project = popoto.KeyField()
    client = popoto.KeyField()
    entity = popoto.StringField()
    name = popoto.StringField()

    class Meta:
        backend = "postgres"


class Link(popoto.Model):
    """A typed link between two facts or entities; `graph` holds the same
    edge for the bounded walk."""

    key = popoto.KeyField()
    ledger = popoto.KeyField()
    project = popoto.KeyField()
    client = popoto.KeyField()
    a = popoto.StringField()
    b = popoto.StringField()
    kind = popoto.StringField()
    source = popoto.StringField(null=True)
    graph = popoto.CoOccurrenceField()

    class Meta:
        backend = "postgres"


class Source(popoto.Model):
    """One channel the project's facts are read from: when a read was last
    tried, when one last succeeded, and how far it got."""

    channel = popoto.KeyField()
    project = popoto.KeyField()
    client = popoto.KeyField()
    last_tried = popoto.FloatField()
    last_ok = popoto.FloatField(null=True)
    upto = popoto.StringField(null=True)

    class Meta:
        backend = "postgres"


def fold(name: str) -> str:
    """An alias as stored: case and runs of space folded."""
    return " ".join(str(name).split()).casefold()


def when(value: Any) -> float:
    """Epoch seconds from a number or an ISO date or time (UTC when it
    names no zone)."""
    if isinstance(value, int | float):
        return float(value)
    try:
        t = datetime.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError(f"{value!r} is not a date (YYYY-MM-DD or an ISO time)") from None
    return t.timestamp() if t.tzinfo else t.replace(tzinfo=UTC).timestamp()


def day(t: float | None) -> str:
    if not t:
        return "never"
    d = datetime.fromtimestamp(t, UTC)
    return (
        d.strftime("%Y-%m-%d")
        if (d.hour, d.minute, d.second) == (0, 0, 0)
        else d.strftime("%Y-%m-%d %H:%M UTC")
    )


# -- naming -------------------------------------------------------------------------


def _ref(ledger: str, value: Any) -> tuple[str, str] | None:
    """A name's ledger and row id: `<ledger>/<id>`, or a bare id of this
    ledger; None for anything else."""
    text = str(value).strip()
    if text.isdigit():
        return ledger, text
    at, key = split(text)
    return (at, key) if at and key.isdigit() else None


def fact(ledger: str, project: str, client: str, name: Any) -> Fact | None:
    found = _ref(ledger, name)
    if found is None:
        return None
    hits = Fact.query.filter(ledger=found[0], key=found[1], project=project, client=client)
    return hits[0] if hits else None


def entity(ledger: str, project: str, client: str, name: Any) -> Entity | None:
    """The entity a name or alias resolves to, or its row name."""
    found = _ref(ledger, name)
    if found is not None:
        hits = Entity.query.filter(ledger=found[0], key=found[1], project=project, client=client)
        if hits:
            return hits[0]
    held = Alias.query.filter(alias=fold(name), project=project, client=client) if fold(name) else []
    if not held:
        return None
    at, key = split(held[0].entity)
    hits = Entity.query.filter(ledger=at, key=key, project=project, client=client)
    return hits[0] if hits else None


def _node(ledger: str, project: str, client: str, name: Any) -> str:
    """A link end: a fact's or an entity's name."""
    found = fact(ledger, project, client, name) or entity(ledger, project, client, name)
    if found is None:
        raise ValueError(f"no fact or entity {name!r} in this project")
    return ref(found.ledger, found.key)


def _text(write: dict[str, Any], field: str) -> str:
    value = write.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{write.get('op')} needs {field}")
    return value


# -- writes -------------------------------------------------------------------------


def prepare(
    dsn: str, *, ledger: str, project: str, client: str, write: dict[str, Any]
) -> tuple[list[tuple[str, dict]], str | None]:
    """The rows one write appends, in order, each payload without its
    project, client, key or provenance; and the name the write resolves to
    when no row is needed (an entity already known, an alias it holds).
    A write that cannot be stored as asked raises ValueError."""
    use(dsn)
    op = write.get("op")
    if op in ("fact", "supersede"):
        since = when(write.get("since") if write.get("since") is not None else _text(write, "since"))
        payload = {
            "kind": _text(write, "kind"),
            "claim": _text(write, "claim"),
            "source": _text(write, "source"),
            "source_at": when(write["source_at"]) if write.get("source_at") is not None else since,
            "since": since,
            "about": [_entity_ref(ledger, project, client, n) for n in write.get("about") or []],
        }
        payload.update({k: write[k] for k in ("reported", "evidence", "why_differs") if write.get(k)})
        if op == "fact":
            return [("fact.added", payload)], None
        old = fact(ledger, project, client, _text(write, "old"))
        if old is None:
            raise ValueError(f"no fact {write['old']!r} in this project")
        head = SupersessionProtocol.chain(old)[-1]
        if (head.ledger, head.key) != (old.ledger, old.key):
            raise ValueError(
                f"{ref(old.ledger, old.key)} is superseded by {ref(head.ledger, head.key)}; supersede that"
            )
        if since < old.since:
            raise ValueError(
                f"the new fact's date {day(since)} precedes the start of {ref(old.ledger, old.key)}, {day(old.since)}"
            )
        payload.update(old=ref(old.ledger, old.key), reason=_text(write, "reason"))
        return [("fact.superseded", payload)], None
    if op == "confirm":
        found = fact(ledger, project, client, write.get("fact", ""))
        if found is None:
            raise ValueError(f"no fact {write.get('fact')!r} in this project")
        answer = _ref(ledger, write.get("answer", ""))
        if answer is None:
            raise ValueError("confirm needs answer, the ledger row of the answer (<ledger>/<id>)")
        return [("fact.confirmed", {"fact": ref(found.ledger, found.key), "answer": ref(*answer),
                                    "by": write.get("by") or "tom"})], None  # fmt: skip
    if op == "entity":
        name, aliases = _text(write, "name"), [a for a in write.get("aliases") or [] if fold(a)]
        held = {ref(e.ledger, e.key) for n in [name, *aliases] if (e := entity(ledger, project, client, n))}
        if len(held) > 1:
            raise ValueError(f"{name!r} and its aliases name different entities: {', '.join(sorted(held))}")
        if not held:
            return [("entity.added", {"kind": _text(write, "kind"), "name": name, "aliases": aliases})], None
        (known,) = held
        new = [a for a in dict.fromkeys([name, *aliases]) if entity(ledger, project, client, a) is None]
        return [("entity.aliased", {"entity": known, "alias": a}) for a in new], known
    if op == "alias":
        found = entity(ledger, project, client, _text(write, "entity"))
        if found is None:
            raise ValueError(f"no entity {write['entity']!r} in this project")
        alias, own = _text(write, "alias"), ref(found.ledger, found.key)
        holder = entity(ledger, project, client, alias)
        if holder is not None and ref(holder.ledger, holder.key) != own:
            raise ValueError(f"{alias!r} already names {ref(holder.ledger, holder.key)} ({holder.name})")
        return ([] if holder is not None else [("entity.aliased", {"entity": own, "alias": alias})]), own
    if op == "link":
        a, b = (
            _node(ledger, project, client, _text(write, "a")),
            _node(ledger, project, client, _text(write, "b")),
        )
        if a == b:
            raise ValueError("a link joins two different facts or entities")
        return [
            (
                "link.added",
                {"a": a, "b": b, "kind": _text(write, "kind"), "source": write.get("source") or ""},
            )
        ], None
    if op == "source":
        payload = {
            "channel": _text(write, "channel"),
            "ok": bool(write.get("ok", True)),
            "upto": write.get("upto") or "",
        }
        if write.get("at") is not None:
            payload["at"] = when(write["at"])
        return [("source.read", payload)], None
    raise ValueError(f"no write {op!r}; one of fact, supersede, confirm, entity, alias, link, source")


def _entity_ref(ledger: str, project: str, client: str, name: str) -> str:
    found = entity(ledger, project, client, name)
    if found is None:
        raise ValueError(f"no entity {name!r} in this project; add it first")
    return ref(found.ledger, found.key)


def named(ledger: str, row_id: int, type_: str, payload: dict[str, Any]) -> str:
    """The name a row's write resolves to."""
    if type_ == "entity.aliased":
        return payload["entity"]
    if type_ == "fact.confirmed":
        return payload["fact"]
    if type_ == "source.read":
        return payload["channel"]
    return ref(ledger, str(row_id))


def _link(unit: dict[str, Any], key: str, a: str, b: str, kind: str, source: str) -> None:
    Link.create(key=key, ledger=unit["ledger"], project=unit["project"], client=unit["client"],
                a=a, b=b, kind=kind, source=source)  # fmt: skip
    Link._meta.fields["graph"].link(Link, a, b, initial_weight=Defaults.CO_OCCURRENCE_WEIGHT_CAP)


def take(unit: dict[str, Any]) -> None:
    """Store one row (`ingest.save` calls it, the backend installed)."""
    p, where = unit["payload"], {"project": unit["project"], "client": unit["client"]}
    key, ledger = str(unit["id"]), unit["ledger"]
    kind = unit["type"]
    if kind in ("fact.added", "fact.superseded"):
        new = Fact(
            key=key, ledger=ledger, **where, kind=p["kind"], claim=p["claim"], source=p["source"],
            source_at=float(p["source_at"]), since=float(p["since"]), about=list(p.get("about") or []),
            reported=p.get("reported"), evidence=p.get("evidence"), why_differs=p.get("why_differs"),
            supersedes=p.get("old"), reason=p.get("reason"), validity=float(p["since"]),
        )  # fmt: skip
        old = fact(ledger, unit["project"], unit["client"], p["old"]) if p.get("old") else None
        if old is None:
            new.save()
        else:
            SupersessionProtocol.save_and_invalidate(new, closes=old, at=float(p["since"]))
        for n, target in enumerate(p.get("about") or []):
            _link(unit, f"{key}.{n}", ref(ledger, key), target, "about", p["source"])
    elif kind == "fact.confirmed":
        Confirmation.create(fact=p["fact"], answer=p["answer"], by=p.get("by") or "", at=float(unit["at"]))
    elif kind == "entity.added":
        Entity.create(key=key, ledger=ledger, **where, kind=p["kind"], name=p["name"])
        for name in [p["name"], *p.get("aliases", [])]:
            Alias.create(alias=fold(name), **where, entity=ref(ledger, key), name=name)
    elif kind == "entity.aliased":
        Alias.create(alias=fold(p["alias"]), **where, entity=p["entity"], name=p["alias"])
    elif kind == "link.added":
        _link(unit, key, p["a"], p["b"], p["kind"], p.get("source") or "")
    elif kind == "source.read":
        at = float(p.get("at") or unit["at"])
        was = Source.query.filter(channel=p["channel"], **where)
        was = was[0] if was else None
        Source.create(
            channel=p["channel"], **where, last_tried=at,
            last_ok=at if p.get("ok") else (was.last_ok if was else None),
            upto=(p.get("upto") or (was.upto if was else None)) if p.get("ok") else (was.upto if was else None),
        )  # fmt: skip


# -- reads --------------------------------------------------------------------------


def _shown(
    f: Fact, successors: dict[str, Fact], confirmed: dict[str, Confirmation], names: dict[str, str]
) -> dict:
    name = ref(f.ledger, f.key)
    after = successors.get(name)
    c = confirmed.get(name)
    out = {
        "fact": name, "kind": f.kind, "claim": f.claim, "source": f.source, "source_at": day(f.source_at),
        "since": day(f.since), "until": day(after.since) if after else None,
        "superseded_by": ref(after.ledger, after.key) if after else None,
        "supersedes": f.supersedes, "reason": f.reason,
        "status": "confirmed" if c else "inferred", "confirmed_by": c.answer if c else None,
        "about": [names.get(a, a) for a in f.about or []],
        **{k: getattr(f, k) for k in ("reported", "evidence", "why_differs") if getattr(f, k)},
    }  # fmt: skip
    out["entry"] = entry(out)
    return out


def entry(f: dict[str, Any]) -> str:
    """One fact as text: its label, then its claim quoted as data."""
    span = f"since {f['since']}" + (
        f", until {f['until']} (superseded by {f['superseded_by']})" if f["until"] else ""
    )
    status = f"confirmed by {f['confirmed_by']}" if f["status"] == "confirmed" else "inferred"
    label = f"- fact {f['fact']} [{f['kind']}], {span}, {status}; source {f['source']} ({f['source_at']})"
    if f.get("about"):
        label += f"; about {', '.join(f['about'])}"
    body = quote(f["claim"])
    for k in ("reported", "evidence", "why_differs"):
        if f.get(k):
            body += f"\n  {k}:\n{quote(f[k])}"
    if f.get("reason"):
        body += f"\n  superseding {f['supersedes']} because:\n{quote(f['reason'])}"
    return f"{label}:\n{body}"


def _context(
    project: str, client: str
) -> tuple[list[Fact], dict[str, Fact], dict[str, Confirmation], dict[str, str]]:
    facts = list(Fact.query.filter(project=project, client=client))
    successors = {f.supersedes: f for f in facts if f.supersedes}
    names = {ref(f.ledger, f.key) for f in facts}
    confirmed = {c.fact: c for c in Confirmation.query.all() if c.fact in names}
    entities = {ref(e.ledger, e.key): e.name for e in Entity.query.filter(project=project, client=client)}
    return facts, successors, confirmed, entities


def read(dsn: str, *, ledger: str, project: str, client: str, query: dict[str, Any]) -> dict[str, Any]:
    """`as_of` (a date; default now) and `kind`: the facts true then.
    `history`: a fact's supersession chain, oldest first. `related`: what a
    fact or entity links to, within `depth` hops. `sources`: every source.
    Another project's or client's fact or entity is "no such fact"."""
    use(dsn)
    where = {"project": project, "client": client}
    _, successors, confirmed, names = _context(project, client)
    if query.get("sources"):
        return {"sources": [
            {"channel": s.channel, "last_tried": day(s.last_tried), "last_ok": day(s.last_ok), "upto": s.upto or ""}
            for s in sorted(Source.query.filter(**where), key=lambda s: s.channel)
        ]}  # fmt: skip
    if query.get("history"):
        found = fact(ledger, project, client, query["history"])
        if found is None:
            raise ValueError(f"no fact {query['history']!r} in this project")
        return {"facts": [_shown(f, successors, confirmed, names) for f in SupersessionProtocol.chain(found)]}
    if query.get("related"):
        return _related(ledger, project, client, query, successors, confirmed, names)
    if query.get("as_of"):
        at = when(query["as_of"])
        held = Fact.query.filter(**where, validity__as_of=at)
    else:
        held = Fact.query.filter(**where, validity__current=True)
    held = [f for f in held if not query.get("kind") or fold(f.kind) == fold(query["kind"])]
    return {
        "facts": [
            _shown(f, successors, confirmed, names) for f in sorted(held, key=lambda f: (f.since, int(f.key)))
        ]
    }


def _related(ledger, project, client, query, successors, confirmed, names) -> dict[str, Any]:
    seed = _node(ledger, project, client, query["related"])
    try:
        depth = max(1, min(int(query.get("depth") or DEPTH), MOST_DEPTH))
    except ValueError:
        raise ValueError(f"depth {query['depth']!r} is not a number") from None
    reached = Link._meta.fields["graph"].propagate(Link, [seed], depth=depth)
    links = [l for l in Link.query.filter(project=project, client=client) if seed in (l.a, l.b)]
    found = []
    for name, weight in sorted(reached.items(), key=lambda kv: (-kv[1], kv[0])):
        hops = round(
            math.log(Defaults.CO_OCCURRENCE_WEIGHT_CAP * weight, Defaults.CO_OCCURRENCE_DECAY_PER_HOP)
        )
        if (f := fact(ledger, project, client, name)) is not None:
            found.append({"hops": hops, **_shown(f, successors, confirmed, names)})
        elif (e := entity(ledger, project, client, name)) is not None:
            aliases = sorted(
                a.name for a in Alias.query.filter(project=project, client=client) if a.entity == name
            )
            found.append({"hops": hops, "entity": name, "kind": e.kind, "name": e.name, "aliases": aliases,
                          "entry": f"- entity {name} [{e.kind}] {e.name}" + (f" (also {', '.join(a for a in aliases if a != e.name)})" if len(aliases) > 1 else "")})  # fmt: skip
    return {
        "seed": seed,
        "links": [
            {"a": names.get(l.a, l.a), "b": names.get(l.b, l.b), "kind": l.kind, "source": l.source}
            for l in links
        ],
        "related": found,
    }
