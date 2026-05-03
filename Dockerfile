# ============================================================================
# Ultra-lean Dockerfile for 2GB Render instances
# ============================================================================
# Stage 1: Builder (slimmed down for 2GB)
# ============================================================================
FROM python:3.12-slim as builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libopenblas-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Default full stack; for Render + MinerU.net cloud use:
#   docker build --build-arg REQUIREMENTS_FILE=requirements-render.txt ...
ARG REQUIREMENTS_FILE=requirements.txt
COPY ${REQUIREMENTS_FILE} /build/requirements-install.txt

# Install with minimal memory footprint
RUN pip install --user \
    --no-warn-script-location \
    --no-cache-dir \
    -r /build/requirements-install.txt

# ============================================================================
# Stage 2: Ultra-lean Runtime
# ============================================================================
FROM python:3.12-slim

LABEL maintainer="Research Assistant Team"
LABEL description="Ultra-lean for 2GB Render instances"

# CRITICAL for 2GB: Aggressive memory settings
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TORCH_HOME=/tmp/torch_home \
    TRANSFORMERS_CACHE=/tmp/transformers_cache \
    HF_HOME=/tmp/huggingface_cache \
    PYTORCH_ENABLE_MPS_FALLBACK=1 \
    PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:256 \
    TORCH_NUM_THREADS=2 \
    OMP_NUM_THREADS=2 \
    NUMEXPR_NUM_THREADS=2 \
    OPENBLAS_NUM_THREADS=2 \
    MKL_NUM_THREADS=2 \
    MALLOC_TRIM_THRESHOLD_=128000 \
    MALLOC_MMAP_THRESHOLD_=131072 \
    MALLOC_MMAP_MAX_=65536 \
    PYTHONOPTIMIZE=2

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    libopenblas0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

COPY . .

RUN mkdir -p /app/uploads /app/temp /app/mineru_output \
    /tmp/torch_home /tmp/transformers_cache /tmp/huggingface_cache \
    && chmod 755 /app/uploads /app/temp /app/mineru_output

HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

EXPOSE 8000

CMD ["python", "-u", "backend/mineru/main.py"]


