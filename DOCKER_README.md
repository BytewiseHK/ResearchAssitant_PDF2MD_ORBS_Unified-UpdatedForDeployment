# Docker Setup Guide

**Branch `lightweight-cloud`:** the image is **Python 3.12** + **FastAPI** + **MinerU.net** for PDF→Markdown. There is **no** vendored MinerU, PyTorch, or local ML stack in the container.

## Key points

- **Small image** compared to the full local-ML branch (`main`): multi-stage build, `python:3.12-slim`, and a minimal [`requirements.txt`](requirements.txt).
- **PDF uploads** require a [MinerU.net](https://mineru.net) API key (`MINERU_API_KEY`).

## Prerequisites

- Docker & Docker Compose (latest versions)
- **~512MB–1GB RAM** is usually enough for the API process (MinerU runs remotely)
- `MINERU_API_KEY` for `/upload`
- (Optional) OpenRouter API key for LLM features (users can also set keys per session)

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

docker run -d \
  --name research-assistant \
  -p 8000:8000 \
  -e PORT=8000 \
  -e MINERU_API_KEY="your_key" \
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

# Required for PDF upload → markdown (MinerU.net)
MINERU_API_KEY=your_mineru_net_key
MINERU_API_BASE=https://mineru.net
MINERU_CLOUD_LANGUAGE=en
```

### MinerU.net and timeouts

PDF conversion runs on MinerU’s servers. The app polls until the job finishes (`MINERU_CLOUD_POLL_MAX_RETRIES` × `MINERU_CLOUD_POLL_INTERVAL_SECONDS`, defaults 100×5s). Very large PDFs can still hit HTTP timeouts; a future async job + polling API would help.

### Memory and Docker Compose

1. **Multi-stage build**: install deps in a builder stage, copy only wheels into the runtime image.
2. **Slim base**: `python:3.12-slim`.
3. **Compose**: optional `deploy.resources.limits.memory` if you want a hard cap (defaults in `docker-compose.yml` can be lowered for this branch).

### Large PDFs

If `/upload` hits HTTP timeouts, raise `MINERU_CLOUD_POLL_MAX_RETRIES` and/or `MINERU_CLOUD_POLL_INTERVAL_SECONDS`, or add an async job flow so the client polls a task id instead of holding one request open.

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

## Performance metrics (this branch)

| Metric | Typical range |
|--------|----------------|
| Image size | hundreds of MB (no PyTorch / local models) |
| Container memory | 512MB–1GB often sufficient for the API |
| Startup time | a few seconds (no model download on boot) |
| CPU | 1–2 cores is usually fine |

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

## Python 3.12

Dependencies in `requirements.txt` are pinned for **Python 3.12** (FastAPI, httpx, OpenAI SDK, etc.).

## Support

For issues, check:
1. Docker logs: `docker logs research-assistant`
2. Memory usage: `docker stats research-assistant`
3. Build errors: `docker build --progress=plain .`
