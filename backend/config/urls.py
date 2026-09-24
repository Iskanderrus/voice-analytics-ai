from django.contrib import admin
from django.urls import include, path

# /health/live and /health/ready are served by HealthCheckMiddleware.
urlpatterns = [
    path("api/v1/", include("apps.uploads.urls")),
    path("api/v1/", include("apps.analyses.urls")),
    path("api/v1/", include("apps.prompts.urls")),
    path("admin/", admin.site.urls),
]

handler400 = "apps.common.api.bad_request"
handler403 = "apps.common.api.permission_denied"
handler404 = "apps.common.api.not_found"
handler500 = "apps.common.api.server_error"
