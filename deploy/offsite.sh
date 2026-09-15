#!/usr/bin/env bash
# Copy deploy/backups/ to S3, so a backup outlives the server it was made on.
#   bash /offsite.sh        -> loop: sync every day, one hour after BACKUP_HOUR
#   bash /offsite.sh once   -> sync now (./dvv offsite)
# Never deletes in S3: the bucket's lifecycle rule expires copies after 35 days.
set -euo pipefail
: "${BACKUP_S3_BUCKET:?set BACKUP_S3_BUCKET in deploy/.env}"
: "${CLIENT_SLUG:?}"
target="s3://${BACKUP_S3_BUCKET}/${CLIENT_SLUG}/"

sync_once() {
  # Hidden .db-*/.files-* are half-written backups in progress: never copy those.
  aws s3 sync /backups "$target" --exclude ".*" --only-show-errors
  echo "offsite sync to $target done at $(date)"
}

[ "${1:-}" = "once" ] && { sync_once; exit 0; }

hour=$(( (${BACKUP_HOUR:-2} + 1) % 24 ))
while true; do
  now=$(date +%s)
  next=$(date -d "today ${hour}:00" +%s)
  [ "$next" -le "$now" ] && next=$(date -d "tomorrow ${hour}:00" +%s)
  sleep $((next - now))
  sync_once || echo "offsite sync FAILED at $(date)" >&2
done
