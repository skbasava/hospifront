# Deployment — HIMS frontend + BFF + Frappe Health

Two ways to connect the HIMS to Frappe Health — pick one with the switch script:

```bash
cd deployment
./setup.sh        # interactive: asks local vs remote
```

| Mode | What it does | Command it prepares |
|---|---|---|
| **local** | Installs a full Frappe Health backend (ERPNext + Marley) in Docker alongside the HIMS | `docker compose --profile local up -d` |
| **remote** | Connects the BFF to an existing Frappe server you already run (LAN or cloud) | `docker compose up -d` |

You can switch modes at any time by re-running `setup.sh` — it only rewrites
env files, never touches data volumes.

The chain, in both modes:

```
browser → web (nginx, serves the React build, proxies /api → bff)
        → bff (Hono, thin REST adapter, port 3001 internal)
        → frappe (LOCAL: MariaDB + Frappe/ERPNext + Marley containers)
                   (REMOTE: your existing Frappe server over HTTP/S)
```

The browser never talks to Frappe directly and never holds Frappe
credentials. Frappe API keys live only in the BFF container.

## Mode A — local (install Frappe Health in Docker)

### A0. Prerequisites

- Docker Engine 24+ with the Compose plugin (`docker compose version`)
- ~6 GB free RAM for the Frappe stack (MariaDB + Redis + workers)
- Ports free: **3000** (web) and **8080** (Frappe desk, optional exposure)

### A1. Build the Frappe image

The stock `frappe/erpnext` image does not include Marley (Frappe Health,
app name `healthcare`). Build a custom image with both apps pinned:

```bash
cd deployment/frappe
./build-image.sh            # builds hims-frappe:v15 (version-15 branches)
```

`apps.json` pins ERPNext and Marley to their `version-15` branches. ERPNext
is a **mandatory** dependency of Marley — do not remove it.

> To try `version-16` instead: set `FRAPPE_BRANCH=version-16` and edit the
> branches in `apps.json`. Note that ABDM/ABHA support exists only on
> Marley's `abdm-milestone-1-wip` branch — the stable branches have **no
> ABHA fields** on Patient, so the ABHA flow in the UI is a placeholder
> until the ABDM adapter phase.

### A2. Configure secrets

```bash
cd deployment
./setup.sh local   # copies .env.example → .env and bff/.env.example → bff/.env
```

Set strong values for `DB_ROOT_PASSWORD` and `ADMIN_PASSWORD` in `.env`.
Generate a random `VITE_BFF_TOKEN` (e.g. `openssl rand -hex 32`) and put
the **same value** in `bff/.env` as `BFF_API_TOKEN`. The web build bakes
`VITE_BFF_TOKEN` in at image build time; the BFF requires it as a Bearer
token. Neither is a Frappe credential.

### A3. Start the stack

```bash
docker compose --profile local up -d
```

The `local` profile starts the Frappe infrastructure (db, redis, backend,
workers, scheduler, websocket, create-site) plus the always-on `bff` and
`web` services. First boot takes several minutes: `create-site` runs
`bench new-site … --install-app erpnext` and then installs `healthcare`.
Watch it with:

```bash
docker compose logs -f create-site
```

## Mode B — remote (connect to an existing Frappe server)

Use this when Frappe Health already runs somewhere — another host, a VM,
or a managed Frappe/ERPNext installation.

### B1. Point the BFF at your server

```bash
cd deployment
./setup.sh remote https://frappe.your-hospital.example
```

This writes `FRAPPE_BASE_URL` into both `.env` (compose override) and
`bff/.env`. Your server must have:

- the **`healthcare` app (Marley) installed on the site** — and ERPNext,
  which Marley requires (`bench --site <site> install-app healthcare`)
- network reachability from the machine running docker compose
- API keys: Desk → integration user → **API Access → Generate Keys**
  (a dedicated least-privilege user for production, Administrator is fine
  for a first test)

Put the keys in `deployment/bff/.env` (`FRAPPE_API_KEY` / `FRAPPE_API_SECRET`).

### B2. Start only the HIMS layer

```bash
docker compose up -d --build bff web
```

No Frappe containers are started in this mode (the `local` profile stays
off). The Settings page in the app shows **Mode: Remote · external Frappe
server** with your target host.

## Verify (both modes)

```bash
# BFF liveness through the web proxy (same origin the browser uses)
curl -s http://localhost:3000/api/v1/health
# → {"status":"ok","frappe":"up","target":"frontend:8080","mode":"local"}
#   (503 + "degraded" while keys are missing or Frappe is unreachable)

# Patient search (empty query = recent registrations)
curl -s -H "Authorization: Bearer $VITE_BFF_TOKEN" \
  "http://localhost:3000/api/v1/patients/search?q="

# Create a synthetic patient
curl -s -X POST -H "Authorization: Bearer $VITE_BFF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Test Patient","sex":"F","mobile":"9999900001","age":30}' \
  http://localhost:3000/api/v1/patients
```

Then open **http://localhost:3000** — the header badge should read
**Live** (green), and **Settings → Backend connection** shows the mode
and target. Registration writes real Patient documents; in local mode you
can see them in Desk under **Healthcare → Patient**.

> Use **synthetic patient data only** in this environment. Never load real
> patient records into a development deployment.

## Tear down / reset

```bash
docker compose down                    # stop, keep data volumes
docker compose --profile local down -v # stop and DELETE all data (fresh start)
```

## Architecture notes

- **Thin BFF** (`../bff`): validates input (zod), normalizes Frappe errors
  to `{ "error": { "code", "message" } }`, and is the only component
  holding Frappe credentials. The frontend talks REST to the BFF only.
- **Same-origin in production**: nginx proxies `/api/*` to `bff:3001`, so
  there is no CORS surface and no credential in the browser bundle beyond
  the shared app token.
- **No upstream forks**: we never modify Frappe/ERPNext/Marley source.
  Everything custom lives in this repo (frontend + BFF). When Marley's
  ABDM work lands in a stable branch, the ABHA adapter plugs into the BFF
  without touching the frontend contract.
- The BFF runs fine without Frappe (health reports `degraded`) so the
  frontend always renders — it falls back to demo data with a visible
  **Demo** badge. Hiding failures is worse than showing them.

## Troubleshooting

| Symptom | Check |
|---|---|
| Badge stuck on **Demo** | `docker compose logs bff`; usually missing/invalid `FRAPPE_API_KEY` |
| `health` returns 502 `FRAPPE_AUTH` | Keys revoked or integration user lacks permissions |
| Remote mode: `health` 503 `degraded` | `FRAPPE_BASE_URL` wrong or server unreachable from the container — `docker compose exec bff wget -qO- $FRAPPE_BASE_URL` |
| Remote mode: 404 on Patient endpoints | `healthcare` app not installed on the remote site |
| `create-site` fails (local) | Re-run `docker compose --profile local up create-site`; check DB container is healthy |
| Registration 409 `DUPLICATE` | A Patient with that mobile already exists — search first |
| Port already in use | Change the left-hand side of the `ports:` mapping in `compose.yml` |
