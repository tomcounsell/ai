"""Frozen memory-corpus export, canonical digest, and restore (#3216).

The project's memory corpus is exported once per evaluation run through
popoto's ORM-native transfer API. The corpus identity is
:func:`canonical_corpus_digest`, never a hash of the raw JSONL bytes:
``export_records`` output is non-deterministic across processes (record
order follows set-iteration order and the manifest carries a fresh
``exported_at`` on every call), so a raw-bytes comparison would fire
``infra_failure`` on every real corpus.

The canonical form splits the manifest off line one, pops ``exported_at``
by name, dumps the remaining manifest with ``sort_keys=True``, sorts the
record lines by each record's ``key``, and dumps each record body with
``sort_keys=True`` so intra-record field order cannot differ. The remaining
manifest is compared byte-equal between arms separately, so a future
volatile manifest key surfaces as a mismatch rather than being absorbed.

Every Redis touch in this module goes through the Popoto ORM
(``Memory.export_records`` / ``Memory.import_records``). No raw Redis
command is issued here.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def parse_manifest(jsonl_text: str) -> dict:
    """Return the export manifest (line one) with all original fields."""
    first_line = jsonl_text.splitlines()[0]
    return json.loads(first_line)


def canonical_manifest(jsonl_text: str) -> str:
    """Return the manifest minus ``exported_at``, dumped with sorted keys.

    Popping is by name: any other future volatile key is NOT absorbed here,
    so it surfaces as an inter-arm mismatch at run time.
    """
    manifest = parse_manifest(jsonl_text)
    manifest.pop("exported_at", None)
    return json.dumps(manifest, sort_keys=True)


def canonical_corpus_digest(jsonl_text: str) -> str:
    """Return the stable corpus identity digest for one JSONL export.

    Stable across processes where the raw bytes are not: ``exported_at``
    is excluded by name, record order is normalized by key, and every
    object is dumped with ``sort_keys=True``.
    """
    manifest_canon = canonical_manifest(jsonl_text)
    bodies = []
    for line in jsonl_text.splitlines()[1:]:
        if not line.strip():
            continue
        body = json.loads(line)
        bodies.append((body.get("key", ""), json.dumps(body, sort_keys=True)))
    bodies.sort(key=lambda item: item[0])
    payload = manifest_canon + "\n" + "\n".join(body for _, body in bodies)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _git_sha() -> str:
    """Best-effort current git SHA; ``unknown`` on failure (cf. snapshot.py)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


@dataclass
class CorpusExport:
    """One frozen corpus with its identity and artifact references."""

    project_key: str
    digest: str
    jsonl_text: str
    record_count: int
    manifest: dict
    manifest_canon: str
    exported_at: str
    git_sha: str
    corpus_ref: str
    provenance_ref: str
    provenance: dict


def export_corpus(project_key: str, *, store=None) -> CorpusExport:
    """Export the project's memory corpus and freeze it to the artifact store.

    Returns a :class:`CorpusExport` carrying the canonical digest (the
    corpus identity for the run), the raw JSONL, and references to the
    stored corpus bytes plus a provenance header (record count from the
    manifest's ``matched_count``, ISO timestamp, git SHA), following
    ``tools/memory_eval/snapshot.py``'s provenance shape.
    """
    from models.memory import Memory

    if store is None:
        from models.verifying_artifact_store import VerifyingArtifactStore

        store = VerifyingArtifactStore()

    result = Memory.export_records(project_key=project_key)
    jsonl_text = result.data or ""
    manifest = parse_manifest(jsonl_text)
    digest = canonical_corpus_digest(jsonl_text)
    manifest_canon = canonical_manifest(jsonl_text)
    exported_at = datetime.now(UTC).isoformat()
    git_sha = _git_sha()
    provenance = {
        "project_key": project_key,
        "record_count": manifest.get("matched_count", result.matched_count),
        "exported_at": exported_at,
        "git_sha": git_sha,
        "corpus_digest": digest,
    }
    corpus_ref = store.save(
        jsonl_text.encode("utf-8"),
        key=f"corpus-{project_key}-{digest[:16]}",
        model_class_name="ImprovementCorpus",
    )
    provenance_ref = store.save(
        json.dumps(provenance, sort_keys=True).encode("utf-8"),
        key=f"corpus-{project_key}-{digest[:16]}-provenance",
        model_class_name="ImprovementCorpus",
    )
    return CorpusExport(
        project_key=project_key,
        digest=digest,
        jsonl_text=jsonl_text,
        record_count=provenance["record_count"],
        manifest=manifest,
        manifest_canon=manifest_canon,
        exported_at=exported_at,
        git_sha=git_sha,
        corpus_ref=corpus_ref,
        provenance_ref=provenance_ref,
        provenance=provenance,
    )


def restore_corpus(
    jsonl_text: str | bytes, *, on_conflict: str = "overwrite", on_embedding_mismatch: str = "carry"
):
    """Restore a frozen corpus into the current process's pool via the ORM.

    Preserves keys, saves with ``skip_auto_now=True`` (so the relevance
    timestamps carry rather than resetting to import time), and carries the
    exported vectors instead of re-embedding: no Ollama call, deterministic.
    """
    from models.memory import Memory

    if isinstance(jsonl_text, bytes):
        jsonl_text = jsonl_text.decode("utf-8")
    return Memory.import_records(
        io.StringIO(jsonl_text),
        on_conflict=on_conflict,
        on_embedding_mismatch=on_embedding_mismatch,
    )
