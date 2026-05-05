# Temperature
from(bucket: "wetterstation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wetterstation")
  |> filter(fn: (r) => r._field == "temperatur")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)

# Humidity
from(bucket: "wetterstation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wetterstation")
  |> filter(fn: (r) => r._field == "luftfeuchtigkeit")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)

# Air pressure
from(bucket: "wetterstation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wetterstation")
  |> filter(fn: (r) => r._field == "luftdruck")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)

# Precipitation — use sum instead of mean since it's a rate accumulation
from(bucket: "wetterstation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wetterstation")
  |> filter(fn: (r) => r._field == "niederschlag")
  |> aggregateWindow(every: v.windowPeriod, fn: sum, createEmpty: false)

# Wind speed
from(bucket: "wetterstation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wetterstation")
  |> filter(fn: (r) => r._field == "windgeschwindigkeit")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)

# Brightness / Lux
from(bucket: "wetterstation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wetterstation")
  |> filter(fn: (r) => r._field == "helligkeit")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)

# Wind direction — use Stat or Table panel (it's a string tag, not a field)
from(bucket: "wetterstation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wetterstation")
  |> filter(fn: (r) => r._field == "temperatur")  // any field to get the row
  |> keep(columns: ["_time", "windrichtung"])
  |> last()

# All fields in one panel (overview)
from(bucket: "wetterstation")
  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)
  |> filter(fn: (r) => r._measurement == "wetterstation")
  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)

# Forecast (next 7 days) - Temperature
from(bucket: "wetterstation")
  |> range(start: now(), stop: now() + 8d)
  |> filter(fn: (r) => r._measurement == "wetterstation_forecast")
  |> filter(fn: (r) => r.source == "v1")
  |> filter(fn: (r) => r._field == "temperatur")
  |> sort(columns: ["_time"], desc: false)

# Forecast (next 7 days) - Precipitation
from(bucket: "wetterstation")
  |> range(start: now(), stop: now() + 8d)
  |> filter(fn: (r) => r._measurement == "wetterstation_forecast")
  |> filter(fn: (r) => r.source == "v1")
  |> filter(fn: (r) => r._field == "niederschlag")
  |> sort(columns: ["_time"], desc: false)

# Rule engine recommendation table (Workout + Drink)
from(bucket: "wetterstation")
  |> range(start: now(), stop: now() + 8d)
  |> filter(fn: (r) => r._measurement == "wetterstation_forecast")
  |> filter(fn: (r) => r.source == "v1")
  |> filter(fn: (r) => r._field == "workout" or r._field == "drink" or r._field == "rationale")
  |> pivot(rowKey: ["_time", "date"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "date", "workout", "drink", "rationale"])
  |> sort(columns: ["_time"], desc: false)

# Forecast overview table (all metrics + recommendations)
from(bucket: "wetterstation")
  |> range(start: now(), stop: now() + 8d)
  |> filter(fn: (r) => r._measurement == "wetterstation_forecast")
  |> filter(fn: (r) => r.source == "v1")
  |> pivot(rowKey: ["_time", "date"], columnKey: ["_field"], valueColumn: "_value")
  |> keep(columns: ["_time", "date", "temperatur", "luftfeuchtigkeit", "luftdruck", "niederschlag", "windgeschwindigkeit", "helligkeit", "workout", "drink"])
  |> sort(columns: ["_time"], desc: false)