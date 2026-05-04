"""Known Electron app bundle IDs.

Electron apps lazily build their accessibility tree, so an AX node ref
returned by ``get_window_state`` can become invalid before the next call
even if the window itself stays open. ``tools.computer`` re-queries the
AX tree before each action when the target's bundle_id matches one of
these (Race 3 mitigation, plan rev3 C3 / rev4).

To extend: add the bundle_id string to :data:`ELECTRON_BUNDLE_IDS`. No
behavior change is needed beyond that -- the helper :func:`is_electron_bundle`
just looks up membership.
"""

from __future__ import annotations

# Bundle IDs of common Electron-based macOS apps the agent might drive.
# These are the apps where the AX tree staleness race is observed.
ELECTRON_BUNDLE_IDS: frozenset[str] = frozenset(
    {
        "com.tinyspeck.slackmacgap",  # Slack
        "com.microsoft.VSCode",  # VS Code
        "org.telegram.desktop",  # Telegram Desktop
        "com.hnc.Discord",  # Discord
        "com.electron.notion",  # Notion
        "com.figma.Desktop",  # Figma
        "com.spotify.client",  # Spotify
    }
)


def is_electron_bundle(bundle_id: str) -> bool:
    """Return True if the bundle_id is a known Electron app.

    Args:
        bundle_id: The macOS bundle identifier (e.g. ``com.microsoft.VSCode``).

    Returns:
        True if the bundle is in :data:`ELECTRON_BUNDLE_IDS`. False otherwise,
        including when ``bundle_id`` is None or empty.
    """
    if not bundle_id:
        return False
    return bundle_id in ELECTRON_BUNDLE_IDS
