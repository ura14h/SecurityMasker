# SecurityMasker proxy image (purpose-built gateway, no LiteLLM — ADR-0006).
# Hardened: minimal base, non-root, no build caches, healthcheck (§35).
#
# Reproducible install: runtime deps come from requirements.lock (exact pins), then
# the package is installed --no-deps so nothing is re-resolved (doc/06 P2-3). For a
# fully reproducible production build, pin the base image by digest, e.g.
#   FROM python:3.12-slim@sha256:<digest>
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Locked dependencies first (better layer caching + reproducibility).
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

# Then the app itself, without re-resolving its dependency ranges.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

COPY config ./config

# Run as a non-root user (§33).
RUN useradd --system --uid 10001 --home-dir /app securitymasker \
    && chown -R securitymasker /app
USER securitymasker

EXPOSE 4000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/health')" || exit 1

# SECURITYMASKER_CONFIG selects the dictionary; SECURITYMASKER_OPENAI_UPSTREAM /
# SECURITYMASKER_ANTHROPIC_UPSTREAM select the upstreams (set in compose).
CMD ["securitymasker", "gateway", "--host", "0.0.0.0", "--port", "4000"]

# --- demo stage -------------------------------------------------------------
# Adds the synthetic mock upstream ONLY for the docker-compose demo. The mock and
# other test code are intentionally kept OUT of the production `runtime` image
# (doc/06 P2-3). Build the demo explicitly with `--target demo`.
FROM runtime AS demo
USER root
COPY tests/integration/mock_upstream.py ./tests/integration/mock_upstream.py
COPY tests/integration/__init__.py ./tests/integration/__init__.py
COPY tests/__init__.py ./tests/__init__.py
RUN chown -R securitymasker /app/tests
USER securitymasker
