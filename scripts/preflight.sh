#!/usr/bin/env bash
# Check the server is ready before deploying: settings, logins, DNS, disk and memory, backups.
#
#   ./scripts/preflight.sh
#
# Problems that would stop the store working (or take real money wrongly) are errors, and exit 1.
# Anything you might have meant is a warning. scripts/deploy.sh runs this first.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
ENV_FILE="${ENV_FILE:-.env}"
STAFF_DIR="${STAFF_DIR:-deploy/staff}"
errors=0 warnings=0

ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; warnings=$((warnings + 1)); }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; errors=$((errors + 1)); }
val()  { grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2-; }

echo "Settings ($ENV_FILE)"
if [ ! -f "$ENV_FILE" ]; then
  fail "No $ENV_FILE. Run ./scripts/configure.sh first."
  echo; echo "1 problem to fix before deploying."; exit 1
fi

site="$(val SITE_ADDRESS)"; base="$(val PUBLIC_BASE_URL)"; profiles="$(val COMPOSE_PROFILES)"
daraja="$(val DARAJA_ENV)"; cards="$(val CARD_PROVIDER)"; sms="$(val SMS_PROVIDER)"
demo=false; [[ ",$profiles," == *",demo,"* ]] && demo=true

for s in DB_PASSWORD CALLBACK_SECRET; do
  v="$(val "$s")"
  if [ -z "$v" ]; then fail "$s is empty."
  elif [ "${#v}" -lt 24 ]; then fail "$s is too short (${#v} characters); use at least 24 (openssl rand -hex 24)."
  else ok "$s is set."
  fi
done

if [[ "$site" =~ ^[a-z0-9.-]+\.[a-z]{2,}$ ]] && [[ ! "$site" =~ ^[0-9.]+$ ]]; then
  ok "Domain: $site"
  [ "$base" = "https://$site" ] || fail "PUBLIC_BASE_URL should be https://$site (it's \"$base\"). M-Pesa and card results come back there."
else
  fail "SITE_ADDRESS should be the store's domain (it's \"${site:-empty}\"). Without one there's no HTTPS: no camera scanning, no M-Pesa callbacks."
fi

if $demo; then
  warn "Demo mode: M-Pesa and cards go to the simulators. No real money moves."
  [ "$daraja" = simulator ] || fail "Demo mode needs DARAJA_ENV=simulator (it's \"$daraja\")."
else
  case "$daraja" in
    production) ok "M-Pesa: production (real money)." ;;
    sandbox) warn "M-Pesa: Safaricom sandbox (test money only). Switch to production when the store goes live." ;;
    simulator) fail "DARAJA_ENV=simulator without demo mode: the M-Pesa simulator won't be running. Run ./scripts/configure.sh." ;;
    *) fail "DARAJA_ENV must be sandbox or production (it's \"$daraja\")." ;;
  esac
  if [ "$daraja" = sandbox ] || [ "$daraja" = production ]; then
    for v in DARAJA_CONSUMER_KEY DARAJA_CONSUMER_SECRET DARAJA_PASSKEY DARAJA_SHORTCODE; do
      [ -n "$(val "$v")" ] || fail "$v is empty."
    done
    if [ "$daraja" = production ] && [ "$(val DARAJA_SHORTCODE)" = 174379 ]; then
      fail "DARAJA_SHORTCODE is still the sandbox's 174379. Use the store's own Paybill or Till."
    fi
    if [ "$(val DARAJA_TRANSACTION_TYPE)" = CustomerBuyGoodsOnline ] && [ -z "$(val DARAJA_PARTY_B)" ]; then
      fail "A Till (CustomerBuyGoodsOnline) needs DARAJA_PARTY_B, the till number."
    fi
  fi
  case "$cards" in
    "") ok "Cards: off (M-Pesa only)." ;;
    paystack)
      key="$(val PAYSTACK_SECRET_KEY)"
      if [[ "$key" == sk_live_* ]]; then ok "Cards: Paystack, live key."
      elif [[ "$key" == sk_test_* ]]; then warn "Cards: Paystack test key (test cards only)."
      else fail "CARD_PROVIDER=paystack needs PAYSTACK_SECRET_KEY (sk_live_... or sk_test_...)."
      fi
      [ "$daraja" = production ] && [[ "$key" == sk_test_* ]] && warn "Real M-Pesa but test cards: switch to the sk_live_ key too."
      ;;
    simulator) fail "CARD_PROVIDER=simulator without demo mode: the card simulator won't be running." ;;
    *) fail "CARD_PROVIDER must be paystack or empty (it's \"$cards\")." ;;
  esac
fi

case "$sms" in
  "") warn "Texts to customers: off. Customers won't be texted when their order is paid, ready or on its way." ;;
  simulator)
    if $demo; then ok "Texts to customers: simulated (shown on the dashboard, not sent)."
    else warn "SMS_PROVIDER=simulator: texts are recorded but no customer gets them. Use africastalking for a live store."
    fi ;;
  africastalking)
    if [ -z "$(val AT_USERNAME)" ] || [ -z "$(val AT_API_KEY)" ]; then
      fail "SMS_PROVIDER=africastalking needs AT_USERNAME and AT_API_KEY."
    elif [ "$(val AT_USERNAME)" = sandbox ]; then
      warn "Texts: Africa's Talking sandbox (they appear in its simulator, not on phones)."
    else
      sender="$(val AT_SENDER_ID)"
      ok "Texts: Africa's Talking, from ${sender:-their shared number}."
    fi ;;
  *) fail "SMS_PROVIDER must be africastalking, simulator or empty (it's \"$sms\")." ;;
esac

echo "Logins ($STAFF_DIR)"
if [ -s "$STAFF_DIR/users" ] && grep -qv '^#' "$STAFF_DIR/users"; then
  count="$(grep -cv '^#' "$STAFF_DIR/users")"
  managers="$(grep -c ' manager$' "$STAFF_DIR/roles" 2>/dev/null || true)"
  if [ "${managers:-0}" -ge 1 ]; then ok "$count login(s), $managers manager(s)."
  else fail "No manager login. Add one: ./scripts/staff.sh add <your-name> --manager"
  fi
  if grep -qE '^(manager|staff|rider) ' "$STAFF_DIR/users" && ! $demo; then
    warn "Demo logins (manager/staff/rider) exist. Give each person their own: ./scripts/staff.sh remove manager"
  fi
else
  fail "No staff logins; the proxy won't start without one. ./scripts/staff.sh add <your-name> --manager"
fi

echo "Server"
if [[ "$site" == *.* ]] && command -v getent >/dev/null; then
  resolved="$(getent ahostsv4 "$site" 2>/dev/null | awk 'NR==1{print $1}')"
  mine="$(curl -fsS --max-time 3 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null || true)"
  if [ -z "$resolved" ]; then fail "$site doesn't resolve yet. Add an A record pointing at this server, then wait a few minutes."
  elif [ -n "$mine" ] && [ "$resolved" != "$mine" ]; then fail "$site points at $resolved, but this Droplet is $mine."
  else ok "$site resolves to ${resolved}${mine:+ (this Droplet)}."
  fi
fi
free_gb="$(df -Pk . | awk 'NR==2{print int($4/1048576)}')"
if [ "$free_gb" -lt 3 ]; then fail "Only ${free_gb} GB of disk free; builds and backups need at least 3 GB."
else ok "${free_gb} GB of disk free."
fi
if [ -r /proc/meminfo ]; then
  mem_mb="$(awk '/MemTotal|SwapTotal/{s+=$2} END{print int(s/1024)}' /proc/meminfo)"
  if [ "$mem_mb" -lt 1900 ]; then warn "Memory plus swap is ${mem_mb} MB; image builds need about 2 GB. Run ./scripts/server-setup.sh to add swap."
  else ok "Memory plus swap: ${mem_mb} MB."
  fi
fi
if crontab -l 2>/dev/null | grep -qF "scripts/backup.sh"; then ok "Nightly backup scheduled."
else warn "No nightly backup scheduled. ./scripts/server-setup.sh adds one."
fi
if [ -n "$(val SPACES_BUCKET)" ]; then ok "Backups also go to Spaces bucket $(val SPACES_BUCKET)."
else warn "Backups stay on this Droplet only. Add a Spaces bucket (./scripts/configure.sh) or turn on Droplet backups."
fi

echo
if [ "$errors" -gt 0 ]; then
  echo "$errors problem(s) to fix before deploying, $warnings warning(s)."
  exit 1
fi
echo "Ready to deploy ($warnings warning(s))."
