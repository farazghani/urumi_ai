FROM ghcr.io/astral-sh/uv:0.12.3 AS uv
FROM python:3.13-slim
COPY --from=uv /uv /uvx /usr/local/bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    DASHBOARD_DATA=/data \
    SERVE_FRONTEND=0
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev \
    && useradd --system --uid 10001 --create-home urumi \
    && mkdir /data && chown 10001:10001 /data && chmod 700 /data
USER 10001:10001
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=3)"
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers", "--forwarded-allow-ips=*"]
