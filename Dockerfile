# syntax=docker/dockerfile:1.7
#
# Public demo image (INTAKE_DEMO_MODE=1): intake app on 0.0.0.0:$PORT, mock ERP on
# 127.0.0.1:8001 inside the container. No API key, no Docker-in-Docker: LLM and KoSIT
# results come from the committed caches (see src/intake/demo.py).
#
#   docker build -t intake-demo .
#   docker run --rm -p 8000:8000 intake-demo        # http://localhost:8000

ARG PYTHON_IMAGE=python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

# --- build: git is only needed to install e-rechnung-inbox from its pinned commit ------
FROM ${PYTHON_IMAGE} AS build
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY docker/constraints.txt ./docker/constraints.txt
RUN pip install --no-cache-dir --prefix=/install -c docker/constraints.txt .

# --- runtime ---------------------------------------------------------------------------
FROM ${PYTHON_IMAGE}
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    INTAKE_DEMO_MODE=1 \
    PORT=8000
COPY --from=build /install /usr/local
RUN useradd --system --uid 10001 --home-dir /app app
WORKDIR /app
# Only what the demo reads: the test split samples and the two caches.
COPY --chown=app:app eval/data/test ./eval/data/test
COPY --chown=app:app eval/llm_cache ./eval/llm_cache
COPY --chown=app:app eval/kosit_cache ./eval/kosit_cache
COPY --chown=app:app docker/demo-entrypoint.sh ./docker/demo-entrypoint.sh
RUN mkdir -p /app/data && chown app:app /app/data && chmod 0755 docker/demo-entrypoint.sh
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=4)"
CMD ["/app/docker/demo-entrypoint.sh"]
