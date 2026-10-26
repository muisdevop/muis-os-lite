#!/usr/bin/env bash
# One-command deploy of MUIS OS Lite to an Ubuntu VPS with automatic HTTPS.
#
#   git clone https://github.com/muisdevop/muis-os-lite.git && cd muis-os-lite
#   sudo ./scripts/deploy.sh brain.example.com [you@example.com]
#
# Before running: point an A record for the domain at this server's public IP.
# What it does: installs Docker + the compose plugin if missing, creates .env (asks for keys
# when run interactively, generates an APP_PASSWORD), opens ports 80/443 in ufw if it is active,
# then starts the API, Qdrant and Caddy (Let's Encrypt HTTPS). Re-run any time to update.
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
if [[ -z "$DOMAIN" ]]; then
  echo "usage: sudo $0 <domain> [acme-email]" >&2
  exit 1
fi
if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (sudo $0 $*)" >&2
  exit 1
fi
cd "$(dirname "$0")/.."
log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

# 1. Docker + compose plugin
if ! command -v docker >/dev/null 2>&1; then
  log "Installing Docker"
  curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version >/dev/null 2>&1; then
  log "Installing the docker compose plugin"
  apt-get update -qq && apt-get install -y -qq docker-compose-plugin
fi
systemctl enable --now docker >/dev/null 2>&1 || true

# 2. .env
set_env() {  # set_env KEY VALUE  (replace or append, value kept literal)
  local key="$1" value="$2"
  if grep -qE "^${key}=" .env; then
    KEY="$key" VALUE="$value" python3 - <<'PY'
import os, re
key, value = os.environ["KEY"], os.environ["VALUE"]
text = open(".env").read()
text = re.sub(rf"^{re.escape(key)}=.*$", lambda _: f"{key}={value}", text, flags=re.M)
open(".env", "w").write(text)
PY
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}
get_env() { grep -E "^$1=" .env | head -1 | cut -d= -f2- || true; }

if [[ ! -f .env ]]; then
  log "Creating .env from .env.example"
  cp .env.example .env
  chmod 600 .env
fi
for key in NEBIUS_API_KEY TAVILY_API_KEY; do
  if [[ -z "$(get_env "$key")" && -t 0 ]]; then
    read -r -s -p "$key (leave empty to skip): " value; echo
    [[ -n "$value" ]] && set_env "$key" "$value"
  fi
done
if [[ -z "$(get_env APP_PASSWORD)" ]]; then
  PASSWORD="$(openssl rand -base64 18 2>/dev/null || head -c 18 /dev/urandom | base64)"
  PASSWORD="${PASSWORD//[^A-Za-z0-9]/}"
  set_env APP_PASSWORD "$PASSWORD"
  NEW_PASSWORD=1
fi
[[ -z "$(get_env NEBIUS_API_KEY)$(get_env LLM_API_KEY)" ]] && \
  echo "   note: no NEBIUS_API_KEY yet; chat stays disabled until you add it to .env and re-run"

# 3. Caddy site config (generated, git-ignored, so `git pull` never conflicts)
SITE_BLOCK="$(awk '/^\{\$DOMAIN\}/,0' deploy/Caddyfile)"
if [[ -n "$EMAIL" ]]; then
  printf '{\n\temail %s\n}\n\n%s\n' "$EMAIL" "$SITE_BLOCK" > deploy/Caddyfile.local
else
  printf '%s\n' "$SITE_BLOCK" > deploy/Caddyfile.local
fi

# 4. Firewall
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  log "Opening 80/443 in ufw"
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; ufw allow 443/udp >/dev/null
fi

# 5. Start
log "Building and starting MUIS OS Lite for https://${DOMAIN}"
export DOMAIN
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --remove-orphans

log "Waiting for the API to become healthy"
for _ in $(seq 1 40); do
  if docker compose exec -T api python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)" >/dev/null 2>&1; then
    log "API is up"
    break
  fi
  sleep 3
done

echo
log "Done. Open https://${DOMAIN}  (the first HTTPS request can take ~30s while the certificate is issued)"
if [[ "${NEW_PASSWORD:-0}" == "1" ]]; then
  echo "   Web UI password (APP_PASSWORD, saved in .env): ${PASSWORD}"
fi
echo "   Logs:    docker compose logs -f api"
echo "   Update:  git pull && sudo ./scripts/deploy.sh ${DOMAIN}"
