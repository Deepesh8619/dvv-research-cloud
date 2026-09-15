# Switching to AWS

One client = one Lightsail server running the bundle, the open model on Bedrock
in the same region, and backups copied to S3. Measured size for 200 concurrent
users: 2 vCPU / 4 GB (peak 287 MB RAM and 74% of one core for the app itself).

| Per client, per month | Cost |
|---|---|
| Lightsail 2 vCPU / 4 GB / 80 GB SSD | $24 |
| Bedrock Qwen3-32B (~20 questionnaires + 1,000 copilot/chat answers) | ~$5–10 |
| S3 backups | ~$1 |
| Route 53 zone + domain | ~$1.50 |
| **Total** | **≈ $30–40** |

## 1. The account — once (only you can do this)

1. Create the AWS account. Turn on **MFA for the root user**, then stop using root.
2. In **IAM Identity Center**, create an admin user for yourself, with MFA. That is
   the only human login.
3. Install the AWS CLI on DVV's Mac and sign in as that admin:
   `aws configure sso` then `aws sso login`.
4. Apply for **AWS Activate Founders** ($1,000 credits).
5. Open the Bedrock console in **Mumbai (ap-south-1)**. If it asks you to enable
   access to **Qwen3 32B** (Alibaba, open-source), enable it.

## 2. Set up a client — from DVV's Mac

```bash
deploy/aws/provision.sh acme --alert-email you@dvvanalytics.com --admin-cidr <your-ip>/32 --dry-run
deploy/aws/provision.sh acme --alert-email you@dvvanalytics.com --admin-cidr <your-ip>/32
```

The dry run prints every change first. The real run creates the budget (once),
the backup bucket (once), the client's app identity, its keys and its server,
and writes the keys to `deploy/aws/out/acme.env` (never commit or paste it).
It prints the server's IP: point the client's domain **A record** at it.
EU client: add `--region eu-central-1` (Frankfurt).

## 3. Install on the server

SSH in from the Lightsail console (or `ssh ubuntu@<ip>`), then:

```bash
cat ~/.ssh/dvv_deploy.pub
```

Add that key on GitHub: repo → Settings → Deploy keys → Add, **read-only**. Then:

```bash
git clone git@github.com:Deepesh8619/dvv-research-cloud.git /opt/dvv
cd /opt/dvv/deploy
./dvv init acme research.acme.com owner@acme.com     # prints the owner password once
nano .env
```

In `.env`: paste the lines from `deploy/aws/out/acme.env`, paste `DVV_LICENCE`
(`python scripts/issue_licence.py issue --client "Acme" --expires 2027-09-30` on
DVV's Mac), and set `COMPOSE_PROFILES=offsite`. Then:

```bash
./dvv up
./dvv check-ai        # expect: answered by Bedrock ... PASS
```

Delete `deploy/aws/out/acme.env` from DVV's Mac once it is on the server.

## 4. Go-live gate — all must pass before the client's first project

On the server, create a throwaway test project with a one-off owner:

```bash
./dvv load-seed          # prints a random password, once
```

From DVV's Mac, 200 concurrent users against the real server:

```bash
python scripts/load_test.py --base-url https://research.acme.com \
  --email load@local.test --password '<printed password>' --study load
```

Pass = 0 errors and page submits under 100 ms p95. Then, on the server, remove
the test project, its responses and that account:

```bash
./dvv load-cleanup
```

On the server — prove a backup survives the server:

```bash
./dvv backup && ./dvv offsite          # a fresh copy lands in S3
./dvv fetch                            # and comes back
./dvv restore backups/db-<newest>.dump backups/files-<newest>.tgz
```

## Running it

- **Upgrades:** `./dvv upgrade` (backs up first, rolls back if unhealthy).
- **Budget:** alerts arrive at 50 / 80 / 100% of the monthly limit.
- **Rotate the Bedrock key every 90 days:** in IAM → Users → `dvv-acme-app` →
  Security credentials, create a new Amazon Bedrock API key, put it in `.env`,
  `./dvv up`, then delete the old one.
- **Another client:** run step 2 with a new name. Clients share nothing but the
  bucket, and each can only reach its own folder in it.

## Access control

| Who | Can | How |
|---|---|---|
| Root user | Nothing day to day | MFA, locked away |
| You (admin) | Everything | Identity Center login with MFA |
| `dvv-<client>-app` | Invoke Qwen3 open models on Bedrock; read/write its own backup folder | `app-policy.json` — nothing else |
| Server's deploy key | Pull this repo | GitHub read-only deploy key, per server |
| People inside the app | Owner / founder / analyst / reviewer | App roles; sign-in lockout after 5 failures |

Upgrade path when a client needs more: an EC2 server with an instance role and
short-term Bedrock keys removes the long-lived keys entirely.
