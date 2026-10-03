"""Sign Valor's account into the bridge's own session.

Run by Tom, interactively: `python -m bridges.telegram login`. The phone
number and the two-factor password are asked at the prompt and stored
nowhere. The session file lands in the kernel key directory, mode 600.
Nothing prints any part of the API hash.
"""

from __future__ import annotations

import asyncio
import getpass
import os
from pathlib import Path

from bridges.telegram.wire import TEST_DC


async def login(session: Path, api_id: int, api_hash: str, *, test_dc: bool = False, ask=input) -> str:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    session.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    client = TelegramClient(str(session), api_id, api_hash, flood_sleep_threshold=0)
    if test_dc:
        client.session.set_dc(*TEST_DC)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            phone = ask("Phone number: ").strip()
            await client.send_code_request(phone)
            code = ask("Code: ").strip()
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                await client.sign_in(password=getpass.getpass("Two-factor password: "))
        me = await client.get_me()
    finally:
        await client.disconnect()
    for path in (session, Path(f"{session}.session")):
        if path.exists():
            os.chmod(path, 0o600)
    return f"signed in as {me.first_name or ''} (user id {me.id})"


def run(session: Path, api_id: int, api_hash: str, *, test_dc: bool = False) -> str:
    return asyncio.run(login(session, api_id, api_hash, test_dc=test_dc))
