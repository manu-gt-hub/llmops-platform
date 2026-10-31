# One image for the gateway and the demo agent (different commands).
FROM python:3.13-slim

# Build metadata, shown on /version, /metrics and the dashboards.
ARG APP_VERSION=""
ARG GIT_COMMIT=local
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="llmops-platform" \
      org.opencontainers.image.source="https://github.com/manu-gt-hub/llmops-platform" \
      org.opencontainers.image.revision="${GIT_COMMIT}" \
      org.opencontainers.image.created="${BUILD_DATE}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_VERSION=${APP_VERSION} \
    GIT_COMMIT=${GIT_COMMIT} \
    BUILD_DATE=${BUILD_DATE}

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY VERSION ./
COPY config/ config/
COPY doc/ doc/
COPY evals/ evals/
COPY gateway/ gateway/
COPY agent/ agent/

RUN useradd --create-home --uid 10001 app && mkdir -p results && chown app results
USER app

EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8080/health', timeout=2)" || exit 1
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
