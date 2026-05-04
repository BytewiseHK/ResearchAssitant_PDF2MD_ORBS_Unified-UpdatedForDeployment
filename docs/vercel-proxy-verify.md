# Vercel proxy: verify API routing

The Research UI calls JSON APIs on the **same hostname** as the page (e.g. `https://your-project.vercel.app/research/candidates`). Vercel must **rewrite** those paths to the Render FastAPI service defined in the root [`vercel.json`](../vercel.json): both the **`rewrites`** block (preferred by current Vercel) and the legacy **`routes`** block list the same destinations.

## Quick checks after deploy

1. Confirm the deployment Git revision includes an updated [`vercel.json`](../vercel.json) with `rewrites` entries for `/research/candidates` and `/write/candidates`.
2. In the Vercel dashboard, open **Deployments** for the project tied to your production domain and confirm the latest build used that commit.

### Same-origin comparison

If **`POST /generate`** succeeds from the browser but **`POST /research/candidates`** returns **404**, the response is almost certainly coming from **Vercel** (no proxy match), not from FastAPI. Fix routing or redeploy the commit that contains the updated config.

### curl (replace `YOUR_HOST` with your Vercel origin, no trailing slash)

Suggest-sources endpoint:

```bash
curl -i -X POST "https://YOUR_HOST/research/candidates" \
  -H "Content-Type: application/json" \
  -d "{\"prompt\":\"test\"}"
```

Compare with generate (same cookie/session behavior as the app would need in browser):

```bash
curl -i -X POST "https://YOUR_HOST/generate" \
  -H "Content-Type: application/json" \
  -d "{\"prompt\":\"test\"}"
```

Interpretation:

- **404** from Vercel: routing still wrong or stale deployment.
- **401 / 422 / 502 / 504**: request reached some backend path (progress); tune FastAPI or Render.
- **200** with JSON: proxy and backend are aligned.

---

## Why not set `api-base-url` to Render in `research/index.html`?

The frontend uses **`credentials: 'include'`** so the session cookie issued for your **Vercel** origin is sent with API requests.

If you point API URLs directly at **`https://….onrender.com`**:

- The browser treats that as **cross-origin** relative to the Vercel page.
- Cookies scoped to **Vercel** are **not** sent to **Render**, so session-based OpenRouter key storage breaks unless you redesign auth (e.g. bearer tokens or a same-origin BFF).

Additionally, FastAPI **CORS** must list your exact Vercel origin (see `CORS_ORIGINS` on Render). The default code path only includes known origins; adding a new Vercel project URL requires updating that env var if you ever call Render directly.

**Recommendation:** Keep API traffic **same-origin on Vercel** via [`vercel.json`](../vercel.json) rewrites to Render, and only use **`meta name="api-base-url"`** when you deliberately host HTML and API under different origins **and** have a compatible auth/CORS setup.

---

## Large PDF / notebook uploads: `ROUTER_EXTERNAL_TARGET_ERROR` and `direct-api-base-url`

Vercel’s edge proxy to Render has a **short max duration**. `POST /upload` and `POST /files-upload` stay open until **MinerU.net** and follow-up work finish, so **multi‑MB PDFs** often hit **`ROUTER_EXTERNAL_TARGET_ERROR`** (not a Render RAM limit by itself).

**Fix (implemented in the app):** set a second meta tag to the **Render API origin** (no path, no trailing slash), e.g. the same host as in [`vercel.json`](../vercel.json) rewrites:

- In [`frontend/research/index.html`](../frontend/research/index.html) and [`frontend/notebook/index.html`](../frontend/notebook/index.html):

  ` <meta name="direct-api-base-url" content="https://YOUR-SERVICE.onrender.com"> `

The UI sends **long** requests (`/upload`, `/files-upload`, and ORBS `/analyze`) **directly to Render** and passes **`X-RA-Session-Id`** (from `GET /session/status`) so the session matches the cookie session created through the Vercel-proxied API. **CORS** on Render must allow your Vercel origin (set `CORS_ORIGINS` to include your exact `https://….vercel.app` URL, or multiple comma-separated values).

If **`direct-api-base-url` is empty**, the bundled frontend uses a **default Render origin** only when the page is served from **`*.vercel.app`** (same host as in [`vercel.json`](../vercel.json)). Override with the meta tag per deployment if your API lives elsewhere.

Leave **`direct-api-base-url` empty** when the HTML is served **locally** or **same-origin as the API**, so uploads stay on that host.
