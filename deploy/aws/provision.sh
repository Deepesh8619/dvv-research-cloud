#!/usr/bin/env bash
# Set up AWS for one DVV Research Cloud client. Run on DVV's machine, signed in
# to the AWS CLI as an admin (aws configure / aws sso login).
#
#   deploy/aws/provision.sh <client-slug> --alert-email you@dvv.com [options] [--dry-run]
#
#   --region      AWS region (default ap-south-1 Mumbai; eu-central-1 = Frankfurt)
#   --ram         server memory in GB (default 4: 2 vCPU, the measured 200-user size)
#   --budget      monthly USD budget for the whole account, alerts at 50/80/100% (default 50)
#   --admin-cidr  your IP as a.b.c.d/32: SSH is then allowed from there only
#   --dry-run     print every change without making it
#
# Creates (skipping anything that already exists):
#   account-wide, once: a monthly budget with email alerts; the backup bucket
#     (private, encrypted, copies expire after 35 days)
#   per client: an app identity that can ONLY invoke the open model and read/write
#     its own backup folder; its Bedrock API key (90 days) and S3 access key; a
#     Lightsail server with a static IP and ports 80/443.
# The client's secrets are written to deploy/aws/out/<client>.env (mode 600,
# gitignored) — copy that file to the server, never paste it into chat or git.
set -euo pipefail
cd "$(dirname "$0")"

client="${1:-}"; shift || true
region=ap-south-1; ram=4; budget=50; email=""; admin_cidr=""; dry=0
while [ $# -gt 0 ]; do
  case "$1" in
    --region) region=$2; shift 2 ;;
    --ram) ram=$2; shift 2 ;;
    --budget) budget=$2; shift 2 ;;
    --alert-email) email=$2; shift 2 ;;
    --admin-cidr) admin_cidr=$2; shift 2 ;;
    --dry-run) dry=1; shift ;;
    *) echo "unknown option $1"; exit 1 ;;
  esac
done
[[ "$client" =~ ^[a-z0-9-]{2,30}$ ]] || { sed -n '5,13p' "$0"; exit 1; }
[ -n "$email" ] || { echo "--alert-email is required (budget alerts go there)"; exit 1; }

# Mutating calls go through `change`: printed in --dry-run, executed otherwise.
# Read-only lookups always run — they cost nothing and the plan depends on them.
change() { if [ $dry = 1 ]; then echo "  would run: aws $*"; else aws "$@"; fi; }
say() { echo; echo "== $*"; }

account=$(aws sts get-caller-identity --query Account --output text)
bucket="dvv-backups-${account}-${region}"
user="dvv-${client}-app"
instance="dvv-${client}"
echo "account $account · region $region · client $client$([ $dry = 1 ] && echo ' · DRY RUN')"

say "Budget: \$${budget}/month, alerts to ${email} at 50/80/100%"
if aws budgets describe-budget --account-id "$account" --budget-name dvv-monthly >/dev/null 2>&1; then
  echo "  exists"
else
  notes=""
  for pct in 50 80 100; do
    notes+="{\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":${pct},\"ThresholdType\":\"PERCENTAGE\"},\"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"${email}\"}]},"
  done
  change budgets create-budget --account-id "$account" \
    --budget "{\"BudgetName\":\"dvv-monthly\",\"BudgetLimit\":{\"Amount\":\"${budget}\",\"Unit\":\"USD\"},\"TimeUnit\":\"MONTHLY\",\"BudgetType\":\"COST\"}" \
    --notifications-with-subscribers "[${notes%,}]"
fi

say "Backup bucket s3://${bucket}"
if aws s3api head-bucket --bucket "$bucket" --region "$region" 2>/dev/null; then
  echo "  exists"
else
  change s3api create-bucket --bucket "$bucket" --region "$region" \
    --create-bucket-configuration "LocationConstraint=${region}"
  change s3api put-public-access-block --bucket "$bucket" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  change s3api put-bucket-encryption --bucket "$bucket" --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  change s3api put-bucket-lifecycle-configuration --bucket "$bucket" --lifecycle-configuration \
    '{"Rules":[{"ID":"expire-old-backups","Status":"Enabled","Filter":{"Prefix":""},"Expiration":{"Days":35}}]}'
fi

say "App identity ${user}: the open model + its own backup folder, nothing else"
policy=$(sed -e "s/__REGION__/${region}/g" -e "s/__BUCKET__/${bucket}/g" -e "s/__CLIENT__/${client}/g" app-policy.json)
if aws iam get-user --user-name "$user" >/dev/null 2>&1; then
  echo "  exists — policy refreshed, keys left as they are (rotate with the README)"
  change iam put-user-policy --user-name "$user" --policy-name dvv-app --policy-document "$policy"
  new_keys=0
else
  change iam create-user --user-name "$user" --tags Key=dvv-client,Value="$client"
  change iam put-user-policy --user-name "$user" --policy-name dvv-app --policy-document "$policy"
  new_keys=1
fi

say "Server ${instance}: Lightsail, ${ram} GB"
blueprint=$(aws lightsail get-blueprints --region "$region" --query \
  "blueprints[?platform=='LINUX_UNIX' && isActive && starts_with(blueprintId,'ubuntu_24')].blueprintId | [0]" --output text)
bundle=$(aws lightsail get-bundles --region "$region" --query \
  "sort_by(bundles[?isActive && ramSizeInGb==\`${ram}\` && contains(supportedPlatforms,'LINUX_UNIX') && !contains(bundleId,'ipv6')], &price)[0].[bundleId,price,cpuCount]" --output text)
read -r bundle_id price cpus <<<"$bundle"
[ -n "$blueprint" ] && [ "$blueprint" != "None" ] && [ -n "$bundle_id" ] && [ "$bundle_id" != "None" ] \
  || { echo "  no Ubuntu 24 image or ${ram} GB plan found in ${region}"; exit 1; }
echo "  image ${blueprint}, plan ${bundle_id}: ${cpus} vCPU, ${ram} GB, \$${price}/month"
if aws lightsail get-instance --instance-name "$instance" --region "$region" >/dev/null 2>&1; then
  echo "  exists"
else
  change lightsail create-instances --region "$region" --instance-names "$instance" \
    --availability-zone "${region}a" --blueprint-id "$blueprint" --bundle-id "$bundle_id" \
    --user-data "$(cat user-data.sh)" --tags key=dvv-client,value="$client"
  change lightsail allocate-static-ip --region "$region" --static-ip-name "${instance}-ip"
  if [ $dry = 0 ]; then
    echo "  waiting for the server to start..."
    for _ in $(seq 60); do
      state=$(aws lightsail get-instance-state --region "$region" --instance-name "$instance" \
        --query state.name --output text 2>/dev/null || true)
      [ "$state" = "running" ] && break
      sleep 5
    done
  fi
  change lightsail attach-static-ip --region "$region" --static-ip-name "${instance}-ip" \
    --instance-name "$instance"
fi
ssh_rule="fromPort=22,toPort=22,protocol=TCP"
[ -n "$admin_cidr" ] && ssh_rule+=",cidrs=${admin_cidr}"
change lightsail put-instance-public-ports --region "$region" --instance-name "$instance" \
  --port-infos "$ssh_rule" fromPort=80,toPort=80,protocol=TCP fromPort=443,toPort=443,protocol=TCP

if [ $dry = 0 ] && [ "$new_keys" = 1 ]; then
  say "Keys (shown nowhere else — written to deploy/aws/out/${client}.env)"
  mkdir -p out && umask 077
  bedrock_key=$(aws iam create-service-specific-credential --user-name "$user" \
    --service-name bedrock.amazonaws.com --credential-age-days 90 \
    --query 'ServiceSpecificCredential.ServiceApiKeyValue' --output text)
  read -r key_id secret <<<"$(aws iam create-access-key --user-name "$user" \
    --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text)"
  cat > "out/${client}.env" <<ENV
# ${client} — generated $(date +%F). Append to the server's /opt/dvv/deploy/.env, then delete this file.
BEDROCK_API_KEY=${bedrock_key}
BEDROCK_REGION=${region}
BACKUP_S3_BUCKET=${bucket}
AWS_ACCESS_KEY_ID=${key_id}
AWS_SECRET_ACCESS_KEY=${secret}
AWS_DEFAULT_REGION=${region}
ENV
  echo "  written. The Bedrock key expires in 90 days — rotation steps are in the README."
fi

ip=$( [ $dry = 1 ] && echo "<static ip>" || aws lightsail get-static-ip --region "$region" \
  --static-ip-name "${instance}-ip" --query staticIp.ipAddress --output text)
say "Next: point the client's domain (A record) at ${ip}, then follow deploy/aws/README.md step 3"
