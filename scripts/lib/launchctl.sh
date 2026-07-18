# Shared launchd bootstrap helper — fail-soft recovery for the errno-5 race.
#
# `launchctl bootstrap` can fail with `Bootstrap failed: 5: Input/output error`
# even when the underlying service is fine to load. There are two distinct
# errno-5 shapes:
#
#   1. Drain race: the service label is still registered/draining in the
#      `gui/<uid>/` domain when `bootstrap` runs — immediately after a `bootout`,
#      or because a prior crash left a stale half-load. `kickstart -k` recovers
#      this shape (the label IS registered, so it can be kicked).
#   2. Fresh-install transient: `bootstrap` hits a transient errno-5 but the
#      label is NOT yet registered. `kickstart -k` cannot recover this (nothing
#      registered to kick). The fix is to simply retry the `bootstrap` — the
#      transient clears (this is what cleared the 2026-07-15 incident manually).
#
# The helper therefore runs TWO independent loops:
#
#   Loop A (bootstrap retry, BEFORE load): on a transient errno-5
#     (`5: Input/output error` in captured stderr) it sleeps and re-`bootstrap`s,
#     up to LAUNCHCTL_BOOTSTRAP_RETRIES total attempts. Only errno-5 retries; any
#     other non-zero failure breaks out immediately to the kickstart fallback so
#     a genuine plist error is not masked behind N sleeps.
#
#   kickstart fallback (kept, single-shot): if loop A never loaded, fall back to
#     one `launchctl kickstart -k <domain>/<label>` — the drain-race recovery.
#     This is deliberately NOT retried (the drain race is a single-shot recovery).
#
#   Loop B (live-PID probe, AFTER load, OPT-IN): if a non-empty 4th `verify-pid`
#     argument is passed, a SEPARATE bounded probe loop re-runs
#     `launchctl print gui/<uid>/<label>` and checks for a `pid = <N>` line,
#     sleeping between attempts, up to LAUNCHCTL_BOOTSTRAP_RETRIES times. This
#     loop NEVER re-invokes `bootstrap` or `kickstart` — the label is already
#     registered, so re-bootstrapping cannot reproduce errno-5 and re-kickstarting
#     is explicitly forbidden. A "successful" bootstrap does not prove the process
#     actually spawned; loop B waits out a slow-forking resident process and treats
#     a persistently missing PID as a not-live failure.
#
# Resident-vs-scheduled opt-in rule: only RESIDENT services (RunAtLoad + KeepAlive,
# e.g. worker, reflection-worker, email-bridge, bridge, and worker-start which reuses
# the worker label) pass `verify-pid`. SCHEDULED services (StartCalendarInterval /
# StartInterval, e.g. nightly-tests, sdlc-reflection, update-cron, and BOTH watchdogs —
# worker-watchdog at StartInterval 90 and bridge-watchdog at StartInterval 60) have no
# persistent PID between runs and must NOT pass it — a blanket PID check would falsely
# fail every scheduled service (aborting a real install at a `|| exit 1` site).
#
# Tunable, env-overridable constants (provisional/tunable — grain of salt):
#   LAUNCHCTL_BOOTSTRAP_RETRIES     (default 3) — total bootstrap attempts / PID probes.
#   LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP (default 2) — seconds between attempts (loops A and B).
# `scripts/update/service.py::install_worker` reads the SAME env-var names/defaults for
# its bootstrap-retry parity (its PID check stays single-shot by design).
#
# See issue #2104 (bootstrap retry + opt-in live-PID verify), issue #2013 (this
# helper's introduction) and issue #2017/PR #2018 (the sibling fix that hardened
# `remote-update.sh`'s worker-restart not-loaded branch, the canonical reference).
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
#   # scheduled service (3-arg — no live-PID probe):
#   launchctl_bootstrap_fail_soft "gui/$(id -u)" "$plist_path" "$label"
#   # resident service (4-arg — opt in to the live-PID probe):
#   launchctl_bootstrap_fail_soft "gui/$(id -u)" "$plist_path" "$label" verify-pid
#
# Returns 0 if the service ends up loaded (bootstrap succeeded after retries, or
# bootstrap failed but kickstart -k recovered it) and, when verify-pid is set,
# also live. Returns 1 and prints a distinct, greppable WARNING to stderr only
# when a loop genuinely exhausts — a real double-failure or a resident service
# that never came up live, not masked as success.
launchctl_bootstrap_fail_soft() {
    local domain="$1" plist="$2" label="$3" verify_pid="${4:-}"

    local retries="${LAUNCHCTL_BOOTSTRAP_RETRIES:-3}"      # provisional/tunable — total bootstrap attempts on transient EIO
    local sleep_s="${LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP:-2}"  # provisional/tunable — seconds between attempts (loops A and B)

    # Loop A: bootstrap retry, errno-5-gated, BEFORE load. Retry ONLY on the
    # transient EIO shape; any other non-zero failure breaks out immediately so
    # we do not burn retries (and sleeps) on a genuine plist error.
    local loaded=false attempt err
    for attempt in $(seq 1 "$retries"); do
        if err=$(launchctl bootstrap "$domain" "$plist" 2>&1); then
            loaded=true
            break
        fi
        case "$err" in
            *"5: Input/output error"*)
                # transient EIO — sleep and retry unless this was the last attempt
                [ "$attempt" -lt "$retries" ] && sleep "$sleep_s"
                ;;
            *)
                # non-EIO genuine failure — do not burn retries; go straight to kickstart
                break
                ;;
        esac
    done

    # kickstart fallback (single-shot — the drain-race recovery). kickstart -k is
    # the atomic recovery for the still-registered/draining label; correct here
    # because an errno-5 failure specifically means the label IS already registered.
    if ! $loaded; then
        if launchctl kickstart -k "${domain}/${label}" 2>/dev/null; then
            loaded=true
        fi
    fi
    if ! $loaded; then
        echo "WARNING: launchctl bootstrap+kickstart failed for ${label}" >&2
        return 1
    fi

    # Loop B: opt-in live-PID probe, AFTER load, SEPARATE loop. Never re-invokes
    # bootstrap or kickstart — it only re-reads liveness via `launchctl print`,
    # giving a slow-forking resident process time to spawn between attempts. Only
    # resident services pass verify-pid; scheduled services have no persistent PID.
    if [ -n "$verify_pid" ]; then
        local i
        for i in $(seq 1 "$retries"); do
            if launchctl print "${domain}/${label}" 2>/dev/null | grep -Eq '^[[:space:]]*pid = [0-9]+'; then
                return 0
            fi
            sleep "$sleep_s"
        done
        echo "WARNING: launchctl bootstrap+kickstart failed for ${label}" >&2
        return 1
    fi

    return 0
}
