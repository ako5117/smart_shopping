#!/usr/bin/env sh
# Nightly backup of the Inventory and Payments databases, safe while the services are running
# (SQLite's online backup, not a file copy). Keeps the last 14 days.
#
#   ./scripts/backup.sh                      # writes to ./backups
#   BACKUP_DIR=/root/backups ./scripts/backup.sh
#
# Cron, every night at 02:30 (run `crontab -e` on the Droplet):
#   30 2 * * * cd /root/smart_shopping && ./scripts/backup.sh >> /var/log/smart-shopping-backup.log 2>&1
set -eu
cd "$(dirname "$0")/.."
BACKUP_DIR="${BACKUP_DIR:-./backups}"
STAMP="$(date +%Y-%m-%d_%H%M)"
mkdir -p "$BACKUP_DIR"

backup() {  # service, database file inside the container
  docker compose exec -T "$1" python -c "
import sqlite3, sys
src = sqlite3.connect('$2'); dst = sqlite3.connect('/tmp/backup.db')
src.backup(dst); dst.close(); src.close()"
  docker compose cp "$1:/tmp/backup.db" "$BACKUP_DIR/$1-$STAMP.db"
  docker compose exec -T "$1" rm -f /tmp/backup.db
  echo "$(date '+%F %T') backed up $1 to $BACKUP_DIR/$1-$STAMP.db"
}

backup inventory /data/inventory.db
backup payments /data/payments.db
find "$BACKUP_DIR" -name '*.db' -mtime +14 -delete
