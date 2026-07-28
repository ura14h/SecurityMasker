FROM docker.io/library/python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

LABEL org.opencontainers.image.title="SecurityMasker Windows Linux-hosted spike"
LABEL org.opencontainers.image.version="0.1.0"
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/var/lib/securitymasker-model \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /opt/securitymasker

COPY requirements.lock requirements-torch-cpu.lock requirements-ner.lock ./
RUN python3 -m pip install -r requirements.lock \
    && python3 -m pip install --no-deps -r requirements-torch-cpu.lock \
    && python3 -m pip install --no-deps -r requirements-ner.lock

COPY pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md securitymasker.py ./
COPY src ./src
RUN python3 -m pip install --no-build-isolation --no-deps . \
    && groupadd --gid 10001 securitymasker \
    && useradd --uid 10001 --gid 10001 --create-home \
        --shell /usr/sbin/nologin securitymasker \
    && install -d -o 10001 -g 10001 -m 0700 \
        /var/lib/securitymasker-model \
        /var/lib/securitymasker-product

USER 10001:10001
STOPSIGNAL SIGTERM

ENTRYPOINT ["securitymasker"]
