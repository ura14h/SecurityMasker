# SecurityMasker Gateway image (LiteLLM + SecurityMasker in one process).
# Hardened: minimal base, non-root, no build caches, healthcheck (§35).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first (better layer caching). LiteLLM is pinned in pyproject.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[litellm]"

# App configs, the loader shim, and the demo mock upstream.
COPY config ./config
COPY tests/integration/mock_upstream.py ./tests/integration/mock_upstream.py
COPY tests/integration/__init__.py ./tests/integration/__init__.py
COPY tests/__init__.py ./tests/__init__.py

# Run as a non-root user (§33).
RUN useradd --system --uid 10001 --home-dir /app securitymasker \
    && chown -R securitymasker /app
USER securitymasker

EXPOSE 4000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health/liveliness')" || exit 1

# SECURITYMASKER_CONFIG must be set for masking to be active (else no-op).
CMD ["litellm", "--config", "config/litellm.docker.yaml", "--port", "4000"]
