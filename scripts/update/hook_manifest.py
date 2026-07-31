"""Loader for the static hook-registration manifest (`.claude/hooks/manifest.toml`).

This module is the single parser for the declarative hook manifest that
replaces the hand-maintained project `.claude/settings.json` `hooks` block
and the hardcoded `_SDLC_HOOK_DEFS` list in `scripts/update/hardlinks.py`.
See `docs/plans/hook-registration-manifest-dispatcher.md` for the full design.

`load_hook_manifest()` is fail-closed: a missing, malformed, or empty
manifest raises `HookManifestError` rather than silently returning an empty
list, because generation must never silently wipe a `settings.json` hooks
block from a broken/empty manifest read.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MANIFEST_PATH = Path(__file__).resolve().parents[2] / ".claude" / "hooks" / "manifest.toml"

_REQUIRED_FIELDS = ("manifest_id", "event", "matcher", "script", "timeout", "scope", "blocking")
_VALID_SCOPES = ("project", "global")


class HookManifestError(ValueError):
    """Raised when the hook manifest is missing, malformed, or empty."""


@dataclass(frozen=True)
class HookDeclaration:
    """A single statically-declared hook registration.

    Mirrors one `[[hook]]` table in `.claude/hooks/manifest.toml`.
    """

    manifest_id: str
    event: str
    matcher: str
    script: str
    timeout: int
    scope: str
    blocking: bool
    args: tuple[str, ...] = ()


def load_hook_manifest(manifest_path: Path | None = None) -> list[HookDeclaration]:
    """Parse `.claude/hooks/manifest.toml` into typed `HookDeclaration`s.

    Args:
        manifest_path: override path (defaults to
            `.claude/hooks/manifest.toml` relative to the repo root).

    Returns:
        Declarations in file declaration order (order is load-bearing — see
        the manifest's own header comment and the plan's "Technical
        Approach" section on the empty-diff-on-regen guarantee).

    Raises:
        HookManifestError: the file is missing, is not valid TOML, has no
            `[[hook]]` entries, or an entry is missing a required field /
            has an invalid `scope`. Duplicate `manifest_id`s also raise,
            since manifest_id is the generators' add/update/remove key and
            must be unique.
    """
    path = manifest_path or DEFAULT_MANIFEST_PATH

    if not path.exists():
        raise HookManifestError(f"Hook manifest not found: {path}")

    try:
        raw = path.read_bytes()
    except OSError as e:
        raise HookManifestError(f"Failed to read hook manifest {path}: {e}") from e

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise HookManifestError(f"Hook manifest {path} is not valid TOML: {e}") from e
    except UnicodeDecodeError as e:
        raise HookManifestError(f"Hook manifest {path} is not valid UTF-8: {e}") from e

    entries = data.get("hook")
    if not entries or not isinstance(entries, list):
        raise HookManifestError(
            f"Hook manifest {path} declares no [[hook]] entries — refusing to "
            "generate from an empty manifest (would silently wipe the hooks block)."
        )

    declarations: list[HookDeclaration] = []
    seen_ids: set[str] = set()

    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise HookManifestError(f"Hook manifest {path} entry #{i} is not a table: {entry!r}")

        missing = [f for f in _REQUIRED_FIELDS if f not in entry]
        if missing:
            raise HookManifestError(
                f"Hook manifest {path} entry #{i} ({entry.get('manifest_id', '?')!r}) "
                f"is missing required field(s): {missing}"
            )

        manifest_id = entry["manifest_id"]
        if not isinstance(manifest_id, str) or not manifest_id:
            raise HookManifestError(
                f"Hook manifest {path} entry #{i} has an invalid manifest_id: {manifest_id!r}"
            )
        if manifest_id in seen_ids:
            raise HookManifestError(
                f"Hook manifest {path} declares duplicate manifest_id: {manifest_id!r}"
            )
        seen_ids.add(manifest_id)

        scope = entry["scope"]
        if scope not in _VALID_SCOPES:
            raise HookManifestError(
                f"Hook manifest {path} entry {manifest_id!r} has invalid scope "
                f"{scope!r} (must be one of {_VALID_SCOPES})"
            )

        timeout = entry["timeout"]
        if not isinstance(timeout, int) or isinstance(timeout, bool):
            raise HookManifestError(
                f"Hook manifest {path} entry {manifest_id!r} has a non-integer timeout: {timeout!r}"
            )

        blocking = entry["blocking"]
        if not isinstance(blocking, bool):
            raise HookManifestError(
                f"Hook manifest {path} entry {manifest_id!r} has non-boolean blocking: {blocking!r}"
            )

        args = entry.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise HookManifestError(
                f"Hook manifest {path} entry {manifest_id!r} has a non-string-list args: {args!r}"
            )

        declarations.append(
            HookDeclaration(
                manifest_id=manifest_id,
                event=entry["event"],
                matcher=entry["matcher"],
                script=entry["script"],
                timeout=timeout,
                scope=scope,
                blocking=blocking,
                args=tuple(args),
            )
        )

    return declarations
