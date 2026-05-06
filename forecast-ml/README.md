# forecast-ml — API Documentation

Python/Flask service that generates a 7-day weather forecast for **St. Johann in Tirol** using Open-Meteo NWP data corrected for local sensor bias, with Holt-Winters as a statistical fallback.

Base URL (Docker): `http://localhost:5001`

---

## Endpoint Overview

| Endpoint | Method | Caller | Description |
|---|---|---|---|
| `/health` | GET | any | Service and training status |
| `/train` | POST | scheduler / admin | Re-train the bias-correction model |
| `/forecast/generate` | POST | scheduler / admin | Fetch NWP, apply bias, write to InfluxDB |
| `/forecast` | GET | **mobile app / end user** | Read stored forecast — no computation |
| `/train-and-forecast` | GET | scheduler / admin | Train + generate in one call |

> **Design principle:** forecast *generation* (expensive) and forecast *reading* (cheap) are intentionally separated. Mobile clients should only ever call `GET /forecast`. Schedule `POST /forecast/generate` server-side (e.g. once per hour).

---

## Endpoints

### `GET /health`

Returns the current service state.

**Response `200 OK`**
```json
{
  "status": "ok",
  "trained": true,
  "open_meteo_rows": 17520,
  "forecast_source": "NWP+bias_correction (HW fallback)",
  "error": null
}
```

| Field | Type | Description |
|---|---|---|
| `status` | string | Always `"ok"` |
| `trained` | boolean | `true` once the model has been trained successfully |
| `open_meteo_rows` | integer | Number of historical rows fetched from Open-Meteo archive during training |
| `forecast_source` | string | Fixed description of the forecasting strategy in use |
| `error` | string \| null | Last training error message, or `null` |

---

### `POST /train`

Triggers a full (synchronous) model training run. Blocks until training is complete (~30–60 s).

Training steps:
1. Fetches ~730 days of hourly historical data from the Open-Meteo archive API (St. Johann in Tirol, lat 47.522, lon 12.422).
2. Fetches the last 365 days of local sensor readings from InfluxDB (`wetterstation` measurement).
3. Merges both sources — local data overwrites Open-Meteo at overlapping timestamps.
4. Downsamples to 3-hour resolution to reduce memory usage.
5. Fits a Holt-Winters ExponentialSmoothing model (damped additive trend, additive seasonal, period = 56 = 8 steps/day × 7 days) per field.
6. Computes a **local bias** per field = `mean(sensor − Open-Meteo)` over their overlapping period.

The service auto-trains on startup in a background thread. Call this endpoint manually only when you want to refresh the model with new sensor data.

**Request** — no body required.

**Response `200 OK`**
```json
{
  "status": "ok",
  "fields_trained": ["temperatur", "luftfeuchtigkeit", "luftdruck", "niederschlag", "windgeschwindigkeit", "helligkeit", "windrichtung"],
  "training_rows": 5842,
  "open_meteo_rows": 17520
}
```

| Field | Type | Description |
|---|---|---|
| `fields_trained` | string[] | Weather fields for which a model was successfully trained |
| `training_rows` | integer | Number of 3-hour rows used for fitting (after downsampling) |
| `open_meteo_rows` | integer | Number of hourly rows from the Open-Meteo archive |

**Response `500 Internal Server Error`**
```json
{ "error": "No fields could be trained – no InfluxDB data and Open-Meteo unreachable." }
```

---

### `POST /forecast/generate`

Fetches the live Open-Meteo NWP 7-day forecast, applies the per-field local bias correction learned during training, and writes 168 hourly data points to InfluxDB. **This is the only endpoint that writes to InfluxDB.**

Requires the model to be trained first. Returns `503` otherwise.

**Forecasting strategy (primary):** Fetches the live Open-Meteo 7-day NWP hourly forecast, then adds the per-field local bias learned during training to correct for sensor/location offsets.

**Fallback:** If the NWP API is unreachable, uses the Holt-Winters statistical models to predict 56 steps at 3-hour resolution and interpolates to hourly.

After computing the forecast the endpoint:
- Deletes all existing points in the `wetterstation_ml_forecast` measurement (tag `source=prophet`).
- Writes 168 new hourly `Point` records tagged `source=prophet`.

**Request** — no body required.

**Response `200 OK`**
```json
{
  "status": "ok",
  "forecast_source": "NWP+bias_correction",
  "measurement": "wetterstation_ml_forecast",
  "hourly_points": 168,
  "forecast": [
    {
      "date": "2026-05-06",
      "temperatur": 21.08,
      "luftfeuchtigkeit": 52.12,
      "luftdruck": 996.01,
      "niederschlag": 0.12,
      "windgeschwindigkeit": 8.4,
      "helligkeit": 31200.5,
      "windrichtung": "WNW"
    },
    { "date": "2026-05-07", "...": "..." }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `forecast_source` | string | `"NWP+bias_correction"` or `"HW_statistical_fallback"` |
| `measurement` | string | InfluxDB measurement the data was written to |
| `hourly_points` | integer | Total number of hourly points written (always 168 = 7 × 24) |
| `forecast` | object[] | Daily summary — one entry per calendar day (see table below) |

**Response `503 Service Unavailable`** — model not yet trained:
```json
{ "error": "Models not trained yet. POST /train first." }
```

---

### `GET /forecast`

Returns the currently stored 7-day forecast directly from InfluxDB. **No computation is performed.** Safe to call from mobile clients at any frequency.

**Response `200 OK`**
```json
{
  "status": "ok",
  "hourly_points": 168,
  "hourly": [
    {
      "timestamp": "2026-05-06T07:00:00+00:00",
      "temperatur": 20.14,
      "luftfeuchtigkeit": 54.3,
      "luftdruck": 995.87,
      "niederschlag": 0.0,
      "windgeschwindigkeit": 7.2,
      "helligkeit": 12400.0,
      "windrichtung": "W"
    },
    { "...": "..." }
  ],
  "forecast": [
    {
      "date": "2026-05-06",
      "temperatur": 21.08,
      "luftfeuchtigkeit": 52.12,
      "luftdruck": 996.01,
      "niederschlag": 0.12,
      "windgeschwindigkeit": 8.4,
      "helligkeit": 31200.5,
      "windrichtung": "WNW"
    },
    { "...": "..." }
  ]
}
```

| Field | Type | Description |
|---|---|---|
| `hourly_points` | integer | Number of hourly records returned |
| `hourly` | object[] | All 168 individual hourly data points with ISO 8601 timestamps |
| `forecast` | object[] | Daily summary — one entry per calendar day (see table below) |

**Daily summary / hourly point fields:**

| Field | Unit | Description |
|---|---|---|
| `date` / `timestamp` | `YYYY-MM-DD` / ISO 8601 | Calendar date (summary) or exact UTC timestamp (hourly) |
| `temperatur` | °C | Temperature (daily: mean) |
| `luftfeuchtigkeit` | % | Relative humidity (daily: mean) |
| `luftdruck` | hPa | Atmospheric pressure, sensor-corrected (daily: mean) |
| `niederschlag` | mm | Precipitation (daily: mean per hour) |
| `windgeschwindigkeit` | km/h | Wind speed (daily: mean) |
| `helligkeit` | lux | Brightness / shortwave radiation × 120 (daily: mean) |
| `windrichtung` | compass | Wind direction string, e.g. `"WNW"` (daily: most frequent) |

**Response `404 Not Found`** — no forecast has been generated yet:
```json
{ "error": "No forecast data available. Call POST /forecast/generate first." }
```

---

### `GET /train-and-forecast`

Convenience endpoint that runs training and forecast generation in sequence (synchronous). Equivalent to `POST /train` followed by `POST /forecast/generate`.

Useful for a single scheduled call that refreshes both model and forecast data.

**Response** — identical to `POST /forecast/generate`.

**Response `500 Internal Server Error`** — if training fails:
```json
{ "error": "..." }
```

---

## Weather Fields

| Field name | Open-Meteo variable | Unit |
|---|---|---|
| `temperatur` | `temperature_2m` | °C |
| `luftfeuchtigkeit` | `relative_humidity_2m` | % |
| `luftdruck` | `surface_pressure` | hPa |
| `niederschlag` | `precipitation` | mm |
| `windgeschwindigkeit` | `wind_speed_10m` | km/h |
| `windrichtung` | `wind_direction_10m` | compass string |
| `helligkeit` | `shortwave_radiation` × 120 | lux |

---

## Configuration

All values are set via environment variables (see `docker-compose.yml`):

| Variable | Default | Description |
|---|---|---|
| `INFLUX_URL` | `http://influxdb:8086` | InfluxDB 2 URL |
| `INFLUX_TOKEN` | `Egger#123` | InfluxDB authentication token |
| `INFLUX_ORG` | `wetterstation` | InfluxDB organisation |
| `INFLUX_BUCKET` | `wetterstation` | InfluxDB bucket for sensor data |

---

## Data Flow

```
Open-Meteo Archive (730 days)  ─┐
                                 ├─► merge & downsample (3h) ─► Holt-Winters fit ─► bias
Local InfluxDB sensor data ──────┘                                                      │
                                                          POST /train                   │
                                                                                        ▼
Open-Meteo NWP forecast (7 days, hourly) ──────────────────────────► + bias ─► InfluxDB wetterstation_ml_forecast
                                                    POST /forecast/generate             │
                                                                                        │
                                              ┌─────────────────────────────────────────┘
                                              ▼
                                      GET /forecast  ──►  Mobile App / Grafana dashboard
```

The service auto-trains on startup in a background thread. Call `GET /forecast` only after `/health` returns `"trained": true` **and** at least one `POST /forecast/generate` has been made.
