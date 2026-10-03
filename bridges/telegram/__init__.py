"""The Telegram bridge: Valor's own account, on the port in `core/bridge.py`.

`wire.py` is the only module that imports Telethon; everything else talks to
the narrow `Wire` interface, which the tests' emulator also implements.
"""
