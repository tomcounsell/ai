"""The email bridge's configuration: the settings it runs with and the
mailbox logins from the kernel key directory. The passwords live here only,
never in a record, a log line, or the environment."""

import ssl
from dataclasses import dataclass, field
from datetime import date

from core import credentials
from core.settings import settings

KEY_NAMES = ["IMAP_USER", "IMAP_PASSWORD", "SMTP_USER", "SMTP_PASSWORD"]
KEYS_COMMAND = "python -m bridges.email keys"


@dataclass(frozen=True)
class SMTPTimeouts:
    """Per-command SMTP timeouts, in seconds: the minimums RFC 5321 section
    4.5.3.2 gives, which an SMTP client sets per command and per TCP send
    of the body rather than on the whole transaction, so a large message is
    never cut off for its size. EHLO, STARTTLS, AUTH, and QUIT have no value
    of their own there; they take `command`, the value it gives the other
    commands that wait on one reply (MAIL, RCPT)."""

    greeting: float = 300.0  # 4.5.3.2.1, the initial 220
    command: float = 300.0  # 4.5.3.2.2 and 4.5.3.2.3, MAIL and RCPT
    data_start: float = 120.0  # 4.5.3.2.4, awaiting DATA's 354
    data_block: float = 180.0  # 4.5.3.2.5, each TCP send of the body
    data_end: float = 600.0  # 4.5.3.2.6, awaiting the final 250


@dataclass(frozen=True)
class Config:
    address: str
    since: date
    imap_host: str
    imap_port: int
    smtp_host: str
    smtp_port: int
    imap_user: str
    smtp_user: str
    imap_password: str = field(repr=False)
    smtp_password: str = field(repr=False)
    cafile: str = ""
    poll_s: float = 30.0
    imap_timeout_s: float = 30.0
    smtp_timeouts: SMTPTimeouts = SMTPTimeouts()

    def context(self) -> ssl.SSLContext:
        """Certificates verified: the system's CAs, or `cafile`."""
        return ssl.create_default_context(cafile=self.cafile or None)

    @classmethod
    def from_settings(cls) -> Config:
        """The configuration `run` starts with; a missing setting or key
        fails the start, naming it."""
        if not settings.email_address:
            raise credentials.CredentialError("email_address is not set (VALOR_EMAIL_ADDRESS)")
        if not settings.email_since:
            raise credentials.CredentialError("email_since is not set (VALOR_EMAIL_SINCE, YYYY-MM-DD)")
        keys = {n: credentials.read_key(settings.mail_keyfile, n, KEYS_COMMAND) for n in KEY_NAMES}
        return cls(
            address=settings.email_address.lower(),
            since=date.fromisoformat(settings.email_since),
            imap_host=settings.imap_host,
            imap_port=settings.imap_port,
            smtp_host=settings.smtp_host,
            smtp_port=settings.smtp_port,
            imap_user=keys["IMAP_USER"],
            smtp_user=keys["SMTP_USER"],
            imap_password=keys["IMAP_PASSWORD"],
            smtp_password=keys["SMTP_PASSWORD"],
            cafile=settings.mail_cafile,
            poll_s=settings.email_poll_s,
            imap_timeout_s=settings.imap_timeout_s,
        )
