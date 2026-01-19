#!/bin/bash
# Interactive Telegram login script
# Creates/refreshes the session file used by the bridge

cd "$(dirname "$0")/.."

python3 << 'PYTHON'
import asyncio
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from dotenv import load_dotenv
import os

load_dotenv(".env")

API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "")
PHONE = os.getenv("TELEGRAM_PHONE", "")
PASSWORD = os.getenv("TELEGRAM_PASSWORD", "")
SESSION_PATH = "data/valor_bridge"

async def login():
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)

    print(f"Connecting to Telegram...")
    await client.connect()

    if await client.is_user_authorized():
        print("Already logged in!")
        me = await client.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username})")
    else:
        print(f"Sending code to {PHONE}...")
        await client.send_code_request(PHONE)

        code = input("Enter the code you received: ")

        try:
            await client.sign_in(PHONE, code)
        except SessionPasswordNeededError:
            print("2FA enabled, using configured password...")
            await client.sign_in(password=PASSWORD)

        me = await client.get_me()
        print(f"Logged in as: {me.first_name} (@{me.username})")

    await client.disconnect()
    print("Session saved. You can now start the bridge.")

asyncio.run(login())
PYTHON
