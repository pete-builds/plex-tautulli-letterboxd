# Python is pinned to 3.14 in exactly four places and they must agree:
# pyproject.toml (requires-python), uv.lock, .python-version, and here.
FROM python:3.15.0b4-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies resolve from the lockfile and cache independently of app source.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project --no-dev

COPY src/ ./src/
RUN uv sync --locked --no-dev


FROM python:3.15.0b4-slim-bookworm AS runtime

RUN groupadd --system --gid 10001 boxd \
    && useradd --system --uid 10001 --gid boxd --no-create-home boxd

WORKDIR /app

COPY --from=builder --chown=boxd:boxd /app/.venv /app/.venv
COPY --from=builder --chown=boxd:boxd /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BIND_HOST=0.0.0.0 \
    BIND_PORT=8000

USER boxd
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('BIND_PORT','8000')}/healthz\", timeout=4).status==200 else 1)"

# Shell form so BIND_HOST / BIND_PORT from .env are honoured; exec so uvicorn
# is PID 1 and receives SIGTERM directly.
CMD ["sh", "-c", "exec uvicorn boxd_bridge.main:create_app --factory --host \"$BIND_HOST\" --port \"$BIND_PORT\""]
