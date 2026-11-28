#!/usr/bin/env bash
# Install and start a self-hosted DungScraper API on Ubuntu or Debian (amd64 or arm64).
# Safe to run again: existing API keys, data and containers are kept.
#
#   curl -fsSL https://raw.githubusercontent.com/dakisxxx-oss/DungScraper/main/scripts/install.sh | sudo bash
set -euo pipefail

REPO_URL="${DUNGSCRAPER_REPO_URL:-https://github.com/dakisxxx-oss/DungScraper.git}"
INSTALL_DIR="${DUNGSCRAPER_INSTALL_DIR:-/opt/dungscraper}"
PORT="${DUNGSCRAPER_PORT:-8741}"

step() { printf '==> %s\n' "$1"; }
fail() { printf 'ERROR: %s\n' "$1" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Run this script as root (sudo bash)."

command -v apt-get >/dev/null 2>&1 || fail "This installer supports Ubuntu and Debian (apt)."
command -v git >/dev/null 2>&1 || { step "Installing git"; DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git ca-certificates curl >/dev/null; }

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  step "Installing Docker Engine and the Compose plugin"
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates curl >/dev/null
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  codename="${VERSION_CODENAME:-stable}"
  arch="$(dpkg --print-architecture)"
  echo "deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${codename} stable" > /etc/apt/sources.list.d/docker.list
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null
  systemctl enable --now docker >/dev/null
fi

command -v docker >/dev/null 2>&1 || fail "Docker did not install."
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin did not install."

if [ -d "${INSTALL_DIR}/.git" ]; then
  step "Updating ${INSTALL_DIR}"
  git -C "${INSTALL_DIR}" fetch --quiet origin
  git -C "${INSTALL_DIR}" pull --ff-only --quiet origin main || git -C "${INSTALL_DIR}" pull --ff-only --quiet
else
  step "Cloning into ${INSTALL_DIR}"
  mkdir -p "$(dirname "${INSTALL_DIR}")"
  git clone --depth 1 --branch main "${REPO_URL}" "${INSTALL_DIR}"
fi
cd "${INSTALL_DIR}"

if [ ! -f .env ]; then
  step "Creating .env with a new API key"
  KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  umask 077
  printf 'DUNGSCRAPER_API_KEYS=%s\nDUNGSCRAPER_BIND=0.0.0.0\n' "${KEY}" > .env
  chmod 600 .env
else
  step "Keeping the existing .env (keys are not rotated)"
  chmod 600 .env
  if ! grep -q '^DUNGSCRAPER_BIND=' .env; then
    printf 'DUNGSCRAPER_BIND=0.0.0.0\n' >> .env
  fi
  KEY="$(grep '^DUNGSCRAPER_API_KEYS=' .env | head -n1 | cut -d= -f2- | tr -d '\r')"
fi

step "Opening TCP port ${PORT} on the host firewall"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi "Status: active"; then
  ufw allow "${PORT}/tcp" >/dev/null || true
fi
if command -v iptables >/dev/null 2>&1; then
  iptables -C INPUT -p tcp -m state --state NEW --dport "${PORT}" -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 5 -p tcp -m state --state NEW --dport "${PORT}" -j ACCEPT || true
  if [ -f /etc/iptables/rules.v4 ] && ! grep -q -- "--dport ${PORT}" /etc/iptables/rules.v4; then
    sed -i "0,/-A INPUT -j REJECT/s//-A INPUT -p tcp -m state --state NEW -m tcp --dport ${PORT} -j ACCEPT\n-A INPUT -j REJECT/" /etc/iptables/rules.v4 || true
  fi
fi

if docker image inspect dungscraper:latest >/dev/null 2>&1; then
  step "Starting DungScraper (existing image)"
  docker compose up -d
else
  step "Building and starting DungScraper (first run compiles the image)"
  docker compose up --build -d
fi

step "Waiting for /health"
ok=0
for _ in $(seq 1 60); do
  if curl -fsS -m 3 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 2
done
[ "${ok}" -eq 1 ] || fail "The API did not become healthy. Check: docker compose --project-directory ${INSTALL_DIR} logs"

IP="$(curl -fsS -m 3 https://ifconfig.me 2>/dev/null || true)"
[ -n "${IP}" ] || IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "${IP}" ] || IP="127.0.0.1"

cat <<MSG

DungScraper is running.
  Local:  http://127.0.0.1:${PORT}/health
  Public: http://${IP}:${PORT}/r/https://example.com

API key file: ${INSTALL_DIR}/.env
API key:      ${KEY}

Send it as:  Authorization: Bearer <key>   or   X-API-Key: <key>
On Oracle Cloud, also add an ingress rule in the VCN security list for TCP port ${PORT}.
A domain and TLS can be added later in front of this port.

MSG
