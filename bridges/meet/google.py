"""Valor's Google accounts, and their sign-in, passed by the call's Chrome
with the login Valor holds in 1Password, so a lapsed session (the Cowboy's
and the Captain's, 2026-11-13) renews itself without Tom at the Mac.

An `Account` is Valor's own (`persona/identity.toml`'s email) or one a
client gave it, which that client's project specs name in `google`
(docs/plans/9f4967d9-google-per-project.md). Each has its own gws folder,
its own call profile and its own login; own's are today's, unchanged.

An account's login is the item `Google <email>` in the vault `VAULT`: its password, and its
one-time password, which 1Password computes from the authenticator's setup
key, so the key never leaves the vault. Read by name, since an `op://`
reference cannot hold the `@`. Each value is read when Google asks for it,
typed into Google's page and kept nowhere. Without the item, nothing here
touches a page.

Google's pages are told apart by their path: "Verify it's you"
(`confirmidentifier`), the account chooser, the email, the choice of how
to sign in (`challenge/selection`, reached by "Try another way" from a
passkey or any other challenge), the password (`challenge/pwd`) and the
authenticator's code (`challenge/totp`). The password comes first, then
the code, as Google's 2-Step Verification asks.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from core import credentials, persona, workspace
from core.settings import settings

VAULT = "m-valor"
SIGN_IN = "cd ~/src/ai && .venv/bin/python -m core meet-sign-in"


@dataclass(frozen=True)
class Account:
    """One of Valor's Google accounts, by its address."""

    email: str

    @classmethod
    def own(cls) -> Account:
        return cls(persona.identity(settings.persona_dir)["email"].lower())

    @classmethod
    def of(cls, project: str | None) -> Account:
        """The account `project`'s spec names in `google`; own for no
        project, no `google`, or a spec that does not load."""
        try:
            google = workspace.Spec.load(project).google if project else None
        except workspace.Refused:
            google = None
        return cls(google) if google else cls.own()

    @classmethod
    def here(cls, specs: list[workspace.Spec] | None = None) -> list[Account]:
        """Own, then each other account a project placed on this Mac names
        (of `specs`, by default every spec)."""
        own = cls.own()
        placed = [s for s in specs if s.placed_here] if specs is not None else workspace.Spec.here()
        named = sorted({s.google for s in placed if s.google and s.google != own.email})
        return [own, *(cls(e) for e in named)]

    @property
    def is_own(self) -> bool:
        return self == Account.own()

    @property
    def config_dir(self) -> str:
        """gws's folder: `~/.config/gws` for own, else its own beside it."""
        return str(Path.home() / ".config" / ("gws" if self.is_own else f"gws-{self.email}"))

    @property
    def env(self) -> dict[str, str]:
        """What gws runs with to be this account: nothing for own."""
        return {} if self.is_own else {"GOOGLE_WORKSPACE_CLI_CONFIG_DIR": self.config_dir}

    @property
    def profile(self) -> str:
        """The call profile: `settings.meet_profile` for own, else its own beside it."""
        return settings.meet_profile if self.is_own else f"{settings.meet_profile}-{self.email}"

    @property
    def item(self) -> str:
        return f"Google {self.email}"

    @property
    def sign_in(self) -> str:
        """The command that signs this account's call profile in."""
        return SIGN_IN if self.is_own else f"{SIGN_IN} --account {self.email}"

    def ready(self) -> bool:
        """Whether the call profile is signed in as this account, not another."""
        from core import call

        return call.signed_in_as(self.profile, self.email)


# Half-second tries one of Google's pages may stay unchanged after Valor
# answered it before the sign-in counts as failed (a code refused, say).
STUCK_TRIES = 20
CLICK_MS = 5000  # a button or choice not drawn by then is tried again
# Each field by its name or its type: a call's headless Chrome gets Google's
# lighter sign-in, whose email field is a plain text input (2026-11-14).
EMAIL_FIELD = "input[name=identifier], input[type=email]"
PASSWORD_FIELD = "input[name=Passwd], input[type=password]"
CODE_FIELD = "input[name=totpPin], input[type=tel]"


def secret(field: str, account: Account | None = None) -> str | None:
    """The account's item's `password`, or its `otp` code now; None without
    it. `account` defaults to own."""
    item = (account or Account.own()).item
    which = ["--otp"] if field == "otp" else ["--fields", f"label={field}", "--reveal"]
    return credentials.op(["item", "get", item, "--vault", VAULT, *which], settings.vault_env)


async def _answer(page, path: str, password: str, passed: set[str], account: Account) -> None:
    """What Valor does on the Google page at `path` for `account`;
    `passed`, the pages it has answered."""
    if "confirmidentifier" in path:
        await page.click('button:has-text("Next")', timeout=CLICK_MS)
    elif "accountchooser" in path:
        await page.click(f"text={account.email}", timeout=CLICK_MS)
    elif "identifier" in path:
        await page.fill(EMAIL_FIELD, account.email, timeout=CLICK_MS)
        await page.keyboard.press("Enter")
    elif "challenge/pwd" in path:
        await page.fill(PASSWORD_FIELD, password, timeout=CLICK_MS)
        await page.keyboard.press("Enter")
    elif "challenge/totp" in path:
        await page.fill(CODE_FIELD, secret("otp", account) or "", timeout=CLICK_MS)
        await page.keyboard.press("Enter")
    elif "challenge/selection" in path:
        choice = "text=/Authenticator/i" if "pwd" in passed else "text=/Enter your password/i"
        await page.click(choice, timeout=CLICK_MS)
    elif "/challenge/" in path:  # a passkey, a prompt on a phone: not Valor's
        await page.click('button:has-text("Try another way")', timeout=CLICK_MS)
    # Else one of Google's own redirects (ServiceLogin, CheckCookie, SetSID), passing.


async def renew(page, start: str, timeout_s: float = 120, account: Account | None = None) -> bool:
    """Google's sign-in as `account` (own by default) walked from `start`
    until the page leaves Google's accounts for the page it continues to;
    True once it has. False, with `page` untouched, when the item cannot be
    read; False when a page Valor answered stays put."""
    from playwright.async_api import Error

    account = account or Account.own()
    password = secret("password", account)
    if not password:
        return False
    await page.goto(start, wait_until="domcontentloaded")
    passed: set[str] = set()
    answered, same = None, 0
    for _ in range(int(timeout_s * 2)):
        parts = urlsplit(page.url)
        if not (parts.hostname or "").startswith("accounts.google."):
            return True
        if parts.path != answered:
            answered, same = parts.path, 0
            try:
                await _answer(page, parts.path, password, passed, account)
            except Error:
                answered = None  # not drawn yet; answered on the next try
            else:
                passed.add(parts.path.rsplit("/", 1)[-1])
        elif (same := same + 1) >= STUCK_TRIES:
            return False
        await asyncio.sleep(0.5)
    return False
