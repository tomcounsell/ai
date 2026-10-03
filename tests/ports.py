"""Ports the tests' own services listen on.

`VALOR_TEST_PORTS` (`LOW-HIGH`) moves every test-started Postgres and Redis
into one span, so test runs side by side on one machine never share a
port; unset, each call site keeps its own default span.
"""

import os


def span(default: tuple[int, int]) -> tuple[int, int]:
    value = os.environ.get("VALOR_TEST_PORTS")
    if not value:
        return default
    low, high = value.split("-")
    return int(low), int(high)
