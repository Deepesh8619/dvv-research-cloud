# DVV Research Cloud — one image, one service: the API AND the web app.
#
# Stage 1 builds the React app; stage 2 is the Python API, which serves that
# build from the same origin (api/main.py). One origin means no CORS config,
# no API URL baked into the bundle at build time, and one deploy.

FROM node:22-slim AS web
WORKDIR /web
# Lockfile first so `npm ci` is cached unless dependencies actually change.
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app

# Requirements first so pip is cached unless deps change — numpy/pandas/duckdb
# are not reinstalled on every code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=web /web/dist ./web/dist

# Uploaded files, computed decks, charts. Mount a persistent volume HERE on the
# host; without one, everything under it is lost on every redeploy.
RUN mkdir -p /app/studies

# The host injects $PORT. Shell form so it actually expands.
# `python -m uvicorn`: the exact start path the process pool (api/heavy.py) was
# benchmarked under; spawned workers re-import __main__ safely this way.
CMD python -m uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}
