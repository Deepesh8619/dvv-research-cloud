# DVV Research Cloud — backend API.
#
# The frontend (web/) is deployed separately as a static site (Vercel/Render
# Static Site) — it doesn't need Python or this image. See DEPLOY.md.

FROM python:3.12-slim

WORKDIR /app

# Copy requirements first so `pip install` is cached unless deps actually
# change — avoids reinstalling numpy/pandas/duckdb on every code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Studies (uploaded files, computed decks, charts) persist here. On Render
# this must be mounted as a persistent Disk — see DEPLOY.md; without one,
# uploads are lost on every redeploy/restart (fine for a first demo, not for
# real use).
RUN mkdir -p /app/studies

# Render (and most PaaS hosts) inject $PORT at runtime and route traffic to
# it — the container must bind to whatever value that is, not a fixed port.
# Shell form (not exec-form CMD) so $PORT actually expands; a plain
# `uvicorn ... --port $PORT` in exec form would pass the literal string
# "$PORT" to uvicorn and fail to parse as an int.
CMD uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}
