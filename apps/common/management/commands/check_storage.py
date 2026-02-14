"""
Management command to verify storage backend configuration and connectivity.

Usage:
    uv run python manage.py check_storage
"""

from django.core.management.base import BaseCommand

from apps.common.services.storage import (
    check_storage_config,
    delete_file,
    get_file_content,
    store_file,
)


class Command(BaseCommand):
    help = "Verify storage backend configuration and connectivity"

    def handle(self, *args, **options):
        # Check config
        status = check_storage_config()
        self.stdout.write(f"Backend: {status['backend']}")

        if not status["ok"]:
            self.stderr.write(
                self.style.ERROR(
                    f"Missing settings: {', '.join(status['missing_keys'])}"
                )
            )
            return

        self.stdout.write(self.style.SUCCESS("Configuration: OK"))

        # Connectivity test: write, read, delete a probe file
        probe_key = "_storage_probe/health_check.txt"
        probe_data = b"storage-health-check"
        try:
            url = store_file(probe_key, probe_data, "text/plain")
            self.stdout.write(f"  store -> {url}")

            content = get_file_content(probe_key)
            assert content == probe_data, "Round-trip mismatch"
            self.stdout.write("  read  -> OK")

            delete_file(probe_key)
            self.stdout.write("  delete -> OK")

            self.stdout.write(self.style.SUCCESS("Connectivity: OK"))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Connectivity FAILED: {e}"))
