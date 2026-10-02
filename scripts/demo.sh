#!/usr/bin/env bash
# One-command Smart Shopping demo: every service (with the online shop), the M-Pesa simulator and simulated shelf sensors,
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
  secret="$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')"
  db_password="$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')"
  sed -e "s|^CALLBACK_SECRET=.*|CALLBACK_SECRET=${secret}|" \
      -e "s|^DB_PASSWORD=.*|DB_PASSWORD=${db_password}|" \
      -e "s|^STORE_NAME=.*|STORE_NAME=Smart Shopping Demo Store|" \
      .env.example > .env
  chmod 600 .env
fi

if [ ! -f .demo-logins ]; then
  say "Creating demo logins: 'manager' (prices, counts), 'staff' (restock, exit check) and 'rider' (deliveries)"
  : > .demo-logins
  for login in manager staff rider; do
    pw="$(od -An -tx1 -N6 /dev/urandom | tr -d ' \n')"
    if ./scripts/staff.sh list | grep -q "^$login "; then
      ./scripts/staff.sh password "$login" --password "$pw"
    else
      case "$login" in
        manager) ./scripts/staff.sh add manager --manager --password "$pw" ;;
        rider) ./scripts/staff.sh add rider --rider --password "$pw" ;;
        *) ./scripts/staff.sh add staff --password "$pw" ;;
      esac
    fi
    echo "$login $pw" >> .demo-logins
  done
  chmod 600 .demo-logins
fi
if ! grep -q '^rider ' .demo-logins; then  # logins made before riders existed
  pw="$(od -An -tx1 -N6 /dev/urandom | tr -d ' \n')"
  if ./scripts/staff.sh list | grep -q "^rider "; then ./scripts/staff.sh password rider --password "$pw"
  else ./scripts/staff.sh add rider --rider --password "$pw"; fi
  echo "rider $pw" >> .demo-logins
fi
if ! grep -q '^DB_PASSWORD=.' .env; then  # .env from before the shared database
  grep -q '^DB_PASSWORD=' .env || echo 'DB_PASSWORD=' >> .env
  sed -i.bak "s|^DB_PASSWORD=.*|DB_PASSWORD=$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')|" .env && rm -f .env.bak
fi
if grep -q '^COMPOSE_PROFILES=demo' .env; then
  grep -q '^CARD_PROVIDER=' .env || echo 'CARD_PROVIDER=simulator' >> .env  # .env from before card payments
else
  echo "Note: .env is not in demo mode (COMPOSE_PROFILES=demo); the simulators won't start."
fi

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
manager_pw="$(awk '$1=="manager"{print $2}' .demo-logins 2>/dev/null)"
staff_pw="$(awk '$1=="staff"{print $2}' .demo-logins 2>/dev/null)"
rider_pw="$(awk '$1=="rider"{print $2}' .demo-logins 2>/dev/null)"
say "Demo is running"
cat <<EOF
  Store dashboard   http://localhost/dashboard/
                    manager / ${manager_pw:-?}   (can change prices and correct counts)
                    staff   / ${staff_pw:-?}   (can restock and do exit checks)
  Scan & Go         http://localhost/shop/${lan_ip:+     on a phone on the same Wi-Fi: http://${lan_ip}/shop/}
  Online shop       http://localhost/store/${lan_ip:+    on a phone on the same Wi-Fi: http://${lan_ip}/store/}
  Rider app         http://localhost/rider/${lan_ip:+    on a phone on the same Wi-Fi: http://${lan_ip}/rider/}
                    rider   / ${rider_pw:-?}

  M-Pesa simulator: any phone number pays after 3 s; ending 000 cancels; ending 111 fails.
  Card simulator:   4084 0840 8408 4081 pays; 4000 0000 0000 0002 is declined.
  Shelf sensors are simulated: new shelf activity every few seconds on the dashboard.
  Barcodes to type in Scan & Go: 6161000000040 (milk), 6161000000026 (sugar), 6161000000064 (bread)

  Walkthrough:  docs/demo-walkthrough.md
  Stop:         ./scripts/demo.sh --stop       Fresh start:  ./scripts/demo.sh --reset
EOF
