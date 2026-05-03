# ============================================================================
# Multi-stage Dockerfile for Research Assistant with Memory Optimization
# ============================================================================
# Stage 1: Builder
# ============================================================================
FROM python:3.12-slim as builder

# Set build environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    libopenblas-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python packages to a specific directory
RUN pip install --user \
    --no-warn-script-location \
    --compile \
    -r requirements.txt

# ============================================================================
# Stage 2: Runtime (Final Image)
# ============================================================================
FROM python:3.12-slim

LABEL maintainer="Research Assistant Team"
LABEL description="Dockerized Research Assistant with Python 3.12 and mineru"

# Runtime environment variables for memory optimization
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TORCH_HOME=/tmp/torch_home \
    TRANSFORMERS_CACHE=/tmp/transformers_cache \
    HF_HOME=/tmp/huggingface_cache \
    PYTORCH_ENABLE_MPS_FALLBACK=1 \
    PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512 \
    TORCH_NUM_THREADS=4 \
    OMP_NUM_THREADS=4

WORKDIR /app

# Install only runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    libopenblas0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /root/.local /root/.local

# Update PATH
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY . .

# Create necessary directories with proper permissions
RUN mkdir -p /app/uploads \
    /app/temp \
    /app/mineru_output \
    /tmp/torch_home \
    /tmp/transformers_cache \
    /tmp/huggingface_cache \
    && chmod -R 755 /app/uploads /app/temp /app/mineru_output

# Health check endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8000}/health || exit 1

# Expose port
EXPOSE 8000

# Start application
CMD ["python", "-u", "backend/mineru/main.py"]

