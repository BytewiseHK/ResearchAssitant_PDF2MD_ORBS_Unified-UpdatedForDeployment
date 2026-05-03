# Multi-stage image: FastAPI + MinerU.net client only (no local ML).
# Branch: lightweight-cloud

FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

COPY requirements.txt .
RUN pip install --user \
    --no-warn-script-location \
    --no-cache-dir \
    -r requirements.txt

FROM python:3.12-slim

LABEL maintainer="Research Assistant Team"
LABEL description="Cloud PDF (MinerU.net) + FastAPI"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MALLOC_TRIM_THRESHOLD_=128000 \
    PYTHONOPTIMIZE=2

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

COPY . .

RUN mkdir -p /app/uploads /app/temp /app/mineru_output \
    && chmod 755 /app/uploads /app/temp /app/mineru_output

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

EXPOSE 8000

CMD ["python", "-u", "backend/mineru/main.py"]
