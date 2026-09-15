# Deploying DVV Research Cloud to a client

Each client gets **its own complete copy** on its own server: web app + API,
Postgres, HTTPS, nightly backups, and optionally a local AI model — all defined
in `deploy/docker-compose.yml`, all operated with `deploy/dvv`. Nothing runs on
any outside platform; the server is plain rented compute.

**On AWS:** follow [deploy/aws/README.md](deploy/aws/README.md) — one script sets up the
account's budget, backup bucket, per-client identity and server.

## Server size

| Client's AI mode | Server |
|---|---|
| External open-weights API (Groq/Cerebras) | 2 vCPU, **2 GB RAM**, 40 GB disk |
| Local model, CPU only (works, slower) | 4 vCPU, **8 GB RAM**, 80 GB disk |
| Local model, fast | a GPU server |

Ubuntu 24.04. Any provider — only compute is used. Pick the region the client's
data should live in.

## New client (about 15 minutes)

On **DVV's machine**, issue the licence:

```bash
python scripts/issue_licence.py issue --client "Acme Research" --expires 2027-09-30
```

On the **client's server**:

```bash
curl -fsSL https://get.docker.com | sh                      # Docker, official installer
git clone https://github.com/Deepesh8619/dvv-research-cloud /opt/dvv
cd /opt/dvv/deploy
./dvv init acme research.acme.com owner@acme.com            # prints the owner password once
nano .env                                                    # paste DVV_LICENCE; choose the AI mode
./dvv up
```

Before `up`, point the domain's DNS **A record** at the server — Caddy then
fetches and renews the HTTPS certificate on its own.

## Running it

| Command | Does |
|---|---|
| `./dvv status` | containers, health (running commit + licence state), latest backup |
| `./dvv logs [app\|db\|caddy\|backup]` | follow logs |
| `./dvv backup` | back up now (it also runs nightly at `BACKUP_HOUR`) |
| `./dvv restore backups/db-<stamp>.dump [backups/files-<stamp>.tgz]` | restore; takes a safety backup first |
| `./dvv upgrade` | backup → pull → rebuild → **rolls back automatically** if the new version isn't healthy |
| `./dvv up` | apply `.env` changes (new licence, AI mode, keys) |

## Backups must leave the server

Nightly backups land in `deploy/backups/` — **on the same disk**, so they die
with the server. From DVV's machine, pull them off regularly:

```bash
rsync -a root@<server>:/opt/dvv/deploy/backups/ ~/dvv-backups/acme/
```

## Licences

`DVV_LICENCE` is signed with DVV's private key (`~/.dvv-keys/licence_signing_key`
on DVV's machine — **back it up**; it never goes in the repo or an image).
A copy refuses to start without a valid licence. When one lapses, everything
keeps working for 14 days; after that the management app stops (402) but
**live survey links and share links keep working**. Renew: issue a new one,
paste it into `.env`, `./dvv up`.

## Local development

AI on a laptop uses a 12-hour Bedrock key from your own AWS sign-in — no
long-lived key ever sits on a laptop:

```bash
aws sso login --profile dvv
export AWS_PROFILE=dvv
export BEDROCK_API_KEY=$(python scripts/bedrock_dev_token.py)   # pip install aws-bedrock-token-generator
```

```bash
python -m uvicorn api.main:app --reload --port 8000   # SQLite, dev password, no licence needed
npm run dev --prefix web                               # web on :3000, proxies /api
```
