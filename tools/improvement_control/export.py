"""Dump and restore the ``improve:*`` namespace against lane 7's export
contract (``POPOTO_IMPROVEMENT_CONTENT_PATH``, ``docs/infra/improvement-cloud-execution.md``).

The case list to export comes from the ORM (``ImprovementCase`` rows for the
project), never a keyspace scan -- the projection knows which cases exist
even though it isn't the authority for their state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tools.improvement_control import keys
from tools.improvement_control.intents import list_intents

#: Bumped only on an incompatible archive-shape change, never on a per-field
#: addition -- an importer refuses a mismatch rather than guessing.
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ImportRefusal:
    reason: str  # "NAMESPACE_NOT_EMPTY" | "SCHEMA_MISMATCH" | "ARCHIVE_NOT_FOUND" | "FOREIGN_KEY"


def _control_redis():
    from utils.redis_client import text_redis

    return text_redis()


def _case_ids(project_key: str) -> list[str]:
    from models.improvement_case import ImprovementCase

    return [c.id for c in ImprovementCase.query.filter(project_key=project_key)]


def export_namespace(project_key: str, root: Path) -> Path:
    """Write ``{root}/exports/{utc_stamp}/{namespace.json, artifacts.json}``.

    An empty namespace (no cases) still writes a valid archive with zero
    cases, so ``import`` has something well-formed to refuse or accept.
    """
    r = _control_redis()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(root) / "exports" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    namespace: dict = {
        "schema": SCHEMA_VERSION,
        "project_key": project_key,
        "exported_at": stamp,
        "cases": {},
        "slots": r.hgetall(keys.slots_key(project_key)),
        "ns_pause": r.hgetall(keys.pause_key(project_key)),
        "unit2": {
            k: r.hgetall(k) for k in r.scan_iter(match=f"improve:{project_key}:budget:unit2:*")
        },
    }
    digests: list[dict] = []
    for case_id in _case_ids(project_key):
        head = r.hgetall(keys.head_key(project_key, case_id))
        journal = r.lrange(keys.journal_key(project_key, case_id), 0, -1)
        intent_ids = sorted(r.smembers(keys.intents_set_key(project_key, case_id)))
        namespace["cases"][case_id] = {
            "head": head,
            "journal": journal,
            "intents": intent_ids,
            "intent_hashes": {
                aid: r.hgetall(keys.intent_key(project_key, case_id, aid)) for aid in intent_ids
            },
        }
        # `artifacts.json` is the tail's own `artifact_ref` fields, one row
        # per entry that carries one; the journal is the only index of what
        # the verifying store holds for a case.
        for raw_entry in journal:
            entry = json.loads(raw_entry)
            ref = entry.get("artifact_ref")
            if ref:
                digests.append(
                    {
                        "case_id": case_id,
                        "action_id": entry.get("action_id"),
                        "artifact_ref": ref,
                        "payload_digest": entry.get("payload_digest"),
                    }
                )

    (out_dir / "namespace.json").write_text(json.dumps(namespace, indent=2, sort_keys=True))
    (out_dir / "artifacts.json").write_text(
        json.dumps(
            {"schema": SCHEMA_VERSION, "project_key": project_key, "digests": digests}, indent=2
        )
    )
    return out_dir


def _namespace_is_empty(project_key: str) -> bool:
    """Whether the *control-plane* namespace (``improve:{project_key}:*``) has
    ever been written to -- not whether an ``ImprovementCase`` ORM row exists.
    The projection is a separate store; a case can exist there with no
    control-namespace state at all (lane 2b's cases predate this lane)."""
    return not bool(_control_redis().exists(keys.schema_key(project_key)))


def import_namespace(
    archive: Path, *, project_key: str, force: bool = False
) -> ImportRefusal | None:
    """Restore ``namespace.json`` from ``archive`` (a directory from :func:`export_namespace`).

    Refuses onto a non-empty namespace unless ``force``, refuses a schema
    mismatch unconditionally, and refuses (``FOREIGN_KEY``) an archive whose
    ``project_key`` differs from the argument or whose unit-2 section names a
    key outside ``improve:{project_key}:budget:unit2:``, before writing
    anything. Every restored key goes through the package's own builders or
    :func:`keys.assert_control_key`; unit-2 hashes get their retention TTL
    re-applied. With ``force``, each archived case's journal, intents set,
    and intent hashes are deleted before they are restored, so a restore
    onto live state replaces history rather than appending to it. Returns
    ``None`` on success.
    """
    from tools.paid_inference_meter import KEY_EXPIRY_SECONDS

    namespace_path = Path(archive) / "namespace.json"
    try:
        data = json.loads(namespace_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return ImportRefusal("ARCHIVE_NOT_FOUND")
    if data.get("schema") != SCHEMA_VERSION:
        return ImportRefusal("SCHEMA_MISMATCH")
    if data.get("project_key") != project_key:
        return ImportRefusal("FOREIGN_KEY")
    unit2_prefix = f"improve:{project_key}:budget:unit2:"
    unit2 = data.get("unit2", {}) or {}
    if any(not isinstance(k, str) or not k.startswith(unit2_prefix) for k in unit2):
        return ImportRefusal("FOREIGN_KEY")
    if not force and not _namespace_is_empty(project_key):
        return ImportRefusal("NAMESPACE_NOT_EMPTY")

    r = _control_redis()
    r.set(keys.schema_key(project_key), str(SCHEMA_VERSION))
    if data.get("slots"):
        r.hset(keys.slots_key(project_key), mapping=data["slots"])
    if data.get("ns_pause"):
        r.hset(keys.pause_key(project_key), mapping=data["ns_pause"])
    for key, mapping in unit2.items():
        if mapping:
            r.hset(keys.assert_control_key(key), mapping=mapping)
            r.expire(keys.assert_control_key(key), KEY_EXPIRY_SECONDS)
    for case_id, case_data in data.get("cases", {}).items():
        journal_key = keys.journal_key(project_key, case_id)
        intents_key = keys.intents_set_key(project_key, case_id)
        if force:
            live_intents = set(r.smembers(intents_key)) | set(case_data.get("intent_hashes", {}))
            r.delete(journal_key, intents_key)
            for aid in live_intents:
                r.delete(keys.intent_key(project_key, case_id, aid))
        if case_data.get("head"):
            r.hset(keys.head_key(project_key, case_id), mapping=case_data["head"])
        for entry in case_data.get("journal", []):
            r.rpush(journal_key, entry)
        for action_id in case_data.get("intents", []):
            r.sadd(intents_key, action_id)
        for action_id, intent_hash in case_data.get("intent_hashes", {}).items():
            if intent_hash:
                r.hset(keys.intent_key(project_key, case_id, action_id), mapping=intent_hash)
    return None


def restored_intents_for_verification(project_key: str, case_id: str):
    """Convenience: the round-trip test's own re-read, through the sanctioned
    per-case reader rather than the raw archive dict."""
    return list_intents(project_key, case_id)
