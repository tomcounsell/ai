"""Live-network YouTube search tests.

These hit the real YouTube endpoint, so they live in ``tests/integration/``
rather than ``tests/unit/``. No ``-m`` filter is applied to the unit suite, so
while this class sat in ``tests/unit/test_youtube_search.py`` its
``integration``/``slow`` markers were decorative and every unit run paid for a
network round trip (#2628).
"""

import pytest

from tools.youtube_search import youtube_search_sync

pytestmark = [pytest.mark.integration, pytest.mark.slow]


class TestSearchIntegration:
    """Integration tests that hit real YouTube. Require network."""

    def test_real_search_returns_results(self):
        results = youtube_search_sync("python tutorial", limit=3)
        assert len(results) > 0
        for r in results:
            assert r["title"]
            assert r["url"]
            assert r["video_id"]
            assert "youtube.com" in r["url"] or "youtu.be" in r["url"]

    def test_real_search_limit(self):
        results = youtube_search_sync("python", limit=2)
        assert len(results) <= 2
