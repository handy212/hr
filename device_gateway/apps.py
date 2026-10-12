from django.apps import AppConfig


class DeviceGatewayConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "device_gateway"

    def ready(self):
        from django.urls import include, path

        from horilla.urls import urlpatterns

        urlpatterns.append(
            path("api/terminal/", include("device_gateway.urls")),
        )
        urlpatterns.append(
            path("device-gateway/", include("device_gateway.urls_admin")),
        )
        import device_gateway.scheduler  # noqa: F401  (registers the offline job)
        from device_gateway import sidebar  # noqa: F401  (attendance menu entry)
        super().ready()
