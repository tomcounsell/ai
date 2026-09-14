"""The one sanctioned ``op item create`` path on the improvement path (Decision 8).

``tools/improvement_resources.py`` only reads the vault; this module is the
one writer, gated on this file's own existence (``_probe_vault_write`` in
that module). The value never appears in argv, a log line, the returned
result, or the ``ImprovementEvidence`` row -- only a title and a
``sha256:<hex>`` fingerprint ever leave this function.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

Runner = Callable[[list[str]], "subprocess.CompletedProcess"]


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


@dataclass(frozen=True)
class VaultWriteResult:
    item_title: str
    fingerprint: str | None
    state: str  # "created" | "refused"
    detail: str = ""


def _fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_credential(
    title: str,
    value: str,
    *,
    category: str = "API Credential",
    vault: str = "m-valor",
    project_key: str = "valor",
    runner: Runner | None = None,
) -> VaultWriteResult:
    """Write ``value`` to a new 1Password item titled ``title``.

    Empty or whitespace-only ``title``/``value`` refuse before any process
    is spawned. Any ``op`` failure (non-zero exit, missing binary) refuses
    with the first line of stderr, which is secret-free by construction --
    ``op``'s own error output never echoes the value it was given.
    """
    if not title or not title.strip() or not value or not value.strip():
        return VaultWriteResult(title, None, "refused", detail="EMPTY")

    if runner is None:
        runner = _default_runner

    template = {
        "title": title,
        "category": category,
        "fields": [{"id": "credential", "type": "CONCEALED", "value": value}],
    }

    tmp_dir = Path(tempfile.mkdtemp(prefix="valor-vault-write-"))
    template_path = tmp_dir / "item.json"
    try:
        template_path.write_text(json.dumps(template))
        template_path.chmod(0o600)
        completed = runner(
            ["op", "item", "create", "--vault", vault, "--template", str(template_path)]
        )
    except FileNotFoundError:
        return VaultWriteResult(title, None, "refused", detail="op binary not found")
    except subprocess.CalledProcessError as e:
        first_line = (e.stderr or "").splitlines()[0] if e.stderr else str(e)
        return VaultWriteResult(title, None, "refused", detail=first_line)
    except Exception as e:
        logger.debug("[vault-write] unexpected error creating %r: %s", title, e)
        return VaultWriteResult(title, None, "refused", detail=type(e).__name__)
    finally:
        try:
            template_path.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except OSError:
            pass

    if not isinstance(completed, subprocess.CompletedProcess) or completed.returncode != 0:
        stderr = getattr(completed, "stderr", "") or ""
        first_line = stderr.splitlines()[0] if stderr else "op item create failed"
        return VaultWriteResult(title, None, "refused", detail=first_line)

    fingerprint = _fingerprint(value)
    try:
        from models.improvement_evidence import ImprovementEvidence

        ImprovementEvidence.record_once(
            project_key,
            "resource_acquired",
            source_ref=f"vault:{title}",
            text=title,
            detail=fingerprint,
        )
    except Exception as e:
        logger.debug("[vault-write] evidence record failed (non-fatal): %s", e)

    return VaultWriteResult(title, fingerprint, "created")


def render_resource_acquired_section(rows) -> str:
    """Render a ``resource_acquired`` evidence list as a digest section.

    ``rows`` is any iterable of objects with ``text`` (the item title) and
    ``detail`` (the fingerprint) attributes -- ``ImprovementEvidence`` rows,
    or anything shaped like one for a test.
    """
    rows = list(rows)
    if not rows:
        return "No resources acquired."
    lines = ["Resources acquired:"]
    for row in rows:
        title = getattr(row, "text", None) or "(untitled)"
        fingerprint = getattr(row, "detail", None) or "(no fingerprint)"
        lines.append(f"- {title} ({fingerprint})")
    return "\n".join(lines)
