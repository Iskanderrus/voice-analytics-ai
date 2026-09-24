import os

os.environ.setdefault("DJANGO_DEBUG", "false")
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ["S3_ENDPOINT_URL"] = ""  # moto intercepts real AWS endpoints
os.environ["S3_PUBLIC_ENDPOINT_URL"] = ""
os.environ["LOG_FORMAT"] = "console"

from config.settings import *  # noqa: E402, F403

PIPELINE_RETRY_BASE_SECONDS = 0
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
REST_FRAMEWORK = {**REST_FRAMEWORK, "DEFAULT_THROTTLE_CLASSES": []}  # noqa: F405
