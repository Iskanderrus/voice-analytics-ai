"""Django settings. Every deploy-specific value comes from the environment."""

import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise ImproperlyConfigured(f"Missing required environment variable {name}")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, str(default)).lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    return int(env(name, str(default)))


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env("DJANGO_SECRET_KEY", "insecure-dev-only-key" if DEBUG else None)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework.authtoken",
    "apps.common",
    "apps.uploads",
    "apps.prompts",
    "apps.analyses",
]

MIDDLEWARE = [
    "apps.common.middleware.HealthCheckMiddleware",
    "apps.common.middleware.RequestContextMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": dj_database_url.parse(
        env("DATABASE_URL", "postgres://voice:voice@localhost:5432/voice"),
        conn_max_age=env_int("DB_CONN_MAX_AGE", 60),
        conn_health_checks=True,
    )
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "EXCEPTION_HANDLER": "apps.common.api.exception_handler",
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.UserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"user": env("API_USER_THROTTLE", "600/hour")},
    "UNAUTHENTICATED_USER": None,
}

CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "")
CORS_ALLOW_HEADERS = ["authorization", "content-type", "x-request-id"]
CORS_EXPOSE_HEADERS = ["x-request-id"]

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", True)
CSRF_COOKIE_SECURE = SESSION_COOKIE_SECURE

S3_BUCKET = env("S3_BUCKET", "voice-audio")
S3_REGION = env("AWS_REGION", "us-east-1")
S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", "") or None
S3_PUBLIC_ENDPOINT_URL = env("S3_PUBLIC_ENDPOINT_URL", "") or S3_ENDPOINT_URL

UPLOAD_MAX_BYTES = env_int("UPLOAD_MAX_BYTES", 200 * 1024 * 1024)
UPLOAD_URL_TTL_SECONDS = env_int("UPLOAD_URL_TTL_SECONDS", 900)
UPLOAD_PENDING_TTL_SECONDS = env_int("UPLOAD_PENDING_TTL_SECONDS", 24 * 3600)
UPLOAD_FINALIZATION_LEASE_SECONDS = env_int("UPLOAD_FINALIZATION_LEASE_SECONDS", 600)
UPLOAD_MAINTENANCE_INTERVAL_SECONDS = env_int("UPLOAD_MAINTENANCE_INTERVAL_SECONDS", 300)
UPLOAD_MAINTENANCE_BATCH_SIZE = env_int("UPLOAD_MAINTENANCE_BATCH_SIZE", 100)

CELERY_BROKER_URL = env("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = None
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "visibility_timeout": env_int("CELERY_VISIBILITY_TIMEOUT", 4 * 3600),
}
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_WORKER_HIJACK_ROOT_LOGGER = False

PIPELINE_MAX_STAGE_ATTEMPTS = env_int("PIPELINE_MAX_STAGE_ATTEMPTS", 4)
PIPELINE_RETRY_BASE_SECONDS = env_int("PIPELINE_RETRY_BASE_SECONDS", 10)
PIPELINE_RETRY_MAX_SECONDS = env_int("PIPELINE_RETRY_MAX_SECONDS", 300)
PIPELINE_STAGE_LEASE_SECONDS = env_int("PIPELINE_STAGE_LEASE_SECONDS", 1800)
PIPELINE_STALL_SECONDS = env_int("PIPELINE_STALL_SECONDS", 3600)
PIPELINE_SWEEP_INTERVAL_SECONDS = env_int("PIPELINE_SWEEP_INTERVAL_SECONDS", 300)
LLM_MAX_OUTPUT_ATTEMPTS = env_int("LLM_MAX_OUTPUT_ATTEMPTS", 2)
TRANSCRIPT_MAX_CHARS = env_int("TRANSCRIPT_MAX_CHARS", 60_000)

STT_PROVIDER = env("STT_PROVIDER", "whisper_local")
STT_MODEL = env("STT_MODEL", "base")
STT_TIMEOUT_SECONDS = env_int("STT_TIMEOUT_SECONDS", 600)
WHISPER_DEVICE = env("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = env("WHISPER_COMPUTE_TYPE", "int8")

LLM_PROVIDER = env("LLM_PROVIDER", "ollama")
LLM_MODEL = env("LLM_MODEL", "qwen2.5:3b-instruct")
LLM_TIMEOUT_SECONDS = env_int("LLM_TIMEOUT_SECONDS", 300)
LLM_TEMPERATURE = float(env("LLM_TEMPERATURE", "0"))
OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", "http://localhost:11434")

OPENAI_API_KEY = env("OPENAI_API_KEY", "")
OPENAI_BASE_URL = env("OPENAI_BASE_URL", "") or None

CELERY_BEAT_SCHEDULE = {
    "recover-stalled-analysis-jobs": {
        "task": "apps.analyses.tasks.requeue_stalled_analysis_jobs",
        "schedule": PIPELINE_SWEEP_INTERVAL_SECONDS,
    },
    "maintain-upload-storage": {
        "task": "apps.uploads.tasks.maintain_uploads",
        "schedule": UPLOAD_MAINTENANCE_INTERVAL_SECONDS,
    },
}

LOG_LEVEL = env("LOG_LEVEL", "INFO")
LOG_FORMAT = env("LOG_FORMAT", "json")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "context": {"()": "apps.common.logging.ContextFilter"},
    },
    "formatters": {
        "json": {"()": "apps.common.logging.JsonFormatter"},
        "console": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s %(context)s",
        },
    },
    "handlers": {
        "stdout": {
            "class": "logging.StreamHandler",
            "formatter": LOG_FORMAT,
            "filters": ["context"],
        },
    },
    "root": {"handlers": ["stdout"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
        "httpx": {"level": "WARNING"},
        "botocore": {"level": "WARNING"},
        "faster_whisper": {"level": "WARNING"},
    },
}
