# One image for the API (gunicorn) and the worker (celery); the command differs.
FROM python:3.13-slim AS build

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

FROM python:3.13-slim

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /models && chown app:app /models
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    HF_HOME=/models/huggingface
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --chown=app:app . .
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput --verbosity 0
USER app

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "30", "--access-logfile", "-"]
