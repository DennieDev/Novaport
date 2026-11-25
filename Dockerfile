# syntax=docker/dockerfile:1

# Static files, the same for every platform, so built once on the build machine.
FROM --platform=$BUILDPLATFORM node:24-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim AS python
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM docker:29-cli AS docker-cli

FROM --platform=$BUILDPLATFORM debian:trixie-slim AS sops
ARG TARGETARCH
ARG SOPS_VERSION=v3.13.3
ARG SOPS_SHA256_amd64=e5bec3346a873ae91d871550f3e698c1aad962aff462a080e40f25fde17fef6b
ARG SOPS_SHA256_arm64=53b0abacd38ef1b12a66d6c100956691b9cefce018d91f81e73ddf7438b94d77
ADD https://github.com/getsops/sops/releases/download/${SOPS_VERSION}/sops-${SOPS_VERSION}.linux.${TARGETARCH} /sops
RUN eval "expected=\$SOPS_SHA256_${TARGETARCH}" \
    && echo "${expected}  /sops" | sha256sum -c - \
    && chmod 0755 /sops

FROM python:3.14-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/*
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins/docker-compose \
    /usr/local/libexec/docker/cli-plugins/docker-compose
COPY --from=sops /sops /usr/local/bin/sops
COPY --from=python /app/.venv /app/.venv
COPY --from=frontend /src/build /app/static
RUN mkdir -p /var/lib/swarmer
VOLUME /var/lib/swarmer

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    SWARMER_STATIC_DIR=/app/static

LABEL org.opencontainers.image.title="Swarmer" \
      org.opencontainers.image.source="https://github.com/dennielabs/swarmer" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

EXPOSE 8000
ENTRYPOINT ["swarmer"]
CMD ["server"]
