from unittest.mock import patch

import pytest


def test_liveness_ignores_host_validation(client):
    # ALB health checks use the target IP as Host, which is not in ALLOWED_HOSTS.
    response = client.get("/health/live", HTTP_HOST="10.0.12.34:8000")

    assert response.status_code == 200


@pytest.mark.django_db
def test_readiness_reports_failing_dependency(client):
    with patch("apps.common.health._check_redis", side_effect=ConnectionError("down")):
        response = client.get("/health/ready", HTTP_HOST="10.0.12.34:8000")

    assert response.status_code == 503
    assert response.json()["checks"] == {"database": "ok", "redis": "error", "storage": "ok"}


@pytest.mark.parametrize(
    ("path", "headers", "status"),
    [
        ("/api/v1/analyses/not-a-uuid", {}, 404),
        (
            "/api/v1/analyses/00000000-0000-0000-0000-000000000000",
            {"HTTP_HOST": "evil.example"},
            400,
        ),
    ],
)
def test_django_level_errors_use_the_json_envelope(client, path, headers, status):
    response = client.get(path, **headers)

    assert response.status_code == status
    assert response["Content-Type"] == "application/json"
    assert set(response.json()["error"]) == {"code", "message"}
