# SecurityMasker proxy image (purpose-built gateway, no LiteLLM — ADR-0006).
# Hardened: minimal base, non-root, no build caches, healthcheck (§35).
#
# Reproducible install: runtime deps come from requirements.lock (exact pins), then
# the package is installed --no-deps so nothing is re-resolved (doc/06 P2-3).
#
# The base image is pinned by DIGEST, not just by tag: `python:3.12-slim` is
# republished continuously, so a tag-only reference means two builds of the same
# commit can contain different bases. The tag is kept alongside for readability —
# Docker uses the digest and ignores the tag. This is a multi-arch index digest,
# so it resolves correctly on both arm64 and amd64.
# Refresh procedure: docs/operations.md ("Updating pinned base images").
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# NOTE: SECURITYMASKER_ALLOW_PUBLIC_BIND is deliberately NOT set here. Baking it
# into the image would let `docker run -p 4000:4000` expose an unauthenticated
# proxy without anyone acknowledging it. The operator sets it explicitly (compose
# does, alongside a loopback-only publish) — doc/06 P0-9.

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

# Use /ready, not /health: readiness also probes the session store, so a broken
# Redis or master key marks the container unhealthy instead of silently serving
# requests that will fail (doc/06 P0-1).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/ready')" || exit 1

# SECURITYMASKER_CONFIG selects the dictionary; SECURITYMASKER_OPENAI_UPSTREAM /
# SECURITYMASKER_ANTHROPIC_UPSTREAM select the upstreams (set in compose).
CMD ["securitymasker", "gateway", "--host", "0.0.0.0", "--port", "4000"]

# --- ner stage ---------------------------------------------------------------
# Optional Japanese NER (ADR-0009). A SEPARATE image, not a flag on the default
# one: it adds torch and ~800MB of resident model, which most deployments should
# not pay for. Build explicitly with `--target ner`.
#
# The model itself is NOT baked in here — it is fetched at deploy time into a
# mounted cache with `securitymasker models fetch`, which verifies every artifact
# against its manifest. Baking it would put a 1GB binary in the image layer and
# make re-pinning a rebuild.
FROM runtime AS ner
USER root
COPY requirements-ner.lock ./
RUN pip install --no-cache-dir -r requirements-ner.lock
USER securitymasker
# Set HF_HOME to a writable, mountable location so the fetched model survives
# container restarts and can be verified once per deploy rather than per start.
ENV HF_HOME=/app/.cache/huggingface

# --- demo stage -------------------------------------------------------------
# Adds the synthetic mock upstream ONLY for the docker-compose demo. The mock and
# other test code are intentionally kept OUT of the production `runtime` image
# (doc/06 P2-3). Build the demo explicitly with `--target demo`.
FROM runtime AS demo
USER root
COPY devtools ./devtools
RUN chown -R securitymasker /app/tests
USER securitymasker
