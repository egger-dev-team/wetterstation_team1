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
  |> filter(fn: (r) => r._field == "windgeschwindigkeit")
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