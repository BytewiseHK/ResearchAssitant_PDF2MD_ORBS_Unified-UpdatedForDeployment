#!/bin/bash
# Memory optimization setup for mineru and PyTorch in Docker

export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1

# PyTorch memory optimization
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512
export TORCH_NUM_THREADS=4
export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4

# Transformers cache optimization
export TRANSFORMERS_CACHE=/tmp/transformers_cache
export HF_HOME=/tmp/huggingface_cache
export TORCH_HOME=/tmp/torch_home

# Disable unnecessary features
export PYTORCH_ENABLE_MPS_FALLBACK=1

# Memory limits for garbage collection
export PYTHONHASHSEED=0

# Disable model parallelism for CPU
export CUDA_VISIBLE_DEVICES=""

echo "Memory optimization variables set. Ready for mineru processing."
