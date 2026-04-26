"""Multi-format document converter for the knowledge pipeline.

Converts binary/non-markdown sources (PDF, DOCX, PPTX, XLSX, HTML, images,
etc.) into `.md` sidecar files that the existing indexer can consume
unchanged. The source file remains canonical; the sidecar is a regenerable
search proxy.

Two code paths, selected by the ``MARKITDOWN_LLM_MODEL`` environment variable:

1. **Subprocess path (default):** ``markitdown <source> -o <tmp>``. Handles
   90%+ of realistic vault content — PDF/DOCX/XLSX/HTML/EPUB/MSG with a text
   layer need no LLM. ``check=False`` + explicit returncode/stderr inspection
   so ``ConversionError`` carries a truncated stderr snippet on failure.

2. **Python API path (LLM configured):** Lazy ``import markitdown`` inside
   the function. Builds an OpenAI-compat client pointed at Anthropic's
   ``/v1/`` endpoint using ``ANTHROPIC_API_KEY`` + ``HAIKU``. Only invoked
   when ``MARKITDOWN_LLM_MODEL`` is set AND the extension is in
   ``LLM_BENEFICIAL_EXTENSIONS``.

The Python API path has a probe-and-cache mechanism
(``_llm_path_available``): on first invocation the converter constructs
the client and pings it with a 1-token request. If the probe fails we log
ONCE at WARNING and route all subsequent image/PPTX conversions through
the subprocess path. There is NO OpenAI (``gpt-4o-mini``) fallback — that
surface was explicitly eliminated (see plan C5 / Risk 3).

Generated sidecars carry YAML frontmatter with ``source_hash`` (sha256),
``source_path``, ``generated_by: markitdown``, ``generated_at``,
``regenerated_at``, and ``llm_model`` (resolved ``config.models.HAIKU``
value or ``none``). Idempotency: the converter reads the sidecar
frontmatter before running and skips when the source hash is unchanged.

Audio formats (``.mp3``, ``.wav``, ``.m4a``) are deliberately absent from
``CONVERTIBLE_EXTENSIONS`` — markitdown's audio path uses an
unauthenticated Google Web Speech API key (see plan spike-2), which is
unacceptable for consulting material.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from config.models import HAIKU

logger = logging.getLogger(__name__)

# Formats markitdown can extract without an LLM (subprocess path). Images
# are listed here so standalone screenshots dropped into the vault trigger
# the watcher (the subprocess path produces a filename-only markdown; the
# Python API path produces a vision-generated description when
# MARKITDOWN_LLM_MODEL is set). Audio formats are deliberately excluded —
# markitdown's audio converter uses Google Web Speech unauthenticated.
CONVERTIBLE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".html",
        ".htm",
        ".msg",
        ".epub",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
    }
)

# Subset that benefits from the LLM vision path when MARKITDOWN_LLM_MODEL
# is set. PPTX for embedded image OCR; standalone images for captioning.
LLM_BENEFICIAL_EXTENSIONS: frozenset[str] = frozenset(
    {".pptx", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
)

_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

# 20MB cap on image files routed to a vision API.
_IMAGE_MAX_BYTES = 20_000_000

_SUBPROCESS_TIMEOUT_SECONDS = 120

_STDERR_SNIPPET_LIMIT = 500

# Probe cache: None = not yet probed, True = probe succeeded, False = probe
# failed (subprocess fallback active for the rest of this process).
_llm_path_available: bool | None = None


class ConversionError(RuntimeError):
    """Raised when markitdown fails to produce a usable sidecar."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_existing_frontmatter(sidecar: Path) -> dict[str, str] | None:
    """Return the YAML frontmatter dict of an existing sidecar, or None.

    Deliberately a narrow parser — only reads the leading ``---`` block and
    matches ``key: value`` lines. We do not want a full YAML dependency for
    a 6-field header.
    """
    if not sidecar.exists():
        return None
    try:
        with sidecar.open("r", encoding="utf-8") as f:
            first_line = f.readline().rstrip("\n")
            if first_line != "---":
                return None
            result: dict[str, str] = {}
            for line in f:
                line = line.rstrip("\n")
                if line == "---":
                    return result
                if ":" in line:
                    key, _, value = line.partition(":")
                    result[key.strip()] = value.strip()
    except OSError:
        return None
    return None


def _frontmatter_block(
    source_hash: str,
    source_path: Path,
    llm_model: str,
    generated_at: str,
    regenerated_at: str,
) -> str:
    return (
        "---\n"
        f"source_hash: {source_hash}\n"
        f"source_path: {source_path.name}\n"
        "generated_by: markitdown\n"
        f"generated_at: {generated_at}\n"
        f"regenerated_at: {regenerated_at}\n"
        f"llm_model: {llm_model}\n"
        "---\n"
    )


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write(sidecar: Path, body: str) -> None:
    """Write body to sidecar atomically via tmp + os.replace."""
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f".{sidecar.name}.tmp.",
        dir=str(sidecar.parent),
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp_name, sidecar)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _resolve_markitdown_binary() -> str | None:
    """Return the absolute path to the markitdown CLI, or None if missing.

    The worker/bridge process runs under ``.venv/bin/python`` which does NOT
    automatically put the venv's ``bin/`` on ``$PATH``. Prefer the sibling
    ``markitdown`` next to ``sys.executable`` so we resolve to the extra
    installed in *this* interpreter's venv regardless of PATH.
    """
    venv_bin = Path(sys.executable).parent / "markitdown"
    if venv_bin.is_file() and os.access(venv_bin, os.X_OK):
        return str(venv_bin)
    # Fall back to whatever is on PATH (system install, dev convenience).
    return shutil.which("markitdown")


def _run_subprocess(source: Path) -> str:
    """Invoke `markitdown <source>` and return stdout on success.

    Raises ConversionError on non-zero return, empty stdout with
    non-empty stderr, or timeout. `check=False` is required so we can
    inspect returncode + stderr and attach a truncated snippet — per plan
    C1 Implementation Note.
    """
    binary = _resolve_markitdown_binary()
    if binary is None:
        raise ConversionError(
            "markitdown CLI not found — install the [knowledge] extra (uv sync --all-extras)"
        )
    try:
        result = subprocess.run(
            [binary, str(source)],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ConversionError(
            f"markitdown binary vanished between resolve and exec: {binary}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ConversionError(
            f"markitdown timed out after {_SUBPROCESS_TIMEOUT_SECONDS}s on {source}"
        ) from exc

    stdout = result.stdout or ""
    stderr = (result.stderr or "").strip()
    stderr_snippet = stderr[:_STDERR_SNIPPET_LIMIT]

    if result.returncode != 0:
        raise ConversionError(f"markitdown exit {result.returncode} on {source}: {stderr_snippet}")
    if len(stdout) == 0 and len(stderr) > 0:
        raise ConversionError(f"markitdown produced empty output on {source}: {stderr_snippet}")
    return stdout


def _probe_llm_client() -> bool:
    """Build the OpenAI-compat client against Anthropic and send a 1-token ping.

    Returns True if the client constructs and a minimal completion returns a
    response; False on any exception. Called at most once per process.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning(
            "MARKITDOWN_LLM_MODEL is set but ANTHROPIC_API_KEY is missing — "
            "subprocess fallback for all image/PPTX conversions"
        )
        return False
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning(
            "openai package not importable — subprocess fallback for all image/PPTX conversions"
        )
        return False
    try:
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.anthropic.com/v1/",
        )
        client.chat.completions.create(
            model=HAIKU,
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
    except Exception as exc:
        logger.warning(
            "markitdown LLM probe failed: %s — falling back to subprocess "
            "path for all image/PPTX conversions",
            exc,
        )
        return False
    return True


def _llm_enabled(ext: str) -> bool:
    """Decide whether to attempt the Python API path.

    Probes once and caches the result in the module-level
    ``_llm_path_available`` flag; subsequent calls skip straight to the
    cached decision.
    """
    global _llm_path_available
    if not os.environ.get("MARKITDOWN_LLM_MODEL"):
        return False
    if ext not in LLM_BENEFICIAL_EXTENSIONS:
        return False
    if _llm_path_available is None:
        _llm_path_available = _probe_llm_client()
    return _llm_path_available


def _run_llm_api(source: Path) -> str:
    """Run markitdown's Python API with the Haiku vision path.

    Caller must have already confirmed `_llm_enabled(ext)` is True.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ConversionError("ANTHROPIC_API_KEY required for markitdown LLM path")

    try:
        import markitdown as _mid
        from openai import OpenAI
    except ImportError as exc:
        raise ConversionError(f"markitdown/openai import failed: {exc}") from exc

    client = OpenAI(api_key=api_key, base_url="https://api.anthropic.com/v1/")
    md = _mid.MarkItDown(llm_client=client, llm_model=HAIKU)
    try:
        result = md.convert(str(source))
    except Exception as exc:
        raise ConversionError(f"markitdown.convert failed on {source}: {exc}") from exc
    text = getattr(result, "text_content", None) or ""
    if not text.strip():
        raise ConversionError(f"markitdown.convert produced empty text for {source}")
    return text


def convert_to_sidecar_with_status(
    source_path: Path | str,
    *,
    force: bool = False,
) -> tuple[Path | None, str]:
    """Convert a source and return (sidecar, status).

    ``status`` is one of:

    - ``"written"`` — markitdown ran and a fresh sidecar was atomically
      written. ``sidecar`` is the resulting path.
    - ``"skipped_hash"`` — an existing sidecar already matched the source
      hash, so no work was done. ``sidecar`` is the existing path.
    - ``"skipped_other"`` — nothing was attempted: ``.md`` input, an
      unconvertible extension, source missing, zero-byte source, or an
      image over the size guard. ``sidecar`` is ``None``.

    Used by callers like ``valor-ingest --scan`` that need to distinguish
    "actually converted N files" from "found N hash-matched sidecars".

    Raises :class:`ConversionError` when markitdown errors out, identical
    to :func:`convert_to_sidecar`.
    """
    source = Path(source_path).resolve()
    ext = source.suffix.lower()

    # Loop-prevention guard: any .md input is itself a sidecar or a
    # hand-written note — never feed back through the converter.
    if ext == ".md":
        return None, "skipped_other"

    if ext not in CONVERTIBLE_EXTENSIONS:
        return None, "skipped_other"

    if not source.exists():
        logger.debug("convert: source vanished: %s", source)
        return None, "skipped_other"

    try:
        size = source.stat().st_size
    except OSError as exc:
        logger.warning("convert: stat failed on %s: %s", source, exc)
        return None, "skipped_other"

    if size == 0:
        logger.debug("convert: skipping zero-byte source: %s", source)
        return None, "skipped_other"

    if ext in _IMAGE_EXTENSIONS and size > _IMAGE_MAX_BYTES:
        logger.warning(
            "skipping %s: %d bytes exceeds %d image size limit",
            source,
            size,
            _IMAGE_MAX_BYTES,
        )
        return None, "skipped_other"

    sidecar = source.with_name(source.name + ".md")

    # Content-hash idempotency: read the sidecar's frontmatter before
    # running markitdown. If the source hash matches, skip entirely.
    source_hash = _sha256_file(source)
    existing = _read_existing_frontmatter(sidecar)
    if not force and existing and existing.get("source_hash") == source_hash:
        logger.debug("convert: hash unchanged, skipping %s", source)
        return sidecar, "skipped_hash"

    # Choose path.
    use_llm = _llm_enabled(ext)
    if use_llm:
        try:
            body = _run_llm_api(source)
            llm_model = HAIKU
        except ConversionError as exc:
            logger.warning(
                "LLM path failed on %s (%s) — falling back to subprocess",
                source,
                exc,
            )
            body = _run_subprocess(source)
            llm_model = "none"
    else:
        body = _run_subprocess(source)
        llm_model = "none"

    now = _now_iso()
    generated_at = existing.get("generated_at") if existing else None
    if not generated_at:
        generated_at = now
    regenerated_at = now

    frontmatter = _frontmatter_block(
        source_hash=source_hash,
        source_path=source,
        llm_model=llm_model,
        generated_at=generated_at,
        regenerated_at=regenerated_at,
    )
    _atomic_write(sidecar, frontmatter + body)
    logger.info("markitdown: wrote %s (llm=%s)", sidecar, llm_model)
    return sidecar, "written"


def convert_to_sidecar(
    source_path: Path | str,
    *,
    force: bool = False,
) -> Path | None:
    """Convert a binary/non-markdown source into a ``.md`` sidecar.

    Returns the sidecar ``Path`` on success or hash-match skip. Returns
    ``None`` when the source is itself a ``.md`` (no conversion needed),
    the source has no convertible extension, an image exceeds the 20MB
    guard, or the source is missing/zero-byte.

    Backward-compatible thin wrapper over
    :func:`convert_to_sidecar_with_status` — collapses the (path, status)
    pair into the original ``Path | None`` return shape. Callers that
    need to distinguish "actually wrote" from "skipped due to hash"
    (``--scan`` reporting) should call
    :func:`convert_to_sidecar_with_status` directly.

    Raises :class:`ConversionError` when markitdown errors out. Callers
    running inside the watcher must wrap this in try/except to honor the
    "never crash the bridge" contract.
    """
    sidecar, _status = convert_to_sidecar_with_status(source_path, force=force)
    return sidecar


def reset_llm_probe_cache() -> None:
    """Test helper — reset the probe cache so tests can re-exercise it."""
    global _llm_path_available
    _llm_path_available = None
