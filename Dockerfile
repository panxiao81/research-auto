FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    HOME=/home/appuser

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && pip install --no-cache-dir uv \
    && uv sync --frozen --no-dev \
    && rm -rf /var/lib/apt/lists/*


COPY static ./static
COPY templates ./templates

RUN useradd -m appuser \
    && chown -R appuser:appuser /app /home/appuser

ENV HOME=/home/appuser

RUN uv run playwright install --with-deps --only-shell chromium \
    && chown -R appuser:appuser /home/appuser/.cache/ms-playwright

USER appuser

EXPOSE 8000

CMD ["uv", "run", "--no-sync", "research-auto", "api", "--host", "0.0.0.0", "--port", "8000"]
