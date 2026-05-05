FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HOST=0.0.0.0 \
    PORT=41234 \
    CLASS_COORDINATOR_DB=/data/class_coordinator.sqlite3

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app.py ./
COPY class_coordinator ./class_coordinator
COPY data ./data
COPY static ./static
COPY templates ./templates

RUN useradd --system --create-home --home-dir /home/app app \
    && mkdir -p /data \
    && chown -R app:app /app /data

USER app

VOLUME ["/data"]
EXPOSE 41234

CMD ["uv", "run", "--frozen", "app.py"]
