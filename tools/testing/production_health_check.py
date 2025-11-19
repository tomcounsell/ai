#!/usr/bin/env python3
"""
Production health check script.

Run with: python tools/testing/production_health_check.py
"""

import sys
from typing import Dict, List, Tuple

try:
    import requests
except ImportError:
    print("Error: requests library not found. Install with: pip install requests")
    sys.exit(1)

PRODUCTION_BASE_URL = "https://ai.yuda.me"

ENDPOINTS_TO_CHECK = [
    # (url, expected_status, check_cors, description)
    ("/", 200, False, "Homepage"),
    ("/health/", 200, False, "Basic health check"),
    ("/health/deep/", 200, False, "Deep health check"),
    ("/mcp/creative-juices/", 200, False, "Creative Juices landing"),
    (
        "/mcp/creative-juices/manifest.json",
        200,
        True,
        "Creative Juices manifest",
    ),
    ("/mcp/creative-juices/README.md", 200, True, "Creative Juices README"),
]


def check_endpoint(
    url: str, expected_status: int, check_cors: bool, description: str
) -> tuple[bool, str]:
    """Check a single endpoint."""
    full_url = f"{PRODUCTION_BASE_URL}{url}"

    try:
        response = requests.get(full_url, timeout=10)

        # Check status code
        if response.status_code != expected_status:
            return False, f"Expected {expected_status}, got {response.status_code}"

        # Check CORS headers if required
        if check_cors:
            cors_header = response.headers.get("Access-Control-Allow-Origin")
            if not cors_header:
                return False, "Missing CORS header"

        return True, "OK"

    except requests.exceptions.RequestException as e:
        return False, f"Request failed: {str(e)}"


def run_health_checks() -> bool:
    """Run all health checks and report results."""
    print(f"Running production health checks for {PRODUCTION_BASE_URL}...")
    print("=" * 80)

    results: list[dict] = []
    all_passed = True

    for url, expected_status, check_cors, description in ENDPOINTS_TO_CHECK:
        print(f"\nChecking: {description}")
        print(f"  URL: {url}")

        passed, message = check_endpoint(url, expected_status, check_cors, description)

        results.append(
            {
                "description": description,
                "url": url,
                "passed": passed,
                "message": message,
            }
        )

        if passed:
            print(f"  ✅ {message}")
        else:
            print(f"  ❌ {message}")
            all_passed = False

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    passed_count = sum(1 for r in results if r["passed"])
    total_count = len(results)

    print(f"\nPassed: {passed_count}/{total_count}")

    if all_passed:
        print("\n✅ All health checks passed!")
        return True
    else:
        print("\n❌ Some health checks failed!")
        return False


if __name__ == "__main__":
    success = run_health_checks()
    sys.exit(0 if success else 1)
