#!/usr/bin/env python
"""
Unified Test Runner for Django Project Template

This script consolidates multiple test runners into a single unified interface.
It can run all types of tests:
- Unit tests
- Integration tests
- Browser-based tests (E2E, HTMX, responsive design)
- Visual tests
- Coverage reports

Examples:
    # Run all tests
    ./test_runner.py

    # Run with coverage
    ./test_runner.py --coverage

    # Run specific test types
    ./test_runner.py --type unit
    ./test_runner.py --type e2e --no-headless

    # Run tests with HTML coverage report
    ./test_runner.py --coverage --html-report
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def ensure_django_settings():
    """Ensure Django settings module is set."""
    if "DJANGO_SETTINGS_MODULE" not in os.environ:
        os.environ["DJANGO_SETTINGS_MODULE"] = "settings"
    print(f"Using Django settings module: {os.environ['DJANGO_SETTINGS_MODULE']}")


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Run Django tests")

    # Test selection
    parser.add_argument(
        "--type",
        choices=["all", "unit", "integration", "e2e", "visual", "htmx", "responsive"],
        default="all",
        help="Type of tests to run",
    )

    # Test paths
    parser.add_argument(
        "--path",
        default=None,
        help="Specific test path or pattern to run (e.g., apps/common/tests/test_models)",
    )

    # Browser options
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser tests in non-headless mode",
    )

    # Coverage options
    parser.add_argument(
        "--coverage", action="store_true", help="Run tests with coverage"
    )
    parser.add_argument(
        "--html-report", action="store_true", help="Generate HTML coverage report"
    )
    parser.add_argument(
        "--xml-report", action="store_true", help="Generate XML coverage report"
    )

    return parser.parse_args()


def get_pytest_args(args):
    """Build pytest command arguments based on options."""
    pytest_args = ["python", "-m", "pytest"]

    # Test path selection
    if args.path:
        pytest_args.append(args.path)
    elif args.type == "unit":
        pytest_args.append("apps/**/tests/test_*.py")
    elif args.type == "integration":
        pytest_args.append("apps/**/tests/test_integration_*.py")
    elif args.type == "e2e":
        pytest_args.append("apps/**/tests/test_e2e_*.py")
    elif args.type == "visual":
        pytest_args.append("apps/**/tests/test_visual_*.py")
    elif args.type == "htmx":
        pytest_args.append("apps/**/tests/test_htmx_*.py")
    elif args.type == "responsive":
        pytest_args.append("apps/**/tests/test_responsive_*.py")
    # "all" type doesn't add any path arguments

    # Add coverage args if requested
    if args.coverage:
        pytest_args.extend(["--cov=apps"])

        if args.html_report:
            pytest_args.append("--cov-report=html:reports/coverage_html")

        if args.xml_report:
            pytest_args.append("--cov-report=xml:reports/coverage.xml")

    # Add browser options for E2E tests
    if args.type in ["e2e", "visual", "htmx", "responsive"] and args.no_headless:
        os.environ["HEADLESS"] = "false"

    return pytest_args


def run_tests(cmd_args):
    """Run the tests with the given command arguments."""
    cmd_str = " ".join(cmd_args)
    print(f"\nRunning: {cmd_str}\n")

    try:
        result = subprocess.run(cmd_args, check=True)
        return result.returncode
    except subprocess.CalledProcessError as e:
        print(f"Tests failed with exit code {e.returncode}")
        return e.returncode


def main():
    """Main entry point of the test runner."""
    # Parse command-line arguments
    args = parse_args()

    # Ensure Django settings are set
    ensure_django_settings()

    # Create reports directory if needed
    if args.coverage and (args.html_report or args.xml_report):
        Path("reports").mkdir(exist_ok=True)

    # Build and run pytest command
    pytest_args = get_pytest_args(args)
    return run_tests(pytest_args)


if __name__ == "__main__":
    sys.exit(main())
