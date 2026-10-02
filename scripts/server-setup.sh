#!/usr/bin/env bash
# Prepare a fresh Ubuntu Droplet (22.04 or 24.04) to run Smart Shopping. Run once, as root, from the repo:
#
#   git clone https://github.com/ako5117/smart_shopping.git /opt/smart_shopping
#   cd /opt/smart_shopping && ./scripts/server-setup.sh
#
# It installs Docker, opens only SSH, HTTP and HTTPS in the firewall, adds swap (image builds need it on
# a small Droplet), turns on automatic security updates, sets the clock to Nairobi time and schedules
# the nightly database backup. Safe to run again: each step checks first.
# Next: ./scripts/configure.sh, then ./scripts/deploy.sh (see deploy/README.md).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { echo "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "Run as root: sudo ./scripts/server-setup.sh"
# shellcheck source=/dev/null
. /etc/os-release
[ "${ID:-}" = ubuntu ] || echo "Warning: written for Ubuntu; this is ${PRETTY_NAME:-unknown}. Carrying on."
export DEBIAN_FRONTEND=noninteractive
apt-get update -q >/dev/null

say "1/6 Docker"
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  echo "Already installed: $(docker --version)"
else
  apt-get install -y -q ca-certificates curl
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker >/dev/null 2>&1 || true

say "2/6 Firewall: SSH, HTTP and HTTPS only"
apt-get install -y -q ufw >/dev/null
ufw allow OpenSSH >/dev/null 2>&1 || ufw allow 22/tcp >/dev/null   # SSH: keep your way in
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw allow 443/udp >/dev/null   # HTTP/3
ufw --force enable >/dev/null
ufw status | sed -n '1,12p'
echo "The database and the services aren't published on any port; only the proxy (80, 443) is."

say "3/6 Swap"
if swapon --show | grep -q .; then
  swapon --show
else
  if fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile; then
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "Added 2 GB of swap."
  else
    rm -f /swapfile
    echo "Warning: couldn't add swap here. Builds may run out of memory on a 1 GB Droplet; carrying on."
  fi
fi

say "4/6 Automatic security updates"
apt-get install -y -q unattended-upgrades >/dev/null
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
  > /etc/apt/apt.conf.d/20auto-upgrades
echo "On: security updates install every night."

say "5/6 Clock: Africa/Nairobi"
timedatectl set-timezone Africa/Nairobi 2>/dev/null || ln -sf /usr/share/zoneinfo/Africa/Nairobi /etc/localtime
date

say "6/6 Nightly database backup (02:30)"
apt-get install -y -q cron >/dev/null
systemctl enable --now cron >/dev/null 2>&1 || true
LINE="30 2 * * * cd $REPO && ./scripts/backup.sh >> /var/log/smart-shopping-backup.log 2>&1"
if crontab -l 2>/dev/null | grep -qF "./scripts/backup.sh"; then
  echo "Already scheduled."
else
  { crontab -l 2>/dev/null || true; echo "$LINE"; } | crontab -   # root has no crontab at first
  echo "Scheduled: $LINE"
fi

say "Server ready"
cat <<EOF
Next:
  1. Point your domain at this Droplet (an A record to $(curl -fsS --max-time 3 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null || echo "its public IP")).
  2. ./scripts/configure.sh        writes .env (domain, store, M-Pesa, cards)
  3. ./scripts/staff.sh add <your-name> --manager
  4. ./scripts/deploy.sh           checks everything, builds and starts the stack
EOF
