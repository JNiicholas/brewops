# BrewOps

Telemetry app for the office coffee machines. Python with FastAPI, data in SQLite
(stdlib `sqlite3`, no ORM).

## Architecture

```
ingest → db → api → frontend
```

- **`src/brewops/ingest/`** — parses CSV event logs and loads them into SQLite.
  `loader.py` has the parsing/validation logic; `cli.py` wires up the `ingest`
  and `seed` commands.
- **`src/brewops/db/`** — `schema.py` creates tables (`machines`, `drink_types`,
  `brew_events`, `maintenance_events`) and seeds `machines`/`drink_types`;
  `connection.py` opens the connection (`row_factory = sqlite3.Row`, foreign
  keys on); `queries.py` holds all reads/writes — routes never touch SQL
  directly.
- **`src/brewops/api/`** — one FastAPI app (`main.py`) serving both the JSON
  API (`/api/*`) and the static frontend from the same process/port.
- **`src/brewops/frontend/`** — `index.html` + `app.js` + `style.css`, vanilla
  JS, no build step, no framework.

## Two ingestion paths

Both land in `brew_events` / `maintenance_events`, distinguished by
`source` (`'csv'` or `'manual'`):

1. **CSV** — most machines emit event logs dropped in `data/inbox/` (or any
   folder) as `brews_*.csv` / `maintenance_*.csv` / `manual_*.csv`. Loaded via
   `uv run seed` (inbox) or `uv run ingest <path>` (ad hoc). `loader.py`
   validates each row (known machine id, known drink type, parseable
   non-future timestamp, positive duration) and skips+reports bad rows rather
   than failing the whole file.
2. **Manual entry** — the floor-2 machine ("Old Faithful") has no telemetry;
   humans log its brews/maintenance by hand through the dashboard forms, which
   POST to `/api/brews` and `/api/maintenance` (see `main.py`). Same
   validation rules, enforced in the route handlers via `queries.py` lookups.

## Running and testing

```
uv sync
uv run seed          # create brewops.db, ingest data/inbox/
uv run start          # serve on http://localhost:8123
uv run ingest <path>   # ingest more CSVs (file or folder)
uv run pytest          # test suite (tests/test_{db,ingest,api,frontend}.py)
```

`BREWOPS_DB` env var overrides the db file path (default `./brewops.db`);
useful for pointing tests or a second instance at a different file.

## Conventions

- **Timestamps** are naive local time, stored and parsed as
  `'YYYY-MM-DD HH:MM:SS'` (`TIMESTAMP_FORMAT` in `loader.py`, `main.py` also
  accepts the `datetime-local` input format and normalizes it). Future
  timestamps are rejected everywhere.
- **Drink types** are a DB table (`drink_types`: `name` = key, `label` =
  display text), not an enum — query it rather than hardcoding drink lists.
  Show `label` to users, use `name` as the foreign key value.
- **`queries.py` is the only place SQL lives.** Routes and ingest call into it;
  don't inline `conn.execute(...)` elsewhere.
- **Health/stats endpoints build one dict by merging query results**
  (`machine | {...}`) rather than a dedicated response model — follow that
  pattern when adding fields instead of introducing a new shape.
- Rejected CSV rows are collected, not fatal — an ingest run reports
  `(file, line, reason)` for each bad row and still loads the good ones.
- `/api/stats` and `/api/machines/{id}` accept optional inclusive `start`/`end`
  (`YYYY-MM-DD`) query params for date-range filtering of brew-derived metrics
  (`total_brews`, `per_drink`, `per_day`, `brew_count`, `last_brew`, `specialty`,
  `busiest_day`). Maintenance/error data is deliberately not date-filtered.
