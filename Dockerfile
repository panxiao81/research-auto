FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/root/.local/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts

RUN uv sync --frozen || uv sync
RUN uv run playwright install --with-deps chromium

EXPOSE 8000

CMD ["uv", "run", "research-auto", "api", "--host", "0.0.0.0", "--port", "8000"]
