"""Search Anna's Archive and download via the fast_download API.

Usage:
  python annas_get.py search "How to Write Short Roy Peter Clark" --ext epub
  python annas_get.py download <md5> --output ./library/raw/

Requires ANNAS_ARCHIVE_ACCOUNT_ID and ANNAS_ARCHIVE_SECRET_KEY in the
environment for downloads (see the ebook-ingest SKILL.md Configuration section).
"""

import argparse
import os
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

MIRRORS = [
    "https://annas-archive.org",
    "https://annas-archive.li",
    "https://annas-archive.se",
    "https://annas-archive.in",
    "https://annas-archive.pm",
]

UA = "Mozilla/5.0 (compatible; personal-ebook-ingest/1.0)"


def pick_mirror() -> str:
    """Return the first mirror that responds 200 to /."""
    for m in MIRRORS:
        try:
            r = httpx.get(m, timeout=5.0, headers={"User-Agent": UA})
            if r.status_code == 200:
                return m
        except httpx.HTTPError:
            continue
    raise RuntimeError("No Anna's Archive mirror reachable")


def search(query: str, ext: str | None = None, limit: int = 10) -> list[dict]:
    """Scrape search results. Returns list of {md5, title, meta}."""
    base = pick_mirror()
    params = {"q": query}
    if ext:
        params["ext"] = ext
    r = httpx.get(f"{base}/search", params=params, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    # Result cards link to /md5/<hash>; metadata is in adjacent text
    for a in soup.select("a[href^='/md5/']")[:limit]:
        md5 = a["href"].split("/md5/")[1].split("?")[0]
        title = a.get_text(strip=True)[:200]
        # Sibling text often has "epub, EN, 1.2MB" etc.
        meta = a.find_next("div")
        meta_text = meta.get_text(" ", strip=True) if meta else ""
        results.append(
            {
                "md5": md5,
                "title": title,
                "meta": meta_text,
            }
        )
    return results


def fast_download(md5: str, output_dir: Path) -> Path:
    """Use the fast_download JSON API to retrieve a direct URL, then fetch the file."""
    account_id = os.environ.get("ANNAS_ARCHIVE_ACCOUNT_ID")
    secret_key = os.environ.get("ANNAS_ARCHIVE_SECRET_KEY")
    if not account_id or not secret_key:
        raise RuntimeError("ANNAS_ARCHIVE_ACCOUNT_ID and ANNAS_ARCHIVE_SECRET_KEY must both be set")

    base = pick_mirror()
    api_url = f"{base}/dyn/api/fast_download.json"
    r = httpx.get(
        api_url,
        params={"md5": md5, "account_id": account_id, "secret_key": secret_key},
        timeout=30,
        headers={"User-Agent": UA},
    )
    r.raise_for_status()
    payload = r.json()

    if "download_url" not in payload:
        raise RuntimeError(f"API returned no download_url: {payload}")

    direct_url = payload["download_url"]
    # File extension is in the URL path or in the API response
    ext = payload.get("ext") or direct_url.rsplit(".", 1)[-1].split("?")[0]
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{md5}.{ext}"

    with httpx.stream(
        "GET", direct_url, timeout=300, headers={"User-Agent": UA}, follow_redirects=True
    ) as resp:
        resp.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                f.write(chunk)

    print(f"Downloaded: {out_path} ({out_path.stat().st_size:,} bytes)")
    return out_path


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--ext", choices=["epub", "pdf", "mobi", "azw3", "djvu"])
    s.add_argument("--limit", type=int, default=10)

    d = sub.add_parser("download")
    d.add_argument("md5")
    d.add_argument("--output", type=Path, default=Path("./library/raw"))

    args = p.parse_args()
    if args.cmd == "search":
        for hit in search(args.query, ext=args.ext, limit=args.limit):
            print(f"{hit['md5']}  {hit['title']}\n    {hit['meta']}")
    elif args.cmd == "download":
        fast_download(args.md5, args.output)
