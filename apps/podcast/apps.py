from django.apps import AppConfig


class PodcastConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.podcast"

    def ready(self):
        import apps.podcast.signals  # noqa: F401
