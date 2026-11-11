from django.apps import AppConfig

class PpmAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ppm_app"
    verbose_name = "PPM — управление портфелем ИТ-проектов"

    def ready(self):
        from . import signals  # noqa: F401
