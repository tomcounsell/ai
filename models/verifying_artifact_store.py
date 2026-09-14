"""Verifying content store for improvement artifacts.

Popoto's ``FilesystemStore`` (a pip dependency, never edited in place) stores a
``ContentField`` value under a content-addressed reference,
``$CF:{sha256}:{relative_path}``. Its ``load()`` checks the hash on the **live**
path and, when that check fails or the live file is gone, falls back to the
versioned archive at ``.versions/{prefix}/{hash}{ext}`` — and returns those
bytes **without re-hashing them** (``stores/filesystem.py``, the
``# Fall back to version archive`` branch).

For a document chunk that fallback is harmless. For an evaluation artifact it is
not. The whole point of ``docs/plans/recursive-self-improvement.md`` is that a
verdict is only worth what its evidence is worth, and an artifact that silently
loads as something other than what was hashed at write time turns a corrupted
file into a scored result. A corrupted artifact must **invalidate** an
evaluation, never quietly become one.

``VerifyingArtifactStore`` re-hashes on **every** load path, archive included,
and raises :class:`ArtifactIntegrityError` on a mismatch. Callers treat that
exception as "this evaluation cannot be scored", which is the honest outcome.

The store also keeps improvement artifacts under their own retention root
(``POPOTO_IMPROVEMENT_CONTENT_PATH``, defaulting to
``~/.popoto/improvement_content``) rather than mixing them into the shared
popoto content directory, so retention and export/import policy for evaluation
evidence can differ from ordinary content without a path heuristic. The
default lives outside any repo checkout on purpose: a container image rebuild
destroys the checkout, and with it every artifact a trial gathered.

Durable-state topology for cloud sandboxes (lane 7, #3274): each sandbox runs
a sandbox-local Redis and exports its evidence and artifacts to the durable
store named here, rather than all sandboxes sharing one network-reachable
Redis. spike-4 found ``RedisSettings.url`` defaults to
``redis://localhost:6379/0`` with no TLS, password, or ``rediss://`` handling
anywhere in settings, while the ``worker:registered_pid:*`` liveness
convention presumes one shared Redis. The shared option was refused because
reaching it needs transport security that does not exist: TLS and auth support
in settings, network boundaries around the machine-global production Redis,
and credential distribution to hosts that cannot renew anything themselves.
That is named work for its own task, not a footnote to a sandbox trial. The
local option costs something too: the dashboard cannot read a sandbox's Redis
directly, so the export path is load-bearing and every teardown is gated on a
verified export that fails closed. The cross-process round trip in
``tests/unit/test_artifact_retention_root.py`` is the phase-2 proof:
``_default_base_path`` reads the env var fresh on every call, so a second
local process stands in for the container.
"""

from __future__ import annotations

import os

from popoto.stores.filesystem import FilesystemStore


class ArtifactIntegrityError(Exception):
    """An artifact's bytes do not hash to the digest its reference names.

    Raised by :meth:`VerifyingArtifactStore.load`. An evaluation that catches
    this must record an integrity failure and refuse to produce a verdict —
    an unverifiable artifact is not weak evidence, it is no evidence.
    """


def _default_base_path() -> str:
    """Resolve the retention root for improvement artifacts.

    Reads the env var fresh on every call (not through the cached settings
    singleton) so a test can point the store at a tmp directory after
    ``config.settings`` has already been constructed elsewhere in the process.
    Without the override the root is ``~/.popoto/improvement_content``: outside
    any repo checkout so an image rebuild cannot destroy it, and beside (never
    inside) the shared popoto content directory.
    """
    override = os.environ.get("POPOTO_IMPROVEMENT_CONTENT_PATH")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".popoto", "improvement_content")


class VerifyingArtifactStore(FilesystemStore):
    """``FilesystemStore`` that re-hashes on every load, archive path included.

    Overrides only ``load()``. ``save()``, ``delete()``, and the path helpers
    are the parent's, so references written by this store are ordinary popoto
    references and stay readable by any tool that knows the format.
    """

    def __init__(self, base_path: str | None = None, extension: str = ".txt"):
        super().__init__(base_path=base_path or _default_base_path(), extension=extension)

    def load(self, reference: str) -> bytes:
        """Load and verify content, or raise.

        Args:
            reference: ``$CF:{sha256}:{relative_path}``.

        Returns:
            The content bytes, proven to hash to the digest in ``reference``.

        Raises:
            ArtifactIntegrityError: The bytes found do not match the digest.
                This covers the archive fallback the parent returns unverified.
            FileNotFoundError: Neither the live path nor the archive holds the
                artifact.
        """
        content_hash, relative_path = self._parse_reference(reference)
        live_path = os.path.join(self.base_path, relative_path)
        version_path = self._version_path(content_hash)

        live_bytes: bytes | None = None
        if os.path.exists(live_path):
            with open(live_path, "rb") as f:
                live_bytes = f.read()
            if self._compute_hash(live_bytes) == content_hash:
                return live_bytes

        if os.path.exists(version_path):
            with open(version_path, "rb") as f:
                archived = f.read()
            # The parent returns these bytes unverified. Verify them.
            if self._compute_hash(archived) == content_hash:
                return archived
            raise ArtifactIntegrityError(
                f"Archived artifact for {reference} does not match its digest "
                f"(archive path {version_path}). The evaluation citing it cannot be scored."
            )

        if live_bytes is not None:
            # The live file exists but is not what was written, and there is no
            # archive to fall back to. That is corruption, not absence.
            raise ArtifactIntegrityError(
                f"Live artifact for {reference} does not match its digest "
                f"(live path {live_path}) and no verified archive copy exists. "
                "The evaluation citing it cannot be scored."
            )

        raise FileNotFoundError(
            f"Artifact not found for reference {reference}. Checked: {live_path}, {version_path}"
        )

    def exists(self, reference: str) -> bool:
        """True only when :meth:`load` would return bytes.

        The parent's ``exists()`` mirrors its own unverified archive fallback,
        so it can answer True for an artifact this store refuses to load. This
        override keeps the guarantee the parent documents — ``exists()`` True
        implies ``load()`` succeeds — under the stricter rule.
        """
        try:
            self.load(reference)
        except (ArtifactIntegrityError, FileNotFoundError, ValueError):
            return False
        return True


#: Module-level singleton shared by ImprovementExperiment.manifest and
#: ImprovementEvaluation.judge_records.
verifying_artifact_store = VerifyingArtifactStore()
