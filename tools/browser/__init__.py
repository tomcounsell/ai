"""
Browser Automation Tool

Web automation using Playwright for navigation, screenshots, and interaction.
"""

import base64
import os
from typing import Literal

# Check if playwright is available
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class BrowserError(Exception):
    """Browser operation failed."""

    def __init__(self, message: str, category: str = "execution"):
        self.message = message
        self.category = category
        super().__init__(message)


def _check_playwright():
    """Check if Playwright is available."""
    if not PLAYWRIGHT_AVAILABLE:
        return {"error": "Playwright not installed. Run: pip install playwright && playwright install chromium"}
    return None


def navigate(
    url: str,
    wait_for: str = "load",
    timeout_ms: int = 30000,
) -> dict:
    """
    Navigate to URL and return page content.

    Args:
        url: URL to navigate to
        wait_for: Wait condition (load, domcontentloaded, networkidle)
        timeout_ms: Timeout in milliseconds

    Returns:
        dict with keys:
            - title: Page title
            - url: Final URL (after redirects)
            - content: Page text content
            - html: Page HTML (truncated)
            - error: Error message (if failed)
    """
    error = _check_playwright()
    if error:
        return error

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(url, wait_until=wait_for, timeout=timeout_ms)

            result = {
                "title": page.title(),
                "url": page.url,
                "content": page.inner_text("body")[:10000],  # Limit content
                "html": page.content()[:50000],  # Limit HTML
            }

            browser.close()
            return result

    except PlaywrightTimeout:
        return {"error": f"Navigation timed out after {timeout_ms}ms", "url": url}
    except Exception as e:
        return {"error": f"Navigation failed: {str(e)}", "url": url}


def screenshot(
    url: str,
    full_page: bool = False,
    timeout_ms: int = 30000,
) -> dict:
    """
    Capture screenshot of a webpage.

    Args:
        url: URL to capture
        full_page: Capture full scrollable page
        timeout_ms: Timeout in milliseconds

    Returns:
        dict with keys:
            - image_base64: Base64 encoded PNG image
            - width: Image width
            - height: Image height
            - url: Final URL
            - error: Error message (if failed)
    """
    error = _check_playwright()
    if error:
        return error

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 720})

            page.goto(url, wait_until="networkidle", timeout=timeout_ms)

            screenshot_bytes = page.screenshot(full_page=full_page)
            image_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

            # Get dimensions from viewport or page
            if full_page:
                # Get full page dimensions
                dimensions = page.evaluate("""() => {
                    return {
                        width: document.documentElement.scrollWidth,
                        height: document.documentElement.scrollHeight
                    }
                }""")
            else:
                dimensions = {"width": 1280, "height": 720}

            result = {
                "image_base64": image_base64,
                "width": dimensions["width"],
                "height": dimensions["height"],
                "url": page.url,
            }

            browser.close()
            return result

    except PlaywrightTimeout:
        return {"error": f"Screenshot timed out after {timeout_ms}ms", "url": url}
    except Exception as e:
        return {"error": f"Screenshot failed: {str(e)}", "url": url}


def extract_text(
    url: str,
    selector: str = "body",
    timeout_ms: int = 30000,
) -> dict:
    """
    Extract text content from specific elements.

    Args:
        url: URL to extract from
        selector: CSS selector for elements
        timeout_ms: Timeout in milliseconds

    Returns:
        dict with keys:
            - text: Extracted text content
            - elements: Number of matching elements
            - url: Final URL
            - error: Error message (if failed)
    """
    error = _check_playwright()
    if error:
        return error

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            elements = page.query_selector_all(selector)
            texts = [el.inner_text() for el in elements]

            result = {
                "text": "\n\n".join(texts),
                "elements": len(elements),
                "url": page.url,
            }

            browser.close()
            return result

    except PlaywrightTimeout:
        return {"error": f"Extract timed out after {timeout_ms}ms", "url": url}
    except Exception as e:
        return {"error": f"Text extraction failed: {str(e)}", "url": url}


def fill_form(
    url: str,
    form_data: dict,
    submit_selector: str | None = None,
    timeout_ms: int = 30000,
) -> dict:
    """
    Fill form fields and optionally submit.

    Args:
        url: URL with form
        form_data: Dict of selector -> value pairs
        submit_selector: CSS selector for submit button (optional)
        timeout_ms: Timeout in milliseconds

    Returns:
        dict with keys:
            - filled: List of filled fields
            - submitted: Whether form was submitted
            - url: Final URL (after submit)
            - error: Error message (if failed)
    """
    error = _check_playwright()
    if error:
        return error

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            filled = []
            for selector, value in form_data.items():
                try:
                    page.fill(selector, value)
                    filled.append(selector)
                except Exception as e:
                    pass  # Skip fields that can't be filled

            submitted = False
            if submit_selector:
                try:
                    page.click(submit_selector)
                    page.wait_for_load_state("networkidle", timeout=timeout_ms)
                    submitted = True
                except Exception:
                    pass

            result = {
                "filled": filled,
                "submitted": submitted,
                "url": page.url,
            }

            browser.close()
            return result

    except PlaywrightTimeout:
        return {"error": f"Form fill timed out after {timeout_ms}ms", "url": url}
    except Exception as e:
        return {"error": f"Form fill failed: {str(e)}", "url": url}


def click(
    url: str,
    selector: str,
    wait_after: bool = True,
    timeout_ms: int = 30000,
) -> dict:
    """
    Click an element on a page.

    Args:
        url: URL to load
        selector: CSS selector for element to click
        wait_after: Wait for navigation after click
        timeout_ms: Timeout in milliseconds

    Returns:
        dict with keys:
            - clicked: Whether click succeeded
            - url: Final URL (after click)
            - error: Error message (if failed)
    """
    error = _check_playwright()
    if error:
        return error

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            page.click(selector, timeout=timeout_ms)

            if wait_after:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)

            result = {
                "clicked": True,
                "url": page.url,
            }

            browser.close()
            return result

    except PlaywrightTimeout:
        return {"error": f"Click timed out after {timeout_ms}ms", "url": url}
    except Exception as e:
        return {"error": f"Click failed: {str(e)}", "url": url}


def wait_for_element(
    url: str,
    selector: str,
    state: Literal["attached", "visible", "hidden", "detached"] = "visible",
    timeout_ms: int = 30000,
) -> dict:
    """
    Wait for an element to reach a specific state.

    Args:
        url: URL to load
        selector: CSS selector to wait for
        state: Element state to wait for
        timeout_ms: Timeout in milliseconds

    Returns:
        dict with keys:
            - found: Whether element was found in desired state
            - url: Final URL
            - error: Error message (if failed)
    """
    error = _check_playwright()
    if error:
        return error

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            page.wait_for_selector(selector, state=state, timeout=timeout_ms)

            result = {
                "found": True,
                "url": page.url,
            }

            browser.close()
            return result

    except PlaywrightTimeout:
        return {
            "error": f"Element '{selector}' not found in state '{state}' after {timeout_ms}ms",
            "url": url,
            "found": False,
        }
    except Exception as e:
        return {"error": f"Wait failed: {str(e)}", "url": url, "found": False}


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python -m tools.browser 'https://example.com'")
        sys.exit(1)

    url = sys.argv[1]
    print(f"Navigating to: {url}")

    result = navigate(url)

    if "error" in result:
        print(f"Error: {result['error']}")
        sys.exit(1)
    else:
        print(f"Title: {result['title']}")
        print(f"URL: {result['url']}")
        print(f"Content preview: {result['content'][:500]}...")
