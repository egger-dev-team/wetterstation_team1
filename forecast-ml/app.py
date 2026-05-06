import os
import math
import threading
import logging
import warnings
from datetime import datetime, timezone, timedelta

import requests
import numpy as np
import pandas as pd
from flask import Flask, jsonify
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from statsmodels.tsa.holtwinters import ExponentialSmoothing

warnings.filterwarnings("ignore")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "Egger#123")
INFLUX_ORG = os.environ.get("INFLUX_ORG", "wetterstation")
INFLUX_BUCKET = os.environ.get("INFLUX_BUCKET", "wetterstation")

FORECAST_MEASUREMENT = "wetterstation_ml_forecast"
HORIZON_DAYS = 7
LOOKBACK_DAYS = 365
RESAMPLE_HOURS = 3          # downsample to 3h before fitting to save memory
SEASON_PERIOD = 56          # 8 steps/day × 7 days = weekly seasonality at 3h resolution

# Open-Meteo public archive — St. Johann in Tirol (~47.52°N, 12.42°E)
ST_JOHANN_LAT = 47.522
ST_JOHANN_LON = 12.422
RADIATION_TO_LUX = 120.0  # 1 W/m² ≈ 120 lux for daylight
OPEN_METEO_ARCHIVE_URL  = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Maps our field names to Open-Meteo variable names
OM_VARIABLE_MAP = {
    "temperatur":          "temperature_2m",
    "luftfeuchtigkeit":    "relative_humidity_2m",
    "luftdruck":           "surface_pressure",
    "niederschlag":        "precipitation",
    "windgeschwindigkeit": "wind_speed_10m",
    "windrichtung":        "wind_direction_10m",
    "helligkeit":          "shortwave_radiation",
}

NUMERIC_FIELDS = [
    "temperatur", "luftfeuchtigkeit", "luftdruck",
    "niederschlag", "windgeschwindigkeit", "helligkeit",
]

COMPASS_DIRS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
COMPASS_DEGS = [0.0, 22.5, 45.0, 67.5, 90.0, 112.5, 135.0, 157.5,
                180.0, 202.5, 225.0, 247.5, 270.0, 292.5, 315.0, 337.5]

_models: dict = {}
_lock = threading.Lock()
_status: dict = {"trained": False, "rows": 0, "error": None}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def direction_to_degrees(d) -> float | None:
    key = str(d).strip().upper()
    if key in COMPASS_DIRS:
        return COMPASS_DEGS[COMPASS_DIRS.index(key)]
    return None


def degrees_to_direction(deg: float) -> str:
    deg = float(deg) % 360.0
    dists = [min(abs(deg - r), 360.0 - abs(deg - r)) for r in COMPASS_DEGS]
    return COMPASS_DIRS[int(np.argmin(dists))]


def build_client() -> InfluxDBClient:
    return InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)


def _query_field(query_api, field: str, agg_fn: str = "mean") -> pd.DataFrame:
    flux = (
        f'from(bucket: "{INFLUX_BUCKET}")\n'
        f'  |> range(start: -{LOOKBACK_DAYS}d)\n'
        f'  |> filter(fn: (r) => r._measurement == "wetterstation" and r._field == "{field}")\n'
        f'  |> aggregateWindow(every: 1h, fn: {agg_fn}, createEmpty: false)\n'
        f'  |> sort(columns: ["_time"])'
    )
    tables = query_api.query(flux)
    rows = [
        {"ds": rec.get_time(), "y": rec.get_value()}
        for tbl in tables
        for rec in tbl.records
    ]
    if not rows:
        return pd.DataFrame(columns=["ds", "y"])
    df = pd.DataFrame(rows)
    df["ds"] = pd.to_datetime(df["ds"], utc=True).dt.tz_localize(None)
    df = df.sort_values("ds").drop_duplicates("ds").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Open-Meteo historical data
# ---------------------------------------------------------------------------

def _fetch_open_meteo(lookback_days: int = 730) -> dict:
    """
    Fetch hourly historical weather for St. Johann in Tirol from the Open-Meteo
    archive API (free, no key required). Returns {field: DataFrame(ds, y)}
    with the same format as _query_field().
    windrichtung is returned as compass strings; helligkeit as approximate lux.
    """
    end_date   = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    start_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    params = {
        "latitude":        ST_JOHANN_LAT,
        "longitude":       ST_JOHANN_LON,
        "hourly":          ",".join(OM_VARIABLE_MAP.values()),
        "start_date":      start_date,
        "end_date":        end_date,
        "wind_speed_unit": "kmh",
        "timezone":        "UTC",
    }
    log.info("Fetching Open-Meteo archive %s → %s (St. Johann in Tirol)", start_date, end_date)
    resp = requests.get(OPEN_METEO_ARCHIVE_URL, params=params, timeout=30)
    resp.raise_for_status()
    hourly = resp.json().get("hourly", {})
    times  = pd.to_datetime(hourly["time"])  # UTC-naive (timezone=UTC)

    result: dict = {}
    for field, om_var in OM_VARIABLE_MAP.items():
        df = pd.DataFrame({"ds": times, "y": hourly.get(om_var, [])})
        df = df.dropna(subset=["y"]).reset_index(drop=True)
        if field == "windrichtung":
            df["y"] = df["y"].apply(lambda d: degrees_to_direction(float(d)))
        elif field == "helligkeit":
            df["y"] = (df["y"] * RADIATION_TO_LUX).clip(lower=0.0)
        result[field] = df

    log.info("Open-Meteo: %d hourly records loaded", len(times))
    return result


def _fetch_open_meteo_forecast() -> dict:
    """
    Fetch the free 7-day NWP hourly forecast from Open-Meteo for St. Johann.
    Returns {field: DataFrame(ds, y)} – same format as _query_field().
    """
    params = {
        "latitude":        ST_JOHANN_LAT,
        "longitude":       ST_JOHANN_LON,
        "hourly":          ",".join(OM_VARIABLE_MAP.values()),
        "forecast_days":   HORIZON_DAYS,
        "wind_speed_unit": "kmh",
        "timezone":        "UTC",
    }
    resp = requests.get(OPEN_METEO_FORECAST_URL, params=params, timeout=20)
    resp.raise_for_status()
    hourly = resp.json().get("hourly", {})
    times  = pd.to_datetime(hourly["time"])  # UTC-naive

    result: dict = {}
    for field, om_var in OM_VARIABLE_MAP.items():
        df = pd.DataFrame({"ds": times, "y": hourly.get(om_var, [])})
        df = df.dropna(subset=["y"]).reset_index(drop=True)
        if field == "windrichtung":
            df["y"] = df["y"].apply(lambda d: degrees_to_direction(float(d)))
        elif field == "helligkeit":
            df["y"] = (df["y"] * RADIATION_TO_LUX).clip(lower=0.0)
        result[field] = df

    log.info("Open-Meteo NWP forecast: %d hourly records fetched", len(times))
    return result


# Physical bounds for clamping forecast output
FIELD_BOUNDS: dict = {
    "luftfeuchtigkeit":  (0.0,  100.0),
    "niederschlag":      (0.0,  None),
    "windgeschwindigkeit": (0.0, None),
    "helligkeit":        (0.0,  None),
    "luftdruck":         (800.0, 1100.0),
}


def _fit_hw(values: np.ndarray):
    """
    Fit Holt-Winters with damped additive trend to prevent divergence on short series.
    Falls back to trend-only, then returns None for pattern-mean fallback.
    """
    n = len(values)
    if n < SEASON_PERIOD * 2:
        return None
    try:
        model = ExponentialSmoothing(
            values,
            trend="add",
            damped_trend=True,
            seasonal="add",
            seasonal_periods=SEASON_PERIOD,
            initialization_method="estimated",
        ).fit(optimized=True, remove_bias=True)
        return model
    except Exception:
        pass
    try:
        model = ExponentialSmoothing(
            values,
            trend="add",
            damped_trend=True,
            initialization_method="estimated",
        ).fit(optimized=True)
        return model
    except Exception:
        return None


def _predict(entry: dict, steps: int) -> np.ndarray:
    """Return array of `steps` predicted values from a stored model entry."""
    fitted = entry.get("model")
    if fitted is not None:
        try:
            return np.asarray(fitted.forecast(steps), dtype=float)
        except Exception:
            pass
    # Fallback: tile the last daily pattern
    last = entry["last_values"]
    pattern = last[-SEASON_PERIOD:] if len(last) >= SEASON_PERIOD else last
    reps = math.ceil(steps / len(pattern))
    return np.tile(pattern, reps)[:steps].astype(float)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def _do_train() -> None:
    log.info("Training started …")
    try:
        # 1. Fetch public historical data (~2 years) from Open-Meteo
        om_data: dict = {}
        try:
            om_data = _fetch_open_meteo(lookback_days=730)
        except Exception as exc:
            log.warning("Open-Meteo fetch failed (%s) — falling back to InfluxDB only", exc)

        # 2. Fetch recent local sensor data from InfluxDB
        client = build_client()
        qapi = client.query_api()
        new_models: dict = {}
        reference_len = 0

        for field in NUMERIC_FIELDS:
            df_local = _query_field(qapi, field)
            df_om    = om_data.get(field, pd.DataFrame(columns=["ds", "y"]))
            # Merge: Open-Meteo provides historical base; local sensor data
            # overwrites any overlapping hourly timestamps (keep last = local).
            df = pd.concat([df_om, df_local], ignore_index=True)
            df = df.sort_values("ds").drop_duplicates("ds", keep="last").reset_index(drop=True)
            if df.empty or len(df) < 2:
                log.warning("Skipping %s: only %d rows", field, len(df))
                continue
            # Downsample to RESAMPLE_HOURS to reduce HW fitting memory
            df = (
                df.set_index("ds")["y"]
                .resample(f"{RESAMPLE_HOURS}h")
                .mean()
                .dropna()
                .reset_index()
                .rename(columns={"ds": "ds", "y": "y"})
            )
            reference_len = max(reference_len, len(df))
            values = df["y"].values.astype(float)
            fitted = _fit_hw(values)
            # Compute local bias = mean(local_sensor – open_meteo) over overlap
            bias = 0.0
            if not df_local.empty and not df_om.empty:
                merged = pd.merge(
                    df_local.rename(columns={"y": "local"}),
                    df_om.rename(columns={"y": "om"}),
                    on="ds", how="inner",
                )
                if not merged.empty:
                    bias = float((merged["local"] - merged["om"]).mean())
            new_models[field] = {"model": fitted, "last_values": values, "bias": bias}
            log.info(
                "Trained %s: %d total rows (InfluxDB: %d, Open-Meteo: %d), model=%s, bias=%.3f",
                field, len(df), len(df_local), len(df_om), "HW" if fitted else "pattern-mean", bias,
            )

        # windrichtung: string → degrees → sin/cos pair
        df_wd_local = _query_field(qapi, "windrichtung", agg_fn="last")
        client.close()

        df_wd_om = om_data.get("windrichtung", pd.DataFrame(columns=["ds", "y"]))
        df_wd = pd.concat([df_wd_om, df_wd_local], ignore_index=True)
        df_wd = df_wd.sort_values("ds").drop_duplicates("ds", keep="last").reset_index(drop=True)

        if not df_wd.empty and len(df_wd) >= 2:
            df_wd["deg"] = df_wd["y"].apply(direction_to_degrees)
            df_wd = df_wd.dropna(subset=["deg"]).reset_index(drop=True)
            if len(df_wd) >= 2:
                # Resample wind direction via circular mean (sin/cos then resample)
                df_wd_idx = df_wd.set_index("ds")
                sin_rs = np.sin(np.radians(df_wd_idx["deg"])).resample(f"{RESAMPLE_HOURS}h").mean().dropna()
                cos_rs = np.cos(np.radians(df_wd_idx["deg"])).resample(f"{RESAMPLE_HOURS}h").mean().dropna()
                common = sin_rs.index.intersection(cos_rs.index)
                sin_vals = sin_rs.loc[common].values
                cos_vals = cos_rs.loc[common].values
                new_models["windrichtung"] = {
                    "sin": {"model": _fit_hw(sin_vals), "last_values": sin_vals},
                    "cos": {"model": _fit_hw(cos_vals), "last_values": cos_vals},
                }
                log.info(
                    "Trained windrichtung: %d total rows (InfluxDB: %d, Open-Meteo: %d)",
                    len(df_wd), len(df_wd_local), len(df_wd_om),
                )

        if not new_models:
            raise RuntimeError("No fields could be trained – no InfluxDB data and Open-Meteo unreachable.")

        with _lock:
            _models.clear()
            _models.update(new_models)
            _status["trained"] = True
            _status["rows"] = reference_len
            _status["om_rows"] = len(om_data.get("temperatur", pd.DataFrame()))
            _status["error"] = None

        log.info("Training complete: %s", list(new_models.keys()))

    except Exception as exc:
        log.exception("Training failed")
        with _lock:
            _status["error"] = str(exc)
            _status["trained"] = False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "trained": _status["trained"],
        "open_meteo_rows": _status.get("om_rows", 0),
        "forecast_source": "NWP+bias_correction (HW fallback)",
        "error": _status.get("error"),
    })


@app.route("/train", methods=["POST"])
def train():
    _do_train()
    if _status["error"]:
        return jsonify({"error": _status["error"]}), 500
    return jsonify({
        "status": "ok",
        "fields_trained": list(_models.keys()),
        "training_rows": _status["rows"],
        "open_meteo_rows": _status.get("om_rows", 0),
    })


@app.route("/forecast")
def forecast():
    with _lock:
        if not _status["trained"]:
            return jsonify({"error": "Models not trained yet. POST /train first."}), 503
        snap = dict(_models)

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    now_naive = now.replace(tzinfo=None)   # for comparison with tz-naive NWP timestamps
    n_hours = HORIZON_DAYS * 24
    # 1h output timestamps (hour 1 … hour 168)
    future_1h = pd.date_range(start=now + timedelta(hours=1), periods=n_hours, freq="h")

    # -----------------------------------------------------------------------
    # PRIMARY PATH: NWP forecast from Open-Meteo + local bias correction
    # -----------------------------------------------------------------------
    nwp_source = False
    try:
        nwp = _fetch_open_meteo_forecast()
        hourly: dict = {}

        for field in NUMERIC_FIELDS:
            if field not in nwp:
                continue
            df_nwp = nwp[field].copy()
            df_nwp["ds"] = pd.to_datetime(df_nwp["ds"])
            df_nwp = df_nwp[df_nwp["ds"] > now_naive].reset_index(drop=True)
            # Reindex to exactly future_1h (NWP already is hourly)
            s = df_nwp.set_index("ds")["y"]
            s.index = s.index.tz_localize(None) if s.index.tz is None else s.index.tz_convert(None)
            future_1h_naive = future_1h.tz_localize(None)
            s = s.reindex(s.index.union(future_1h_naive)).interpolate(method="time").reindex(future_1h_naive)
            vals = s.values.astype(float)
            # Apply local bias correction from training
            bias = snap.get(field, {}).get("bias", 0.0)
            vals = vals + bias
            # Clamp
            lo, hi = FIELD_BOUNDS.get(field, (None, None))
            if lo is not None:
                vals = np.maximum(vals, lo)
            if hi is not None:
                vals = np.minimum(vals, hi)
            hourly[field] = vals

        # windrichtung: use NWP direction directly (bias doesn't apply to compass)
        if "windrichtung" in nwp:
            df_wd = nwp["windrichtung"].copy()
            df_wd["ds"] = pd.to_datetime(df_wd["ds"])
            df_wd = df_wd[df_wd["ds"] > now_naive].reset_index(drop=True)
            # Align to future_1h via forward-fill on a 1h reindex
            s_wd = df_wd.set_index("ds")["y"]
            s_wd.index = s_wd.index.tz_localize(None) if s_wd.index.tz is None else s_wd.index.tz_convert(None)
            s_wd = s_wd.reindex(future_1h_naive, method="nearest")
            hourly["windrichtung"] = list(s_wd.values)

        nwp_source = True
        log.info("Forecast: using Open-Meteo NWP + local bias correction")

    except Exception as exc:
        log.warning("NWP fetch failed (%s) — falling back to Holt-Winters", exc)

    # -----------------------------------------------------------------------
    # FALLBACK PATH: Holt-Winters statistical model
    # -----------------------------------------------------------------------
    if not nwp_source:
        steps_3h = HORIZON_DAYS * (24 // RESAMPLE_HOURS)
        future_3h = pd.date_range(start=now + timedelta(hours=RESAMPLE_HOURS),
                                   periods=steps_3h, freq=f"{RESAMPLE_HOURS}h")

        def _interp_to_hourly(values_3h: np.ndarray) -> np.ndarray:
            s = pd.Series(values_3h, index=future_3h)
            return s.reindex(s.index.union(future_1h)).interpolate(method="time").reindex(future_1h).values

        hourly = {}
        for field in NUMERIC_FIELDS:
            if field in snap:
                raw_3h = _predict(snap[field], steps_3h)
                raw_1h = _interp_to_hourly(raw_3h)
                lo, hi = FIELD_BOUNDS.get(field, (None, None))
                if lo is not None:
                    raw_1h = np.maximum(raw_1h, lo)
                if hi is not None:
                    raw_1h = np.minimum(raw_1h, hi)
                hourly[field] = raw_1h

        if "windrichtung" in snap:
            wd = snap["windrichtung"]
            sin_3h = _interp_to_hourly(_predict(wd["sin"], steps_3h))
            cos_3h = _interp_to_hourly(_predict(wd["cos"], steps_3h))
            hourly["windrichtung"] = [
                degrees_to_direction(math.degrees(math.atan2(float(s), float(c))))
                for s, c in zip(sin_3h, cos_3h)
            ]

    # --- delete old forecast points before writing new ones ---
    client = build_client()
    try:
        delete_api = client.delete_api()
        delete_api.delete(
            start="1970-01-01T00:00:00Z",
            stop="2100-01-01T00:00:00Z",
            predicate=f'_measurement="{FORECAST_MEASUREMENT}" AND source="prophet"',
            bucket=INFLUX_BUCKET,
            org=INFLUX_ORG,
        )
    except Exception as exc:
        log.warning("Could not delete old forecast points: %s", exc)

    # --- write hourly points to InfluxDB ---
    write_api = client.write_api(write_options=SYNCHRONOUS)
    result_rows = []

    for i, ts in enumerate(future_1h):
        point = (
            Point(FORECAST_MEASUREMENT)
            .tag("source", "prophet")
            .time(ts, "s")
        )
        rec = {"timestamp": ts.isoformat()}
        for field in NUMERIC_FIELDS:
            if field in hourly:
                val = round(float(hourly[field][i]), 2)
                point = point.field(field, val)
                rec[field] = val
        if "windrichtung" in hourly:
            wd_val = hourly["windrichtung"][i]
            point = point.field("windrichtung", str(wd_val))
            rec["windrichtung"] = str(wd_val)

        write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
        result_rows.append(rec)

    client.close()
    log.info("Forecast written: %d hourly points → %s (source=%s)",
             len(result_rows), FORECAST_MEASUREMENT, "NWP+bias" if nwp_source else "HW-fallback")
    # Return a daily summary for the JSON response
    df_summary = pd.DataFrame(result_rows)
    df_summary["date"] = pd.to_datetime(df_summary["timestamp"]).dt.date
    summary = []
    for date, grp in df_summary.groupby("date"):
        row = {"date": str(date)}
        for field in NUMERIC_FIELDS:
            if field in grp.columns:
                row[field] = round(grp[field].mean(), 2)
        if "windrichtung" in grp.columns:
            row["windrichtung"] = grp["windrichtung"].value_counts().index[0]
        summary.append(row)
    return jsonify({
        "status": "ok",
        "forecast_source": "NWP+bias_correction" if nwp_source else "HW_statistical_fallback",
        "measurement": FORECAST_MEASUREMENT,
        "hourly_points": len(result_rows),
        "forecast": summary,
    })


@app.route("/train-and-forecast")
def train_and_forecast():
    _do_train()
    if _status["error"]:
        return jsonify({"error": _status["error"]}), 500
    return forecast()


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Auto-training on startup (background thread) …")
    threading.Thread(target=_do_train, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
