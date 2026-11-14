"""Valor's own Google sign-in, passed by the call's Chrome with the login
Valor holds in 1Password, so a lapsed session (the Cowboy's and the
Captain's, 2026-11-13) renews itself without Tom at the Mac.

The login is the item `ITEM` in the vault `VAULT`: its password, and its
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
from urllib.parse import urlsplit

from core import credentials
from core.settings import settings

ITEM, VAULT, EMAIL = "Google valor@yuda.me", "m-valor", "valor@yuda.me"
# Half-second tries one of Google's pages may stay unchanged after Valor
# answered it before the sign-in counts as failed (a code refused, say).
STUCK_TRIES = 20
CLICK_MS = 5000  # a button or choice not drawn by then is tried again
# Each field by its name or its type: a call's headless Chrome gets Google's
# lighter sign-in, whose email field is a plain text input (2026-11-14).
EMAIL_FIELD = "input[name=identifier], input[type=email]"
PASSWORD_FIELD = "input[name=Passwd], input[type=password]"
CODE_FIELD = "input[name=totpPin], input[type=tel]"


def secret(field: str) -> str | None:
    """The item's `password`, or its `otp` code now; None without it."""
    which = ["--otp"] if field == "otp" else ["--fields", f"label={field}", "--reveal"]
    return credentials.op(["item", "get", ITEM, "--vault", VAULT, *which], settings.vault_env)


async def _answer(page, path: str, password: str, passed: set[str]) -> None:
    """What Valor does on the Google page at `path`; `passed`, the pages
    it has answered."""
    if "confirmidentifier" in path:
        await page.click('button:has-text("Next")', timeout=CLICK_MS)
    elif "accountchooser" in path:
        await page.click(f"text={EMAIL}", timeout=CLICK_MS)
    elif "identifier" in path:
        await page.fill(EMAIL_FIELD, EMAIL, timeout=CLICK_MS)
        await page.keyboard.press("Enter")
    elif "challenge/pwd" in path:
        await page.fill(PASSWORD_FIELD, password, timeout=CLICK_MS)
        await page.keyboard.press("Enter")
    elif "challenge/totp" in path:
        await page.fill(CODE_FIELD, secret("otp") or "", timeout=CLICK_MS)
        await page.keyboard.press("Enter")
    elif "challenge/selection" in path:
        choice = "text=/Authenticator/i" if "pwd" in passed else "text=/Enter your password/i"
        await page.click(choice, timeout=CLICK_MS)
    elif "/challenge/" in path:  # a passkey, a prompt on a phone: not Valor's
        await page.click('button:has-text("Try another way")', timeout=CLICK_MS)
    # Else one of Google's own redirects (ServiceLogin, CheckCookie, SetSID), passing.


async def renew(page, start: str, timeout_s: float = 120) -> bool:
    """Google's sign-in walked from `start` until the page leaves Google's
    accounts for the page it continues to; True once it has. False, with
    `page` untouched, when the item cannot be read; False when a page Valor
    answered stays put."""
    from playwright.async_api import Error

    password = secret("password")
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
                await _answer(page, parts.path, password, passed)
            except Error:
                answered = None  # not drawn yet; answered on the next try
            else:
                passed.add(parts.path.rsplit("/", 1)[-1])
        elif (same := same + 1) >= STUCK_TRIES:
            return False
        await asyncio.sleep(0.5)
    return False
