"""URL slugs from titles."""

import re
import unicodedata


def slugify(title: str, max_length: int = 60) -> str:
    """Lowercase ASCII, words joined by single hyphens, no leading or trailing
    hyphen, cut at max_length on a hyphen boundary where possible."""
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(text) <= max_length:
        return text
    cut = text[:max_length]
    return cut.rsplit("-", 1)[0] if "-" in cut else cut
