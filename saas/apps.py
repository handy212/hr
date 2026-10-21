from django.apps import AppConfig


class SaasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "saas"
    verbose_name = "SaaS Platform"

    def ready(self):
        from saas import menu  # noqa: F401
        from saas.ownership import register_ownership_signals
        register_ownership_signals()
