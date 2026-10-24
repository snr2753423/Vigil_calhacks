FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Mirror the repo layout so app.config resolves ROOT_DIR=/srv/vigil (secrets mount at /srv/vigil/secrets).
WORKDIR /srv/vigil/backend

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/srv/vigil/backend/.venv/bin:$PATH"
EXPOSE 8000
# One worker only: the simulator, evaluator and Snowflake loops are in-process background tasks. Open WebSockets get
# 5 s to close on shutdown, then the lifespan cancels running Snowflake queries (see app/main.py).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "5"]
