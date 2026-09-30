"""Shared regex patterns and utilities for extracting data from GitHub URLs."""

import logging
import re

logger = logging.getLogger(__name__)

ISSUE_NUMBER_RE = re.compile(r"/issues/(\d+)")
PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


def construct_canonical_url(url: str | None, gh_repo: str | None) -> str | None:
    """Construct a canonical GitHub URL from a worker-provided URL.

    Extracts the issue or PR number from the URL and constructs the canonical
    URL using the configured GH_REPO, preventing wrong-repo URLs.
    """
    if not url or not isinstance(url, str):
        return None

    url = url.strip()
    if not url:
        return None

    if not gh_repo:
        logger.warning(
            f"Cannot construct canonical URL: GH_REPO not configured. Original URL discarded: {url}"
        )
        return None

    pr_match = PR_NUMBER_RE.search(url)
    if pr_match:
        number = pr_match.group(1)
        return f"https://github.com/{gh_repo}/pull/{number}"

    issue_match = ISSUE_NUMBER_RE.search(url)
    if issue_match:
        number = issue_match.group(1)
        return f"https://github.com/{gh_repo}/issues/{number}"

    logger.warning(f"Cannot extract issue/PR number from URL: {url}. URL discarded.")
    return None
