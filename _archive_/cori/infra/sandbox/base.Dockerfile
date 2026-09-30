# Base image for every sandbox profile. Tech stack §6.
# Python 3.14, uv, git. Nothing else; dependencies arrive per root from a
# lockfile at image build time (infra/sandbox/images.py), so a running sandbox
# never needs a package registry.
FROM python:3.14-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/

# UV_PROJECT_ENVIRONMENT is outside /work because the bind mount shadows
# anything at /work/.venv and a .venv there would land in every snapshot.
# UV_NO_SYNC keeps `uv run --frozen` from building the project itself, which
# would fetch a build backend from an index the host-only network cannot reach.
ENV UV_LINK_MODE=copy \
    UV_PYTHON=/usr/local/bin/python3.14 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_SYNC=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 agent \
    && mkdir -p /work /opt/venv /opt/project \
    && chown agent:agent /work /opt/venv /opt/project
WORKDIR /work
USER agent
CMD ["sleep", "infinity"]
