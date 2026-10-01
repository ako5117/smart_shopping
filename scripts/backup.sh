#!/usr/bin/env sh
# Nightly backup of the shared database (products, stock, sales, payments, staff actions), taken with
# pg_dump while everything keeps running. Keeps the last 14 days.
#
#   ./scripts/backup.sh                      # writes to ./backups
#   BACKUP_DIR=/root/backups ./scripts/backup.sh
#
# Cron, every night at 02:30 (run `crontab -e` on the Droplet):
#   30 2 * * * cd /root/smart_shopping && ./scripts/backup.sh >> /var/log/smart-shopping-backup.log 2>&1
#
# Restore (replaces the current data):
#   docker compose stop inventory payments
#   docker compose exec -T db pg_restore -U smartshopping -d smartshopping --clean --if-exists < backups/<file>.dump
#   docker compose start inventory payments
set -eu
cd "$(dirname "$0")/.."
BACKUP_DIR="${BACKUP_DIR:-./backups}"
FILE="$BACKUP_DIR/smartshopping-$(date +%Y-%m-%d_%H%M).dump"
mkdir -p "$BACKUP_DIR"

docker compose exec -T db pg_dump -U smartshopping -d smartshopping --format=custom > "$FILE.partial"
[ -s "$FILE.partial" ] || { echo "$(date '+%F %T') backup FAILED: empty dump" >&2; rm -f "$FILE.partial"; exit 1; }
mv "$FILE.partial" "$FILE"
echo "$(date '+%F %T') backed up the database to $FILE ($(du -h "$FILE" | cut -f1))"
find "$BACKUP_DIR" -name 'smartshopping-*.dump' -mtime +14 -delete
