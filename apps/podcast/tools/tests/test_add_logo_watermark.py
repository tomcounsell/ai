"""Tests for add_logo_watermark.py pure functions."""

import sys
from pathlib import Path

import pytest
from PIL import Image

# Add parent directory to path to import the module
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.mark.skip(reason="Function not yet implemented: add_border in add_logo_watermark.py")
class TestAddBorder:
    """Tests for the add_border function and hex color parsing."""

    def test_hex_color_with_hash(self):
        """Test parsing hex color with # prefix."""
        # Create a simple test image
        img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))

        # Add yellow border
        bordered = add_border(img, 10, "#FFC20E")

        # Check new dimensions
        assert bordered.width == 120
        assert bordered.height == 120

        # Check border color (top-left corner should be yellow)
        pixel = bordered.getpixel((0, 0))
        assert pixel == (255, 194, 14, 255)  # RGBA for #FFC20E

    def test_hex_color_without_hash(self):
        """Test parsing hex color without # prefix (should still work via lstrip)."""
        img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))

        bordered = add_border(img, 10, "FFC20E")

        pixel = bordered.getpixel((0, 0))
        assert pixel == (255, 194, 14, 255)

    def test_black_border(self):
        """Test black border color."""
        img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))

        bordered = add_border(img, 5, "#000000")

        # Check border color
        pixel = bordered.getpixel((0, 0))
        assert pixel == (0, 0, 0, 255)

    def test_white_border(self):
        """Test white border color."""
        img = Image.new("RGBA", (100, 100), (0, 0, 0, 255))

        bordered = add_border(img, 5, "#FFFFFF")

        pixel = bordered.getpixel((0, 0))
        assert pixel == (255, 255, 255, 255)

    def test_red_border(self):
        """Test red border color."""
        img = Image.new("RGBA", (50, 50), (255, 255, 255, 255))

        bordered = add_border(img, 3, "#FF0000")

        pixel = bordered.getpixel((0, 0))
        assert pixel == (255, 0, 0, 255)

    def test_green_border(self):
        """Test green border color."""
        img = Image.new("RGBA", (50, 50), (255, 255, 255, 255))

        bordered = add_border(img, 3, "#00FF00")

        pixel = bordered.getpixel((0, 0))
        assert pixel == (0, 255, 0, 255)

    def test_blue_border(self):
        """Test blue border color."""
        img = Image.new("RGBA", (50, 50), (255, 255, 255, 255))

        bordered = add_border(img, 3, "#0000FF")

        pixel = bordered.getpixel((0, 0))
        assert pixel == (0, 0, 255, 255)

    def test_border_width_calculation(self):
        """Test that border width is correctly applied on all sides."""
        img = Image.new("RGBA", (100, 100), (128, 128, 128, 255))
        border_width = 20

        bordered = add_border(img, border_width, "#FFC20E")

        # New dimensions should be original + 2*border_width
        assert bordered.width == 100 + (border_width * 2)
        assert bordered.height == 100 + (border_width * 2)

    def test_original_image_preserved(self):
        """Test that the original image is correctly centered in the border."""
        # Create image with distinctive color
        original_color = (100, 150, 200, 255)
        img = Image.new("RGBA", (50, 50), original_color)
        border_width = 10

        bordered = add_border(img, border_width, "#000000")

        # Check that center pixel matches original color
        center_x = border_width + 25  # border + half of original width
        center_y = border_width + 25
        pixel = bordered.getpixel((center_x, center_y))
        assert pixel == original_color

    def test_small_border(self):
        """Test with very small border width."""
        img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))

        bordered = add_border(img, 1, "#FF0000")

        assert bordered.width == 102
        assert bordered.height == 102

    def test_large_border(self):
        """Test with large border width."""
        img = Image.new("RGBA", (50, 50), (255, 255, 255, 255))
        border_width = 50

        bordered = add_border(img, border_width, "#0000FF")

        assert bordered.width == 150
        assert bordered.height == 150

        # Border should be visible on edges
        assert bordered.getpixel((0, 0)) == (0, 0, 255, 255)
        assert bordered.getpixel((149, 149)) == (0, 0, 255, 255)

    def test_rectangular_image(self):
        """Test border on non-square image."""
        img = Image.new("RGBA", (200, 100), (255, 255, 255, 255))
        border_width = 15

        bordered = add_border(img, border_width, "#FFC20E")

        assert bordered.width == 230  # 200 + 30
        assert bordered.height == 130  # 100 + 30

    def test_hex_color_case_insensitive(self):
        """Test that hex color parsing works with different cases."""
        img = Image.new("RGBA", (50, 50), (255, 255, 255, 255))

        # Lowercase
        bordered1 = add_border(img.copy(), 5, "#ffffff")
        # Uppercase
        bordered2 = add_border(img.copy(), 5, "#FFFFFF")
        # Mixed
        bordered3 = add_border(img.copy(), 5, "#FfFfFf")

        # All should produce same result
        assert bordered1.getpixel((0, 0)) == (255, 255, 255, 255)
        assert bordered2.getpixel((0, 0)) == (255, 255, 255, 255)
        assert bordered3.getpixel((0, 0)) == (255, 255, 255, 255)

    def test_default_border_color(self):
        """Test the default yellow border color."""
        img = Image.new("RGBA", (100, 100), (255, 255, 255, 255))

        # Use default color (should be #FFC20E)
        bordered = add_border(img, 10)

        pixel = bordered.getpixel((0, 0))
        assert pixel == (255, 194, 14, 255)
