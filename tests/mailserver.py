"""Local mail servers for the email tests: Dovecot for IMAP, and a small
SMTP server that files what it accepts in the sender's Sent folder, as
Gmail does.

Dovecot runs as the test's user with no root, speaking plain IMAP on
127.0.0.1; clients reach it over TLS through a terminator whose
certificate a test CA signs (Dovecot's own TLS drops the session after
login on macOS). A second terminator presents a certificate the CA did
not sign. Ports come from `VALOR_TEST_PORTS` (`first-last`) when set, else
from the OS.
"""

import asyncio
import base64
import imaplib
import os
import re
import shutil
import signal
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ADDRESS = "valor@test.local"
PASSWORD = "test-mailbox-password"
MAX_SIZE = 35_882_577  # what Gmail's EHLO advertises


class Ports:
    """Free local ports, from `VALOR_TEST_PORTS` when set."""

    def __init__(self):
        spec = os.environ.get("VALOR_TEST_PORTS", "")
        if spec:
            first, _, last = spec.partition("-")
            self.pool = list(range(int(first), int(last or first) + 1))
        else:
            self.pool = None

    @staticmethod
    def _free(port: int) -> bool:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                return False
        return True

    def take(self) -> int:
        if self.pool is None:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                return s.getsockname()[1]
        while self.pool:
            port = self.pool.pop(0)
            if self._free(port):
                return port
        raise RuntimeError("VALOR_TEST_PORTS has no free port left")


def _openssl(*args: str, cwd: Path) -> None:
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


def make_certs(root: Path) -> None:
    """`ca.pem`, a server cert it signs (`srv.pem`, `srv.key`) for
    localhost and 127.0.0.1, and a self-signed one (`rogue.pem`)."""
    _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=valor test CA",
             "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
             "-keyout", "ca.key", "-out", "ca.pem", cwd=root)  # fmt: skip
    (root / "san.ext").write_text(
        "subjectAltName=DNS:localhost,IP:127.0.0.1\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n"
    )
    _openssl("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=localhost",
             "-keyout", "srv.key", "-out", "srv.csr", cwd=root)  # fmt: skip
    _openssl("x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
             "-days", "2", "-extfile", "san.ext", "-out", "srv.pem", cwd=root)  # fmt: skip
    _openssl("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", "/CN=localhost",
             "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
             "-keyout", "rogue.key", "-out", "rogue.pem", cwd=root)  # fmt: skip


def _server_context(root: Path, name: str) -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(root / f"{name}.pem", root / f"{name}.key")
    return ctx


class Loop:
    """An asyncio loop on its own thread, for the servers."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def call(self, coro, timeout: float = 30):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, watch=None) -> None:
    try:
        while data := await reader.read(65536):
            if watch:
                watch(data)
            writer.write(data)
            await writer.drain()
    except ConnectionError, OSError, ssl.SSLError:
        pass
    finally:
        try:
            writer.close()
        except ConnectionError, OSError, RuntimeError:
            pass


class Terminator:
    """TLS on `port`, plain to Dovecot's `backend` port. `idling` is set
    each time Dovecot answers an IDLE with its continuation."""

    def __init__(self, loop: Loop, port: int, backend: int, ctx: ssl.SSLContext):
        self.loop, self.port, self.backend, self.ctx = loop, port, backend, ctx
        self.server = None
        self.idling = threading.Event()

    def _from_dovecot(self, data: bytes) -> None:
        if b"+ idling" in data:
            self.idling.set()

    async def _handle(self, reader, writer):
        try:
            r2, w2 = await asyncio.open_connection("127.0.0.1", self.backend)
        except OSError:
            writer.close()
            return
        await asyncio.gather(_pipe(reader, w2), _pipe(r2, writer, self._from_dovecot))

    def start(self) -> None:
        async def go():
            self.server = await asyncio.start_server(self._handle, "127.0.0.1", self.port, ssl=self.ctx)

        self.loop.call(go())

    def stop(self) -> None:
        async def go():
            self.server.close()
            await self.server.wait_closed()

        if self.server is not None:
            self.loop.call(go())
            self.server = None


class Dovecot:
    """A rootless Dovecot with one mailbox, `ADDRESS`, holding INBOX and a
    folder flagged `\\Sent`."""

    def __init__(self, root: Path, port: int):
        self.root, self.port = root, port
        # Socket paths must be short, so the run directory is under /tmp.
        self.base = Path(tempfile.mkdtemp(prefix="vd", dir="/tmp"))
        self.conf = root / "dovecot.conf"
        self.binary = shutil.which("dovecot") or "/opt/homebrew/sbin/dovecot"
        self.doveadm = shutil.which("doveadm") or "/opt/homebrew/bin/doveadm"

    def start(self) -> None:
        if not Path(self.binary).exists():
            raise RuntimeError("dovecot is not installed (brew install dovecot)")
        user = os.environ.get("USER") or os.getlogin()
        group = subprocess.run(["id", "-gn"], capture_output=True, text=True, check=True).stdout.strip()
        (self.root / "users").write_text(f"{ADDRESS}:{{PLAIN}}{PASSWORD}::::::\n")
        for d in ("state", "mail", "home"):
            (self.root / d).mkdir(exist_ok=True)
        self.conf.write_text(
            f"""dovecot_config_version = 2.4.0
dovecot_storage_version = 2.4.0
base_dir = {self.base}
state_dir = {self.root}/state
log_path = {self.root}/log
default_internal_user = {user}
default_internal_group = {group}
default_login_user = {user}
protocols = imap
listen = 127.0.0.1
ssl = no
auth_mechanisms = plain login
auth_allow_cleartext = yes
default_vsz_limit = 1T
mail_driver = maildir
mail_path = {self.root}/mail/%{{user}}
passdb passwd-file {{
  passwd_file_path = {self.root}/users
}}
userdb static {{
  fields {{
    uid = {os.getuid()}
    gid = {os.getgid()}
    home = {self.root}/home/%{{user}}
  }}
}}
service imap-login {{
  chroot =
  restart_request_count = unlimited
  inet_listener imap {{
    port = {self.port}
  }}
  inet_listener imaps {{
    port = 0
  }}
}}
service anvil {{
  chroot =
}}
namespace inbox {{
  inbox = yes
  separator = /
  mailbox Sent {{
    special_use = \\\\Sent
    auto = create
  }}
}}
"""
        )
        # Dovecot daemonizes; its output goes to the log, not a pipe a
        # daemon would hold open.
        subprocess.run([self.binary, "-c", str(self.conf)], check=True, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                self.login().logout()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(f"dovecot did not start; see {self.root / 'log'}")

    def stop(self) -> None:
        pidfile = self.base / "master.pid"
        if pidfile.exists():
            pid = int(pidfile.read_text().strip())
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            for _ in range(50):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
        shutil.rmtree(self.base, ignore_errors=True)

    def login(self) -> imaplib.IMAP4:
        conn = imaplib.IMAP4("127.0.0.1", self.port, timeout=10)
        conn.login(ADDRESS, PASSWORD)
        return conn

    def append(self, folder: str, raw: bytes, *, seen: bool = False, when: float | None = None) -> None:
        conn = self.login()
        try:
            date = imaplib.Time2Internaldate(when if when is not None else time.time())
            typ, data = conn.append(folder, r"(\Seen)" if seen else "", date, raw)
            assert typ == "OK", data
        finally:
            conn.logout()

    def deliver(self, raw: bytes, *, when: float | None = None) -> None:
        """Mail arriving in INBOX, unseen."""
        self.append("INBOX", raw, when=when)

    def messages(self, folder: str) -> list[tuple[bytes, set[str]]]:
        """Each message in `folder`, oldest first, with its flags."""
        conn = self.login()
        try:
            conn.select(folder, readonly=True)
            _, data = conn.uid("SEARCH", "ALL")
            out = []
            for uid in (data[0] or b"").split():
                _, got = conn.uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
                head, body = got[0]
                flags = set(re.search(rb"FLAGS \(([^)]*)\)", head).group(1).decode().split())
                out.append((body, flags))
            return out
        finally:
            conn.logout()

    def clear(self) -> None:
        conn = self.login()
        try:
            for folder in ("INBOX", "Sent"):
                conn.select(folder)
                conn.uid("STORE", "1:*", "+FLAGS.SILENT", r"(\Deleted)")
                conn.expunge()
        finally:
            conn.logout()

    def set_uidvalidity(self, value: int) -> None:
        subprocess.run(
            [self.doveadm, "-c", str(self.conf), "mailbox", "update", "-u", ADDRESS,
             "--uid-validity", str(value), "INBOX"],
            check=True, capture_output=True,
        )  # fmt: skip


@dataclass
class Accepted:
    mail_from: str
    rcpts: list[str]
    data: bytes


CHUNK = 65536  # bytes per read of a body


@dataclass
class Behavior:
    """What the SMTP server does on the next sessions."""

    refuse: set[str] = field(default_factory=set)  # recipients answered 550
    reply_delay_s: float = 0.0  # held before the final 250, after filing
    read_delay_s: float = 0.0  # held before each read of the body
    read_bytes_per_s: float = 0.0  # the body read at this rate; 0 reads at once
    on_connect: Callable[[], None] | None = None
    on_data: Callable[[], None] | None = None  # after DATA's 354, before the body is read
    file_in_sent: bool = True
    hang_up_after_354: bool = False  # closes the connection instead of reading the body
    final_reply: str = "250 2.0.0 ok queued"  # the line sent after the body is taken


class SMTPServer:
    """EHLO with SIZE, STARTTLS, and AUTH PLAIN; files each accepted message
    in Dovecot's Sent before its 250 reply."""

    def __init__(self, loop: Loop, port: int, ctx: ssl.SSLContext, dovecot: Dovecot):
        self.loop, self.port, self.ctx, self.dovecot = loop, port, ctx, dovecot
        self.behavior = Behavior()
        self.accepted: list[Accepted] = []
        self.connections = 0
        self.server = None

    def reset(self) -> None:
        self.behavior = Behavior()
        self.accepted = []
        self.connections = 0

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.connections += 1
        b = self.behavior
        if b.on_connect:
            b.on_connect()
        tls = authed = False
        mail_from, rcpts = None, []

        async def reply(line: str):
            writer.write(line.encode() + b"\r\n")
            await writer.drain()

        try:
            await reply("220 localhost ESMTP test")
            while line := await reader.readline():
                cmd = line.decode(errors="replace").strip()
                verb = cmd.split(" ", 1)[0].upper()
                if verb in ("EHLO", "HELO"):
                    exts = [f"SIZE {MAX_SIZE}", "8BITMIME"]
                    exts += ["AUTH PLAIN LOGIN"] if tls else ["STARTTLS"]
                    await reply(
                        "\r\n".join(["250-localhost", *[f"250-{e}" for e in exts[:-1]], f"250 {exts[-1]}"])
                    )
                elif verb == "STARTTLS" and not tls:
                    await reply("220 go ahead")
                    await writer.start_tls(self.ctx)
                    tls = True
                elif verb == "AUTH" and tls:
                    parts = cmd.split()
                    if parts[1].upper() == "PLAIN" and len(parts) == 3:
                        _, user, pw = base64.b64decode(parts[2]).split(b"\0")
                    elif parts[1].upper() == "LOGIN":
                        await reply("334 VXNlcm5hbWU6")
                        user = base64.b64decode(await reader.readline())
                        await reply("334 UGFzc3dvcmQ6")
                        pw = base64.b64decode(await reader.readline())
                    else:
                        await reply("504 unsupported")
                        continue
                    authed = (user.decode(), pw.decode()) == (ADDRESS, PASSWORD)
                    await reply("235 ok" if authed else "535 bad credentials")
                elif verb == "MAIL":
                    if not authed:
                        await reply("530 authenticate first")
                        continue
                    m = re.search(r"SIZE=(\d+)", cmd, re.IGNORECASE)
                    if m and int(m.group(1)) > MAX_SIZE:
                        await reply("552 message size exceeds fixed maximum message size")
                        continue
                    mail_from, rcpts = re.search(r"<([^>]*)>", cmd).group(1), []
                    await reply("250 ok")
                elif verb == "RCPT":
                    addr = re.search(r"<([^>]*)>", cmd).group(1).lower()
                    if addr in b.refuse:
                        await reply(f"550 5.1.1 {addr} does not exist")
                    else:
                        rcpts.append(addr)
                        await reply("250 ok")
                elif verb == "DATA":
                    if not rcpts:
                        await reply("503 no valid recipients")
                        continue
                    await reply("354 go ahead")
                    if b.on_data:
                        b.on_data()
                    if b.hang_up_after_354:
                        break
                    data = await self._body(reader, b)
                    if b.file_in_sent:
                        await asyncio.to_thread(self.dovecot.append, "Sent", data, seen=True)
                    self.accepted.append(Accepted(mail_from, list(rcpts), data))
                    if b.reply_delay_s:
                        await asyncio.sleep(b.reply_delay_s)
                    await reply(b.final_reply)
                    mail_from, rcpts = None, []
                elif verb == "RSET":
                    mail_from, rcpts = None, []
                    await reply("250 ok")
                elif verb == "NOOP":
                    await reply("250 ok")
                elif verb == "QUIT":
                    await reply("221 bye")
                    break
                else:
                    await reply("502 not implemented")
        except ConnectionError, OSError, ssl.SSLError, asyncio.IncompleteReadError:
            pass
        finally:
            try:
                writer.close()
            except ConnectionError, OSError, RuntimeError:
                pass

    @staticmethod
    async def _body(reader: asyncio.StreamReader, b: Behavior) -> bytes:
        """The body up to its end of data line, read in chunks of `CHUNK`,
        each read held `read_delay_s` and paced to `read_bytes_per_s`."""
        buf = bytearray()
        while not (buf == b".\r\n" or buf.endswith(b"\r\n.\r\n")):
            if b.read_delay_s:
                await asyncio.sleep(b.read_delay_s)
            chunk = await reader.read(CHUNK)
            if not chunk:
                raise ConnectionError("the client went away mid-body")
            buf += chunk
            if b.read_bytes_per_s:
                await asyncio.sleep(len(chunk) / b.read_bytes_per_s)
        lines = bytes(buf[:-3]).split(b"\r\n")[:-1]
        return b"".join((line[1:] if line.startswith(b"..") else line) + b"\r\n" for line in lines)

    def start(self) -> None:
        async def go():
            self.server = await asyncio.start_server(self._handle, "127.0.0.1", self.port)

        self.loop.call(go())

    def stop(self) -> None:
        async def go():
            self.server.close()

        if self.server is not None:
            self.loop.call(go())
            self.server = None


@dataclass
class Mail:
    """The running servers and the addresses a client uses."""

    root: Path
    dovecot: Dovecot
    imap: Terminator
    rogue: Terminator
    smtp: SMTPServer
    loop: Loop

    @property
    def cafile(self) -> str:
        return str(self.root / "ca.pem")

    def config(self, **overrides):
        from datetime import date

        from bridges.email.config import Config

        values = {
            "address": ADDRESS,
            "since": date(2026, 1, 1),
            "imap_host": "localhost",
            "imap_port": self.imap.port,
            "smtp_host": "localhost",
            "smtp_port": self.smtp.port,
            "imap_user": ADDRESS,
            "smtp_user": ADDRESS,
            "imap_password": PASSWORD,
            "smtp_password": PASSWORD,
            "cafile": self.cafile,
        }
        values.update(overrides)
        return Config(**values)

    def reset(self) -> None:
        self.smtp.reset()
        self.dovecot.clear()
        self.imap.idling.clear()
        if self.imap.server is None:
            self.imap.start()


def start(root: Path) -> Mail:
    """Every server, started. Raises naming what is missing."""
    for tool in ("openssl",):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} is not installed")
    root.mkdir(parents=True, exist_ok=True)
    make_certs(root)
    ports = Ports()
    dovecot = Dovecot(root, ports.take())
    dovecot.start()
    loop = Loop()
    imap = Terminator(loop, ports.take(), dovecot.port, _server_context(root, "srv"))
    rogue = Terminator(loop, ports.take(), dovecot.port, _server_context(root, "rogue"))
    smtp = SMTPServer(loop, ports.take(), _server_context(root, "srv"), dovecot)
    for s in (imap, rogue, smtp):
        s.start()
    return Mail(root, dovecot, imap, rogue, smtp, loop)


def stop(mail: Mail) -> None:
    for s in (mail.smtp, mail.imap, mail.rogue):
        s.stop()
    mail.loop.close()
    mail.dovecot.stop()
