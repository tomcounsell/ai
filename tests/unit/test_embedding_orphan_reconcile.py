"""Tests for ``scripts/embedding_orphan_reconcile.py`` (#1214).

Covers:
- Dry-run default — no files removed.
- ``--apply`` — orphans removed, live files preserved (5 live + 50 stray fixture).
- Positive-assertion safety check — refuses to apply if any live filename
  is in the to-delete set (catches inverted-logic bugs deterministically).
- Pre-flight guard — refuses to apply if ``$Class:Memory`` returns empty
  (B-A regression: defense-in-depth even if the helper is wired wrong).
- Mtime guard — recently-written orphans are kept.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from unittest import mock

import pytest

# Add scripts/ to path so we can import the script as a module
SCRIPT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts",
)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import embedding_orphan_reconcile as recon  # noqa: E402


def _live_filename(redis_key: str) -> str:
    return hashlib.sha256(redis_key.encode("utf-8")).hexdigest() + ".npy"


def _make_npy(path: str, mtime_offset_seconds: float = 0) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x00" * 16)
    if mtime_offset_seconds:
        old = time.time() - mtime_offset_seconds
        os.utime(path, (old, old))


@pytest.fixture
def fixture_5_live_50_stray(tmp_path):
    """Build a 5-live + 50-stray .npy fixture in a temp embedding dir.

    Returns (live_set, stray_set, emb_dir, expected_keep_set).
    """
    base = str(tmp_path / "content")
    emb_dir = os.path.join(base, ".embeddings", "Memory")
    os.makedirs(emb_dir, exist_ok=True)

    live_keys = [f"Memory:live{i}:proj" for i in range(5)]
    live_filenames = {_live_filename(k) for k in live_keys}
    for fname in live_filenames:
        _make_npy(os.path.join(emb_dir, fname), mtime_offset_seconds=99999)

    stray_filenames = {f"{i:064x}.npy" for i in range(1, 51)}  # 50 fake hashes
    for fname in stray_filenames:
        _make_npy(os.path.join(emb_dir, fname), mtime_offset_seconds=99999)

    return live_keys, live_filenames, stray_filenames, emb_dir, base


def _patch_environ_for(emb_base: str):
    return mock.patch.dict(os.environ, {"POPOTO_CONTENT_PATH": emb_base})


def _patch_helper(expected_keep):
    """Patch the shared helper to return a known expected_keep set."""
    return mock.patch(
        "popoto.fields.embedding_field._compute_expected_keep",
        return_value=expected_keep,
    )


def _patch_redis_scard(value: int):
    """Patch the redis client used inside reconcile() for the live count print."""
    fake = mock.MagicMock()
    fake.scard.return_value = value
    return mock.patch("popoto.redis_db.POPOTO_REDIS_DB", fake)


def _patch_memory_class():
    """Provide a Memory mock with a stable canonical class-set key."""
    fake = mock.MagicMock()
    fake.__name__ = "Memory"
    fake._meta.db_class_set_key.redis_key = "$Class:Memory"
    return mock.patch.object(recon, "_resolve_memory_class", return_value=fake)


class TestDryRun:
    def test_dry_run_default_does_not_unlink(self, fixture_5_live_50_stray):
        live_keys, live_filenames, stray_filenames, emb_dir, base = fixture_5_live_50_stray
        with (
            _patch_environ_for(base),
            _patch_helper(live_filenames),
            _patch_redis_scard(5),
            _patch_memory_class(),
        ):
            removed = recon.reconcile(dry_run=True, min_age_seconds=300, verbose=False)

        # Nothing removed
        assert removed == 0
        # All files still present
        assert len(os.listdir(emb_dir)) == 55


class TestApply:
    def test_apply_removes_strays_keeps_live(self, fixture_5_live_50_stray):
        live_keys, live_filenames, stray_filenames, emb_dir, base = fixture_5_live_50_stray
        with (
            _patch_environ_for(base),
            _patch_helper(live_filenames),
            _patch_redis_scard(5),
            _patch_memory_class(),
        ):
            removed = recon.reconcile(dry_run=False, min_age_seconds=300, verbose=False)

        assert removed == 50
        remaining = set(os.listdir(emb_dir))
        assert remaining == live_filenames, (
            f"Live files must survive; got {len(remaining)} remaining: "
            f"missing={live_filenames - remaining}, "
            f"unexpected={remaining - live_filenames}"
        )

    def test_apply_skips_recent_orphans(self, tmp_path):
        """Files newer than min_age_seconds must NOT be deleted."""
        base = str(tmp_path / "content")
        emb_dir = os.path.join(base, ".embeddings", "Memory")
        os.makedirs(emb_dir, exist_ok=True)

        recent_orphan = os.path.join(emb_dir, "f" * 64 + ".npy")
        _make_npy(recent_orphan, mtime_offset_seconds=10)  # 10s old, fresh

        with (
            _patch_environ_for(base),
            _patch_helper({"someother.npy"}),  # treat the recent file as orphan
            _patch_redis_scard(1),
            _patch_memory_class(),
        ):
            removed = recon.reconcile(dry_run=False, min_age_seconds=300, verbose=False)

        assert removed == 0
        assert os.path.exists(recent_orphan)


class TestSafetyChecks:
    def test_collision_refuses_to_apply(self, fixture_5_live_50_stray):
        """C5: assert collision between expected_keep and to_delete refuses apply."""
        live_keys, live_filenames, stray_filenames, emb_dir, base = fixture_5_live_50_stray
        # Pretend the helper returns an empty set (simulating an inverted bug
        # — every live file would now be classified as orphan)
        # but keep one live file ALSO appearing as a "stray" in expected_keep
        # to trigger a collision. Easier: set expected_keep to include a stray
        # filename, so when classifier runs it produces a to_delete set that
        # intersects expected_keep.
        # Actually the simpler path: the collision is computed AFTER
        # classification — so spike classify_orphans to return both.
        bad_to_delete = {next(iter(live_filenames))}  # one live filename
        bad_expected = live_filenames

        with (
            _patch_environ_for(base),
            _patch_redis_scard(5),
            _patch_memory_class(),
            mock.patch.object(
                recon,
                "_classify_orphans",
                return_value=(
                    bad_expected,
                    bad_to_delete,
                    set(),
                    set(),
                    emb_dir,
                ),
            ),
        ):
            with pytest.raises(SystemExit) as exc:
                recon.reconcile(dry_run=False, min_age_seconds=300, verbose=False)
            msg = str(exc.value)
            assert "REFUSE" in msg
            assert "live-record files" in msg

    def test_empty_expected_keep_refuses_to_apply(self, fixture_5_live_50_stray):
        """B-A regression: refuse to apply if $Class:Memory returns empty.

        Defense-in-depth — even if the shared helper is regressed to read
        the wrong (empty) key, this script must not delete every file.
        """
        live_keys, live_filenames, stray_filenames, emb_dir, base = fixture_5_live_50_stray
        with (
            _patch_environ_for(base),
            _patch_helper(set()),  # simulate the data-destruction bug
            _patch_redis_scard(0),
            _patch_memory_class(),
            # Spy on os.unlink — must NEVER be called
            mock.patch("embedding_orphan_reconcile.os.unlink") as unlink_spy,
        ):
            with pytest.raises(SystemExit) as exc:
                recon.reconcile(dry_run=False, min_age_seconds=300, verbose=False)
            msg = str(exc.value)
            assert "REFUSE" in msg
            assert "data-destruction" in msg
            assert unlink_spy.call_count == 0


class TestNoDirectory:
    def test_missing_directory_returns_zero(self, tmp_path):
        """Fresh-install case: no embedding directory exists."""
        base = str(tmp_path / "no_such_path")
        with (
            _patch_environ_for(base),
            _patch_helper(set()),
            _patch_redis_scard(0),
            _patch_memory_class(),
        ):
            removed = recon.reconcile(dry_run=False, min_age_seconds=300, verbose=False)
        assert removed == 0
