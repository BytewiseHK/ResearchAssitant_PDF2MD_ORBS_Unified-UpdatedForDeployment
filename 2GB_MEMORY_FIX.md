# 2GB Render Instance Memory Fix

## Problem
- **Error**: `ROUTER_EXTERNAL_TARGET_ERROR` on Render
- **Root Cause**: Memory limit exceeded on 2GB instance
- **Why**: Mineru + PyTorch + Transformers models are memory-intensive

## Solution Summary

### 1. **Ultra-Lean Docker Image**
- ✅ Multi-stage build optimized for 2GB
- ✅ Removed debug/build tools from runtime
- ✅ Stripped `/tmp` and build artifacts in final image

### 2. **Aggressive Memory Settings**
- `TORCH_NUM_THREADS=1` (reduced from 4)
- `PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:256` (prevents fragmentation)
- `PYTHONOPTIMIZE=2` (bytecode optimization)
- `SESSION_TTL_SECONDS=1800` (30min sessions vs 2h - faster cleanup)
- Memory allocator tuning for constrained environments

### 3. **Automatic Memory Management**
Added middleware in `main.py`:
- Aggressive garbage collection every 5 requests or 60 seconds
- `torch.cuda.empty_cache()` called automatically
- Health check endpoint (`/health`) that doesn't load models

### 4. **Memory Monitoring Endpoint**
```bash
curl -X POST http://localhost:8000/memory/cleanup
# Response:
# {
#   "status": "cleaned",
#   "memory_before_mb": 1850.5,
#   "memory_after_mb": 1420.3,
#   "freed_mb": 430.2
# }
```

## Files Modified

| File | Changes |
|------|---------|
| `Dockerfile` | Ultra-lean multi-stage, aggressive memory env vars |
| `render.yaml` | Session TTL 1800s, threading =1, malloc tuning |
| `backend/mineru/main.py` | Memory middleware, health check, cleanup endpoint |
| `requirements.txt` | Added psutil for memory monitoring |

## Deployment Steps

### 1. Commit Changes
```bash
git add Dockerfile render.yaml backend/mineru/main.py requirements.txt
git commit -m "2GB Render optimization: memory middleware, lean Docker image"
git push
```

### 2. Redeploy on Render
```
Render Dashboard → Select Service → Manual Deploy
```

### 3. Monitor Memory
After deploy, check Render logs:
```
tail -f render.log | grep -i memory
```

## How to Monitor on Render

1. Go to Render Dashboard
2. Select your service → Logs
3. Watch for:
   - `Memory optimization variables set` = ✅ Good
   - `OOM` or `killed` = ❌ Still failing
4. Test health endpoint (should work immediately):
   ```bash
   curl https://your-service.onrender.com/health
   ```

## Key Improvements

| Metric | Before | After |
|--------|--------|-------|
| Session timeout | 2h (7200s) | 30min (1800s) - **faster memory release** |
| Thread count | 4 | **1** - no oversubscription |
| Memory allocator | default | **optimized for 2GB** |
| Garbage collection | on-demand | **every 5 reqs or 60s** |
| Image bloat | build tools included | **removed from runtime** |
| Python optimization | none | **PYTHONOPTIMIZE=2** |

## If Still Running Out of Memory

Try these in order:

### Option 1: Reduce Session TTL Further
In `render.yaml`:
```yaml
- key: SESSION_TTL_SECONDS
  value: "900"  # 15 minutes instead of 30
```

### Option 2: Disable PDF Processing
If PDFs are causing OOM (mineru is heavy):
```python
# In main.py, comment out upload endpoint or add check:
@app.post("/upload")
async def upload_file(...):
    raise HTTPException(503, "PDF processing disabled on 2GB instance")
```

### Option 3: Upgrade to 4GB Instance
- Render Starter: $7/month → Pro: $12/month for 4GB

### Option 4: Use Background Workers
Process PDFs asynchronously with Celery (complex refactor)

## Performance Expectations on 2GB

- ✅ Chat with OpenRouter API: **Fast** (no local models)
- ✅ Notebook analysis: **Fast** (small models)
- ⚠️ PDF processing: **Slow or may fail** (mineru is 500MB+ loaded)
- ✅ Database queries: **Fast** (SQLite is lightweight)

## Testing Locally

Test the 2GB setup locally:
```bash
# Simulate 2GB memory limit
docker run \
  -m 2g \
  --memory-swap 2g \
  -p 8000:8000 \
  research-assistant:latest
```

## Endpoints to Test

```bash
# 1. Health check (lightweight)
curl http://localhost:8000/health

# 2. Session creation (lightweight)
curl http://localhost:8000/session/status

# 3. Cleanup memory (diagnostic)
curl -X POST http://localhost:8000/memory/cleanup

# 4. Chat (API call, no local models)
curl -X POST http://localhost:8000/chatbot-answer \
  -H "Content-Type: application/json" \
  -d '{"role":"user","content":"Hello"}'

# 5. PDF upload (HEAVY - only if needed)
# This may fail on 2GB!
```

## What Was Causing the OOM?

1. **mineru initialization** - Downloads ~500MB of models
2. **Transformers cache** - Auto-downloads models not in cache
3. **PyTorch on CPU** - Keeps models in RAM (unlike GPU)
4. **Session accumulation** - Old sessions holding data
5. **No garbage collection** - Accumulated memory leaks

## What This Fix Does

1. **Limits threads** - Prevents oversubscription
2. **Aggressive GC** - Frees memory every 5 requests
3. **Short sessions** - 30min expiry instead of 2h
4. **Lean image** - No unnecessary build tools
5. **Monitoring** - `/memory/cleanup` endpoint to diagnose

## Monitoring Script (Optional)

Add to your monitoring/alerting:
```bash
# Check memory every minute
while true; do
  memory=$(curl -s http://localhost:8000/memory/cleanup | jq '.memory_after_mb')
  if (( $(echo "$memory > 1800" | bc -l) )); then
    echo "WARNING: Memory at ${memory}MB"
  fi
  sleep 60
done
```

## Next Steps

1. **Deploy** and monitor for 24 hours
2. **If stable**: ✅ You're done!
3. **If still OOM**: Try Option 1 or 2 above
4. **If OOM frequently**: Upgrade to 4GB (Option 3)

---

## Quick Reference: What Changed

**Render Configuration (`render.yaml`)**:
```yaml
SESSION_TTL_SECONDS: "1800"        # vs 7200
TORCH_NUM_THREADS: "1"              # vs 2
PYTORCH_CUDA_ALLOC_CONF: "256"      # vs 512
```

**Docker (`Dockerfile`)**:
```dockerfile
PYTHONOPTIMIZE: "2"
MALLOC_TRIM_THRESHOLD_: "128000"
MAX_SPLIT_SIZE_MB: "256"
```

**Code (`main.py`)**:
- Added `_MemoryManagementMiddleware` → GC every 5 requests
- Added `/health` endpoint → Check without loading models
- Added `/memory/cleanup` endpoint → Force cleanup

---

**Status**: ✅ Ready to deploy!
