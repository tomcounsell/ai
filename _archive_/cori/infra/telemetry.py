"""OpenTelemetry to Logfire. Tech stack §12: operational telemetry only,
never the audit log. The write token lives in the Keychain as
`logfire_token`; nothing under .logfire/ is kept.

    uv run python -m infra.telemetry     # exports one hello span
"""

import logfire

from infra.secrets import read_secret


def configure(service_name: str = "cori") -> None:
    logfire.configure(
        token=read_secret("logfire_token"),
        service_name=service_name,
        console=False,
        send_to_logfire=True,
    )


def hello() -> None:
    with logfire.span("hello from {service}", service="cori") as span:
        span.set_attribute("milestone", "M0")
        logfire.info("one span, exported")
    logfire.force_flush()


if __name__ == "__main__":
    configure()
    hello()
    print("exported; check `uv run logfire projects status`")
