# syntax=docker/dockerfile:1
FROM python:3.13-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

# curl for the container HEALTHCHECK; dig (bind9-dnsutils) for custom-domain
# DNS verification (apps.projects.domains). Everything else ships as wheels.
RUN apt-get update && apt-get install -y --no-install-recommends curl bind9-dnsutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-prod.txt ./
RUN pip install -r requirements-prod.txt

COPY . .

# Storefront skins: compile one Tailwind bundle per skin with the standalone CLI
# (no Node; bundles the `forms` plugin). Output lands in static/, collected at
# container start. If this ever fails to produce a file, the skin templates fall
# back to the Tailwind Play CDN at runtime.
ARG TAILWIND_VERSION=v3.4.17
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH:-amd64}" in \
      amd64) tw_arch=x64 ;; \
      arm64) tw_arch=arm64 ;; \
      *) tw_arch=x64 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/tailwindcss \
      "https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-linux-${tw_arch}"; \
    chmod +x /usr/local/bin/tailwindcss; \
    python tools/build_tailwind_skins.py; \
    rm /usr/local/bin/tailwindcss

RUN adduser --system --group --no-create-home app \
    && mkdir -p /app/staticfiles /app/media \
    && chown -R app:app /app
USER app

EXPOSE 8000
ENTRYPOINT ["./docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "-c", "gunicorn.conf.py"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz/ || exit 1
