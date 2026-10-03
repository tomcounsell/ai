"""The email bridge in a process of its own, for the kill tests in
`tests/test_email_kernel.py`: `python -m tests.email_child`.

`EMAIL_DSN` names the database and `EMAIL_CONFIG` is the bridge's `Config`
as JSON, `since` an ISO date.
"""

import asyncio
import json
import os
from datetime import date

from bridges.email import EmailBridge
from bridges.email.config import Config
from core import bridge


def main() -> None:
    dsn = os.environ["EMAIL_DSN"]
    values = json.loads(os.environ["EMAIL_CONFIG"])
    values["since"] = date.fromisoformat(values["since"])
    asyncio.run(bridge.serve(EmailBridge(Config(**values), dsn), dsn))


if __name__ == "__main__":
    main()
