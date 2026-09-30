"""Verification: the kernel's checks, the blind slice, the Verifier's Brief,
and the verdict. Plan 11; seams §3.6, Round two §3.5; architecture §5.

Nothing outside the kernel process reads the tool log or the effect ledger.
`verify_objective` is called by `kernel/runs.py::run_brief` after an
Executor's terminal has landed, and calls `run_brief` back for the Verifier's
own Brief, so `kernel/runs.py` resolves this module by name inside the two
calls that need it.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import importlib
import random
import re
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

import httpx
from psycopg.types.json import Jsonb

from infra.sandbox.proc import run as run_process
from infra.secrets import read_secret as _read_secret
from kernel import api, runs, tree
from kernel.events import append, read_for
from schemas.brief import Brief, ContextBlock, ContextSlice, DelegateRequest
from schemas.budget import Budget
from schemas.capability import Capability
from schemas.events import Event
from schemas.ids import BriefId, ObjectiveId, SpaceId
from schemas.objective import ArtifactKind, Objective
from schemas.records import EffectLedgerRecord, Invocation, ToolLogRecord
from schemas.report import (
    CheckResult,
    CitationResolution,
    CriterionResult,
    Report,
    Verdict,
)
from schemas.sandbox import MOUNT_TARGET, SandboxProfile, SnapshotRef
from schemas.space import EffectClass, Space
from schemas.trace import Terminal
from workers.verifier import INSTRUCTION, TOOLS, load_prompt, validate_verdict

# Keychain reads for `no_operator_content`, a module name so a test pins it.
read_secret = _read_secret


class VerifyError(Exception):
    """Base of this module's refusals."""


class UnsupportedArtifactKind(VerifyError):
    """A kind no M0 objective produces (`decision_brief`, seams §1.4)."""


class NotVerifying(VerifyError):
    """`verify_objective` on a node that is not VERIFYING with a report."""


# ---------------------------------------------------------------------------
# Late-bound collaborators, in the tree's pattern: the sandbox provider and
# the connection factory are `kernel.runs`'s, set once by the composition
# root; the manifests and `profile_for` are imported on first use so a test
# binds a fake with `monkeypatch` before the first call.

load_all: Any = None  # kernel.spaces.load_all
root_capabilities: Any = None  # kernel.spaces.root_capabilities
profile_for: Any = None  # infra.sandbox.mounts.profile_for

_IMPORTS: dict[str, tuple[str, str]] = {
    "load_all": ("kernel.spaces", "load_all"),
    "root_capabilities": ("kernel.spaces", "root_capabilities"),
    "profile_for": ("infra.sandbox.mounts", "profile_for"),
}


def _bind(name: str) -> Any:
    value = globals()[name]
    if value is None:
        module, attr = _IMPORTS[name]
        value = getattr(importlib.import_module(module), attr)
        globals()[name] = value
    return value


def _space(space_id: SpaceId) -> Space:
    try:
        return _bind("load_all")()[space_id]
    except KeyError:
        raise VerifyError(f"no space {space_id!r}") from None


def _sha256(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


# Sampling for work that stays in the sandbox (seams §3.6). M0 is 1.0
# everywhere; the table changes when the audit sample exists to check what
# sampling misses (architecture §5).
SAMPLE_PROBABILITY: dict[EffectClass, float] = {
    "read": 1.0,
    "propose": 1.0,
    "act": 1.0,
}
# The most the Verifier takes from the objective's remaining, and the floor
# under which it cannot be funded (plan 11, Design: placeholders in the sense
# tech stack §8 uses for slice caps; the ledger of verdict costs corrects both).
VERIFIER_BUDGET = Budget(usd_micros=2_000_000)
VERIFIER_FLOOR = Budget(usd_micros=200_000)
LENGTH_CAPS: dict[str, int] = {"message": 4_000, "document": 40_000}  # characters
EXCERPT_CAP = 4_000
DETAIL_CAP = 2_000

# The draw behind `should_verify`, a module name so a test can pin it.
_draw = random.random


def should_verify(
    effect_class: EffectClass, *, leaves_space: bool
) -> tuple[bool, float]:
    """1.0 for anything that leaves the space and for `act`; else the sampling
    rate for sandbox-only work (seams §3.6, version 3 ruling 7). Returns
    (selected, probability)."""
    if leaves_space or effect_class == "act":
        return True, 1.0
    probability = SAMPLE_PROBABILITY[effect_class]
    return _draw() < probability, probability


async def record_sampling(
    conn,
    *,
    space: SpaceId,
    objective_id: ObjectiveId,
    effect_class: EffectClass,
    leaves_space: bool,
) -> tuple[bool, float]:
    """Draw and append `verification.sampled` (seams §4) on the caller's
    connection. Returns what `should_verify` returned."""
    selected, probability = should_verify(effect_class, leaves_space=leaves_space)
    await append(
        conn,
        space_id=space,
        type="verification.sampled",
        payload={
            "objective_id": objective_id,
            "effect_class": effect_class,
            "leaves_space": leaves_space,
            "selected": selected,
            "probability": probability,
        },
    )
    return selected, probability


def _tail(text: str, cap: int = DETAIL_CAP) -> str:
    return text if len(text) <= cap else text[-cap:]


# ---------------------------------------------------------------------------
# Deterministic checks (architecture §5; seams §1.6, §3.6)

# The code checks, run in /tmp/verify inside the verify container through
# the image's environment at /opt/venv (sandbox plan tasks 2 and 6). The
# read-only mount at /work stays the record; /tmp is writable under it
# (spike 06).
COPY_COMMAND = (
    "rm -rf /tmp/verify && mkdir -p /tmp/verify && cp -a /work/. /tmp/verify/"
)
BUILD_COMMAND = "cd /tmp/verify && uv run --frozen python -m compileall -q ."
TESTS_COMMAND = "cd /tmp/verify && uv run --frozen pytest -q -p no:cacheprovider"
CHECK_TIMEOUT = 600.0
CODE_CHECKS: tuple[tuple[str, str], ...] = (
    ("build", BUILD_COMMAND),
    ("tests", TESTS_COMMAND),
)


def check_result(name: str, passed: bool, output: str) -> CheckResult:
    """A `CheckResult` over the check's full output: the hash of all of it,
    the tail of it as `detail` (seams §1.6)."""
    return CheckResult(
        name=name, passed=passed, output_sha256=_sha256(output), detail=_tail(output)
    )


async def _code_checks(profile: SandboxProfile) -> list[CheckResult]:
    """A fresh container from the profile `profile_for` built, on hostonly
    like every profile at M0 (seams §1.9); destroyed before the caller reads
    a result, so the Verifier's own container is a second fresh one."""
    sandbox, _, _ = runs._bound()
    handle = await sandbox.create(profile)
    try:
        copied = await sandbox.exec(handle, COPY_COMMAND, timeout=CHECK_TIMEOUT)
        if copied.exit_status != 0:
            output = copied.stdout + copied.stderr
            return [check_result(name, False, output) for name, _ in CODE_CHECKS]
        results = []
        for name, command in CODE_CHECKS:
            r = await sandbox.exec(handle, command, timeout=CHECK_TIMEOUT)
            output = r.stdout + r.stderr
            if r.timed_out:
                output += f"\n{name} timed out after {CHECK_TIMEOUT:.0f} s"
            results.append(check_result(name, r.exit_status == 0, output))
        return results
    finally:
        await sandbox.destroy(handle)


async def _landed_report(conn, objective: Objective) -> Report:
    """The Report of the Executor brief whose report landed last, from the
    `report.landed` event the projection cites (seams §3.1, §4)."""
    if not objective.reports:
        raise NotVerifying(f"{objective.id} has no landed report")
    last = objective.reports[-1]
    events = await read_for(
        conn, space_id=objective.space, key="brief_id", value=last.brief_id
    )
    for e in events:
        if e.type == "report.landed" and e.id == last.event_id:
            return Report.model_validate(e.payload["report"])
    raise NotVerifying(f"{objective.id}: report.landed {last.event_id} is not there")


def _executor_brief(objective: Objective, verifier_ids: frozenset = frozenset()) -> str:
    """Plan 11, Tables: the `brief_id` of a checks or verdicts row is the
    Executor brief whose report landed last, because `owner_brief` names the
    Verifier once it is delegated. The projection folds every landed report
    including a Verifier's, so once the verdict has landed the last report
    is the Verifier's own and `verifier_ids` names the briefs to skip."""
    for ref in reversed(objective.reports):
        if ref.brief_id not in verifier_ids:
            return ref.brief_id
    raise NotVerifying(f"{objective.id} has no landed Executor report")


async def _verifier_brief_ids(conn, objective: Objective) -> frozenset[str]:
    """Every Verifier brief issued on the node, from `brief.issued`."""
    events = await read_for(
        conn, space_id=objective.space, key="objective_id", value=objective.id
    )
    return frozenset(
        e.payload["brief"]["id"]
        for e in events
        if e.type == "brief.issued"
        and (e.payload.get("brief") or {}).get("agent_class") == "Verifier"
    )


async def _record_checks(
    conn,
    objective: Objective,
    brief_id: str,
    checks: list[CheckResult],
    resolutions: dict[str, list[CitationResolution]],
) -> int:
    """Every result is a `checks` row, then one `checks.recorded` event on
    the caller's connection (seams §6, §4). Returns the event id."""
    for c in checks:
        rows = resolutions.get(c.name)
        await conn.execute(
            "INSERT INTO checks (space_id, objective_id, brief_id, name, passed, "
            "output_sha256, detail, resolutions) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                objective.space,
                objective.id,
                brief_id,
                c.name,
                c.passed,
                c.output_sha256,
                c.detail,
                (
                    None
                    if rows is None
                    else Jsonb([r.model_dump(mode="json") for r in rows])
                ),
            ),
        )
    return await append(
        conn,
        space_id=objective.space,
        type="checks.recorded",
        payload={
            "objective_id": objective.id,
            "brief_id": brief_id,
            "checks": [c.model_dump(mode="json") for c in checks],
        },
    )


async def run_checks(objective: Objective, snapshot: SnapshotRef) -> list[CheckResult]:
    """The deterministic checks of the artifact kind, in a fresh verify
    sandbox from `profile_for("verify", ..., snapshot=...)` for `code` and in
    the kernel process for `message` and `document`; rows in `checks` and
    the event `checks.recorded` durable before any prose is read (seams
    §3.6; the carried requirement of prereqs item 18)."""
    kind: ArtifactKind = objective.contract.artifact_kind
    if kind == "decision_brief":
        raise UnsupportedArtifactKind(
            "no M0 objective produces a decision_brief (seams §1.4)"
        )
    space = _space(objective.space)
    brief_id = _executor_brief(objective)
    profile = await _bind("profile_for")(
        "verify", space, brief_id, artifact_kind=kind, snapshot=snapshot
    )
    resolutions: dict[str, list[CitationResolution]] = {}
    try:
        if kind == "code":
            checks = await _code_checks(profile)
        else:
            async with await runs.connect() as conn:
                report = await _landed_report(conn, objective)
            checks, resolutions = await _artifact_checks(
                kind, space, profile, snapshot, report
            )
    finally:
        # The extraction is the checks' alone; `destroy` removed it for a
        # container that ran, and the host-side checks remove it here.
        await asyncio.to_thread(shutil.rmtree, profile.mount_source, True)
    async with await runs.connect() as conn:
        await _record_checks(conn, objective, brief_id, checks, resolutions)
        await conn.commit()
    return checks


def _artifact_text(profile: SandboxProfile, report: Report, kind: str) -> str | None:
    """The text of the Report's first artifact of `kind`, read from the
    extracted snapshot on the host, or None when the Report lists none."""
    for ref in report.artifact_refs:
        if ref.kind != kind:
            continue
        if not ref.path.startswith(MOUNT_TARGET + "/"):
            continue
        rel = ref.path[len(MOUNT_TARGET) + 1 :]
        path = Path(profile.mount_source) / rel
        if path.is_file():
            return path.read_text(errors="replace")
    return None


async def _artifact_checks(
    kind: ArtifactKind,
    space: Space,
    profile: SandboxProfile,
    snapshot: SnapshotRef,
    report: Report,
) -> tuple[list[CheckResult], dict[str, list[CitationResolution]]]:
    """The message and document checks over the artifact text, read from
    the extracted snapshot on the host (tasks 4 and 5)."""
    text = await asyncio.to_thread(_artifact_text, profile, report, kind)
    if text is None:
        output = f"the Report lists no {kind} artifact under {MOUNT_TARGET}"
        names = MESSAGE_CHECKS if kind == "message" else DOCUMENT_CHECKS
        return [check_result(name, False, output) for name in names], {}
    if kind == "message":
        return message_checks(text, space), {}
    return await document_checks(text, Path(profile.mount_source), space)


# --- message (architecture §5; seams §1.1, §1.6; architect's answer to question 1)

MESSAGE_CHECKS = ("recipient_allowed", "length", "no_operator_content")
DOCUMENT_CHECKS = ("citations_resolve", "length")


class ParsedMessage(NamedTuple):
    to: str | None
    subject: str | None
    body: str
    # header lines in the block other than one To: and one Subject:, by name
    other_headers: list[str]


def parse_message(text: str) -> ParsedMessage:
    """The header block is every line before the first blank line: `To:`
    and `Subject:` once each and nothing else (seams §1.6); the body is what
    follows the blank line. Any other line in the block is named in
    `other_headers`, so a `Cc:` or a second `To:` is judged rather than
    dropped. A text with neither header is all body, so `length` still
    measures something."""
    lines = text.split("\n")
    end = next((i for i, l in enumerate(lines) if not l.strip()), len(lines))
    to = subject = None
    others: list[str] = []
    for line in lines[:end]:
        name, sep, value = line.partition(":")
        key = name.strip().lower() if sep else ""
        if key == "to" and to is None:
            to = value.strip()
        elif key == "subject" and subject is None:
            subject = value.strip()
        else:
            others.append(name.strip() if sep else line.strip())
    if to is None and subject is None:
        return ParsedMessage(None, None, text, [])
    return ParsedMessage(to, subject, "\n".join(lines[end + 1 :]), others)


_RECIPIENT_SEPARATOR = re.compile(r"[,;]")


def _address(raw: str) -> str:
    """The bare address out of `Name <addr>` or `addr`."""
    raw = raw.strip()
    if "<" in raw and raw.endswith(">"):
        raw = raw[raw.rfind("<") + 1 : -1]
    return raw.strip()


def _recipients(raw: str) -> list[str]:
    """Every recipient a `To:` value names: the comma- or semicolon-separated
    parts, and a part that holds two addresses counts as two. Decided in
    build: the check passes one recipient and fails closed on more, because
    the audience rule is over the address, singular (seams §1.6)."""
    parts = [p.strip() for p in _RECIPIENT_SEPARATOR.split(raw) if p.strip()]
    if len(parts) == 1 and parts[0].count("@") > 1:
        return parts[0].split()
    return parts


def _same_address(a: str, b: str) -> bool:
    """Decided in the plan: case-insensitive on the domain, exact on the
    local part, the way mail systems treat them."""
    if "@" not in a or "@" not in b:
        return a == b
    la, da = a.rsplit("@", 1)
    lb, db = b.rsplit("@", 1)
    return la == lb and da.lower() == db.lower()


def recipient_allowed(text: str, space: Space) -> CheckResult:
    """Passes when the `To:` address is in `audience.addresses`, or its
    domain is in `audience.domains`, or it is a `mailto:` entry in
    `allowed_targets` (seams §1.1). The manifest is the value the kernel
    reads; the connector's `sender_domain` plays no part."""
    parsed = parse_message(text)
    if not parsed.to:
        return check_result("recipient_allowed", False, "no To: header line")
    if parsed.other_headers:
        return check_result(
            "recipient_allowed",
            False,
            "unexpected header line(s) "
            + ", ".join(parsed.other_headers)
            + "; the block holds To: and Subject: only",
        )
    recipients = _recipients(parsed.to)
    if len(recipients) != 1:
        return check_result(
            "recipient_allowed",
            False,
            f"To: names {len(recipients)} recipients; the check judges one",
        )
    address = _address(recipients[0])
    if "@" not in address:
        return check_result(
            "recipient_allowed", False, f"To: {address!r} is not an address"
        )
    domain = address.rsplit("@", 1)[1].lower()
    if any(_same_address(address, a) for a in space.audience.addresses):
        return check_result(
            "recipient_allowed", True, f"{address} is in audience.addresses"
        )
    if domain in {d.lower() for d in space.audience.domains}:
        return check_result(
            "recipient_allowed", True, f"{domain} is in audience.domains"
        )
    for target in space.allowed_targets:
        if target.startswith("mailto:") and _same_address(address, target[7:]):
            return check_result(
                "recipient_allowed", True, f"{address} is an allowed target"
            )
    return check_result(
        "recipient_allowed",
        False,
        f"{address} is not in the space's audience or its mailto: targets",
    )


def length_check(body: str, kind: str) -> CheckResult:
    cap = LENGTH_CAPS[kind]
    n = len(body)
    return check_result("length", n <= cap, f"{n} characters, cap {cap}")


def message_length(text: str) -> CheckResult:
    return length_check(parse_message(text).body, "message")


def operator_values(space: Space) -> dict[str, str]:
    """The values of the manifest's OPERATOR-labeled secrets, by name. At M0
    these are the only OPERATOR-class strings the kernel can name (the
    operator record is M1). Read here and never stored."""
    return {
        s.name: read_secret(s.keychain_name)
        for s in space.secrets
        if s.data_class == "OPERATOR"
    }


def no_operator_content(text: str, space: Space) -> CheckResult:
    """The artifact contains none of the OPERATOR secret values. The
    detail names the secret, never its value: no secret in any row."""
    leaked = sorted(
        name
        for name, value in operator_values(space).items()
        if value and value in text
    )
    if leaked:
        return check_result(
            "no_operator_content",
            False,
            "the artifact contains the value of OPERATOR secret " + ", ".join(leaked),
        )
    return check_result("no_operator_content", True, "no OPERATOR secret value found")


def message_checks(text: str, space: Space) -> list[CheckResult]:
    return [
        recipient_allowed(text, space),
        message_length(text),
        no_operator_content(text, space),
    ]


# --- document (architecture §5; seams §1.6; prereqs item 18, the carried requirement)

_MARKER = re.compile(r"\[(\d+)\]")
_ENTRY = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_HEADING = re.compile(r"^\s*(?:#+\s*)?references\s*:?\s*$", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s\)\]>\"']+")
_BACKTICK = re.compile(r"`([^`]+)`")
_QUOTED = re.compile(r"\"([^\"]+)\"", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
FETCH_TIMEOUT = 20.0


class Citation:
    """One numbered entry under `References`: a backticked path or a URL,
    and for a URL the passage it relies on, in quotes."""

    def __init__(self, number: int, entry: str):
        self.number = number
        self.entry = entry.strip()
        url = _URL.search(self.entry)
        path = _BACKTICK.search(self.entry)
        self.url = url.group(0).rstrip(".,;") if url else None
        self.path = path.group(1).strip() if path else None
        quoted = _QUOTED.search(self.entry)
        self.passage = quoted.group(1).strip() if quoted else None

    @property
    def target(self) -> str:
        return self.url or self.path or self.entry


def parse_citations(text: str) -> tuple[list[int], dict[int, Citation]]:
    """The `[n]` markers in the body and the numbered entries under the
    `References` heading, an entry running until the next numbered line."""
    lines = text.split("\n")
    body: list[str] = []
    entries: dict[int, Citation] = {}
    in_refs = False
    current: tuple[int, list[str]] | None = None
    for line in lines:
        if not in_refs:
            if _HEADING.match(line):
                in_refs = True
            else:
                body.append(line)
            continue
        m = _ENTRY.match(line)
        if m:
            if current is not None:
                entries[current[0]] = Citation(current[0], " ".join(current[1]))
            current = (int(m.group(1)), [m.group(2)])
        elif current is not None and line.strip():
            current[1].append(line.strip())
        elif line.startswith("#"):
            in_refs = False
            body.append(line)
    if current is not None:
        entries[current[0]] = Citation(current[0], " ".join(current[1]))
    markers = sorted({int(n) for n in _MARKER.findall("\n".join(body))})
    return markers, entries


def _local_roots(space: Space) -> list[Path]:
    roots = []
    for root in space.roots:
        if root.startswith(("~", "/")):
            roots.append(Path(root).expanduser())
    return roots


def _resolve_path(rel: str, mount: Path, space: Space) -> str | None:
    """The file's text when the path exists in the snapshot or under a local
    root of the space, else None. A path that climbs out of its base is
    refused."""
    if rel.startswith("/") or ".." in Path(rel).parts:
        return None
    for base in [mount, *_local_roots(space)]:
        candidate = base / rel
        try:
            if candidate.is_file():
                return candidate.read_text(errors="replace")
        except OSError:
            continue
    return None


def _visible_text(page: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", page))).strip()


async def _fetch(url: str) -> str | None:
    """The page's body text at status 200, fetched in the kernel process
    (the verify sandbox reaches no network of use), else None."""
    try:
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT, follow_redirects=True
        ) as client:
            r = await client.get(url)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    return _visible_text(r.text)


def _resolution(citation: str, excerpt: str | None) -> CitationResolution:
    """Resolved iff an excerpt is there (seams §1.6: required when resolved
    is True), capped at `EXCERPT_CAP`; the hash is over what is recorded."""
    if excerpt is None or not excerpt.strip():
        return CitationResolution(
            citation=citation, resolved=False, excerpt=None, excerpt_sha256=None
        )
    excerpt = excerpt[:EXCERPT_CAP]
    return CitationResolution(
        citation=citation,
        resolved=True,
        excerpt=excerpt,
        excerpt_sha256=_sha256(excerpt),
    )


async def resolve_citations(
    text: str, mount: Path, space: Space
) -> list[CitationResolution]:
    """One `CitationResolution` per citation, in number order: every `[n]`
    marker and every numbered entry. A path resolves to the file's text; a
    URL resolves when the fetch is 200 and the quoted passage occurs
    verbatim in the body text, whitespace folded; the excerpt is the
    passage. An entry with no excerpt to record does not resolve."""
    markers, entries = parse_citations(text)
    numbers = sorted(set(markers) | set(entries))
    out: list[CitationResolution] = []
    for n in numbers:
        entry = entries.get(n)
        if entry is None:
            out.append(_resolution(f"[{n}] has no entry under References", None))
            continue
        label = f"{n}. {entry.target}"
        if entry.url:
            if entry.passage is None:
                out.append(_resolution(label, None))
                continue
            page = await _fetch(entry.url)
            if page is not None and _WS.sub(" ", entry.passage) in page:
                out.append(_resolution(label, entry.passage))
            else:
                out.append(_resolution(label, None))
        elif entry.path:
            body = await asyncio.to_thread(_resolve_path, entry.path, mount, space)
            out.append(_resolution(label, body))
        else:
            out.append(_resolution(label, None))
    return out


def _citations_check(resolutions: list[CitationResolution]) -> CheckResult:
    if not resolutions:
        return check_result("citations_resolve", False, "the document cites nothing")
    lines = [
        f"{r.citation}: {'resolved' if r.resolved else 'unresolved'}"
        + (f" ({len(r.excerpt)} characters of excerpt)" if r.excerpt else "")
        for r in resolutions
    ]
    passed = all(r.resolved for r in resolutions)
    return check_result("citations_resolve", passed, "\n".join(lines))


async def document_checks(
    text: str, mount: Path, space: Space
) -> tuple[list[CheckResult], dict[str, list[CitationResolution]]]:
    resolutions = await resolve_citations(text, mount, space)
    checks = [_citations_check(resolutions), length_check(text, "document")]
    return checks, {"citations_resolve": resolutions}


# ---------------------------------------------------------------------------
# The blind slice (seams §1.5, §3.6; architecture §5; README "Verification is
# blind"). Seven blocks in a fixed order, every one PROJECT, byte-identical
# for the same inputs: no clock value, rows and invocations in a fixed order.

ROOT = Path(__file__).resolve().parents[1]
VOICE_PATH = ROOT / "VOICE.md"
PROMPTS_DIR = ROOT / "prompts" / "verifier"
DIFF_CAP = 40_000
BLOCK_KINDS = (
    "voice",
    "instruction",
    "criteria",
    "artifacts",
    "checks",
    "tool_log",
    "effect_ledger",
)
NONE = "none"


class ChecksMismatch(VerifyError):
    """The check list handed to the render differs from the rows recorded."""


# -- the store, behind names a screen with no store can pin to empty ---------


async def _events_of(objective: Objective) -> list[Event]:
    async with await runs.connect() as conn:
        return await read_for(
            conn, space_id=objective.space, key="objective_id", value=objective.id
        )


async def _tool_rows(brief_id: BriefId, generation: int) -> list[ToolLogRecord]:
    async with await runs.connect() as conn:
        return await api.read_tool_log(conn, brief_id, generation)


LEDGER_COLUMNS = (
    "id",
    "effect_id",
    "space_id",
    "objective_id",
    "brief_id",
    "generation",
    "action_type",
    "effect_class",
    "idempotency_key",
    "target",
    "payload_sha256",
    "payload",
    "event",
    "outcome_kind",
    "result",
    "error",
    "approval_id",
    "schema_version",
    "at",
)


async def _ledger_rows(objective: Objective) -> list[EffectLedgerRecord]:
    async with await runs.connect() as conn:
        cur = await conn.execute(
            f"SELECT {', '.join(LEDGER_COLUMNS)} FROM effect_ledger "
            "WHERE objective_id = %s ORDER BY id",
            (objective.id,),
        )
        rows = await cur.fetchall()
    return [EffectLedgerRecord(**dict(zip(LEDGER_COLUMNS, r))) for r in rows]


CHECK_COLUMNS = ("id", "name", "passed", "output_sha256", "detail", "resolutions")


async def _check_rows(objective: Objective, brief_id: BriefId) -> list[dict]:
    """The `checks` rows of the Executor brief, in insertion order."""
    async with await runs.connect() as conn:
        cur = await conn.execute(
            f"SELECT {', '.join(CHECK_COLUMNS)} FROM checks "
            "WHERE objective_id = %s AND brief_id = %s ORDER BY id",
            (objective.id, brief_id),
        )
        rows = await cur.fetchall()
    return [dict(zip(CHECK_COLUMNS, r)) for r in rows]


# -- pieces --------------------------------------------------------------------


def _block(kind: str, text: str, sources: list[str]) -> ContextBlock:
    return ContextBlock(
        kind=kind, data_class="PROJECT", text=text or NONE, sources=sources
    )


def prompt_texts(kind: str) -> tuple[list[str], list[str]]:
    """`seat.md` then `<kind>.md` from `prompts/verifier/` (task 7 writes
    them); texts and the paths they came from."""
    paths = [PROMPTS_DIR / "seat.md", PROMPTS_DIR / f"{kind}.md"]
    return [p.read_text() for p in paths], [str(p) for p in paths]


def _criteria_text(objective: Objective) -> str:
    c = objective.contract
    lines = [f"premise: {c.premise}", "non_goals:"]
    lines += [f"- {g}" for g in c.non_goals] or ["- none"]
    lines.append("success_criteria:")
    lines += [f"{i}. {s}" for i, s in enumerate(c.success_criteria, 1)]
    lines += [
        f"artifact_kind: {c.artifact_kind}",
        f"task_class: {c.task_class}",
        f"max_data_class: {c.ceilings.max_data_class}",
    ]
    return "\n".join(lines)


def _extract_archive(archive: Path, dest: Path) -> None:
    """Regular files and directories only; a link is skipped and a member
    that climbs out of the destination is refused (the sandbox plan's rule)."""
    with tarfile.open(archive) as tar:
        members = []
        for m in tar.getmembers():
            name = Path(m.name)
            if name.is_absolute() or ".." in name.parts:
                raise VerifyError(f"{archive} holds a member outside it: {name}")
            if m.issym() or m.islnk():
                continue
            members.append(m)
        tar.extractall(dest, members=members, filter="data")


async def _git(cwd: Path, *args: str) -> tuple[int, str]:
    r = await run_process("git", "-C", str(cwd), *args, timeout=120.0)
    return r.returncode, r.stdout.decode("utf-8", "replace")


async def _merge_base_ref(repo: Path) -> str | None:
    """The clone's default branch: `origin/HEAD` where the clone has one,
    else the first of origin/main, origin/master, main, master that exists."""
    code, out = await _git(
        repo, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"
    )
    if code == 0 and out.strip():
        return out.strip()
    for ref in ("origin/main", "origin/master", "main", "master"):
        code, _ = await _git(repo, "rev-parse", "-q", "--verify", ref)
        if code == 0:
            return ref
    return None


async def code_diff(repo: Path) -> str:
    """`git diff --stat` and `git diff` between the merge base with the
    default branch and HEAD, with the flags that print no dates or colors,
    so the same snapshot gives the same bytes; capped at `DIFF_CAP`."""
    if not (repo / ".git").exists():
        return "no repository in the snapshot"
    ref = await _merge_base_ref(repo)
    if ref is None:
        return "no default branch to diff against"
    code, base = await _git(repo, "merge-base", ref, "HEAD")
    if code != 0 or not base.strip():
        return f"no merge base between {ref} and HEAD"
    base = base.strip()
    _, head = await _git(repo, "rev-parse", "HEAD")
    _, stat = await _git(
        repo, "diff", "--no-color", "--no-ext-diff", "--stat", base, "HEAD"
    )
    _, diff = await _git(repo, "diff", "--no-color", "--no-ext-diff", base, "HEAD")
    text = f"merge base {base} ({ref}) to HEAD {head.strip()}\n\n{stat}\n{diff}"
    if len(text) > DIFF_CAP:
        text = text[:DIFF_CAP] + f"\n[cut at {DIFF_CAP} characters]"
    return text


async def _artifacts_text(
    objective: Objective, report: Report, snapshot: SnapshotRef | None
) -> str:
    kind = objective.contract.artifact_kind
    lines = ["artifact_refs:"]
    lines += [f"- {r.kind} {r.path} sha256={r.sha256}" for r in report.artifact_refs]
    if not report.artifact_refs:
        lines.append("- none")
    if snapshot is None:
        lines.append("\nsnapshot: none")
        return "\n".join(lines)
    tmp = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="cori-render-"))
    try:
        await asyncio.to_thread(_extract_archive, Path(snapshot.path), tmp)
        if kind == "code" and (tmp / ".git").exists():
            lines.append("\ndiff:\n" + await code_diff(tmp))
        else:
            # A message or document, or a code snapshot with no repository
            # to diff (the fixture screen): the artifact text itself.
            for ref in report.artifact_refs:
                if ref.kind != kind or not ref.path.startswith(MOUNT_TARGET + "/"):
                    continue
                path = tmp / ref.path[len(MOUNT_TARGET) + 1 :]
                if path.is_file():
                    body = await asyncio.to_thread(path.read_text, errors="replace")
                    lines.append(f"\n--- {ref.path} ---\n{body}")
                else:
                    lines.append(f"\n--- {ref.path} --- not in the snapshot")
    finally:
        await asyncio.to_thread(shutil.rmtree, tmp, True)
    return "\n".join(lines)


def _same_check(c: CheckResult, row: dict) -> bool:
    return (
        c.name == row["name"]
        and c.passed == row["passed"]
        and c.output_sha256 == row["output_sha256"]
        and c.detail == row["detail"]
    )


def _checks_text(checks: list[CheckResult], rows: list[dict]) -> str:
    if not checks:
        return "no checks recorded"
    resolutions = {
        r["name"]: r["resolutions"] for r in rows if r.get("resolutions") is not None
    }
    out = []
    for c in checks:
        out.append(f"{c.name}: {'passed' if c.passed else 'FAILED'}")
        if c.detail:
            out.append("  " + c.detail.replace("\n", "\n  "))
        for r in resolutions.get(c.name, []) or []:
            state = "resolved" if r.get("resolved") else "unresolved"
            out.append(f"  citation {r.get('citation')}: {state}")
            if r.get("excerpt"):
                out.append("    excerpt: " + str(r["excerpt"]).replace("\n", "\n    "))
    return "\n".join(out)


def _input_text(row: ToolLogRecord) -> str:
    inp = row.input or {}
    if row.tool == "bash":
        return f"command: {inp.get('command', '')}"
    if row.tool == "read":
        flag = " (artifact)" if inp.get("artifact") else ""
        return f"path: {inp.get('path', '')}{flag}"
    if row.tool == "write":
        return f"path: {inp.get('path', '')} content_sha256={inp.get('content_sha256', '')}"
    if row.tool == "edit":
        return (
            f"path: {inp.get('path', '')} old_sha256={inp.get('old_sha256', '')} "
            f"new_sha256={inp.get('new_sha256', '')} "
            f"lengths {inp.get('old_length', '')}->{inp.get('new_length', '')}"
        )
    parts = [f"{k}={inp[k]}" for k in sorted(inp)]
    return ", ".join(parts) if parts else "no input"


def render_invocation(inv: Invocation) -> str:
    """One invocation as a line: tool, input, exit status, artifact and its
    hash, duration, and how it closed. Hashes and never output text (seams
    §5.2): the Verifier re-executes what it needs."""
    s = inv.start
    parts = [f"seq {s.seq} {s.tool}: {_input_text(s)}"]
    e = inv.end
    if e is not None:
        parts.append(f"exit {e.exit_status}")
        if e.artifact:
            parts.append(f"artifact {e.artifact} sha256={e.artifact_sha256}")
        if e.stdout_sha256:
            parts.append(f"stdout_sha256={e.stdout_sha256}")
        if e.duration_ms is not None:
            parts.append(f"{e.duration_ms} ms")
    if inv.closed_by == "terminal":
        parts.append("closed by terminal")
    elif inv.closed_by is None:
        parts.append("open")
    return "; ".join(parts)


def render_tool_log(rows: list[ToolLogRecord]) -> tuple[list[str], list[str]]:
    """The invocations of one brief and generation in `seq` order, then the
    questions and answers as text. Returns (lines, sources)."""
    invs = sorted(api.invocations(rows), key=lambda i: i.start.seq or 0)
    lines = [render_invocation(i) for i in invs]
    sources = [f"{i.start.brief_id}#{i.start.seq}" for i in invs]
    talk = sorted(
        (r for r in rows if r.event in ("question", "answer")),
        key=lambda r: (r.seq or 0, r.event != "question", r.id or 0),
    )
    for r in talk:
        lines.append(f"{r.event} seq {r.seq}: {r.text or ''}")
    return lines, sources


def _executor_briefs(events: list[Event]) -> list[tuple[BriefId, int]]:
    """Every Executor brief issued on the node, in issue order, with its
    generation (seams §3.11, plan 11 critique 6)."""
    out = []
    for e in events:
        if e.type != "brief.issued":
            continue
        brief = e.payload.get("brief") or {}
        if brief.get("agent_class") == "Executor":
            out.append((brief["id"], int(brief.get("generation", 1))))
    return out


def render_ledger(rows: list[EffectLedgerRecord]) -> tuple[list[str], list[str]]:
    rows = sorted(rows, key=lambda r: (r.id or 0, r.effect_id, r.event))
    lines = [
        f"{r.action_type} @{r.effect_class} target={r.target} "
        f"key={r.idempotency_key} {r.event}"
        + (f" {r.outcome_kind}" if r.outcome_kind else "")
        for r in rows
    ]
    return lines, [str(r.id) for r in rows if r.id is not None]


def _latest(events: list[Event], type_: str, **match) -> Event | None:
    found = None
    for e in events:
        if e.type == type_ and all(e.payload.get(k) == v for k, v in match.items()):
            found = e
    return found


async def render_verifier_slice(
    objective: Objective, checks: list[CheckResult]
) -> ContextSlice:
    """The seven Verifier blocks of seams §1.5 in order, all PROJECT: voice,
    instruction, criteria, artifacts, checks, tool_log, effect_ledger. Never
    the gateway log, a Report's summary or evidence, or any node, path, or
    memory block. Same inputs give a byte-identical slice."""
    kind = objective.contract.artifact_kind
    brief_id = _executor_brief(objective)
    events = await _events_of(objective)

    voice = _block("voice", VOICE_PATH.read_text(), [str(VOICE_PATH)])
    texts, paths = prompt_texts(kind)
    instruction = _block("instruction", "\n\n".join(t.strip() for t in texts), paths)
    approved = _latest(events, "objective.approved")
    criteria = _block(
        "criteria", _criteria_text(objective), [str(approved.id)] if approved else []
    )

    landed = _latest(events, "report.landed", brief_id=brief_id)
    report = (
        Report.model_validate(landed.payload["report"])
        if landed is not None
        else Report(artifact_refs=[], evidence=[], assumption_deltas=[], summary="")
    )
    taken = _latest(events, "snapshot.taken", brief_id=brief_id)
    snapshot = SnapshotRef.model_validate(taken.payload["snapshot"]) if taken else None
    sources = [str(landed.id)] if landed else []
    if snapshot is not None:
        sources.append(snapshot.id)
    artifacts = _block(
        "artifacts", await _artifacts_text(objective, report, snapshot), sources
    )

    # The check list is compared with the rows read back every time, rows or
    # none: a list rendered against nothing recorded is refused too, so the
    # prose the Verifier reads is always the kernel's record.
    rows = await _check_rows(objective, brief_id)
    if len(rows) != len(checks) or not all(
        _same_check(c, r) for c, r in zip(checks, rows)
    ):
        raise ChecksMismatch(
            f"the check list for {objective.id} differs from its recorded rows "
            f"({len(checks)} given, {len(rows)} recorded)"
        )
    recorded = _latest(events, "checks.recorded", brief_id=brief_id)
    checks_block = _block(
        "checks", _checks_text(checks, rows), [str(recorded.id)] if recorded else []
    )

    log_lines: list[str] = []
    log_sources: list[str] = []
    for exec_brief, generation in _executor_briefs(events):
        lines, srcs = render_tool_log(await _tool_rows(exec_brief, generation))
        log_lines.append(f"brief {exec_brief} generation {generation}:")
        log_lines += ["  " + l for l in lines] or ["  no invocations"]
        log_sources += srcs
    tool_log = _block("tool_log", "\n".join(log_lines), log_sources)

    ledger_lines, ledger_sources = render_ledger(await _ledger_rows(objective))
    effect_ledger = _block("effect_ledger", "\n".join(ledger_lines), ledger_sources)

    blocks = [
        voice,
        instruction,
        criteria,
        artifacts,
        checks_block,
        tool_log,
        effect_ledger,
    ]
    return ContextSlice(
        space=objective.space, blocks=blocks, sha256=ContextSlice.digest(blocks)
    )


# ---------------------------------------------------------------------------
# Verdicts (seams §3.6, §4, §6; Round two, verifier rulings)


async def record_verdict(
    conn,
    brief: Brief | None,
    verdict: Verdict,
    sampled_with: float,
    *,
    model_ref: str,
    prompt_sha256: str,
    objective_id: ObjectiveId | None = None,
) -> None:
    """The `verdicts` row and the `verdict.recorded` event on the caller's
    connection; never a transition. `brief` is the Verifier's Brief, or None
    for a kernel verdict, and then `objective_id` names the node (the seams'
    signature carries no objective, and a kernel verdict has no Brief to
    carry it; decided in build). The row's and the payload's `brief_id` is
    the Executor brief judged (plan 11, Tables)."""
    if brief is not None:
        objective_id = brief.objective_id
    if objective_id is None:
        raise VerifyError("record_verdict needs a Verifier Brief or an objective id")
    objective = await tree.project(conn, objective_id)
    executor_brief = _executor_brief(
        objective, await _verifier_brief_ids(conn, objective)
    )
    await conn.execute(
        "INSERT INTO verdicts (space_id, objective_id, brief_id, verifier_brief_id, "
        "model_ref, prompt_sha256, outcome, predicted_failure, criteria, "
        "scope_findings, summary, sampled_with) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            objective.space,
            objective.id,
            executor_brief,
            None if brief is None else brief.id,
            model_ref,
            prompt_sha256,
            verdict.outcome,
            verdict.predicted_failure,
            Jsonb([c.model_dump(mode="json") for c in verdict.criteria]),
            Jsonb(list(verdict.scope_findings)),
            verdict.summary,
            sampled_with,
        ),
    )
    await append(
        conn,
        space_id=objective.space,
        type="verdict.recorded",
        payload={
            "objective_id": objective.id,
            "brief_id": executor_brief,
            "verifier_brief_id": None if brief is None else brief.id,
            "verdict": verdict.model_dump(mode="json"),
            "sampled_with": sampled_with,
            "model_ref": model_ref,
            "prompt_sha256": prompt_sha256,
        },
    )


def kernel_verdict(objective: Objective, checks: list[CheckResult]) -> Verdict:
    """The verdict the kernel records itself when a deterministic check
    failed: `fail`, every criterion unmet with a reason naming a failed
    check, `predicted_failure` 1.0, the failed checks' details as summary
    (Round two, verifier rulings; the carried requirement)."""
    failed = [c for c in checks if not c.passed]
    names = ", ".join(c.name for c in failed)
    summary = "\n\n".join(f"check {c.name} failed:\n{c.detail}" for c in failed)
    return Verdict(
        outcome="fail",
        predicted_failure=1.0,
        criteria=[
            CriterionResult(
                criterion=c, met=False, reason=f"check {names} failed"[:1000]
            )
            for c in objective.contract.success_criteria
        ],
        scope_findings=[],
        summary=summary[:2000],
    )


NOT_JUDGED = "not judged: the criterion strings did not match the contract"
SUMMARY_CAP = 2000


def abstain_from(verdict: Verdict, criteria: list[str], problems: list[str]) -> Verdict:
    """The abstain a mismatched Verdict becomes (Round two §3.5; plan 11,
    Design): `predicted_failure` and `scope_findings` kept, every contract
    criterion `met=None`, the problems first in the summary, then a blank
    line, then the model's summary cut to fit."""
    head = "\n".join(problems) + "\n\n"
    room = max(0, SUMMARY_CAP - len(head))
    return Verdict(
        outcome="abstain",
        predicted_failure=verdict.predicted_failure,
        criteria=[
            CriterionResult(criterion=c, met=None, reason=NOT_JUDGED) for c in criteria
        ],
        scope_findings=list(verdict.scope_findings),
        summary=(head + verdict.summary[:room])[:SUMMARY_CAP],
    )


async def validate_verdict_terminal(brief: Brief, terminal: Terminal) -> Terminal:
    """Called by `runs.run_brief` before `tree.land_report` on a Verdict
    terminal (Round two §3.5). The criterion strings must be the contract's,
    once each; a mismatch comes back as `abstain` with the problems first in
    the summary, so `land_report` lands it as AWAITING_APPROVAL/
    awaiting_decision and the person sees why. The criteria are rewritten
    as well, because the §1.6 validator makes `outcome` follow from them;
    the model's own criteria survive in the Verifier's terminal tool log
    row. Any other terminal passes through unchanged."""
    if terminal.outcome != "report" or not isinstance(terminal.report, Verdict):
        return terminal
    if brief.objective_id is None:
        return terminal
    async with await runs.connect() as conn:
        objective = await tree.project(conn, brief.objective_id)
    criteria = list(objective.contract.success_criteria)
    problems = validate_verdict(terminal.report, criteria=criteria)
    if not problems:
        return terminal
    return Terminal(
        outcome="report",
        report=abstain_from(terminal.report, criteria, problems),
        error=None,
    )


async def _transition(objective_id: ObjectiveId, state, reason) -> None:
    """One transition in its own transaction; Briefs the tree fenced go to
    `runs.stop` after the commit (seams Round two)."""
    async with await runs.connect() as conn:
        stopped = await tree.transition(conn, objective_id, state, reason)
        await conn.commit()
    if stopped:
        await runs.stop(stopped)


def verifier_capabilities(space_id: SpaceId) -> frozenset[Capability]:
    """`read@read` and `bash@read` scoped to the space, and nothing else
    (seams §1.5, version 3 ruling 6)."""
    return frozenset(
        Capability(name=name, effect_class="read", scope=space_id) for name in TOOLS
    )


async def verify_objective(
    objective_id: ObjectiveId, snapshot: SnapshotRef | None
) -> Verdict | None:
    """Plan 11, Control flow, the ten steps. Called by `runs.run_brief`
    after an Executor's terminal landed, its budget was released, its
    sandbox stopped and confirmed, and its worktree snapshotted."""
    # 1. the node
    async with await runs.connect() as conn:
        objective = await tree.project(conn, objective_id)
        if objective.state != "VERIFYING" or not objective.reports:
            raise NotVerifying(
                f"{objective_id} is {objective.state} with "
                f"{len(objective.reports)} report(s); verification needs "
                "VERIFYING and a report"
            )
        # 2. the sample: nothing leaves the space at M0 (ruling 9)
        selected, sampled_with = await record_sampling(
            conn,
            space=objective.space,
            objective_id=objective.id,
            effect_class=objective.contract.ceilings.max_effect_class,
            leaves_space=False,
        )
        await conn.commit()
    if not selected:
        await _transition(objective_id, "SUCCEEDED", "verification_sampled_out")
        return None
    kind = objective.contract.artifact_kind
    # 3 and 4. the profile and the checks; a snapshot the kernel wrote that
    # disagrees with itself, or no snapshot at all, is worker_failed
    try:
        if snapshot is None:
            raise VerifyError("an Executor report with no snapshot cannot be verified")
        checks = await run_checks(objective, snapshot)
    except Exception:
        await _transition(objective_id, "FAILED", "worker_failed")
        return None
    # 5. a failed check is a verdict, before any prose
    if any(not c.passed for c in checks):
        verdict = kernel_verdict(objective, checks)
        async with await runs.connect() as conn:
            await record_verdict(
                conn,
                None,
                verdict,
                sampled_with,
                model_ref="kernel",
                prompt_sha256=load_prompt(kind)[1],
                objective_id=objective_id,
            )
            await conn.commit()
        await _transition(objective_id, "FAILED", "verification_failed")
        return verdict
    # 6. the blind slice
    slice_ = await render_verifier_slice(objective, checks)
    # 7. the Verifier's Brief from the node's remaining
    space = _space(objective.space)
    async with await runs.connect() as conn:
        headroom = await tree.remaining(conn, objective_id)
        if headroom.usd_micros < VERIFIER_FLOOR.usd_micros:
            await conn.rollback()
            funded = False
        else:
            funded = True
            budget = Budget(
                usd_micros=min(headroom.usd_micros, VERIFIER_BUDGET.usd_micros)
            )
            brief = await tree.delegate(
                conn,
                DelegateRequest(
                    objective_id=objective_id,
                    agent_class="Verifier",
                    budget=budget,
                    capabilities=verifier_capabilities(objective.space),
                    sandbox_profile="verify",
                    report_schema="Verdict",
                    max_data_class="PROJECT",
                    context_slice=slice_,
                    instruction=INSTRUCTION,
                    snapshot=snapshot,
                ),
                issuer=_bind("root_capabilities")(space),
                parent_brief=None,
                spaces={objective.space: space},
            )
            await conn.commit()
    if not funded:
        await _transition(objective_id, "FAILED", "budget_exhausted")
        return None
    # 8. the loop; `run_brief` validates the terminal and lands it, and
    # `tree.land_report` is the one writer of the verdict transition
    terminal = await runs.run_brief(brief)
    if terminal.outcome != "report" or not isinstance(terminal.report, Verdict):
        return None
    # 9. the row and the event for the Terminal `run_brief` landed
    async with await runs.connect() as conn:
        await record_verdict(
            conn,
            brief,
            terminal.report,
            sampled_with,
            model_ref=brief.model_ref,
            prompt_sha256=load_prompt(kind)[1],
        )
        await conn.commit()
    # 10.
    return terminal.report
