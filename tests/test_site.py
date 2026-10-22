"""The public site (site/, served by src/index.js): every link lands, every
source link names a file this checkout has, the sitemap lists the pages, the
removed pages redirect somewhere real, and nothing private is published."""

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PAGES = sorted(SITE.rglob("*.html"))
REPO = "https://github.com/tomcounsell/ai/"


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.refs: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("href", "src") and value:
                self.refs.append(value)
            if name == "id" and value:
                self.ids.add(value)


def _parse(path: Path) -> _Page:
    page = _Page()
    page.feed(path.read_text())
    return page


PARSED = {path: _parse(path) for path in PAGES}


def _target(page: Path, ref: str) -> Path:
    path = urlsplit(ref).path
    if path.startswith("/"):
        found = SITE / path.lstrip("/")
    else:
        found = (page.parent / path).resolve() if path else page
    return found / "index.html" if found.is_dir() else found


def test_every_internal_link_and_anchor_lands():
    broken = []
    for page, parsed in PARSED.items():
        for ref in parsed.refs:
            parts = urlsplit(ref)
            if parts.scheme or ref.startswith(("mailto:", "data:", "//")):
                continue
            target = _target(page, ref)
            if not target.is_file():
                broken.append(f"{page.relative_to(SITE)}: {ref}")
            elif parts.fragment and parts.fragment not in PARSED[target].ids:
                broken.append(f"{page.relative_to(SITE)}: {ref} (no #{parts.fragment})")
    assert not broken, broken


def test_every_source_link_names_a_path_in_this_checkout():
    missing = []
    for page, parsed in PARSED.items():
        for ref in parsed.refs:
            match = re.match(re.escape(REPO) + r"(blob|tree)/main/([^#?]+)", ref)
            if not match:
                continue
            kind, path = match.groups()
            found = ROOT / path.rstrip("/")
            if not (found.is_file() if kind == "blob" else found.is_dir()):
                missing.append(f"{page.relative_to(SITE)}: {kind}/{path}")
    assert not missing, missing


def test_sitemap_lists_exactly_the_pages():
    listed = set(
        re.findall(r"<loc>https://valorengels\.com/([^<]*)</loc>", (SITE / "sitemap.xml").read_text())
    )
    pages = {
        "" if p == SITE / "index.html" else str(p.relative_to(SITE)) for p in PAGES if p.name != "404.html"
    }
    assert listed == pages


def test_each_removed_page_redirects_to_a_page_that_exists():
    worker = (ROOT / "src" / "index.js").read_text()
    moved = re.findall(r'"(/[\w/-]+)":\s*"(/[\w./-]+\.html)(?:#([\w-]+))?"', worker)
    research = {
        f"/research/{name}"
        for name in (
            "valor-vs-paperclip",
            "valor-vs-managed-agents",
            "managed-agents-vs-perplexity-computer",
            "openhuman-vs-hermes",
        )
    }
    assert {old for old, _, _ in moved} == {
        "/runtime",
        "/codebase",
        "/memory",
        "/reflections",
        "/work",
        "/pipeline",
        "/research",
    } | research
    assert ("/reflections", "/chat.html", "routines") in moved
    for old, new, anchor in moved:
        assert not (SITE / (old.lstrip("/") + ".html")).exists(), old
        target = SITE / new.lstrip("/")
        assert target.is_file(), new
        assert not anchor or anchor in PARSED[target].ids, f"{new}#{anchor}"


PRIVATE = {
    "chat id": re.compile(r"-100\d{6,}"),
    "token": re.compile(r"\bsk-[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{36}|\bops_[A-Za-z0-9]{20,}"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
}


def test_nothing_private_is_published():
    found = []
    for path in SITE.rglob("*"):
        if path.suffix not in (".html", ".css", ".js", ".xml", ".txt"):
            continue
        text = path.read_text()
        for kind, pattern in PRIVATE.items():
            for hit in pattern.findall(text):
                if kind == "email" and hit == "valor@yuda.me":
                    continue
                found.append(f"{path.relative_to(SITE)}: {kind} {hit}")
    assert not found, found


@pytest.mark.skipif(not shutil.which("node"), reason="node is not on PATH")
def test_activity_svg_links_public_days_only_and_never_names_a_private_repo():
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    public = {"nameWithOwner": "tomcounsell/ai", "isPrivate": False}
    private = {"nameWithOwner": "hidden-org/secret-repo", "isPrivate": True}
    nodes = [
        {
            "mergedAt": f"{today}T10:00:00Z",
            "url": "https://github.com/hidden-org/secret-repo/pull/9",
            "repository": private,
        },
        {
            "mergedAt": f"{today}T11:00:00Z",
            "url": "https://github.com/tomcounsell/ai/pull/1",
            "repository": public,
        },
        {
            "mergedAt": f"{yesterday}T09:00:00Z",
            "url": "https://github.com/hidden-org/secret-repo/pull/8",
            "repository": private,
        },
    ]
    github = {
        "data": {
            "search": {"issueCount": 3, "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}
        }
    }
    script = (
        "globalThis.caches = { default: { match: async () => undefined, put: async () => {} } };"
        f"globalThis.fetch = async () => new Response({json.dumps(json.dumps(github))});"
        "const src = Buffer.from(process.argv[1]).toString('base64');"
        "const worker = (await import('data:text/javascript;base64,' + src)).default;"
        "const res = await worker.fetch(new Request('https://valorengels.com/activity.svg'),"
        " { GITHUB_TOKEN: 't' }, { waitUntil() {} });"
        "process.stdout.write(res.headers.get('content-type') + '\\n' + await res.text());"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script, (ROOT / "src" / "index.js").read_text()],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    kind, svg = out.split("\n", 1)
    assert kind.startswith("image/svg+xml") and svg.startswith("<svg")
    assert (
        len(re.findall(r"<title>\d{4}-\d\d-\d\d: \d+ merged</title>", svg)) + svg.count('fill="none"') == 182
    )
    assert '<a href="https://github.com/tomcounsell/ai/pull/1" target="_top"><rect' in svg
    assert f"<title>{yesterday}: 1 merged</title>" in svg
    assert not re.search(rf"<a [^>]*><rect[^>]*><title>{yesterday}", svg)
    assert "hidden-org" not in svg and "secret-repo" not in svg
