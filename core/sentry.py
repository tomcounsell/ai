"""Every exception a kernel process raises or catches, reported to the
`valor` Sentry project (docs/plans/sentry.md).

`init` runs first in each resident process: the kernel's `_serve`, each
bridge's `run`, and the voice tool's `run`. Uncaught exceptions and
`logging` errors (the bridges' `log.exception`) report on their own; the
kernel's own handlers, which print and go on, call
`sentry_sdk.capture_exception`, a no-op when `init` did not run. Nothing
reports without the DSN file or in a turn, whose sandbox
cannot read the kernel key directory.
"""

import subprocess
from pathlib import Path

import sentry_sdk

from core.settings import settings

ROOT = Path(__file__).resolve().parent.parent


def init(component: str) -> bool:
    """Start reporting for this process, tagged with `component`, the Mac,
    and the checkout's commit. Returns whether it started."""
    try:
        dsn = Path(settings.sentry_dsn_file).read_text().strip()
    except OSError:
        return False
    if not dsn:
        return False
    head = subprocess.run(
        [settings.git_bin, "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    sentry_sdk.init(
        dsn=dsn,
        release=head.stdout.strip() or None,
        server_name=settings.machine,
        environment="production",
    )
    sentry_sdk.set_tag("component", component)
    return True
