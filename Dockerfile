# syntax=docker/dockerfile:1

# Build stage: install dependencies with uv. Only the resulting virtualenv is kept.
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
# The cache mount keeps uv's download cache out of the image and speeds up rebuilds.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project


# Runtime stage: the same Python image, the virtualenv and the app code.
FROM python:3.13-slim

LABEL org.opencontainers.image.title="fuel-route-api" \
      org.opencontainers.image.description="Plans US road trips with the cheapest fuel stops" \
      org.opencontainers.image.version="0.1.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home --uid 10001 appuser

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY . .
# Settings require a secret key when DEBUG is off; collectstatic doesn't use it.
RUN DJANGO_SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput

# Run as appuser (by numeric ID, so hosts and Kubernetes can check it isn't root). The code stays
# owned by root, so the app can read it but not change it.
USER 10001

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/', timeout=3)"]

# Migrations and data loading run in the one-off `setup` service (scripts/setup.sh).
# Exec form: gunicorn is the main process, so it receives SIGTERM and shuts down gracefully.
CMD ["gunicorn", "--config", "gunicorn.conf.py", "config.wsgi:application"]
