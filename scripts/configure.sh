#!/usr/bin/env bash
# Write the server's .env by answering a few questions. Run on the Droplet, from the repo:
#
#   ./scripts/configure.sh
#
# Two kinds of server:
#   live  real customers: M-Pesa through Safaricom (sandbox while testing, then production), cards
#         through Paystack if you want them.
#   demo  a public demo for partners: everything works on your domain with HTTPS, but payments go to the
#         simulators and no money moves.
#
# Run it again any time to change answers: the current values are offered as defaults, and the database
# password and callback secret are kept (changing the database password would lock the services out).
set -euo pipefail
cd "$(dirname "$0")/.."
ENV_FILE="${ENV_FILE:-.env}"

die() { echo "$*" >&2; exit 1; }
current() { if [ -f "$ENV_FILE" ]; then grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true; fi; }
random() { od -An -tx1 -N24 /dev/urandom | tr -d ' \n'; }

ask() {  # ask VAR "Question" [default]; an existing value in .env wins over the default
  local var="$1" question="$2" def answer
  def="$(current "$var")"; def="${def:-${3:-}}"
  read -rp "$question${def:+ [$def]}: " answer || true
  printf -v "$var" '%s' "${answer:-$def}"
}

ask_secret() {  # like ask, but doesn't show what's typed; Enter keeps the current value
  local var="$1" question="$2" def answer
  def="$(current "$var")"
  read -rsp "$question${def:+ [Enter keeps the current one]}: " answer || true
  echo
  printf -v "$var" '%s' "${answer:-$def}"
}

one_of() {  # one_of VALUE option...
  local v="$1"; shift
  for o in "$@"; do [ "$v" = "$o" ] && return 0; done
  return 1
}

echo "Smart Shopping server settings. Press Enter to accept the value in [brackets]."
echo

ask SITE_ADDRESS "Domain for this server, e.g. shop.awesomtech.co.ke"
SITE_ADDRESS="${SITE_ADDRESS,,}"
[[ "$SITE_ADDRESS" =~ ^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$ ]] \
  || die "\"$SITE_ADDRESS\" isn't a domain name. HTTPS needs one (an IP address won't do)."

MODE_DEFAULT=live
[ "$(current COMPOSE_PROFILES)" = demo ] && MODE_DEFAULT=demo
read -rp "Live store or partner demo? (live/demo) [$MODE_DEFAULT]: " MODE || true
MODE="${MODE:-$MODE_DEFAULT}"
one_of "$MODE" live demo || die "Answer live or demo."

ask STORE_NAME "Store name (customers see it)" "Smart Shopping"
ask STORE_ID "Store code" "001"
ask DELIVERY_AREAS "Delivery areas and fees, e.g. Kilimani:150,Westlands:200 (Enter: demo list around Nairobi)" ""

DARAJA_CONSUMER_KEY="$(current DARAJA_CONSUMER_KEY)"; DARAJA_CONSUMER_SECRET="$(current DARAJA_CONSUMER_SECRET)"
DARAJA_SHORTCODE="$(current DARAJA_SHORTCODE)"; DARAJA_PASSKEY="$(current DARAJA_PASSKEY)"
DARAJA_TRANSACTION_TYPE="$(current DARAJA_TRANSACTION_TYPE)"; DARAJA_PARTY_B="$(current DARAJA_PARTY_B)"
PAYSTACK_SECRET_KEY="$(current PAYSTACK_SECRET_KEY)"

if [ "$MODE" = demo ]; then
  COMPOSE_PROFILES=demo; DARAJA_ENV=simulator; CARD_PROVIDER=simulator
  echo "Demo: M-Pesa and cards go to the simulators. No money moves."
else
  COMPOSE_PROFILES=""
  echo
  echo "M-Pesa (Daraja app at developer.safaricom.co.ke)."
  ENV_DEFAULT="$(current DARAJA_ENV)"; one_of "$ENV_DEFAULT" sandbox production || ENV_DEFAULT=sandbox
  read -rp "Safaricom environment: sandbox (test money) or production [$ENV_DEFAULT]: " DARAJA_ENV || true
  DARAJA_ENV="${DARAJA_ENV:-$ENV_DEFAULT}"
  one_of "$DARAJA_ENV" sandbox production || die "Answer sandbox or production."
  ask DARAJA_CONSUMER_KEY "Consumer key"
  ask_secret DARAJA_CONSUMER_SECRET "Consumer secret"
  ask DARAJA_SHORTCODE "Shortcode (Paybill or the Till's store number; sandbox: 174379)" "174379"
  ask_secret DARAJA_PASSKEY "Passkey"
  ask DARAJA_TRANSACTION_TYPE "Paybill or Till? CustomerPayBillOnline or CustomerBuyGoodsOnline" "CustomerPayBillOnline"
  one_of "$DARAJA_TRANSACTION_TYPE" CustomerPayBillOnline CustomerBuyGoodsOnline \
    || die "Answer CustomerPayBillOnline (Paybill) or CustomerBuyGoodsOnline (Till)."
  if [ "$DARAJA_TRANSACTION_TYPE" = CustomerBuyGoodsOnline ]; then
    ask DARAJA_PARTY_B "Till number (where the money goes)"
    [ -n "$DARAJA_PARTY_B" ] || die "A Till needs its till number."
  else
    DARAJA_PARTY_B=""
  fi
  for v in DARAJA_CONSUMER_KEY DARAJA_CONSUMER_SECRET DARAJA_SHORTCODE DARAJA_PASSKEY; do
    [ -n "${!v}" ] || die "$v is needed for M-Pesa."
  done

  echo
  CARD_DEFAULT=none; [ "$(current CARD_PROVIDER)" = paystack ] && CARD_DEFAULT=paystack
  read -rp "Card payments through Paystack? (paystack/none) [$CARD_DEFAULT]: " CARDS || true
  CARDS="${CARDS:-$CARD_DEFAULT}"
  one_of "$CARDS" paystack none || die "Answer paystack or none."
  if [ "$CARDS" = paystack ]; then
    CARD_PROVIDER=paystack
    ask_secret PAYSTACK_SECRET_KEY "Paystack secret key (sk_test_... or sk_live_...)"
    [[ "$PAYSTACK_SECRET_KEY" =~ ^sk_(test|live)_ ]] || die "That isn't a Paystack secret key."
  else
    CARD_PROVIDER=""; PAYSTACK_SECRET_KEY=""
  fi
fi

echo
echo "Off-site backups to DigitalOcean Spaces (optional; Enter to skip)."
ask SPACES_BUCKET "Spaces bucket name" ""
SPACES_REGION="$(current SPACES_REGION)"; SPACES_KEY="$(current SPACES_KEY)"; SPACES_SECRET="$(current SPACES_SECRET)"
if [ -n "$SPACES_BUCKET" ]; then
  ask SPACES_REGION "Spaces region, e.g. fra1, ams3, sgp1" "fra1"
  ask SPACES_KEY "Spaces access key"
  ask_secret SPACES_SECRET "Spaces secret key"
else
  SPACES_REGION=""; SPACES_KEY=""; SPACES_SECRET=""
fi

DB_PASSWORD="$(current DB_PASSWORD)"; DB_PASSWORD="${DB_PASSWORD:-$(random)}"
CALLBACK_SECRET="$(current CALLBACK_SECRET)"; CALLBACK_SECRET="${CALLBACK_SECRET:-$(random)}"
PUBLIC_BASE_URL="https://$SITE_ADDRESS"

[ -f "$ENV_FILE" ] && cp "$ENV_FILE" "$ENV_FILE.bak"
umask 077
cat > "$ENV_FILE" <<EOF
# Written by scripts/configure.sh on $(date '+%Y-%m-%d %H:%M'). Run it again to change answers. Never commit this file.

# --- Mode ($MODE)
COMPOSE_PROFILES=$COMPOSE_PROFILES
DARAJA_ENV=$DARAJA_ENV
CARD_PROVIDER=$CARD_PROVIDER

# --- Server
SITE_ADDRESS=$SITE_ADDRESS
PUBLIC_BASE_URL=$PUBLIC_BASE_URL
# Generated. Keep them: the database keeps the password it was created with.
DB_PASSWORD=$DB_PASSWORD
CALLBACK_SECRET=$CALLBACK_SECRET

# --- Store
STORE_ID=$STORE_ID
STORE_NAME=$STORE_NAME
DELIVERY_AREAS=$DELIVERY_AREAS

# --- M-Pesa (Daraja)
DARAJA_CONSUMER_KEY=$DARAJA_CONSUMER_KEY
DARAJA_CONSUMER_SECRET=$DARAJA_CONSUMER_SECRET
DARAJA_SHORTCODE=$DARAJA_SHORTCODE
DARAJA_PASSKEY=$DARAJA_PASSKEY
DARAJA_TRANSACTION_TYPE=$DARAJA_TRANSACTION_TYPE
DARAJA_PARTY_B=$DARAJA_PARTY_B

# --- Cards (Paystack). Webhook URL to set on the Paystack dashboard: $PUBLIC_BASE_URL/pay/payments/card/webhook
PAYSTACK_SECRET_KEY=$PAYSTACK_SECRET_KEY

# --- Off-site backups (DigitalOcean Spaces), used by scripts/backup.sh
SPACES_BUCKET=$SPACES_BUCKET
SPACES_REGION=$SPACES_REGION
SPACES_KEY=$SPACES_KEY
SPACES_SECRET=$SPACES_SECRET
EOF

echo
echo "Saved $ENV_FILE ($MODE mode)."
echo "M-Pesa callbacks will go to $PUBLIC_BASE_URL/pay/payments/mpesa/callback/<secret>."
[ "$CARD_PROVIDER" = paystack ] && echo "Set the Paystack webhook URL to $PUBLIC_BASE_URL/pay/payments/card/webhook."
echo "Next: ./scripts/staff.sh add <your-name> --manager   (if you haven't), then ./scripts/deploy.sh"
