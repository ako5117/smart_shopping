#!/usr/bin/env bash
# Deploy or update Smart Shopping on the server.
#
#   ./scripts/deploy.sh                 latest code on the current branch
#   ./scripts/deploy.sh --ref <commit>  a specific commit or tag (e.g. to go back to the previous one)
#   ./scripts/deploy.sh --no-pull       the code that's already here
#
# Steps: check the settings (scripts/preflight.sh), back up the database if it's running, get the code,
# build, start, wait until every service reports healthy, then try the public pages.
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { echo "$*" >&2; exit 1; }
val() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2-; }

ref="" pull=true
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) ref="${2:?--ref needs a commit or tag}"; shift 2 ;;
    --no-pull) pull=false; shift ;;
    *) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
  esac
done

say "Checking settings"
./scripts/preflight.sh || die "Fix the problems above, then run ./scripts/deploy.sh again."

before="$(git rev-parse --short HEAD)"
if docker compose ps --status running --services 2>/dev/null | grep -qx db; then
  say "Backing up the database first"
  ./scripts/backup.sh
fi

if [ -n "$ref" ]; then
  say "Switching to $ref"
  git fetch --tags origin
  git checkout --quiet "$ref"
elif $pull; then
  say "Getting the latest code"
  if git symbolic-ref -q HEAD >/dev/null; then git pull --ff-only
  else echo "Not on a branch (an earlier --ref); keeping $(git rev-parse --short HEAD). Use: git checkout main"
  fi
fi
now="$(git rev-parse --short HEAD)"

say "Building (a few minutes the first time)"
docker compose build

say "Starting"
docker compose up -d --remove-orphans

say "Waiting for every service to be healthy"
deadline=$((SECONDS + 180))
while :; do
  waiting=""
  for id in $(docker compose ps -q); do
    state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id")"
    case "$state" in healthy|running) ;; *) waiting="$waiting $(docker inspect --format '{{.Name}}' "$id" | sed 's/^\///')($state)" ;; esac
  done
  [ -z "$waiting" ] && break
  if [ $SECONDS -ge $deadline ]; then
    docker compose ps
    die "Still not healthy after 3 minutes:$waiting. See: docker compose logs <service>"
  fi
  sleep 5
done
echo "All healthy."

say "Trying the public pages"
base="$(val PUBLIC_BASE_URL)"; base="${base:-http://localhost}"
failed=0
for path in /shop/ /store/ /store/api/catalogue /shop/api/store; do
  if code="$(curl -fsS -o /dev/null -w '%{http_code}' --max-time 20 "$base$path" 2>/dev/null)"; then
    echo "  $code  $base$path"
  else
    echo "  FAIL $base$path"; failed=1
  fi
done
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$base/dashboard/" || true)"
echo "  $code  $base/dashboard/ (401 means the staff login is asked for, as it should be)"
[ "$failed" = 0 ] || die "Some pages didn't load. The certificate can take a minute on the first start; try again, or see: docker compose logs proxy"

say "Deployed $now (was $before)"
echo "To go back: ./scripts/deploy.sh --ref $before"
