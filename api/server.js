'use strict';

const express = require('express');
const { InfluxDB, Point } = require('@influxdata/influxdb-client');

const app = express();
const PORT = 3000;

const influxClient = new InfluxDB({
  url: process.env.INFLUX_URL,
  token: process.env.INFLUX_TOKEN,
});

const writeApi = influxClient.getWriteApi(
  process.env.INFLUX_ORG,
  process.env.INFLUX_BUCKET,
  'ms'
);

const NUMERIC_PARAMS = [
  'temperatur',
  'luftfeuchtigkeit',
  'luftdruck',
  'niederschlag',
  'windgeschwindigkeit',
  'helligkeit',
];

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

app.listen(PORT, () => {
  console.log(`Wetterstation API listening on port ${PORT}`);
});
