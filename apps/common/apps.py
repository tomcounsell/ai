from django.apps import AppConfig


class CommonConfig(AppConfig):
    name = "apps.common"
    label = "common"
    default_auto_field = "django.db.models.BigAutoField"
