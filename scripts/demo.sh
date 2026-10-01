#!/usr/bin/env bash
# One-command Smart Shopping demo: every service, the M-Pesa simulator and simulated shelf sensors,
# with demo products loaded. Needs only Docker. Walkthrough: docs/demo-walkthrough.md
#
#   ./scripts/demo.sh            start (first run sets up .env and prints the staff password)
#   ./scripts/demo.sh --reset    wipe all demo data and start fresh
#   ./scripts/demo.sh --stop     stop the demo (data is kept)
set -euo pipefail
cd "$(dirname "$0")/.."

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

command -v docker >/dev/null || { echo "Docker is not installed. Get Docker Desktop: https://docs.docker.com/get-docker/"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is installed but not running. Start Docker Desktop and try again."; exit 1; }

case "${1:-}" in
  --stop)  docker compose --profile demo stop; exit 0 ;;
  --reset)
    say "Wiping demo data (products, stock, sales, payments)..."
    docker compose --profile demo down -v ;;
  "") ;;
  *) echo "Usage: $0 [--reset | --stop]"; exit 1 ;;
esac

if [ ! -f .env ]; then
  say "First run: creating .env in demo mode"
  password="$(od -An -tx1 -N6 /dev/urandom | tr -d ' \n')"
  hash="$(docker run --rm caddy:2.10-alpine caddy hash-password --plaintext "$password" | tail -n 1)"
  secret="$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')"
  # Compose reads $ as a variable, so the hash's $ signs are written as $$.
  sed -e "s|^STAFF_PASSWORD_HASH=.*|STAFF_PASSWORD_HASH=${hash//\$/\$\$}|" \
      -e "s|^CALLBACK_SECRET=.*|CALLBACK_SECRET=${secret}|" \
      -e "s|^STORE_NAME=.*|STORE_NAME=Smart Shopping Demo Store|" \
      .env.example > .env
  printf '%s\n' "$password" > .demo-staff-password
  chmod 600 .env .demo-staff-password
fi
grep -q '^COMPOSE_PROFILES=demo' .env || echo "Note: .env is not in demo mode (COMPOSE_PROFILES=demo); the simulators won't start."

say "Building and starting the services (the first build takes a few minutes)..."
docker compose up -d --build

say "Waiting for the services..."
for _ in $(seq 1 60); do
  curl -fs -o /dev/null http://localhost/shop/ && break
  sleep 2
done
curl -fs -o /dev/null http://localhost/shop/ || { echo "The site didn't come up. Check: docker compose ps; docker compose logs"; exit 1; }

say "Loading demo products, prices and stock..."
docker compose cp scripts/seed_demo.py inventory:/tmp/seed_demo.py >/dev/null
docker compose cp services/shelf/config.example.json inventory:/tmp/catalogue.json >/dev/null
docker compose exec -T inventory python /tmp/seed_demo.py --url http://localhost:8010 --catalogue /tmp/catalogue.json

lan_ip="$( (hostname -I 2>/dev/null || ipconfig getifaddr en0 2>/dev/null || true) | awk '{print $1}')"
password="$(cat .demo-staff-password 2>/dev/null || echo '(the password you set in .env)')"
say "Demo is running"
cat <<EOF
  Store dashboard   http://localhost/dashboard/    user: staff   password: ${password}
  Scan & Go         http://localhost/shop/${lan_ip:+     on a phone on the same Wi-Fi: http://${lan_ip}/shop/}

  M-Pesa simulator: any phone number pays after 3 s; ending 000 cancels; ending 111 fails.
  Shelf sensors are simulated: new shelf activity every few seconds on the dashboard.
  Barcodes to type in Scan & Go: 6161000000040 (milk), 6161000000026 (sugar), 6161000000064 (bread)

  Walkthrough:  docs/demo-walkthrough.md
  Stop:         ./scripts/demo.sh --stop       Fresh start:  ./scripts/demo.sh --reset
EOF
