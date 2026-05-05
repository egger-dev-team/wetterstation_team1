# Wetterstation – Local Docker Stack

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running

## Setup

### 1. Replace placeholder secrets

Edit `docker-compose.yml` and `grafana/provisioning/datasources/influxdb.yml` and replace all `CHANGE_ME_*` values:

| Placeholder | Description |
|---|---|
| `CHANGE_ME_TOKEN` | InfluxDB API token (use the same value in both files) |
| `CHANGE_ME_PASSWORD` | InfluxDB admin password |
| `CHANGE_ME_GRAFANA_PW` | Grafana admin password |

### 2. Start the stack

```bash
docker compose up -d
```

### 3. Update the Arduino sketch

In `Code-Wettermonster.ino`, change the following two lines:

```cpp
// Before:
const char* id = "1356598";
const char* key = "15244122";
// ...
if (WiFi.status() == WL_CONNECTED && client.connect("upload.wettermonster.de", 80))
// ...
client.print("GET /speichern.php");

// After:
if (WiFi.status() == WL_CONNECTED && client.connect("192.168.x.x", 3000))  // ← your PC's local IP
// ...
client.print("GET /save");
```

Find your PC's local IP with `ipconfig` (Windows) and look for the IPv4 address on your LAN adapter.

## Access

| Service  | URL | Credentials |
|---|---|---|
| **Grafana** | http://localhost:3001 | admin / CHANGE_ME_GRAFANA_PW |
| **InfluxDB** | http://localhost:8086 | admin / CHANGE_ME_PASSWORD |
| **API** | http://localhost:3000/save | — |

## Creating a Grafana Dashboard

1. Open Grafana → **Dashboards → New Dashboard → Add visualization**
2. Select the **InfluxDB** data source
3. Use Flux to query your data, for example:

```flux
from(bucket: "wetterstation")
  |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "wetterstation")
  |> filter(fn: (r) => r._field == "temperatur")
```

4. Repeat for each field (`luftfeuchtigkeit`, `luftdruck`, `niederschlag`, `windgeschwindigkeit`, `helligkeit`)
5. Choose **Time series** as the panel type

## Stopping the stack

```bash
docker compose down
```

Data is persisted in named Docker volumes (`influxdb-data`, `grafana-data`) and survives restarts.
