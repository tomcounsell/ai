"""Inject env vars into a launchd plist's ``EnvironmentVariables`` dict.

Used by ``scripts/install_*.sh`` to bake env vars into the generated plist
at install time. launchd-managed services start with a minimal env, and the
worker/bridge/etc. need at least ``VALOR_VAULT_DIR`` (and a handful of
operational vars) to bootstrap.

Two injection modes, selected by the vault's TCC status:

* **Lean injection (vault NOT on a TCC path, e.g. ~/.valor, /opt/valor)**:
  inject only an allowlist of operational vars (``VALOR_VAULT_DIR``,
  ``VALOR_PROJECT_KEY``, ``VALOR_LAUNCHD``, ``ACTIVE_PROJECTS``,
  ``SERVICE_LABEL_PREFIX``, ``PATH``, ``HOME``). Secrets stay in the
  ``0600`` ``<vault>/.env`` and are loaded by the worker at runtime via
  ``load_dotenv``. The plist contains no secrets and stays at its default
  ``0644`` permissions.

* **Full injection (vault on a TCC path — ~/Desktop, ~/Documents,
  ~/iCloud Drive)**: bake the entire ``.env`` into the plist. macOS TCC
  blocks ``open()``/``stat()`` on these paths from launchd-spawned
  processes (terminal-spawned ones have consent; launchd-spawned ones do
  not), so the worker cannot read ``.env`` at runtime. We pay for the
  workaround with a wider on-disk surface for secrets, and tighten that
  by ``chmod 0600`` on the plist after injection.

Behavior in both modes:
    * Always also injects ``VALOR_VAULT_DIR`` from ``os.environ`` if set,
      even when it's not in the .env file (e.g. user sets it in ``.zshrc``).
    * Idempotent: existing keys in the plist's ``EnvironmentVariables`` are
      preserved; only missing keys are added.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import stat
import sys
from pathlib import Path

# Operational vars baked into the plist regardless of TCC status. These are
# all non-secret (paths, project keys, feature flags) and are required for
# launchd-spawned processes to find their vault and identify themselves.
LEAN_INJECTION_ALLOWLIST: frozenset[str] = frozenset(
    {
        "VALOR_VAULT_DIR",
        "VALOR_PROJECT_KEY",
        "VALOR_LAUNCHD",
        "ACTIVE_PROJECTS",
        "SERVICE_LABEL_PREFIX",
        "PATH",
        "HOME",
    }
)


def _path_is_tcc_restricted(path: Path) -> bool:
    """True if ``path`` lives under a macOS TCC / FileProvider-gated dir.

    Restricted roots (matching ``config.settings.VaultSettings.path_is_tcc_restricted``):
      * ``~/Desktop``, ``~/Documents`` — classic TCC categories.
      * ``~/iCloud Drive`` — Finder alias for iCloud Drive.
      * ``~/Library/Mobile Documents`` — canonical iCloud Drive mount.
      * ``~/Library/CloudStorage`` — Sonoma+ FileProvider mount point for
        iCloud / Dropbox / OneDrive / Google Drive.

    Paths and roots are resolved (symlinks followed) before prefix comparison.
    Duplicated from settings.py so this script has no import-time dependency
    on the project package (runs from install shell scripts in recovery
    paths where the venv may not even be installed).
    """
    home = Path.home()
    restricted_roots = (
        home / "Desktop",
        home / "Documents",
        home / "iCloud Drive",
        home / "Library" / "Mobile Documents",
        home / "Library" / "CloudStorage",
    )

    def _safe_resolve(p: Path) -> Path:
        try:
            return p.resolve(strict=False)
        except (OSError, RuntimeError):
            return p.absolute()

    resolved = _safe_resolve(path)
    for root in restricted_roots:
        resolved_root = _safe_resolve(root)
        if resolved == resolved_root or resolved_root in resolved.parents:
            return True
    return False


def inject(
    plist_path: Path,
    env_file: Path | None,
    *,
    os_environ: dict[str, str],
    vault_dir: Path | None = None,
) -> tuple[int, bool]:
    """Inject env vars into ``plist_path``.

    Returns ``(count_added, secrets_baked_in)``. ``secrets_baked_in`` is
    True when the caller used the full-injection (TCC) path — the caller
    should ``chmod 0600`` the plist in that case to close the
    world-readable hole on secrets at rest.

    ``env_file`` is parsed via ``python-dotenv``. ``VALOR_VAULT_DIR`` is
    also pulled from ``os_environ`` (passed in for testability) so that
    machines where the var lives in shell rc rather than ``.env`` still
    get it baked.

    ``vault_dir`` controls the TCC check: if provided, lean vs full mode
    is decided from this path. If ``None``, we infer from ``os_environ``
    (``VALOR_VAULT_DIR``) and ``env_file`` parent, falling back to lean.
    """
    # Decide injection mode -------------------------------------------------
    resolved_vault: Path | None = vault_dir
    if resolved_vault is None:
        env_var_vault = os_environ.get("VALOR_VAULT_DIR")
        if env_var_vault:
            resolved_vault = Path(env_var_vault).expanduser()
        elif env_file is not None:
            resolved_vault = env_file.expanduser().parent

    secrets_mode = (
        resolved_vault is not None and _path_is_tcc_restricted(resolved_vault.expanduser())
    )

    # Gather candidate keys/values from .env --------------------------------
    env_vars: dict[str, str] = {}
    if env_file is not None:
        try:
            from dotenv import dotenv_values

            parsed = dotenv_values(env_file)
            env_vars = {k: v for k, v in parsed.items() if v is not None}
        except Exception as e:
            print(f"Warning: could not parse {env_file}: {e}", file=sys.stderr)

    # In lean mode, filter env_vars to the operational allowlist
    if not secrets_mode:
        env_vars = {k: v for k, v in env_vars.items() if k in LEAN_INJECTION_ALLOWLIST}

    # VALOR_VAULT_DIR from os.environ (shell rc) is always allowed through
    shell_vault = os_environ.get("VALOR_VAULT_DIR")
    if shell_vault:
        env_vars.setdefault("VALOR_VAULT_DIR", shell_vault)

    # Merge into the plist --------------------------------------------------
    with open(plist_path, "rb") as f:
        plist = plistlib.load(f)

    existing = plist.setdefault("EnvironmentVariables", {})
    injected = 0
    for key, value in env_vars.items():
        if key not in existing:
            existing[key] = value
            injected += 1

    # Atomic write — mode is set at file creation via fchmod *before* any
    # bytes are written, then the temp file is renamed over the destination
    # in one POSIX-atomic step. This closes the window where the plist
    # would briefly be world-readable (0644) between dump and chmod.
    target_mode = stat.S_IRUSR | stat.S_IWUSR if secrets_mode else 0o644
    _write_plist_atomic(plist_path, plist, target_mode)

    return injected, secrets_mode


def _write_plist_atomic(path: Path, body: dict, mode: int) -> None:
    """Write ``body`` to ``path`` atomically with the requested permission mode.

    Creates a temp file in the same directory (required for ``os.replace``
    atomicity guarantees on POSIX), sets the mode via ``os.fchmod`` *before*
    writing, dumps the plist, then renames into place. If anything fails
    mid-stream the temp file is removed and the destination is untouched.
    """
    import tempfile

    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            plistlib.dump(body, f)
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--plist", type=Path, required=True, help="Path to the plist to modify")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Path to a .env file to merge into EnvironmentVariables",
    )
    parser.add_argument(
        "--vault-dir",
        type=Path,
        default=None,
        help=(
            "Vault directory. Used to decide lean (non-TCC) vs full (TCC) "
            "injection. Defaults to VALOR_VAULT_DIR env var, then the --env-file "
            "parent."
        ),
    )
    args = parser.parse_args()

    n, secrets = inject(
        args.plist,
        args.env_file,
        os_environ=dict(os.environ),
        vault_dir=args.vault_dir,
    )
    mode_label = "full (TCC-restricted vault — chmod 0600 applied)" if secrets else "lean"
    print(f"  Injected {n} env vars into plist ({mode_label})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
