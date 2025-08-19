#!/usr/bin/env python
"""
Coverage Reporter

This tool generates test coverage reports in various formats.
It supports HTML, XML, and terminal reports for different test types.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class CoverageReporter:
    """
    Generate test coverage reports in various formats.

    This class provides methods to:
    1. Run tests with coverage measurement
    2. Generate reports in different formats (HTML, XML, terminal)
    3. Create coverage badges for documentation
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.reports_dir = PROJECT_ROOT / "reports"
        os.makedirs(self.reports_dir, exist_ok=True)

    def generate_report(
        self,
        test_paths: list[str],
        report_type: str = "html",
        report_name: str = "coverage",
    ) -> tuple[bool, str]:
        """Generate a coverage report for the specified test paths."""
        if not test_paths:
            return False, "No test paths provided"

        # Determine report path
        if report_type == "html":
            report_path = self.reports_dir / f"{report_name}_html"
            report_arg = f"html:{report_path}"
        elif report_type == "xml":
            report_path = self.reports_dir / f"{report_name}.xml"
            report_arg = f"xml:{report_path}"
        else:
            report_path = None
            report_arg = report_type

        # Prepare pytest command
        cmd = [
            "DJANGO_SETTINGS_MODULE=settings",
            "pytest",
            "--cov=apps",
            f"--cov-report={report_arg}",
        ]
        cmd.extend(test_paths)

        try:
            # Run pytest with coverage
            if self.verbose:
                print(f"Running: {' '.join(cmd)}")

            process = subprocess.run(
                " ".join(cmd),
                shell=True,
                stdout=subprocess.PIPE if not self.verbose else None,
                stderr=subprocess.PIPE if not self.verbose else None,
                text=True,
            )

            # Process result
            if process.returncode != 0:
                error_msg = process.stderr or "Unknown error"
                return False, f"Error generating coverage report: {error_msg}"

            success_msg = "Coverage report generated successfully"
            if report_path:
                success_msg += f" at {report_path}"

            return True, success_msg

        except Exception as e:
            return False, f"Exception generating coverage report: {str(e)}"

    def generate_badge(self, xml_path: Path) -> bool:
        """Generate a coverage badge from an XML report."""
        try:
            # Placeholder for badge generation
            # In a real implementation, this would parse the XML and generate a badge
            badge_path = self.reports_dir / "coverage-badge.svg"

            # Simulate badge creation (in a real app, you'd use a library like coverage-badge)
            with open(badge_path, "w") as f:
                f.write("<svg><!-- Coverage badge would go here --></svg>")

            return True

        except Exception as e:
            if self.verbose:
                print(f"Error generating badge: {e}")

            return False


def main():
    """Main entry point for the command-line tool."""
    parser = argparse.ArgumentParser(description="Test coverage reporter")
    parser.add_argument(
        "test_paths", nargs="+", help="Paths to test files or directories"
    )
    parser.add_argument(
        "--type",
        choices=["html", "xml", "term", "term-missing"],
        default="html",
        help="Type of coverage report to generate",
    )
    parser.add_argument(
        "--name", default="coverage", help="Base name for the coverage report"
    )
    parser.add_argument(
        "--badge",
        action="store_true",
        help="Generate a coverage badge (only works with XML report)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose output"
    )

    args = parser.parse_args()

    reporter = CoverageReporter(verbose=args.verbose)

    success, message = reporter.generate_report(
        args.test_paths,
        args.type,
        args.name,
    )

    print(message)

    if success and args.badge and args.type == "xml":
        xml_path = reporter.reports_dir / f"{args.name}.xml"
        if reporter.generate_badge(xml_path):
            print(
                f"Coverage badge generated at {reporter.reports_dir}/coverage-badge.svg"
            )
        else:
            print("Failed to generate coverage badge")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
