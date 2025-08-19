#!/usr/bin/env python
"""
Browser Test Runner

This tool automates the process of starting a Django server,
running browser-based tests, and shutting down the server.
It supports both E2E tests and visual tests.
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class BrowserTestRunner:
    """
    Run browser-based tests with a managed Django server.

    This class handles:
    1. Starting a Django development server
    2. Running browser-based tests against that server
    3. Shutting down the server after tests complete
    """

    def __init__(
        self,
        headless: bool = True,
        browser: str = "chromium",
        verbose: bool = False,
        wait_time: int = 2,
        slow_mo: int = 0,
    ):
        self.headless = headless
        self.browser = browser
        self.verbose = verbose
        self.wait_time = wait_time
        self.slow_mo = slow_mo
        self.server_process: subprocess.Popen | None = None

    def _start_server(self) -> bool:
        """Start the Django development server."""
        if self.verbose:
            print("Starting Django server...")

        cmd = [
            sys.executable,
            str(PROJECT_ROOT / "manage.py"),
            "runserver",
            "--noreload",
            "8000",
        ]

        try:
            # Start the server process
            self.server_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE if not self.verbose else None,
                stderr=subprocess.PIPE if not self.verbose else None,
                text=True,
            )

            # Wait for server to start
            time.sleep(self.wait_time)

            # Check if server is running by making a request
            check_process = subprocess.run(
                ["curl", "-s", "http://localhost:8000/"],
                capture_output=True,
                text=True,
            )

            if check_process.returncode != 0:
                print("Error: Server not responding")
                self._stop_server()
                return False

            return True

        except Exception as e:
            print(f"Error starting server: {e}")
            self._stop_server()
            return False

    def _stop_server(self):
        """Stop the Django development server."""
        if self.server_process:
            if self.verbose:
                print("Stopping Django server...")

            try:
                # Try graceful shutdown first
                self.server_process.terminate()
                self.server_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill if not responding
                self.server_process.kill()

            self.server_process = None

    def _setup_environment(self) -> dict:
        """Set up environment variables for tests."""
        env = os.environ.copy()

        # Set Django settings module
        env["DJANGO_SETTINGS_MODULE"] = "settings"

        # Configure browser for tests
        env["TEST_BROWSER"] = self.browser

        # Set headless mode
        if self.headless:
            env["TEST_HEADLESS"] = "1"
        else:
            env.pop("TEST_HEADLESS", None)

        # Set slow motion for debugging
        if self.slow_mo > 0:
            env["TEST_SLOW_MO"] = str(self.slow_mo)

        return env

    def run_tests(self, test_paths: list[str]) -> tuple[int, str]:
        """Run browser-based tests, starting and stopping the server."""
        if not test_paths:
            return 1, "No test paths provided"

        # Check if server is already running
        check_server = subprocess.run(
            ["curl", "-s", "http://localhost:8000/"],
            capture_output=True,
        )

        server_started_by_us = False

        # Start the server if it's not already running
        if check_server.returncode != 0:
            if not self._start_server():
                return 1, "Failed to start server"
            server_started_by_us = True
        else:
            if self.verbose:
                print("Using existing Django server")

        try:
            # Set up environment
            env = self._setup_environment()

            # Prepare pytest command
            cmd = [
                "pytest",
                "-v",
            ]
            cmd.extend(test_paths)

            # Run the tests
            if self.verbose:
                print(f"Running tests: {' '.join(cmd)}")

            test_process = subprocess.run(
                cmd,
                env=env,
                stdout=subprocess.PIPE if not self.verbose else None,
                stderr=subprocess.PIPE if not self.verbose else None,
                text=True,
            )

            # Process and return results
            return test_process.returncode, test_process.stdout or ""

        finally:
            # Only stop the server if we started it
            if server_started_by_us:
                self._stop_server()


def main():
    """Main entry point for the command-line tool."""
    parser = argparse.ArgumentParser(description="Browser-based test runner")
    parser.add_argument(
        "test_paths", nargs="+", help="Paths to test files or directories"
    )
    parser.add_argument(
        "--no-headless", action="store_true", help="Run browser in non-headless mode"
    )
    parser.add_argument(
        "--browser",
        choices=["chromium", "firefox", "webkit"],
        default="chromium",
        help="Browser to use for testing",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )
    parser.add_argument(
        "--wait-time",
        type=int,
        default=2,
        help="Time to wait for server to start (seconds)",
    )
    parser.add_argument(
        "--slow-mo",
        type=int,
        default=0,
        help="Slow down browser actions by specified milliseconds",
    )

    args = parser.parse_args()

    runner = BrowserTestRunner(
        headless=not args.no_headless,
        browser=args.browser,
        verbose=args.verbose,
        wait_time=args.wait_time,
        slow_mo=args.slow_mo,
    )

    exit_code, output = runner.run_tests(args.test_paths)

    if not args.verbose and output:
        print(output)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
