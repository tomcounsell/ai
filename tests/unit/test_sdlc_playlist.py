"""Tests for SDLC job playlist and Observer hook.

Validates:
1. Redis playlist operations (push, pop, status, requeue, clear)
2. Observer playlist hook behavior on job completion
3. Failure requeue with retry limit
4. Guard against scheduling the same issue that just completed
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Playlist Redis operation tests (mocked Redis)
# ---------------------------------------------------------------------------


class TestPlaylistOperations:
    """Tests for playlist_push, playlist_pop, playlist_status, playlist_requeue."""

    def _mock_redis(self):
        """Create a mock Redis that simulates list and hash operations."""
        store = {"lists": {}, "hashes": {}}

        mock = MagicMock()

        def rpush(key, value):
            store["lists"].setdefault(key, [])
            store["lists"][key].append(value)

        def lpop(key):
            lst = store["lists"].get(key, [])
            return lst.pop(0) if lst else None

        def llen(key):
            return len(store["lists"].get(key, []))

        def lrange(key, start, end):
            lst = store["lists"].get(key, [])
            if end == -1:
                return lst[start:]
            return lst[start : end + 1]

        def hget(key, field):
            return store["hashes"].get(key, {}).get(field)

        def hincrby(key, field, amount):
            store["hashes"].setdefault(key, {})
            current = int(store["hashes"][key].get(field, 0))
            store["hashes"][key][field] = str(current + amount)

        def exists(key):
            return key in store["hashes"] and len(store["hashes"][key]) > 0

        def hgetall(key):
            return store["hashes"].get(key, {})

        def delete(key):
            store["lists"].pop(key, None)
            store["hashes"].pop(key, None)

        mock.rpush = rpush
        mock.lpop = lpop
        mock.llen = llen
        mock.lrange = lrange
        mock.hget = hget
        mock.hincrby = hincrby
        mock.exists = exists
        mock.hgetall = hgetall
        mock.delete = delete

        return mock

    @patch("tools.job_scheduler._get_redis")
    def test_playlist_push_multiple_issues(self, mock_get_redis):
        from tools.job_scheduler import playlist_push, playlist_status

        mock_get_redis.return_value = self._mock_redis()

        result = playlist_push("valor", [440, 445, 397])
        assert result == 3

        items = playlist_status("valor")
        assert items == [440, 445, 397]

    @patch("tools.job_scheduler._get_redis")
    def test_playlist_pop_returns_first(self, mock_get_redis):
        from tools.job_scheduler import playlist_pop, playlist_push

        mock_get_redis.return_value = self._mock_redis()

        playlist_push("valor", [440, 445])
        result = playlist_pop("valor")
        assert result == 440

    @patch("tools.job_scheduler._get_redis")
    def test_playlist_pop_empty_returns_none(self, mock_get_redis):
        from tools.job_scheduler import playlist_pop

        mock_get_redis.return_value = self._mock_redis()

        result = playlist_pop("valor")
        assert result is None

    @patch("tools.job_scheduler._get_redis")
    def test_playlist_requeue_success(self, mock_get_redis):
        from tools.job_scheduler import playlist_requeue, playlist_status

        mock_get_redis.return_value = self._mock_redis()

        result = playlist_requeue("valor", 440)
        assert result is True

        items = playlist_status("valor")
        assert items == [440]

    @patch("tools.job_scheduler._get_redis")
    def test_playlist_requeue_max_retries(self, mock_get_redis):
        from tools.job_scheduler import playlist_requeue

        mock_get_redis.return_value = self._mock_redis()

        # First requeue succeeds
        assert playlist_requeue("valor", 440) is True
        # Second requeue fails (max 1 retry)
        assert playlist_requeue("valor", 440) is False

    @patch("tools.job_scheduler._get_redis")
    def test_playlist_clear(self, mock_get_redis):
        from tools.job_scheduler import playlist_clear, playlist_push, playlist_status

        mock_get_redis.return_value = self._mock_redis()

        playlist_push("valor", [440, 445])
        playlist_clear("valor")
        items = playlist_status("valor")
        assert items == []

    @patch("tools.job_scheduler._get_redis")
    def test_playlist_status_empty(self, mock_get_redis):
        from tools.job_scheduler import playlist_status

        mock_get_redis.return_value = self._mock_redis()

        items = playlist_status("valor")
        assert items == []


# ---------------------------------------------------------------------------
# Observer playlist hook tests
# ---------------------------------------------------------------------------


class TestObserverPlaylistHook:
    """Tests for the _playlist_hook function in job_queue."""

    def test_hook_pops_next_issue_on_success(self):
        """When an SDLC job completes, the hook should pop and schedule the next issue."""
        from agent.job_queue import _playlist_hook

        with (
            patch("tools.job_scheduler.playlist_pop", return_value=445) as mock_pop,
            patch("agent.job_queue.subprocess") as mock_subprocess,
        ):
            mock_subprocess.run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")

            asyncio.get_event_loop().run_until_complete(
                _playlist_hook(
                    "valor",
                    "12345",
                    "https://github.com/tomcounsell/ai/issues/440",
                    failed=False,
                )
            )

            mock_pop.assert_called_once_with("valor")
            mock_subprocess.run.assert_called_once()
            # Verify the scheduled issue is 445
            call_args = mock_subprocess.run.call_args[0][0]
            assert "445" in call_args

    def test_hook_delivers_summary_on_empty_playlist(self):
        """When playlist is empty after completion, deliver summary."""
        from agent.job_queue import _playlist_hook

        with (
            patch("tools.job_scheduler.playlist_pop", return_value=None),
            patch("agent.job_queue._log_playlist_exhausted") as mock_summary,
        ):
            asyncio.get_event_loop().run_until_complete(
                _playlist_hook("valor", "12345", None, failed=False)
            )

            mock_summary.assert_called_once_with("valor", "12345")

    def test_hook_requeues_on_failure(self):
        """When an SDLC job fails, requeue it to end of playlist."""
        from agent.job_queue import _playlist_hook

        with (
            patch("tools.job_scheduler.playlist_requeue", return_value=True) as mock_requeue,
            patch("tools.job_scheduler.playlist_pop", return_value=None),
            patch("agent.job_queue._log_playlist_exhausted"),
        ):
            asyncio.get_event_loop().run_until_complete(
                _playlist_hook(
                    "valor",
                    "12345",
                    "https://github.com/tomcounsell/ai/issues/440",
                    failed=True,
                )
            )

            mock_requeue.assert_called_once_with("valor", 440)

    def test_hook_skips_same_issue(self):
        """Guard: don't schedule the same issue that just completed."""
        from agent.job_queue import _playlist_hook

        # First pop returns the same issue (440), second pop returns next (445)
        pop_returns = iter([440, 445])

        with (
            patch("tools.job_scheduler.playlist_pop", side_effect=lambda _: next(pop_returns)),
            patch("agent.job_queue.subprocess") as mock_subprocess,
        ):
            mock_subprocess.run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")

            asyncio.get_event_loop().run_until_complete(
                _playlist_hook(
                    "valor",
                    "12345",
                    "https://github.com/tomcounsell/ai/issues/440",
                    failed=False,
                )
            )

            # Should have scheduled 445, not 440
            call_args = mock_subprocess.run.call_args[0][0]
            assert "445" in call_args
