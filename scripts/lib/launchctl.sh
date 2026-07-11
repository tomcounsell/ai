# Shared launchd bootstrap helper — fail-soft recovery for the errno-5 race.
#
# `launchctl bootstrap` can fail with `Bootstrap failed: 5: Input/output error`
# even when the underlying service is fine to load. This is the well-known
# macOS launchd race: the service label is still registered/draining in the
# `gui/<uid>/` domain when `bootstrap` runs — immediately after a `bootout`,
# or because a prior crash left a stale half-load. launchd refuses the fresh
# bootstrap and returns errno 5 rather than waiting out the drain.
#
# `launchctl kickstart -k <domain>/<label>` is the atomic recovery: it kills
# and restarts an already-registered label without requiring the drain to
# complete first, so it succeeds exactly where the errno-5 bootstrap failed.
# This mirrors the pattern already established in `scripts/remote-update.sh`
# (kickstart-first on the loaded branch, bootstrap+kickstart-fallback on the
# not-loaded branch) and `scripts/update/service.py::install_log_rotate_agent`
# (bootstrap rc-check then kickstart -k fallback).
#
# See issue #2013 (this hardening) and issue #2017/PR #2018 (the sibling fix
# that hardened `remote-update.sh`'s worker-restart not-loaded branch, the
# canonical reference pattern for this helper).
#
# This helper deliberately does NOT bootout the label itself: an unconditional
# internal bootout would kill and recreate an already-loaded, healthy service
# on every call, even at sites that never did that before. Call sites that
# already booted out before their bare bootstrap keep doing so immediately
# before calling this helper (matches the `remote-update.sh` pattern exactly,
# where the caller — not a shared primitive — owns the bootout decision).
#
# Usage:
#   source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/launchctl.sh"
#   launchctl bootout "$domain/$label" 2>/dev/null || true   # only if the site already did this
#   launchctl_bootstrap_fail_soft "gui/$(id -u)" "$plist_path" "$label"
#
# Returns 0 if the service ends up loaded (bootstrap succeeded, or bootstrap
# failed but kickstart -k recovered it). Returns 1 and prints a distinct,
# greppable WARNING to stderr only when BOTH bootstrap and kickstart fail —
# a genuine double-failure, not masked as success.
launchctl_bootstrap_fail_soft() {
    local domain="$1" plist="$2" label="$3"

    if launchctl bootstrap "$domain" "$plist" 2>/dev/null; then
        return 0
    fi

    # bootstrap failed (commonly errno 5 = label still registered/draining).
    # kickstart -k is the atomic recovery — same primitive remote-update.sh
    # prefers, and correct here because an errno-5 failure specifically means
    # the label IS already registered in the domain.
    if launchctl kickstart -k "${domain}/${label}" 2>/dev/null; then
        return 0
    fi

    echo "WARNING: launchctl bootstrap+kickstart failed for ${label}" >&2
    return 1
}
