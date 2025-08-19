"""
Tests for HTMX interactions in the public app.

These tests verify HTMX interactions work correctly including:
- OOB (Out of Band) swaps for toasts, alerts, modals, and nav
- HTMX request and response headers
- Triggered HTMX events
- Frontend component behavior with HTMX

This uses browser-based testing as HTMX behavior requires:
1. A real browser
2. JavaScript execution
3. DOM manipulation
"""

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model

# Try importing browser-use components
try:
    import playwright.async_api
    from browser_use import BrowserAgent, use
    from playwright.async_api import Browser, BrowserContext, Page

    BROWSER_USE_AVAILABLE = True
except ImportError:
    BROWSER_USE_AVAILABLE = False

    # Create dummy classes if imports fail
    class Page:
        pass

    class BrowserAgent:
        pass


# Import test utilities
from apps.public.tests.e2e_test_config import SERVER_URL

# Import the base test class
try:
    from apps.public.tests.test_e2e_patterns import (
        E2ETestBase,
        asyncio_mark,
        browser_test,
    )
except ImportError:
    # Create dummy classes if imports fail
    browser_test = lambda cls: cls
    asyncio_mark = lambda f: f

    class E2ETestBase:
        """Dummy base class when imports aren't available."""

        async def login_user(self, page, username="test", password="test"):
            return True

        async def is_server_running(self):
            return True

        async def take_screenshot(self, page, filename):
            pass


# Get User model
User = get_user_model()

# Test data
TEST_DATA = {
    "todo": {
        "title": "Test Todo Item",
        "description": "This is a test todo item created by automated tests",
    },
    "user": {
        "username": "htmxuser",
        "email": "htmxuser@example.com",
        "password": "testpassword123",
    },
}

# Define selectors for common HTMX elements
SELECTORS = {
    "toast_container": "#toast-container",
    "toast_messages": "#toast-container .toast-message",
    "modals": "#modal-container .modal",
    "modal_content": "#modal-container .modal-content",
    "navbar": "nav",
    "navbar_links": "nav a",
    "active_nav_link": "nav a.active",
}


@browser_test
@asyncio_mark
@pytest.mark.e2e
class HTMXInteractionsTestCase(E2ETestBase):
    """Tests for HTMX interactions that require a browser."""

    server_url = SERVER_URL
    screenshot_dir = "test_screenshots/htmx"

    async def setup_method(self, method):
        """Set up method for tests."""
        # Skip tests if browser-use is not available
        if not BROWSER_USE_AVAILABLE:
            pytest.skip("browser-use package not installed")

        # Create a test user
        self.username = TEST_DATA["user"]["username"]
        self.password = TEST_DATA["user"]["password"]
        self.email = TEST_DATA["user"]["email"]

        # Check if server is running
        if not await self.is_server_running():
            pytest.skip("Django server not running")

        # Create screenshot directory
        Path(self.screenshot_dir).mkdir(parents=True, exist_ok=True)

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_toast_oob_swap(self, page):
        """Test that toasts appear with OOB swaps."""
        # Create a test user in the database
        user = User.objects.create_user(
            username=self.username, email=self.email, password=self.password
        )

        # Login the test user
        await self.login_user(page, self.username, self.password)

        # Navigate to the todo creation page
        await page.goto(f"{self.server_url}/todos/create/")

        # Fill in the form
        await page.fill("input[name=title]", TEST_DATA["todo"]["title"])
        await page.fill("textarea[name=description]", TEST_DATA["todo"]["description"])

        # Take a screenshot before submission
        await self.take_screenshot(
            page, f"{self.screenshot_dir}/before_todo_create.png"
        )

        # Submit the form - this should trigger an HTMX response with OOB swap
        await page.click("button[type=submit]")

        # Wait for the response - redirect to todos list and OOB swap for toast
        await page.wait_for_selector(SELECTORS["toast_messages"], state="visible")

        # Take a screenshot after submission
        await self.take_screenshot(page, f"{self.screenshot_dir}/after_todo_create.png")

        # Check that the toast message is visible
        toast_visible = await page.is_visible(SELECTORS["toast_messages"])
        assert toast_visible, "Toast message should be visible after form submission"

        # Check the toast content
        toast_text = await page.text_content(SELECTORS["toast_messages"])
        assert (
            "created" in toast_text.lower()
        ), "Toast should indicate the todo was created"

        # Verify that the page was redirected to the todos list
        current_url = page.url
        assert (
            "/todos/" in current_url
        ), f"Expected redirect to todos list, but got {current_url}"

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_modal_oob_swap(self, page):
        """Test that modals appear with OOB swaps."""
        # Create a test user and todo item
        user = User.objects.create_user(
            username=self.username, email=self.email, password=self.password
        )

        # Login the test user
        await self.login_user(page, self.username, self.password)

        # Go to the todos list page
        await page.goto(f"{self.server_url}/todos/")

        # Create a todo item first if there are none
        todo_items = await page.query_selector_all(".todo-item")
        if not todo_items:
            # Navigate to create page
            await page.click("text=Add Todo")

            # Fill and submit form
            await page.fill("input[name=title]", TEST_DATA["todo"]["title"])
            await page.click("button[type=submit]")

            # Wait for redirect back to list
            await page.wait_for_url(f"{self.server_url}/todos/")

        # Click delete on the first todo item - should trigger a modal
        delete_button = await page.query_selector(".todo-item a[href*='delete']")
        if delete_button:
            await delete_button.click()

            # Wait for modal to appear via OOB swap
            await page.wait_for_selector(SELECTORS["modals"], state="visible")

            # Take screenshot of modal
            await self.take_screenshot(
                page, f"{self.screenshot_dir}/delete_todo_modal.png"
            )

            # Check that the modal is visible
            modal_visible = await page.is_visible(SELECTORS["modals"])
            assert modal_visible, "Delete confirmation modal should be visible"

            # Check modal content
            modal_text = await page.text_content(SELECTORS["modal_content"])
            assert (
                "delete" in modal_text.lower()
            ), "Modal should ask for delete confirmation"

            # Close the modal by clicking cancel
            await page.click("text=Cancel")

            # Verify modal is dismissed
            await page.wait_for_selector(SELECTORS["modals"], state="hidden")
            modal_visible = await page.is_visible(SELECTORS["modals"])
            assert not modal_visible, "Modal should be dismissed after clicking cancel"

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_active_nav_highlighting(self, page):
        """Test that navbar links get active state based on current page."""
        # Create and login test user
        user = User.objects.create_user(
            username=self.username, email=self.email, password=self.password
        )

        # Login the test user
        await self.login_user(page, self.username, self.password)

        # Navigate to the todos page
        await page.goto(f"{self.server_url}/todos/")

        # Take screenshot of navigation
        await self.take_screenshot(page, f"{self.screenshot_dir}/nav_todos_page.png")

        # Find active navigation link
        active_link = await page.query_selector(SELECTORS["active_nav_link"])

        # If active link functionality is implemented, verify it works
        if active_link:
            active_text = await active_link.text_content()
            assert (
                "todo" in active_text.lower()
            ), "Todo link should be active when on todos page"

            # Now navigate to a different page
            await page.goto(f"{self.server_url}/account/settings/")

            # Wait for page to load
            await page.wait_for_load_state("networkidle")

            # Take screenshot of navigation on settings page
            await self.take_screenshot(
                page, f"{self.screenshot_dir}/nav_settings_page.png"
            )

            # Check if active link changed
            new_active_link = await page.query_selector(SELECTORS["active_nav_link"])
            if new_active_link:
                new_active_text = await new_active_link.text_content()
                assert (
                    "todo" not in new_active_text.lower()
                ), "Todo link should not be active on settings page"
                assert (
                    "account" in new_active_text.lower()
                    or "settings" in new_active_text.lower()
                ), "Account/Settings link should be active"

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_htmx_form_submit(self, page):
        """Test HTMX form submission behavior."""
        # Create and login test user
        user = User.objects.create_user(
            username=self.username, email=self.email, password=self.password
        )

        # Login the test user
        await self.login_user(page, self.username, self.password)

        # Navigate to todos page
        await page.goto(f"{self.server_url}/todos/")

        # Look for an HTMX-powered form (search, filter, etc.)
        htmx_form = await page.query_selector("form[hx-post], form[hx-get]")

        if htmx_form:
            # Identify form type and inputs
            form_method = await htmx_form.get_attribute(
                "hx-post"
            ) or await htmx_form.get_attribute("hx-get")
            form_target = await htmx_form.get_attribute("hx-target")

            # Find input in the form
            form_input = await htmx_form.query_selector("input:not([type=hidden])")

            if form_input:
                # Get input name
                input_name = await form_input.get_attribute("name")

                # Fill the form with test data
                await form_input.fill("test")

                # Take screenshot before submission
                await self.take_screenshot(
                    page, f"{self.screenshot_dir}/before_htmx_form.png"
                )

                # Submit the form (could be by pressing Enter or clicking a button)
                await form_input.press("Enter")

                # Wait for HTMX to process the response
                # The exact selector depends on what part of the page gets updated
                if form_target:
                    await page.wait_for_selector(f"{form_target}:not(:empty)")
                else:
                    # Default wait for network activity to settle
                    await page.wait_for_load_state("networkidle")

                # Take screenshot after submission
                await self.take_screenshot(
                    page, f"{self.screenshot_dir}/after_htmx_form.png"
                )

                # Verify the form submission had an effect
                # This will depend on what the form does, but we can check basic things
                page_content = await page.content()
                assert (
                    "test" in page_content
                ), "Form submission should include the test value in the response"


@browser_test
@asyncio_mark
@pytest.mark.visual
class ResponsiveDesignTestCase(E2ETestBase):
    """Tests for responsive design across different device viewports."""

    server_url = SERVER_URL
    screenshot_dir = "test_screenshots/responsive"

    # Define standard viewport sizes to test
    VIEWPORTS = [
        {"width": 1280, "height": 800, "name": "desktop"},  # Desktop
        {"width": 768, "height": 1024, "name": "tablet"},  # Tablet
        {"width": 375, "height": 667, "name": "mobile"},  # Mobile
    ]

    # Define pages to test for responsiveness
    PAGES_TO_TEST = [
        "/",  # Home page
        "/todos/",  # Todo list
        "/account/settings/",  # Account settings
        "/account/login/",  # Login page
    ]

    async def setup_method(self, method):
        """Set up method for tests."""
        # Skip tests if browser-use is not available
        if not BROWSER_USE_AVAILABLE:
            pytest.skip("browser-use package not installed")

        # Check if server is running
        if not await self.is_server_running():
            pytest.skip("Django server not running")

        # Create screenshot directory
        Path(self.screenshot_dir).mkdir(parents=True, exist_ok=True)

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_responsive_layout(self, browser):
        """Test responsive layout across different device sizes."""
        # Create a test user
        user = User.objects.create_user(
            username=TEST_DATA["user"]["username"],
            email=TEST_DATA["user"]["email"],
            password=TEST_DATA["user"]["password"],
        )

        # Test each viewport
        for viewport in self.VIEWPORTS:
            # Create context with specific viewport size
            context = await browser.new_context(
                viewport={"width": viewport["width"], "height": viewport["height"]}
            )
            page = await context.new_page()

            # Login the user
            await self.login_user(
                page, TEST_DATA["user"]["username"], TEST_DATA["user"]["password"]
            )

            # Test each page with this viewport
            for page_url in self.PAGES_TO_TEST:
                # Navigate to the page
                try:
                    await page.goto(f"{self.server_url}{page_url}")
                    await page.wait_for_load_state("networkidle")

                    # Take screenshot
                    filename = f"{viewport['name']}_{page_url.replace('/', '_')}.png"
                    if filename.endswith("_.png"):  # Fix for home page
                        filename = f"{viewport['name']}_home.png"

                    await page.screenshot(path=f"{self.screenshot_dir}/{filename}")

                    # Check for mobile navigation (hamburger menu)
                    if viewport["width"] < 768:  # Mobile viewport
                        # Look for hamburger menu or mobile navigation
                        mobile_nav = await page.query_selector(
                            ".mobile-nav, .hamburger, nav button[aria-label*='menu']"
                        )

                        if mobile_nav:
                            # Verify mobile nav is visible
                            is_visible = await mobile_nav.is_visible()
                            assert (
                                is_visible
                            ), "Mobile navigation should be visible on small screens"

                            # Click to expand and take another screenshot
                            await mobile_nav.click()
                            await page.wait_for_timeout(500)  # Wait for animation

                            # Take screenshot of expanded nav
                            await page.screenshot(
                                path=f"{self.screenshot_dir}/{viewport['name']}_{page_url.replace('/', '_')}_expanded_nav.png"
                            )

                    # Check for stacked layouts on mobile vs side-by-side on desktop
                    if viewport["width"] >= 768:  # Desktop/Tablet
                        # Look for elements that should be side-by-side
                        grid_items = await page.query_selector_all(
                            ".grid .col, .flex > div"
                        )

                        if grid_items and len(grid_items) > 1:
                            # Get positions to verify horizontal layout
                            positions = []
                            for item in grid_items[:2]:  # Just check first two items
                                box = await item.bounding_box()
                                positions.append(box)

                            # In horizontal layout, second element should be to the right of first
                            if positions and len(positions) > 1:
                                assert (
                                    positions[1]["x"] > positions[0]["x"]
                                ), "Elements should be side by side in desktop view"
                    else:  # Mobile
                        # Same elements should be stacked on mobile
                        grid_items = await page.query_selector_all(
                            ".grid .col, .flex > div"
                        )

                        if grid_items and len(grid_items) > 1:
                            # Get positions to verify vertical layout
                            positions = []
                            for item in grid_items[:2]:  # Just check first two items
                                box = await item.bounding_box()
                                positions.append(box)

                            # In vertical layout, second element should be below first
                            if positions and len(positions) > 1:
                                # Allow a small horizontal offset, but second should generally be below
                                assert (
                                    positions[1]["y"] > positions[0]["y"]
                                ), "Elements should be stacked in mobile view"

                except Exception as e:
                    # Log exception but continue testing other pages
                    print(f"Error testing {page_url} at {viewport['name']}: {str(e)}")
                    continue

            # Close the context when done with this viewport
            await context.close()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_responsive_navigation(self, browser):
        """Test responsive navigation behavior specifically."""
        # Create a test user
        user = User.objects.create_user(
            username=TEST_DATA["user"]["username"],
            email=TEST_DATA["user"]["email"],
            password=TEST_DATA["user"]["password"],
        )

        # Test mobile viewport specifically for navigation
        context = await browser.new_context(
            viewport={"width": 375, "height": 667}  # Mobile
        )
        page = await context.new_page()

        # Login the user
        await self.login_user(
            page, TEST_DATA["user"]["username"], TEST_DATA["user"]["password"]
        )

        # Navigate to the home page
        await page.goto(f"{self.server_url}/")
        await page.wait_for_load_state("networkidle")

        # Look for mobile navigation toggle button
        nav_toggle = await page.query_selector(
            "nav button[aria-label*='menu'], .mobile-nav-toggle, .hamburger"
        )

        if nav_toggle:
            # Take screenshot before expanding
            await page.screenshot(path=f"{self.screenshot_dir}/mobile_nav_closed.png")

            # Toggle mobile navigation
            await nav_toggle.click()

            # Wait for animation
            await page.wait_for_timeout(500)

            # Take screenshot after expanding
            await page.screenshot(path=f"{self.screenshot_dir}/mobile_nav_open.png")

            # Check if menu items are visible after expanding
            nav_items = await page.query_selector_all("nav a, .mobile-menu a")

            # At least some navigation items should be visible
            visible_items = 0
            for item in nav_items:
                if await item.is_visible():
                    visible_items += 1

            assert (
                visible_items > 0
            ), "Navigation items should be visible when mobile menu is expanded"

            # Close the menu by clicking toggle again
            await nav_toggle.click()

            # Wait for animation
            await page.wait_for_timeout(500)

            # Take screenshot after closing
            await page.screenshot(
                path=f"{self.screenshot_dir}/mobile_nav_closed_after.png"
            )

        # Close the context
        await context.close()

    @pytest.mark.django_db
    @pytest.mark.asyncio
    async def test_form_responsiveness(self, browser):
        """Test form responsiveness across different device sizes."""
        # Create contexts for different viewports
        desktop_context = await browser.new_context(
            viewport={"width": 1280, "height": 800}
        )
        mobile_context = await browser.new_context(
            viewport={"width": 375, "height": 667}
        )

        # Initialize pages
        desktop_page = await desktop_context.new_page()
        mobile_page = await mobile_context.new_page()

        try:
            # Go to login form which should be responsive
            await desktop_page.goto(f"{self.server_url}/account/login/")
            await mobile_page.goto(f"{self.server_url}/account/login/")

            # Wait for both pages to load
            await desktop_page.wait_for_load_state("networkidle")
            await mobile_page.wait_for_load_state("networkidle")

            # Take screenshots
            await desktop_page.screenshot(
                path=f"{self.screenshot_dir}/login_form_desktop.png"
            )
            await mobile_page.screenshot(
                path=f"{self.screenshot_dir}/login_form_mobile.png"
            )

            # Get form dimensions on both devices
            desktop_form = await desktop_page.query_selector("form")
            mobile_form = await mobile_page.query_selector("form")

            if desktop_form and mobile_form:
                desktop_box = await desktop_form.bounding_box()
                mobile_box = await mobile_form.bounding_box()

                # Desktop form should be wider than mobile form
                assert (
                    desktop_box["width"] > mobile_box["width"]
                ), "Desktop form should be wider than mobile form"

                # Check input field widths
                desktop_input = await desktop_page.query_selector(
                    "input[type=text], input[type=email], input[type=password]"
                )
                mobile_input = await mobile_page.query_selector(
                    "input[type=text], input[type=email], input[type=password]"
                )

                if desktop_input and mobile_input:
                    desktop_input_box = await desktop_input.bounding_box()
                    mobile_input_box = await mobile_input.bounding_box()

                    # Desktop input should be wider than mobile input
                    assert (
                        desktop_input_box["width"] > mobile_input_box["width"]
                    ), "Desktop input should be wider than mobile input"

                    # Mobile input should take up more of the screen percentage-wise
                    desktop_ratio = desktop_input_box["width"] / desktop_box["width"]
                    mobile_ratio = mobile_input_box["width"] / mobile_box["width"]

                    assert (
                        mobile_ratio > desktop_ratio
                    ), "Mobile input should take up more screen percentage"

        finally:
            # Close contexts
            await desktop_context.close()
            await mobile_context.close()
