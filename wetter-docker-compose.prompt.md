---
description: "Generate a Docker Compose stack (Node.js + InfluxDB + Grafana) for the Wetterstation weather sensor project"
name: "Wetterstation Docker Compose"
argument-hint: "Optional: local IP of PC, preferred Node.js port (default 3000)"
agent: "agent"
tools: [create_file, read_file]
---

Generate a complete Docker Compose setup for the Wetterstation weather station project. The ESP8266 microcontroller ([Code-Wettermonster.ino](../../Documents/dev/wetterstation_team1/Code-Wettermonster.ino)) currently sends sensor data via HTTP GET to `upload.wettermonster.de`. The goal is to replace that external service with a self-hosted local stack.

## Architecture

```
ESP8266 (Wettermonster) ──HTTP GET──► Node.js API Server ──► InfluxDB ──► Grafana
```

The PC running Docker must be reachable from the ESP8266 on the same local network.

## Sensor data fields (from the existing sketch)

The ESP8266 sends an HTTP GET request with these query parameters:

| Parameter            | Unit   | Source sensor |
|----------------------|--------|---------------|
| `id`                 | string | device ID     |
| `schluessel`         | string | API key       |
| `temperatur`         | °C     | Si7021        |
| `luftfeuchtigkeit`   | %      | Si7021        |
| `luftdruck`          | hPa    | BMP280        |
| `niederschlag`       | mm/h   | Rain gauge    |
| `windgeschwindigkeit`| km/h   | Anemometer    |
| `windrichtung`       | string | Wind vane     |
| `helligkeit`         | lux    | TSL2591       |

## What to generate

### 1. `docker-compose.yml`

Three services:

**`influxdb`**
- Image: `influxdb:2` (latest v2)
- Ports: `8086:8086`
- Environment: pre-configure org, bucket (`wetterstation`), admin username/password and an initial admin token via `DOCKER_INFLUXDB_INIT_*` variables
- Named volume for data persistence: `influxdb-data`

**`grafana`**
- Image: `grafana/grafana:latest`
- Ports: `3001:3000` (avoid conflict with Node.js)
- Depends on `influxdb`
- Named volume for persistence: `grafana-data`
- Environment: set default admin credentials
- Provision an InfluxDB data source automatically via a mounted config file

**`wetterstation-api`** (Node.js)
- Build from `./api` (Dockerfile in that folder)
- Ports: `3000:3000`
- Depends on `influxdb`
- Environment: InfluxDB URL, token, org, bucket (matching influxdb service config)
- Restart policy: `unless-stopped`

### 2. `api/Dockerfile`

- Base image: `node:20-alpine`
- Copy `package.json`, run `npm ci --omit=dev`, copy source
- Expose port 3000
- Start with `node server.js`

### 3. `api/package.json`

Dependencies:
- `express` – HTTP server
- `@influxdata/influxdb-client` – official InfluxDB v2 client

### 4. `api/server.js`

Express server that:
- Listens on port 3000
- Accepts `GET /save`
- Reads all query parameters listed above
- Validates that numeric values are finite numbers; responds with `400` if invalid
- Writes a single InfluxDB point to the `wetterstation` measurement with:
  - Tags: `id`, `windrichtung`
  - Fields (float): `temperatur`, `luftfeuchtigkeit`, `luftdruck`, `niederschlag`, `windgeschwindigkeit`, `helligkeit`
- Responds with `200 OK` and a plain-text confirmation on success
- Responds with `500` and logs errors on InfluxDB write failure

### 5. `grafana/provisioning/datasources/influxdb.yml`

Auto-provision the InfluxDB v2 data source using the Flux query language, referencing the same token and bucket defined in the Compose file.

### 6. `README-docker.md`

Brief setup instructions:
1. Prerequisites (Docker Desktop)
2. How to start the stack (`docker compose up -d`)
3. How to update the `.ino`: change `upload.wettermonster.de` to the PC's local IP and port 3000, and update the path from `/speichern.php` to `/save`
4. Grafana URL and default credentials
5. How to import/create a dashboard for the `wetterstation` measurement

## Constraints

- All secrets (tokens, passwords) should use clearly labeled placeholder values (e.g. `CHANGE_ME_TOKEN`) with a comment to replace them
- No hard-coded IPs — use service names for inter-container communication
- All files must be created in the workspace folder `c:\Users\user\Documents\dev\wetterstation_team1\`
- Use `docker compose` v2 syntax (no `version:` key)
