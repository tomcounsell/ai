"""
Unit tests for the _downscale_if_needed helper in tools/browser/__init__.py.

These tests are pure unit tests — no browser, no network.
"""

from io import BytesIO


def _make_png(width: int, height: int) -> bytes:
    """Create a minimal valid PNG image of the given dimensions using Pillow."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_downscale_tall_image():
    """A 1280×4000 image should be downscaled so the longest edge is ≤ 1280."""
    from tools.browser import _downscale_if_needed

    data = _make_png(1280, 4000)
    result = _downscale_if_needed(data, max_dim=1280)

    from PIL import Image

    img = Image.open(BytesIO(result))
    assert max(img.width, img.height) <= 1280, (
        f"Expected longest edge ≤ 1280, got {img.width}×{img.height}"
    )


def test_downscale_wide_image():
    """A 3840×720 image should be downscaled so the longest edge is ≤ 1280."""
    from tools.browser import _downscale_if_needed

    data = _make_png(3840, 720)
    result = _downscale_if_needed(data, max_dim=1280)

    from PIL import Image

    img = Image.open(BytesIO(result))
    assert max(img.width, img.height) <= 1280


def test_no_downscale_small_image():
    """A 1280×720 image is already within bounds — bytes should be returned unchanged."""
    from tools.browser import _downscale_if_needed

    data = _make_png(1280, 720)
    result = _downscale_if_needed(data, max_dim=1280)
    assert result == data, "Image within bounds should not be modified"


def test_downscale_preserves_aspect_ratio():
    """Downscaling a 1280×3840 image should preserve the 1:3 aspect ratio."""
    from tools.browser import _downscale_if_needed

    data = _make_png(1280, 3840)
    result = _downscale_if_needed(data, max_dim=1280)

    from PIL import Image

    img = Image.open(BytesIO(result))
    # Height should be max_dim, width should be ~1/3 of that
    assert img.height == 1280
    assert abs(img.width - 427) <= 2, f"Expected width ~427, got {img.width}"


def test_downscale_empty_bytes_returns_original():
    """Empty bytes should be returned unchanged without raising."""
    from tools.browser import _downscale_if_needed

    result = _downscale_if_needed(b"", max_dim=1280)
    assert result == b""


def test_downscale_invalid_bytes_returns_original():
    """Invalid image bytes should be returned unchanged without raising."""
    from tools.browser import _downscale_if_needed

    bad_data = b"this is not an image"
    result = _downscale_if_needed(bad_data, max_dim=1280)
    assert result == bad_data, "Invalid bytes should be returned unchanged"
