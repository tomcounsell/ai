"""A loopback git smart-HTTP server for the credential tests: the trusted
git's `git http-backend` run as CGI over the bare repositories under one
root, on a port in 6481-6489.

Reads are anonymous, as a public GitHub repository's are, unless
`auth_reads`; a push needs `Authorization: Basic base64(x-access-token:
TOKEN)` when a token is set, and gets 401 without it. Every request's
method, path, and headers are logged. `redirect_to` answers every request
with a redirect to another server; `hold_push` makes a push wait, after
`holding` is set, until `release` is set.
"""

import base64
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Self
from urllib.parse import urlsplit

from core import git

PORTS = range(6481, 6490)


class Server:
    def __init__(self, root: Path, *, token: str | None = None, auth_reads: bool = False,
                 redirect_to: str | None = None):  # fmt: skip
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.token = token
        self.auth_reads = auth_reads
        self.redirect_to = redirect_to
        self.hold_push = False
        self.holding = threading.Event()
        self.release = threading.Event()
        self.log: list[dict] = []
        self._httpd: ThreadingHTTPServer | None = None
        self.port = 0

    @property
    def expected(self) -> str | None:
        if self.token is None:
            return None
        return "Basic " + base64.b64encode(f"x-access-token:{self.token}".encode()).decode()

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.port}/{name}"

    def bare(self, name: str, *, head: str = "main") -> Path:
        path = self.root / name
        subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True)
        subprocess.run(["git", "-C", str(path), "symbolic-ref", "HEAD", f"refs/heads/{head}"], check=True)
        return path

    def authorized(self) -> list[dict]:
        return [r for r in self.log if "authorization" in r["headers"]]

    def __enter__(self) -> Self:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self._serve("GET")

            def do_POST(self):
                self._serve("POST")

            def _body(self) -> bytes:
                if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                    out = b""
                    while True:
                        size = int(self.rfile.readline().strip() or b"0", 16)
                        if size == 0:
                            self.rfile.readline()
                            return out
                        out += self.rfile.read(size)
                        self.rfile.readline()
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def _serve(self, method: str):
                owner.log.append({"method": method, "path": self.path,
                                  "headers": {k.lower(): v for k, v in self.headers.items()}})  # fmt: skip
                if owner.redirect_to:
                    self.send_response(302)
                    self.send_header("Location", owner.redirect_to + self.path)
                    self.end_headers()
                    return
                parts = urlsplit(self.path)
                pushing = "git-receive-pack" in self.path
                if (
                    owner.token is not None
                    and (pushing or owner.auth_reads)
                    and self.headers.get("Authorization") != owner.expected
                ):
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="test"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = self._body() if method == "POST" else b""
                if pushing and method == "POST" and owner.hold_push:
                    owner.holding.set()
                    owner.release.wait(60)
                env = {
                    "GIT_PROJECT_ROOT": str(owner.root),
                    "GIT_HTTP_EXPORT_ALL": "1",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "PATH": "/usr/bin:/bin",
                    "HOME": str(owner.root),
                    "PATH_INFO": parts.path,
                    "QUERY_STRING": parts.query,
                    "REQUEST_METHOD": method,
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(len(body)),
                    "REMOTE_USER": "valor",
                    "REMOTE_ADDR": "127.0.0.1",
                }
                if self.headers.get("Content-Encoding"):
                    env["HTTP_CONTENT_ENCODING"] = self.headers["Content-Encoding"]
                if self.headers.get("Git-Protocol"):
                    env["GIT_PROTOCOL"] = self.headers["Git-Protocol"]
                done = subprocess.run([git.binary(), "http-backend"], input=body, env=env,
                                      capture_output=True, check=False)  # fmt: skip
                raw = done.stdout
                sep = raw.find(b"\r\n\r\n")
                head, rest = (raw[:sep], raw[sep + 4 :]) if sep >= 0 else raw.split(b"\n\n", 1)
                status, headers = 200, []
                for line in head.decode("latin-1").splitlines():
                    name, _, value = line.partition(":")
                    if name.lower() == "status":
                        status = int(value.split()[0])
                    elif name:
                        headers.append((name, value.strip()))
                self.send_response(status)
                for name, value in headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(rest)))
                self.end_headers()
                self.wfile.write(rest)

        for port in PORTS:
            try:
                self._httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
                break
            except OSError:
                continue
        else:
            raise RuntimeError("no free port in 6481-6489")
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.release.set()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
