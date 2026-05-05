'use strict';

const express = require('express');
const { InfluxDB, Point } = require('@influxdata/influxdb-client');

const app = express();
const PORT = 3000;
const FORECAST_HORIZON_DAYS = 7;
const FORECAST_LOOKBACK_DAYS = 35;
const FORECAST_MEASUREMENT = 'wetterstation_forecast';
const FORECAST_TAG = 'v1';

const influxClient = new InfluxDB({
  url: process.env.INFLUX_URL,
  token: process.env.INFLUX_TOKEN,
});

const writeApi = influxClient.getWriteApi(
  process.env.INFLUX_ORG,
  process.env.INFLUX_BUCKET,
  'ms'
);
const queryApi = influxClient.getQueryApi(process.env.INFLUX_ORG);

const NUMERIC_PARAMS = [
  'temperatur',
  'luftfeuchtigkeit',
  'luftdruck',
  'niederschlag',
  'windgeschwindigkeit',
  'helligkeit',
];

function toFiniteNumber(val, fallback = 0) {
  const parsed = Number(val);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function round(value, decimals = 2) {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

function average(values) {
  if (!values.length) {
    return 0;
  }
  return values.reduce((sum, v) => sum + v, 0) / values.length;
}

function recommendationFromForecast(dayForecast) {
  const { temperatur, luftfeuchtigkeit, niederschlag, windgeschwindigkeit } =
    dayForecast;

  function weatherBasedDrink() {
    if (temperatur >= 30) {
      return 'Stark isotonisches Getraenk (600 ml)';
    }
    if (temperatur >= 24 && luftfeuchtigkeit >= 75) {
      return 'Elektrolyt-Drink (500 ml)';
    }
    if (niederschlag >= 2 || windgeschwindigkeit >= 40) {
      return 'Warmer Ingwer-Zitronen-Tee (350 ml)';
    }
    if (temperatur <= 8) {
      return 'Kraeutertee warm (300 ml)';
    }
    if (niederschlag > 0.5) {
      return 'Wasser mit Mineralien (400 ml)';
    }
    return 'Wasser mit Zitrone (400 ml)';
  }

  const drink = weatherBasedDrink();

  if (niederschlag > 1.5 || windgeschwindigkeit > 35) {
    return {
      workout: 'Indoor Kraftzirkel (30 min)',
      drink,
      rationale: 'Regen oder starker Wind: indoor und stabil trainieren.',
    };
  }

  if (temperatur >= 26 && luftfeuchtigkeit < 70 && niederschlag < 0.5) {
    return {
      workout: 'Outdoor HIIT (20 min)',
      drink,
      rationale: 'Warm und trocken: intensive Outdoor-Einheit passt gut.',
    };
  }

  if (temperatur >= 15 && temperatur <= 25 && niederschlag < 1 && windgeschwindigkeit < 25) {
    return {
      workout: 'Lockerer Lauf (35 min)',
      drink,
      rationale: 'Milde Bedingungen: Ausdauertraining im Freien sinnvoll.',
    };
  }

  if (temperatur < 10) {
    return {
      workout: 'Mobility + Core (25 min, indoor)',
      drink,
      rationale: 'Kuehle Temperatur: gelenkschonend und warm bleiben.',
    };
  }

  return {
    workout: 'Yoga / Stretch (30 min)',
    drink,
    rationale: 'Standardplan fuer variable Bedingungen.',
  };
}

function weekdayAverage(rows, field, targetWeekday) {
  const values = rows
    .filter((row) => new Date(row._time).getUTCDay() === targetWeekday)
    .map((row) => toFiniteNumber(row._value, NaN))
    .filter((v) => Number.isFinite(v));
  return average(values);
}

async function queryDailySeries(field, aggregateFn) {
  const fluxQuery = `
from(bucket: "${process.env.INFLUX_BUCKET}")
  |> range(start: -${FORECAST_LOOKBACK_DAYS}d)
  |> filter(fn: (r) => r._measurement == "wetterstation")
  |> filter(fn: (r) => r._field == "${field}")
  |> aggregateWindow(every: 1d, fn: ${aggregateFn}, createEmpty: false)
  |> keep(columns: ["_time", "_value", "_field"])
`;

  return queryApi.collectRows(fluxQuery);
}

async function buildWeeklyForecast() {
  const [temperaturRows, luftfeuchtigkeitRows, luftdruckRows, niederschlagRows, windRows, helligkeitRows] =
    await Promise.all([
      queryDailySeries('temperatur', 'mean'),
      queryDailySeries('luftfeuchtigkeit', 'mean'),
      queryDailySeries('luftdruck', 'mean'),
      queryDailySeries('niederschlag', 'sum'),
      queryDailySeries('windgeschwindigkeit', 'mean'),
      queryDailySeries('helligkeit', 'mean'),
    ]);

  const allSeries = [
    temperaturRows,
    luftfeuchtigkeitRows,
    luftdruckRows,
    niederschlagRows,
    windRows,
    helligkeitRows,
  ];

  if (allSeries.some((series) => !series.length)) {
    throw new Error(
      `Insufficient data for forecast. Ensure at least one day of data for all fields within last ${FORECAST_LOOKBACK_DAYS} days.`
    );
  }

  const forecast = [];
  const now = new Date();

  for (let offset = 1; offset <= FORECAST_HORIZON_DAYS; offset += 1) {
    const day = new Date(now);
    day.setUTCHours(0, 0, 0, 0);
    day.setUTCDate(day.getUTCDate() + offset);

    const weekday = day.getUTCDay();
    const dayForecast = {
      dateIso: day.toISOString(),
      temperatur: round(weekdayAverage(temperaturRows, 'temperatur', weekday)),
      luftfeuchtigkeit: round(
        weekdayAverage(luftfeuchtigkeitRows, 'luftfeuchtigkeit', weekday)
      ),
      luftdruck: round(weekdayAverage(luftdruckRows, 'luftdruck', weekday)),
      niederschlag: round(weekdayAverage(niederschlagRows, 'niederschlag', weekday)),
      windgeschwindigkeit: round(
        weekdayAverage(windRows, 'windgeschwindigkeit', weekday)
      ),
      helligkeit: round(weekdayAverage(helligkeitRows, 'helligkeit', weekday)),
    };

    const recommendation = recommendationFromForecast(dayForecast);

    forecast.push({
      ...dayForecast,
      ...recommendation,
    });
  }

  return forecast;
}

async function writeWeeklyForecastToInflux(forecastRows) {
  for (const row of forecastRows) {
    const point = new Point(FORECAST_MEASUREMENT)
      .tag('source', FORECAST_TAG)
      .tag('date', row.dateIso.slice(0, 10))
      .floatField('temperatur', row.temperatur)
      .floatField('luftfeuchtigkeit', row.luftfeuchtigkeit)
      .floatField('luftdruck', row.luftdruck)
      .floatField('niederschlag', row.niederschlag)
      .floatField('windgeschwindigkeit', row.windgeschwindigkeit)
      .floatField('helligkeit', row.helligkeit)
      .stringField('workout', row.workout)
      .stringField('drink', row.drink)
      .stringField('rationale', row.rationale)
      .timestamp(new Date(row.dateIso));

    writeApi.writePoint(point);
  }

  await writeApi.flush();
}

async function generateAndPersistWeeklyForecast() {
  const forecast = await buildWeeklyForecast();
  await writeWeeklyForecastToInflux(forecast);
  return forecast;
}

app.get('/save', async (req, res) => {
  const q = req.query;

  // Validate all required numeric fields
  for (const field of NUMERIC_PARAMS) {
    const val = parseFloat(q[field]);
    if (!isFinite(val)) {
      return res
        .status(400)
        .send(`Invalid or missing value for field: ${field}`);
    }
  }

  try {
    const point = new Point('wetterstation')
      .tag('id', q.id || 'unknown')
      .tag('windrichtung', q.windrichtung || '')
      .floatField('temperatur', parseFloat(q.temperatur))
      .floatField('luftfeuchtigkeit', parseFloat(q.luftfeuchtigkeit))
      .floatField('luftdruck', parseFloat(q.luftdruck))
      .floatField('niederschlag', parseFloat(q.niederschlag))
      .floatField('windgeschwindigkeit', parseFloat(q.windgeschwindigkeit))
      .floatField('helligkeit', parseFloat(q.helligkeit));

    writeApi.writePoint(point);
    await writeApi.flush();

    console.log(`Data saved: ${JSON.stringify(q)}`);
    res.status(200).send('OK');
  } catch (err) {
    console.error('InfluxDB write error:', err);
    res.status(500).send('Error writing to database');
  }
});

app.get('/forecast/weekly', async (_req, res) => {
  try {
    const forecast = await generateAndPersistWeeklyForecast();
    res.status(200).json({
      generatedAt: new Date().toISOString(),
      horizonDays: FORECAST_HORIZON_DAYS,
      forecast,
    });
  } catch (err) {
    console.error('Forecast generation error:', err);
    res.status(500).json({
      error: 'Failed to generate weekly forecast',
      details: err.message,
    });
  }
});

app.get('/forecast/latest', async (_req, res) => {
  const fluxQuery = `
from(bucket: "${process.env.INFLUX_BUCKET}")
  |> range(start: -14d)
  |> filter(fn: (r) => r._measurement == "${FORECAST_MEASUREMENT}")
  |> filter(fn: (r) => r.source == "${FORECAST_TAG}")
  |> sort(columns: ["_time"], desc: false)
`;

  try {
    const rows = await queryApi.collectRows(fluxQuery);
    res.status(200).json({ rows });
  } catch (err) {
    console.error('Forecast read error:', err);
    res.status(500).json({ error: 'Failed to read latest forecast' });
  }
});

function scheduleDailyForecast() {
  const every24hMs = 24 * 60 * 60 * 1000;

  setInterval(async () => {
    try {
      const forecast = await generateAndPersistWeeklyForecast();
      console.log(
        `Scheduled forecast generated for ${forecast.length} day(s) at ${new Date().toISOString()}`
      );
    } catch (err) {
      console.error('Scheduled forecast failed:', err.message);
    }
  }, every24hMs);
}

app.listen(PORT, () => {
  console.log(`Wetterstation API listening on port ${PORT}`);

  // Trigger first run at startup so Grafana has forecast data immediately.
  generateAndPersistWeeklyForecast()
    .then((forecast) => {
      console.log(
        `Initial forecast generated for ${forecast.length} day(s) at ${new Date().toISOString()}`
      );
    })
    .catch((err) => {
      console.error('Initial forecast failed:', err.message);
    });

  scheduleDailyForecast();
});
