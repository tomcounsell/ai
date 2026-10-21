"""The public site (site/, served by src/index.js): every link lands, every
source link names a file this checkout has, the sitemap lists the pages, the
removed pages redirect somewhere real, and nothing private is published."""

import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

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
    listed = set(re.findall(r"<loc>https://valorengels\.com/([^<]*)</loc>", (SITE / "sitemap.xml").read_text()))
    pages = {
        "" if p == SITE / "index.html" else str(p.relative_to(SITE)) for p in PAGES if p.name != "404.html"
    }
    assert listed == pages


def test_each_removed_page_redirects_to_a_page_that_exists():
    worker = (ROOT / "src" / "index.js").read_text()
    moved = re.findall(r'"(/[\w/-]+)":\s*"(/[\w./-]+\.html)(?:#([\w-]+))?"', worker)
    research = {f"/research/{name}" for name in (
        "valor-vs-paperclip", "valor-vs-managed-agents", "managed-agents-vs-perplexity-computer", "openhuman-vs-hermes")}
    assert {old for old, _, _ in moved} == {"/runtime", "/codebase", "/memory", "/reflections", "/research"} | research
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
