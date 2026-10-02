#!/usr/bin/env bash
# Manage staff logins for the dashboard (and, for managers, the raw service APIs).
#
#   ./scripts/staff.sh add mary              add staff; prompts for a password (or --password 'x')
#   ./scripts/staff.sh add adrian --manager  add a manager: can change prices, correct stock, use the APIs
#   ./scripts/staff.sh add otieno --rider    add a rider: uses the rider app (/rider/) only
#   ./scripts/staff.sh password mary         set a new password
#   ./scripts/staff.sh role mary manager     make someone a manager (or: role mary staff / rider)
#   ./scripts/staff.sh remove mary
#   ./scripts/staff.sh list
#
# Logins live in deploy/staff/users ("name bcrypt-hash"), roles in deploy/staff/roles ("name manager" / "name rider").
# Both are git-ignored. Changes take effect at once if the stack is running.
set -euo pipefail
cd "$(dirname "$0")/.."
DIR=deploy/staff
USERS="$DIR/users"
ROLES="$DIR/roles"
mkdir -p "$DIR"
[ -f "$USERS" ] || cp "$DIR/users.example" "$USERS"
[ -f "$ROLES" ] || cp "$DIR/roles.example" "$ROLES"
chmod 600 "$USERS"

die() { echo "$*" >&2; exit 1; }
valid_name() { [[ "$1" =~ ^[a-z0-9][a-z0-9._-]{1,29}$ ]] || die "Use a short lowercase name (letters, digits, . _ -), e.g. mary or j.otieno"; }
exists() { grep -q "^$1 " "$USERS"; }
without() { grep -v "^$1 " "$2" > "$2.tmp" || true; mv "$2.tmp" "$2"; }

hash_password() {
  local pw="$1"
  [ ${#pw} -ge 8 ] || die "Passwords need at least 8 characters."
  docker run --rm caddy:2.10-alpine caddy hash-password --plaintext "$pw" | tail -n 1
}

ask_password() {
  local pw pw2
  read -rsp "Password for $1: " pw; echo >&2
  read -rsp "Again: " pw2; echo >&2
  [ "$pw" = "$pw2" ] || die "The passwords didn't match."
  printf '%s' "$pw"
}

reload() {
  if docker compose ps --status running --services 2>/dev/null | grep -qx proxy; then
    if out="$(docker compose exec -T proxy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1)"; then
      echo "Proxy reloaded; the change is live."
    else
      echo "The proxy did not accept the change:" >&2; echo "$out" | grep -i error >&2; exit 1
    fi
  fi
}

password_arg() {  # --password value from the remaining args, else prompt
  while [ $# -gt 0 ]; do
    case "$1" in --password) printf '%s' "${2:?--password needs a value}"; return ;; esac
    shift
  done
  return 1
}

cmd="${1:-}"; shift || true
case "$cmd" in
  add)
    name="${1:-}"; valid_name "$name"; shift
    exists "$name" && die "$name already has a login. Use: $0 password $name"
    pw="$(password_arg "$@" || ask_password "$name")"
    h="$(hash_password "$pw")"   # exits here on a bad password, before anything is written
    echo "$name $h" >> "$USERS"
    role=staff
    if [[ " $* " == *" --manager "* ]]; then role=manager; elif [[ " $* " == *" --rider "* ]]; then role=rider; fi
    [ "$role" = staff ] || echo "$name $role" >> "$ROLES"
    echo "Added $name ($role)."
    reload ;;
  password)
    name="${1:-}"; valid_name "$name"; shift
    exists "$name" || die "No login called $name."
    pw="$(password_arg "$@" || ask_password "$name")"
    h="$(hash_password "$pw")"
    without "$name" "$USERS"; echo "$name $h" >> "$USERS"
    echo "Password changed for $name."
    reload ;;
  role)
    name="${1:-}"; role="${2:-}"; valid_name "$name"
    exists "$name" || die "No login called $name."
    without "$name" "$ROLES"
    case "$role" in
      manager|rider) echo "$name $role" >> "$ROLES" ;;
      staff) ;;
      *) die "Role must be manager, staff or rider." ;;
    esac
    echo "$name is now $role."
    reload ;;
  remove)
    name="${1:-}"; valid_name "$name"
    exists "$name" || die "No login called $name."
    without "$name" "$USERS"; without "$name" "$ROLES"
    echo "Removed $name."
    reload ;;
  list)
    grep -v '^#' "$USERS" | awk '{print $1}' | while read -r n; do
      [ -n "$n" ] || continue
      r="$(awk -v n="$n" '$1==n{print $2}' "$ROLES" | tail -n 1)"
      echo "$n  ${r:-staff}"
    done ;;
  *)
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
