#!/usr/bin/env bash
# Nightly backup of the database and uploaded files, into deploy/backups/.
#   bash /backup.sh        -> loop: run every day at BACKUP_HOUR (server time)
#   bash /backup.sh once   -> run one backup now and exit (used by ./dvv)
set -euo pipefail

run_once() {
  local stamp; stamp=$(date +%Y%m%d-%H%M%S)
  # Write under a temp name and rename, so a half-written file is never
  # mistaken for a good backup by a restore.
  pg_dump -Fc -f "/backups/.db-$stamp.dump" && mv "/backups/.db-$stamp.dump" "/backups/db-$stamp.dump"
  tar czf "/backups/.files-$stamp.tgz" -C /studies . && mv "/backups/.files-$stamp.tgz" "/backups/files-$stamp.tgz"
  find /backups -maxdepth 1 \( -name 'db-*.dump' -o -name 'files-*.tgz' \) -mtime "+${BACKUP_KEEP_DAYS}" -delete
  echo "backup $stamp done"
}

[ "${1:-}" = "once" ] && { run_once; exit 0; }

while true; do
  now=$(date +%s)
  next=$(date -d "today ${BACKUP_HOUR}:00" +%s)
  [ "$next" -le "$now" ] && next=$(date -d "tomorrow ${BACKUP_HOUR}:00" +%s)
  sleep $((next - now))
  run_once || echo "backup FAILED at $(date)" >&2
done
