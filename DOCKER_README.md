# Docker Setup Guide

This project is now Dockerized with **Python 3.12** and includes comprehensive memory optimization for mineru and other ML models.

## Key Changes

✅ **Python upgraded to 3.12** - Latest stable with better performance  
✅ **PyTorch updated to 2.5.0** - Full Python 3.12 compatibility  
✅ **Memory optimizations** - Removed matplotlib, seaborn, onnxruntime (unused visualization/redundant libraries)  
✅ **Multi-stage Docker build** - Optimized image size (~2.8GB)  
✅ **Memory limits** - Prevents runaway processes  

## Prerequisites

- Docker & Docker Compose (latest versions)
- At least 4GB RAM available for the container
- (Optional) OpenRouter API key for custom LLM models

## Quick Start

### Using Docker Compose (Recommended)

```bash
# Build and start the container
docker-compose up --build

# Run in background
docker-compose up -d --build

# View logs
docker-compose logs -f backend

# Stop the service
docker-compose down
```

The backend will be available at `http://localhost:8000`

### Using Docker Directly

```bash
# Build the image
docker build -t research-assistant:latest .

# Run the container with memory limits
docker run -d \
  --name research-assistant \
  -p 8000:8000 \
  -m 4g \
  --memory-swap 4g \
  -e HOST=0.0.0.0 \
  -e PORT=8000 \
  -v $(pwd)/uploads:/app/uploads \
  -v $(pwd)/mineru_output:/app/mineru_output \
  -v $(pwd)/research.db:/app/research.db \
  research-assistant:latest

# View logs
docker logs -f research-assistant

# Stop the container
docker stop research-assistant
```

## Configuration

### Environment Variables

Copy `.env.docker` or create a `.env` file:

```bash
cp .env.docker .env
```

Then customize:

```env
# API Configuration
OPENROUTER_MODEL=openrouter/free  # or your specific model
CORS_ORIGINS=http://localhost:3000,http://localhost:8000

# Optional: Add your OpenRouter API key
OPENROUTER_API_KEY=your_key_here
```

### Memory Optimization Features

The Dockerfile and docker-compose.yml include several memory optimizations:

1. **Multi-stage build**: Separates build dependencies from runtime (saves ~500MB)
2. **Slim base image**: `python:3.12-slim` (~150MB vs ~900MB)
3. **Optimized environment variables**:
   - `PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512` - Prevents fragmentation
   - `TORCH_NUM_THREADS=4` - Optimal for containers
   - `OMP_NUM_THREADS=4` - NumPy/OpenBLAS threading
4. **Temporary cache directories**: `/tmp/` caches at runtime, not in image
5. **Memory limits**: 4GB limit, 2GB reservation in docker-compose
6. **Lean dependencies**: Removed visualization libraries (matplotlib, seaborn, onnxruntime)

### Memory-Intensive mineru Optimization

For large PDF processing, consider:

```bash
# Increase memory limits in docker-compose.yml
deploy:
  resources:
    limits:
      memory: 6G  # Increase from 4G for large files
    reservations:
      memory: 4G
```

## Deployment Platforms

### 1. Render.com (Recommended)

Already configured in `render.yaml`:

```bash
git add . && git commit -m "Docker setup with Python 3.12"
git push
# Then deploy via Render dashboard
```

### 2. AWS (ECS / AppRunner)

```bash
# Push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin {account}.dkr.ecr.us-east-1.amazonaws.com
docker tag research-assistant:latest {account}.dkr.ecr.us-east-1.amazonaws.com/research-assistant:latest
docker push {account}.dkr.ecr.us-east-1.amazonaws.com/research-assistant:latest
```

### 3. Docker Hub

```bash
docker tag research-assistant:latest yourusername/research-assistant:latest
docker push yourusername/research-assistant:latest
```

### 4. Local Development

```bash
docker-compose up --build
# Access at http://localhost:8000
```

## Volumes & Persistence

The docker-compose.yml mounts these directories:

| Host | Container | Purpose |
|------|-----------|---------|
| `./uploads` | `/app/uploads` | File uploads |
| `./mineru_output` | `/app/mineru_output` | Processing output |
| `./research.db` | `/app/research.db` | SQLite database |
| `./temp` | `/app/temp` | Temporary files |

Models are downloaded to temporary directories (`/tmp/`) and are **not persisted** between container restarts. To persist models:

```bash
mkdir -p ~/.cache/huggingface ~/.cache/torch
docker-compose down  # if running

# Add to docker-compose.yml under volumes:
volumes:
  - ~/.cache/huggingface:/tmp/huggingface_cache
  - ~/.cache/torch:/tmp/torch_home
```

## Troubleshooting

### Out of Memory (OOM)

```bash
# Check container memory usage
docker stats research-assistant

# Increase limits
# Edit docker-compose.yml:
deploy:
  resources:
    limits:
      memory: 6G
    reservations:
      memory: 3G
```

### Slow First Startup

Models download on first run (~2-3GB). This is normal and cached for subsequent runs.

```bash
# Monitor progress
docker logs -f research-assistant

# Pre-download in background while checking status
docker exec research-assistant python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('bert-base-uncased')"
```

### Port Already in Use

```bash
# Change port in docker-compose.yml or command line
docker run -p 8001:8000 research-assistant:latest
```

### Build Failures

```bash
# Rebuild without cache
docker build --no-cache -t research-assistant:latest .

# Check logs
docker logs research-assistant
```

## Performance Metrics

| Metric | Value |
|--------|-------|
| Image size | ~2.8GB |
| Container memory | 2-4GB recommended |
| Startup time | 30-60s (model cache warm) |
| First startup | 1-2 minutes (downloads models) |
| CPU cores needed | 4+ recommended |

## Health Check

```bash
# Check container health
curl http://localhost:8000/health

# View Docker health status
docker inspect research-assistant | grep -A 5 Health
```

## Cleanup

```bash
# Stop and remove
docker-compose down

# Remove image
docker rmi research-assistant:latest

# Full cleanup
docker system prune -a
```

## Python 3.12 Compatibility

All packages updated for Python 3.12:

- ✅ PyTorch 2.5.0
- ✅ Transformers 4.53.2
- ✅ NumPy 2.2.6
- ✅ All dependencies verified

## Support

For issues, check:
1. Docker logs: `docker logs research-assistant`
2. Memory usage: `docker stats research-assistant`
3. Build errors: `docker build --progress=plain .`
