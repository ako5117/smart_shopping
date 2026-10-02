#!/usr/bin/env sh
# Nightly backup of the shared database (products, stock, sales, payments, deliveries, staff actions),
# taken with pg_dump while everything keeps running. Keeps the last 14 days here, and copies each backup to
# DigitalOcean Spaces when SPACES_BUCKET is set in .env (scripts/configure.sh asks for it).
#
#   ./scripts/backup.sh                      # writes to ./backups
#   BACKUP_DIR=/root/backups ./scripts/backup.sh
#
# scripts/server-setup.sh schedules it every night at 02:30.
#
# Restore (replaces the current data):
#   docker compose stop inventory payments dispatch
#   docker compose exec -T db pg_restore -U smartshopping -d smartshopping --clean --if-exists < backups/<file>.dump
#   docker compose start inventory payments dispatch
# From Spaces, first fetch the file (see deploy/README.md, "Your data, and backing it up").
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

# Off-site copy: a backup on the same disk doesn't survive losing the Droplet.
val() { if [ -f .env ]; then grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true; fi; }
BUCKET="$(val SPACES_BUCKET)"
if [ -n "$BUCKET" ]; then
  REGION="$(val SPACES_REGION)"
  ENDPOINT="$(val SPACES_ENDPOINT)"  # optional: another S3-compatible store
  if docker run --rm -v "$(cd "$BACKUP_DIR" && pwd):/backups:ro" \
      -e AWS_ACCESS_KEY_ID="$(val SPACES_KEY)" -e AWS_SECRET_ACCESS_KEY="$(val SPACES_SECRET)" \
      amazon/aws-cli:2.17.0 --endpoint-url "${ENDPOINT:-https://${REGION:-fra1}.digitaloceanspaces.com}" --only-show-errors \
      s3 cp "/backups/$(basename "$FILE")" "s3://$BUCKET/smart-shopping/$(basename "$FILE")"; then
    echo "$(date '+%F %T') copied to Spaces: $BUCKET/smart-shopping/$(basename "$FILE")"
  else
    echo "$(date '+%F %T') backup copy to Spaces FAILED (the local backup is fine): check SPACES_* in .env" >&2
    exit 1
  fi
fi
