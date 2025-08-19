"""
Django management command to capture screenshots of the application.

This command allows developers to capture screenshots of any page in the application
directly from the Django management interface.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.test import Client

from apps.common.utilities.screenshots import ScreenshotService

User = get_user_model()


class Command(BaseCommand):
    help = "Capture a screenshot of a page in the application"

    def add_arguments(self, parser):
        parser.add_argument(
            "path", type=str, help="URL path to capture (e.g., /todos/)"
        )
        parser.add_argument(
            "--output-dir",
            default="screenshots",
            type=str,
            help="Directory to save screenshots",
        )
        parser.add_argument("--filename", type=str, help="Filename for the screenshot")
        parser.add_argument("--width", type=int, default=1280, help="Viewport width")
        parser.add_argument("--height", type=int, default=800, help="Viewport height")
        parser.add_argument(
            "--full-page", action="store_true", help="Capture the full page"
        )
        parser.add_argument(
            "--visible", action="store_true", help="Show the browser during capture"
        )
        parser.add_argument("--wait-for", type=str, help="CSS selector to wait for")
        parser.add_argument(
            "--wait-ms", type=int, default=500, help="Time to wait before capture (ms)"
        )
        parser.add_argument(
            "--use-agent",
            action="store_true",
            help="Use AI agent for more complex scenarios",
        )
        parser.add_argument(
            "--instructions", type=str, help="Instructions for the AI agent"
        )
        parser.add_argument("--username", type=str, help="Username to authenticate as")
        parser.add_argument(
            "--port", type=int, default=8000, help="Port the server is running on"
        )

    def handle(self, *args, **options):
        path = options["path"]
        output_dir = options["output_dir"]
        server_url = f"http://localhost:{options['port']}"

        # Check if we need to log in
        client = None
        if options["username"]:
            try:
                user = User.objects.get(username=options["username"])
                client = Client()
                client.force_login(user)
                self.stdout.write(f"Authenticated as {options['username']}")
            except User.DoesNotExist:
                raise CommandError(f"User '{options['username']}' does not exist")

        # Create screenshot service
        service = ScreenshotService(
            output_dir=output_dir,
            server_url=server_url,
            viewport={"width": options["width"], "height": options["height"]},
            headless=not options["visible"],
            wait_before_capture=options["wait_ms"],
            use_browser_agent=options["use_agent"],
        )

        # Capture the screenshot
        if options["use_agent"]:
            screenshot_path = service.capture_with_browser_agent(
                path, instructions=options["instructions"], filename=options["filename"]
            )
        else:
            screenshot_path = service.capture(
                path,
                filename=options["filename"],
                wait_for_selector=options["wait_for"],
                full_page=options["full_page"],
                cookies=self._get_cookies(client) if client else None,
            )

        if screenshot_path:
            self.stdout.write(
                self.style.SUCCESS(f"Screenshot saved to: {screenshot_path}")
            )
        else:
            self.stdout.write(self.style.ERROR("Failed to capture screenshot"))

    def _get_cookies(self, client):
        """Extract cookies from Django test client."""
        if not client:
            return None

        cookies = []
        for key, value in client.cookies.items():
            cookies.append(
                {"name": key, "value": value.value, "domain": "localhost", "path": "/"}
            )

        return cookies
