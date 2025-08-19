"""
Screenshot service for capturing UI states.

This module provides a simple service for capturing screenshots
from the browser during development, testing, and debugging.
"""

import argparse
import asyncio
import os
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

# Check if necessary packages are available
try:
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    from browser_use import BrowserAgent

    BROWSER_USE_AVAILABLE = True
except ImportError:
    BROWSER_USE_AVAILABLE = False


class ScreenshotService:
    """Service for capturing screenshots during development and debugging."""

    def __init__(
        self,
        output_dir: str = "screenshots",
        use_browser_agent: bool = False,
        server_url: str = "http://localhost:8000",
        viewport: dict[str, int] = {"width": 1280, "height": 800},
        headless: bool = True,
        wait_before_capture: int = 500,  # ms
    ):
        """
        Initialize the screenshot service.

        Args:
            output_dir: Directory to save screenshots
            use_browser_agent: Whether to use browser-use for AI-powered screenshots
            server_url: URL of the development server
            viewport: Browser viewport dimensions
            headless: Whether to run browser in headless mode
            wait_before_capture: Time to wait before capturing screenshot (ms)
        """
        self.output_dir = output_dir
        self.use_browser_agent = use_browser_agent
        self.server_url = server_url
        self.viewport = viewport
        self.headless = headless
        self.wait_before_capture = wait_before_capture

        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)

    def capture(
        self,
        path: str,
        filename: str | None = None,
        wait_for_selector: str | None = None,
        full_page: bool = False,
        cookies: list[dict[str, Any]] | None = None,
        extra_wait_ms: int = 0,
    ) -> str:
        """
        Capture a screenshot of the specified URL path.

        Args:
            path: URL path to capture (relative to server_url)
            filename: Optional filename for the screenshot (generated if None)
            wait_for_selector: Optional CSS selector to wait for before capturing
            full_page: Whether to capture the full page or just the viewport
            cookies: Optional cookies to set before navigating
            extra_wait_ms: Additional time to wait before capturing (ms)

        Returns:
            Path to the saved screenshot
        """
        if not PLAYWRIGHT_AVAILABLE:
            print(
                "Error: Playwright not available. Install with 'uv add --dev playwright'"
            )
            return ""

        # Ensure path starts with a slash
        if not path.startswith("/"):
            path = "/" + path

        # Generate a filename if not provided
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            clean_path = path.replace("/", "_").strip("_")
            filename = f"{timestamp}_{clean_path}.png"

        # Ensure filename has .png extension
        if not filename.endswith(".png"):
            filename += ".png"

        # Full output path
        output_path = os.path.join(self.output_dir, filename)

        # Capture screenshot using Playwright
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context(viewport=self.viewport)

                # Add cookies if provided
                if cookies:
                    context.add_cookies(cookies)

                page = context.new_page()
                page.goto(f"{self.server_url}{path}")

                # Wait for selector if provided
                if wait_for_selector:
                    page.wait_for_selector(wait_for_selector, state="visible")

                # Wait for the specified time
                total_wait = self.wait_before_capture + extra_wait_ms
                if total_wait > 0:
                    page.wait_for_timeout(total_wait)

                # Take the screenshot
                page.screenshot(path=output_path, full_page=full_page)
                browser.close()

                print(f"Screenshot saved to: {output_path}")
                return output_path
        except Exception as e:
            print(f"Error capturing screenshot: {e}")
            return ""

    def capture_with_browser_agent(
        self,
        path: str,
        instructions: str | None = None,
        filename: str | None = None,
    ) -> str:
        """
        Capture a screenshot using browser-use's BrowserAgent with AI assistance.

        Args:
            path: URL path to capture (relative to server_url)
            instructions: Optional instructions for the agent
            filename: Optional filename for the screenshot (generated if None)

        Returns:
            Path to the saved screenshot
        """
        if not BROWSER_USE_AVAILABLE:
            print(
                "Error: browser-use not available. Install with 'uv add --dev browser-use'"
            )
            return ""

        # Generate a filename if not provided
        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            clean_path = path.replace("/", "_").strip("_")
            filename = f"{timestamp}_{clean_path}_agent.png"

        # Ensure filename has .png extension
        if not filename.endswith(".png"):
            filename += ".png"

        # Full output path
        output_path = os.path.join(self.output_dir, filename)

        # Ensure path starts with a slash
        if not path.startswith("/"):
            path = "/" + path

        # Run the capture task
        return asyncio.run(
            self._async_capture_with_agent(path, instructions, output_path)
        )

    async def _async_capture_with_agent(
        self, path: str, instructions: str | None = None, output_path: str = ""
    ) -> str:
        """Async implementation of capture_with_browser_agent."""
        if not instructions:
            instructions = f"""
            Navigate to {self.server_url}{path} and:
            1. Wait for the page to fully load
            2. Take a screenshot of the page
            """

        # Ensure the output directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Create the specific task with the output path
        task = f"""
        {instructions}

        Take a screenshot and save it to {output_path}
        """

        try:
            # Create and run the browser agent
            agent = BrowserAgent(
                tasks=[task],
                browser_type="chromium",
                headless=self.headless,
            )

            result = await agent.run()

            if os.path.exists(output_path):
                print(f"Screenshot saved to: {output_path}")
                return output_path
            else:
                print(f"Screenshot failed: {result}")
                return ""
        except Exception as e:
            print(f"Error during agent screenshot: {e}")
            return ""

    def ensure_server_running(self, port: int = 8000) -> bool:
        """
        Ensure that a Django development server is running.

        If no server is detected on the specified port, this method
        will attempt to start one.

        Args:
            port: Port to check and potentially start a server on

        Returns:
            True if a server is running after the check/start
        """
        import socket

        # Check if server is already running
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(("localhost", port))
        sock.close()

        if result == 0:
            print(f"Server already running on port {port}")
            return True

        # Start a server
        print(f"No server detected on port {port}. Starting one...")
        try:
            proc = subprocess.Popen(
                ["python", "manage.py", "runserver", f"{port}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )

            # Wait for server to start
            time.sleep(2)

            # Check if server started successfully
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex(("localhost", port))
            sock.close()

            if result == 0:
                print(f"Started server on port {port}")
                return True
            else:
                print("Failed to start server")
                return False
        except Exception as e:
            print(f"Error starting server: {e}")
            return False


def capture_screenshot(path: str, filename: str | None = None) -> str:
    """
    Convenience function to quickly capture a screenshot.

    Args:
        path: URL path to capture (relative to server_url)
        filename: Optional filename for the screenshot

    Returns:
        Path to the saved screenshot
    """
    service = ScreenshotService()
    return service.capture(path, filename)


if __name__ == "__main__":
    """
    Command-line interface for the screenshot service.

    Usage examples:

    # Capture a simple screenshot
    python screenshots.py /todos/

    # Use a specific filename
    python screenshots.py /accounts/login/ --filename login_screen.png

    # Wait for a specific element
    python screenshots.py /accounts/profile/ --wait-for "#profile-data"

    # Capture a full page screenshot
    python screenshots.py /docs/ --full-page

    # Use browser-use agent for more complex scenarios
    python screenshots.py /accounts/settings/ --use-agent --instructions "Fill in the email field with test@example.com"
    """
    parser = argparse.ArgumentParser(
        description="Capture screenshots of Django application pages"
    )

    parser.add_argument("path", help="URL path to capture (e.g., /todos/)")
    parser.add_argument("--filename", help="Filename for the screenshot")
    parser.add_argument(
        "--output-dir", default="screenshots", help="Directory to save screenshots"
    )
    parser.add_argument(
        "--server-url", default="http://localhost:8000", help="Server URL"
    )
    parser.add_argument("--wait-for", help="CSS selector to wait for")
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=500,
        help="Additional time to wait before capture (ms)",
    )
    parser.add_argument(
        "--full-page", action="store_true", help="Capture full page, not just viewport"
    )
    parser.add_argument(
        "--visible", action="store_true", help="Show browser window during capture"
    )
    parser.add_argument("--width", type=int, default=1280, help="Viewport width")
    parser.add_argument("--height", type=int, default=800, help="Viewport height")
    parser.add_argument(
        "--use-agent", action="store_true", help="Use browser-use agent for capture"
    )
    parser.add_argument("--instructions", help="Instructions for the browser-use agent")
    parser.add_argument(
        "--ensure-server", action="store_true", help="Ensure a Django server is running"
    )

    args = parser.parse_args()

    # Create service
    service = ScreenshotService(
        output_dir=args.output_dir,
        server_url=args.server_url,
        viewport={"width": args.width, "height": args.height},
        headless=not args.visible,
        wait_before_capture=args.wait_ms,
        use_browser_agent=args.use_agent,
    )

    # Ensure server is running if requested
    if args.ensure_server:
        port = int(args.server_url.split(":")[-1].split("/")[0])
        service.ensure_server_running(port)

    # Capture the screenshot
    if args.use_agent:
        if not BROWSER_USE_AVAILABLE:
            print(
                "Error: browser-use not available. Install with 'uv add --dev browser-use'"
            )
            exit(1)

        service.capture_with_browser_agent(
            args.path, instructions=args.instructions, filename=args.filename
        )
    else:
        if not PLAYWRIGHT_AVAILABLE:
            print(
                "Error: Playwright not available. Install with 'uv add --dev playwright'"
            )
            exit(1)

        service.capture(
            args.path,
            filename=args.filename,
            wait_for_selector=args.wait_for,
            full_page=args.full_page,
        )
