#!/usr/bin/env bash
# Switch the HIMS deployment between two backend connection modes:
#
#   ./setup.sh local                      Install a full Frappe Health backend in Docker
#   ./setup.sh remote <frappe-url>        Connect to an existing (remote) Frappe server
#   ./setup.sh                            Interactive — asks which mode you want
#
# The script only writes env files and prints the exact compose command;
# it never starts containers by itself.
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-}"
REMOTE_URL="${2:-}"

if [[ -z "${MODE}" ]]; then
  echo "How should the HIMS connect to Frappe Health?"
  echo "  1) local  — install a Frappe Health backend in Docker (ERPNext + Marley)"
  echo "  2) remote — connect to an existing Frappe server you already run"
  read -r -p "Choose [1/2]: " choice
  case "${choice}" in
    1|local) MODE="local" ;;
    2|remote) MODE="remote" ;;
    *) echo "Invalid choice." >&2; exit 1 ;;
  esac
fi

# --- shared secrets ---------------------------------------------------------
[[ -f .env ]] || cp .env.example .env
[[ -f bff/.env ]] || cp bff/.env.example bff/.env
echo "✔ env files ready (deployment/.env, deployment/bff/.env)"

if grep -q 'change-me' .env; then
  echo "  ! .env still has placeholder passwords — edit DB_ROOT_PASSWORD / ADMIN_PASSWORD."
fi

case "${MODE}" in
  local)
    # Clear any remote override so the compose default (http://frontend:8080) applies.
    sed -i.bak '/^FRAPPE_BASE_URL=/d' .env && rm -f .env.bak
    sed -i.bak 's|^FRAPPE_BASE_URL=.*|FRAPPE_BASE_URL=http://frontend:8080|' bff/.env && rm -f bff/.env.bak
    cat <<'EOF'

Mode: LOCAL — install Frappe Health in Docker.

Next steps:
  1. Build the image (15–30 min first time):   (cd frappe && ./build-image.sh)
  2. Start everything:                         docker compose --profile local up -d
  3. Wait for site creation:                   docker compose logs -f create-site
  4. In Desk (http://localhost:8080), generate API keys for an integration user,
     put them in deployment/bff/.env, then:    docker compose up -d --build bff web
  5. Verify:                                   curl -s http://localhost:3000/api/v1/health
EOF
    ;;
  remote)
    if [[ -z "${REMOTE_URL}" ]]; then
      read -r -p "Frappe server URL (e.g. https://frappe.example.com): " REMOTE_URL
    fi
    REMOTE_URL="${REMOTE_URL%/}"
    if [[ ! "${REMOTE_URL}" =~ ^https?:// ]]; then
      echo "URL must start with http:// or https://" >&2; exit 1
    fi
    # Compose variable (overrides the local default) + BFF env file.
    if grep -q '^FRAPPE_BASE_URL=' .env; then
      sed -i.bak "s|^FRAPPE_BASE_URL=.*|FRAPPE_BASE_URL=${REMOTE_URL}|" .env && rm -f .env.bak
    else
      printf '\n# Remote Frappe server (set by setup.sh)\nFRAPPE_BASE_URL=%s\n' "${REMOTE_URL}" >> .env
    fi
    sed -i.bak "s|^FRAPPE_BASE_URL=.*|FRAPPE_BASE_URL=${REMOTE_URL}|" bff/.env && rm -f bff/.env.bak
    cat <<EOF

Mode: REMOTE — connecting to ${REMOTE_URL}

Next steps:
  1. On that Frappe server, ensure the 'healthcare' (Marley) app is installed
     on the site, and ERPNext is present (Marley requires it).
  2. In Desk → your integration user → API Access → Generate Keys,
     put FRAPPE_API_KEY / FRAPPE_API_SECRET into deployment/bff/.env.
  3. Start only the HIMS layer (no local Frappe stack):
         docker compose up -d --build bff web
  4. Verify:  curl -s http://localhost:3000/api/v1/health
     → {"status":"ok","frappe":"up","mode":"remote",...}
EOF
    ;;
  *)
    echo "Unknown mode '${MODE}' — use 'local' or 'remote'." >&2; exit 1 ;;
esac
