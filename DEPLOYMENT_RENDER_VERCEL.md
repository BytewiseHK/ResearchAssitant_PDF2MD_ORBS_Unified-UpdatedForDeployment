# Deployment plan: Render (backend) + Vercel (frontend)

This repo serves the frontend statics from the FastAPI backend, **but you can still deploy a separate frontend** to Vercel if you want. Below are the recommended approaches.

## Backend on Render (FastAPI)

### Recommended service type
- **Web Service** (Python)

### Build command

```bash
pip install -r requirements.txt
```

### Start command

```bash
python backend/mineru/main.py
```

### Required environment variables
- **None for OpenRouter by default** (users enter keys per session)
- **Optional**
  - `OPENROUTER_BASE_URL` (defaults to `https://openrouter.ai/api/v1`)
  - `SESSION_TTL_SECONDS` (defaults to 7200)
  - `CORS_ORIGINS` (comma-separated, required if frontend is on Vercel; e.g. `https://your-app.vercel.app`)

### Notes for Render
- Render provides `PORT`; the backend now reads it and binds to `0.0.0.0`.
- The SQLite file `research.db` is local to the instance. If you need persistence across deploys/instance restarts, migrate to a managed DB or attach a persistent disk.

## Frontend on Vercel

You have two choices.

### Option A (simplest): keep frontend served by backend
- No Vercel frontend needed.
- Users access the Render URL and the backend serves `/app`, `/app/research`, `/app/notebook`.

### Option B: deploy static frontend to Vercel and point it at Render

#### What to deploy
- The frontend is static under `frontend/` (HTML + JS + CSS, no build step).

#### Vercel “proxy to Render” setup (recommended for this repo)
- This repo includes a `vercel.json` that:
  - serves the UI under `/app/...` (matching how the backend serves it), and
  - proxies API routes like `/upload`, `/generate`, `/session/*`, etc. to Render.

**Do this before deploying to Vercel:**
- Open `vercel.json` and replace `RENDER_BACKEND_HOST` with your Render backend host, for example:
  - `your-backend.onrender.com`

#### How it should call the backend
- With the `vercel.json` proxy approach, the frontend can keep using same-origin paths (e.g. `/upload`) and cookies work naturally.

#### CORS/cookies note
- Cookie-based sessions require:
  - backend CORS `allow_credentials=True` (already set)
  - `allow_origins` must be restricted to your Vercel domain (configured by `CORS_ORIGINS`)

**Before deploying to Vercel with cookies**, update backend CORS to an explicit list like:
- `https://<your-vercel-app>.vercel.app`


