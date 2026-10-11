"""Watched conditions on committed decisions (docs/routines.md, Watched
conditions; docs/business.md, item 7).

A business's committed decisions and their conditions live in that
business's own repository, never in the kernel's checkout: the file
`decisions/conditions.toml` on the branch commitments merge to (the
project spec's `branch`, else the remote's default branch). `committed`
reads it from the kernel's bare cache of the repository, fetched by its
trusted git, as Valor's GitHub account when given the key file, so a private
repository that account can read is read; `parse` reads the format.

A condition is numeric when it names a `metric` with `above` or `below`,
read from the business's data through a `Source`, or an observation when it
names none, checked against its written text by an `Observer`. Each is
reached by the `source` the condition names. Adapters live in `tools/` and
come in through the composition root; nothing here imports them. Where no
adapter for a condition's source is granted, the condition is recorded as
unread on the run, never dropped.
"""

import hashlib
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from core import credentials, git, workspace

PATH = "decisions/conditions.toml"


class Malformed(ValueError):
    """A conditions file the kernel cannot read."""


@dataclass(frozen=True)
class Condition:
    id: str
    text: str
    source: str
    metric: str | None = None
    above: float | None = None
    below: float | None = None

    @property
    def numeric(self) -> bool:
        return self.metric is not None

    def met_by(self, value: float) -> bool:
        if self.above is not None:
            return value > self.above
        return self.below is not None and value < self.below


@dataclass(frozen=True)
class Decision:
    id: str
    decided: date
    reasoning: str
    conditions: tuple[Condition, ...]


@dataclass(frozen=True)
class Reading:
    """A figure a `Source` read: the value, where it was read (an account,
    a report, a query), and when."""

    value: float
    where: str
    at: datetime


@dataclass(frozen=True)
class Observation:
    """What an `Observer` saw for a condition's text, where, when, and
    whether it meets the condition."""

    saw: str
    where: str
    at: datetime
    met: bool


class Source(Protocol):
    """A business's data, read: billing, analytics, whatever its accounts
    reach. Reading is `read`-class."""

    async def read(self, business: str, metric: str, at: datetime) -> Reading: ...


class Observer(Protocol):
    """Where a condition has no number: Valor looks and says what it saw."""

    async def observe(self, business: str, condition: Condition, at: datetime) -> Observation: ...


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Malformed(f"{where} is text")
    return value


def parse(raw: str) -> list[Decision]:
    """The decisions a conditions file lists. Malformed: bad TOML, a
    decision or condition missing its id, text, reasoning or source, a
    duplicate id, a metric without exactly one of `above` and `below`, or a
    threshold without a metric."""
    try:
        found = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise Malformed(str(exc)) from None
    decisions = found.get("decision") or []
    if not isinstance(decisions, list):
        raise Malformed("[[decision]] is an array of tables")
    out: list[Decision] = []
    seen: set[str] = set()
    for i, d in enumerate(decisions):
        did = _text(d.get("id"), f"decision {i} id")
        if did in seen:
            raise Malformed(f"decision {did} is listed twice")
        seen.add(did)
        decided = d.get("decided")
        if not isinstance(decided, date) or isinstance(decided, datetime):
            raise Malformed(f"decision {did}: decided is a date")
        reasoning = _text(d.get("reasoning"), f"decision {did} reasoning")
        conds: list[Condition] = []
        ids: set[str] = set()
        for j, c in enumerate(d.get("condition") or []):
            cid = _text(c.get("id"), f"decision {did} condition {j} id")
            if cid in ids:
                raise Malformed(f"decision {did}: condition {cid} is listed twice")
            ids.add(cid)
            metric = c.get("metric")
            above, below = c.get("above"), c.get("below")
            for name, v in (("above", above), ("below", below)):
                if v is not None and (isinstance(v, bool) or not isinstance(v, int | float)):
                    raise Malformed(f"decision {did} condition {cid}: {name} is a number")
            if metric is not None:
                _text(metric, f"decision {did} condition {cid} metric")
                if (above is None) == (below is None):
                    raise Malformed(f"decision {did} condition {cid}: a metric takes one of above, below")
            elif above is not None or below is not None:
                raise Malformed(f"decision {did} condition {cid}: a threshold needs a metric")
            conds.append(
                Condition(
                    id=cid,
                    text=_text(c.get("text"), f"decision {did} condition {cid} text"),
                    source=_text(c.get("source"), f"decision {did} condition {cid} source"),
                    metric=metric,
                    above=float(above) if above is not None else None,
                    below=float(below) if below is not None else None,
                )
            )
        out.append(Decision(id=did, decided=decided, reasoning=reasoning, conditions=tuple(conds)))
    return out


@dataclass(frozen=True)
class Committed:
    """A business's conditions file as committed: the commit it was read
    at, its decisions, and each decision's reasoning text at that commit
    (None when the reference is not a file there). `fetched` is the
    repository fetched, and `credential` the key the fetch carried
    (`credentials.GITHUB_KEY`, Valor's GitHub account) or None when it
    carried none."""

    business: str
    commit: str
    decisions: list[Decision]
    reasoning: dict[str, str | None]
    fetched: str = ""
    credential: str | None = None


def committed(
    spec: workspace.Spec, work: Path, *, credential: str | Path | None = None, loopback: bool = False
) -> Committed:
    """The business's conditions file on its committed branch, fetched into
    the kernel's cache, with no decisions when the repository holds none. With
    `credential`, the GitHub key file, a remote repository is fetched as
    Valor's GitHub account, so a private one Valor's account can read is
    read (`workspace._cache`). Raises `workspace.Refused` when the
    repository cannot be fetched and `Malformed` when the file cannot be
    read."""
    cache = workspace._cache(spec, None, work, credential=credential, loopback=loopback)
    keyed = credential is not None and not Path(spec.repo).expanduser().exists()
    branch = spec.branch or workspace._remote_head(cache)
    commit = workspace._commit_of(cache, f"refs/heads/{branch}")
    try:
        raw = git.trusted(cache, "show", f"{commit}:{PATH}", strip=False)
    except git.GitError:
        raw = ""  # no file: no watched conditions
    decisions = parse(raw)
    reasoning: dict[str, str | None] = {}
    for d in decisions:
        try:
            reasoning[d.id] = git.trusted(cache, "show", f"{commit}:{d.reasoning}", strip=False)
        except git.GitError:
            reasoning[d.id] = None
    return Committed(
        spec.name,
        commit,
        decisions,
        reasoning,
        fetched=spec.repo,
        credential=credentials.GITHUB_KEY if keyed else None,
    )


def key(business: str, decision: str, condition: Condition) -> str:
    """One met condition's agenda key: the business, the decision, the
    condition's id and its written terms. A condition whose terms change is
    a new condition."""
    terms = f"{condition.text}\0{condition.metric}\0{condition.above}\0{condition.below}"
    digest = hashlib.sha256(terms.encode()).hexdigest()[:16]
    return f"{business}/{decision}/{condition.id}/{digest}"
