# Deploying DVV Research Cloud

Two pieces, deployed separately:

- **Backend** (FastAPI + Postgres + DuckDB) → Render, via Docker
- **Frontend** (Vite static build) → Vercel

Splitting them means the frontend redeploys in ~30 seconds on every push
instead of waiting on the Python image to rebuild, and either side can be
redeployed independently.

## 0. Test the Docker build locally first

Before touching Render, confirm the image actually builds and runs:

```bash
cd /Users/deepeshsharma/dev/dvv-research-cloud
docker build -t dvv-backend .
docker run -p 8000:8000 -e DATABASE_URL=sqlite:////app/dvv_research.db \
  -e GROQ_API_KEY=$GROQ_API_KEY -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  dvv-backend
curl http://localhost:8000/docs   # should return 200
```

If this doesn't work locally, it won't work on Render either — fix it here
first, where the feedback loop is seconds instead of a full platform deploy.

## 1. Push to GitHub

Render and Vercel both deploy by connecting to a GitHub repo. This repo has
no remote yet — create one (public or private, your call) and push:

```bash
git remote add origin <your-github-repo-url>
git push -u origin main
```

## 2. Backend → Render

**Option A — Blueprint (faster):** Render dashboard → "New +" → "Blueprint" →
point at the repo → it reads `render.yaml` and provisions the web service +
Postgres database together.

**Option B — Manual (if the Blueprint UI doesn't match `render.yaml`'s
schema by the time you're reading this):**
1. "New +" → "PostgreSQL" → create a free database, name it `dvv-postgres`
2. "New +" → "Web Service" → connect the repo → Runtime: **Docker** →
   Dockerfile path: `./Dockerfile`
3. Copy the Postgres "Internal Connection String" into the web service's
   `DATABASE_URL` env var

### Required env vars (set in the Render dashboard, Web Service → Environment)

| Variable | Value | Why |
|---|---|---|
| `DATABASE_URL` | (auto-filled if using Blueprint/internal connection string) | Postgres connection |
| `GROQ_API_KEY` | your key | Primary navigator LLM |
| `ANTHROPIC_API_KEY` | your key | Narrative generation, Claude fallback |
| `JWT_SECRET` | a long random string (Blueprint auto-generates this) | **Must** be set explicitly — the code's fallback persists a generated secret to a local file, which is wiped on every container restart on Render, silently logging everyone out each time otherwise |
| `ADMIN_EMAIL` | your email | First login |
| `ADMIN_PASSWORD` | a real password, not the code's `changeme123` default | **Do not skip this** — the default is a real password disclosed in this public-facing doc the moment this is deployed anywhere reachable |
| `CORS_ORIGINS` | your Vercel URL, e.g. `https://dvv-research.vercel.app` (comma-separate if more than one) | Browser will silently block the frontend's API calls otherwise |
| `PUBLIC_BASE_URL` | your Vercel URL | Used to build share links |

## 3. Frontend → Vercel

```bash
cd web
npx vercel
```

Follow the prompts (link to a new project, root directory `web/`). In the
Vercel dashboard, set one env var before the final deploy:

| Variable | Value |
|---|---|
| `VITE_API_URL` | your Render backend's URL, e.g. `https://dvv-backend.onrender.com` |

Vite bakes env vars into the build at build time, not runtime — if you change
`VITE_API_URL` later, trigger a new deploy for it to take effect.

## 4. Wire them together

Once both are deployed:
1. Set the backend's `CORS_ORIGINS` to the Vercel URL from step 3
2. Redeploy the backend (env var changes require a restart on Render)
3. Open the Vercel URL, log in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`

## Known limitations of this first deploy

- **Free-tier storage is ephemeral.** Without the paid Disk (`render.yaml`'s
  `disk:` block, commented as optional), every uploaded study — data,
  computed crosstabs, rendered decks, chart PNGs — is lost on the next
  deploy or container restart. Fine for "upload a file, show the co-founders,
  done"; not fine for anything meant to persist across sessions.
- **No local Ollama in the cloud.** The basic-mode fallback
  (`navigate_basic`) calls `OLLAMA_HOST`, which only exists on your laptop.
  In production, if Groq fails (rate limit, outage), the navigator falls
  straight to the graceful error message instead of the weak local model —
  slightly worse degradation than local dev, but not a new failure mode.
- **Groq's free-tier daily budget (100k tokens) is shared across every user
  hitting this deployment**, same as it is locally. If co-founders and you
  are both testing the same day, you'll hit the same 429 you've hit locally.
  Consider Groq's pay-as-you-go Dev Tier before a demo that matters.
- **Render's free web service spins down after 15 minutes idle** and takes
  ~30-50s to wake on the next request — the first load after a gap will feel
  slow. Not a bug, just the free tier's tradeoff.
