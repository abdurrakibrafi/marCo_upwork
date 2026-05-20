from django.apps import AppConfig


class NestConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.nest"

    def ready(self):
        import apps.nest.signals  # noqa
