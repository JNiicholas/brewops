# Plan: dashboard date-range filter (ticket 005)

Ticket: `tickets/005-date-filter.md` — "Let people pick a date range and have the
numbers and charts follow."

This plan is written for an implementer with no prior context. Read
`CLAUDE.md` first. Follow its conventions, in particular:

- **All SQL lives in `src/brewops/db/queries.py`.** Never call `conn.execute`
  from `main.py` or anywhere else.
- **No new response models.** Endpoints return a dict built by merging
  (`machine | {...}`); add fields to the existing dict.
- **No frameworks, no build step** in the frontend (vanilla JS).
- Timestamps are naive local-time strings `'YYYY-MM-DD HH:MM:SS'`.

Do the steps in order. Run `uv run pytest` after each of steps 1, 2, 3 and 5.

## Decisions already made (do not re-open)

1. The filter is a **date range** with `start` and `end`, both `YYYY-MM-DD`,
   both **optional**, and **both inclusive** (a brew at `2026-06-02 23:59:59`
   is inside `end=2026-06-02`).
2. With no parameters, every endpoint behaves **exactly as it does today**.
   Existing tests must keep passing unchanged.
3. Brew-derived numbers follow the filter: the stats tiles, per-drink bars,
   timeline, and on each machine card `brew_count`, `last_brew`, `specialty`,
   `busiest_day`.
4. Maintenance data (`last_maintenance`, `recent_errors`) is **NOT filtered**.
   It is machine health, not throughput.
5. A future `end` (or `start`) is allowed. Do not reuse `parse_timestamp`
   from `main.py`, because it rejects future times.
6. Timeline gaps: when **both** `start` and `end` are given, `per_day` is
   zero-filled so every day in the range appears. With fewer than two bounds,
   `per_day` is unchanged (only days that have brews).
7. The filter state is not persisted (no URL or localStorage).

## Step 1 — `src/brewops/db/queries.py`

### 1a. Add a helper

```python
def _range_clause(start: str | None, end: str | None, column: str = "timestamp") -> tuple[str, list[str]]:
    """Return (sql_fragment, params) restricting `column` to [start, end] by day.

    Fragment is '' or starts with ' AND '. Dates are 'YYYY-MM-DD' strings.
    end is inclusive of its whole day, so compare with `< end + 1 day`.
    """
```

Behaviour:

- `start` set: add `{column} >= ?` with param `start` (a bare date sorts before
  any timestamp on that day, so `'2026-06-01' <= '2026-06-01 00:00:00'`).
- `end` set: add `DATE({column}) <= ?` with param `end`. (Simple and correct;
  the index is not needed for correctness on this dataset.)
- Each condition is joined with ` AND `. If neither is set, return `("", [])`.

The fragment always starts with `" AND "` (or is empty). Callers append it
after an existing `WHERE ...`, or write `WHERE 1=1{clause}` when the query had
no `WHERE`. Build the SQL with an f-string only for the fragment (it contains
`?` placeholders and a column name from your code, never user input); pass the
dates as parameters.

### 1b. Change `get_stats(conn)` → `get_stats(conn, start=None, end=None)`

- **total**: `SELECT COUNT(*) AS n FROM brew_events WHERE 1=1{clause}`.
- **per_drink**: the range condition MUST go in the `LEFT JOIN ... ON` clause,
  **not** in a `WHERE`. Otherwise drinks with zero brews in the range disappear
  from the bar chart. Shape:

  ```sql
  SELECT dt.name, dt.label, COUNT(be.id) AS count
  FROM drink_types dt
  LEFT JOIN brew_events be ON be.drink_type = dt.name{clause with column be.timestamp}
  GROUP BY dt.id ORDER BY dt.id
  ```

  Use `column="be.timestamp"` for this call. The fragment starts with `AND`,
  which is valid inside `ON`.
- **per_day**: add `WHERE 1=1{clause}` before `GROUP BY`.
- **Zero-fill** (only when `start` and `end` are both not None and
  `start <= end`): after the query, build a list with one entry
  `{"day": "YYYY-MM-DD", "count": n}` for every date from `start` to `end`
  inclusive, using `datetime.date` and `timedelta(days=1)`. `count` comes from
  the query result, or `0` if the day had no brews. Cap the range at 366 days
  (see edge cases) so a huge range cannot build a giant list.
- Return `{"total_brews": ..., "per_drink": ..., "per_day": ..., "start": start, "end": end}`.
  `start` and `end` are echoed (possibly `None`) so the frontend can show what
  was applied.

### 1c. Change `get_machine_health(conn, machine_id)` → `(conn, machine_id, start=None, end=None)`

Apply the range clause to exactly these four queries, each of which already
has `WHERE machine_id = ?` (append `{clause}` after it and extend the params
tuple):

- `brews` (`COUNT(*)`, `MAX(timestamp)`) → gives `brew_count`, `last_brew`
- `specialty` (use `b.timestamp`, `column="b.timestamp"`)
- `busiest_day`

Do **not** touch `last_maintenance` or `recent_errors`.

With an empty range the existing code already yields `brew_count = 0`,
`last_brew = None`, `specialty = None`, `busiest_day = None` — check that
`COUNT(*)` with no rows returns `0` (it does) and `MAX()` returns `None`.

## Step 2 — `src/brewops/api/main.py`

### 2a. Add a date-parameter validator

```python
def parse_date_param(name: str, value: str | None) -> str | None:
    """Validate a 'YYYY-MM-DD' query param. Returns it unchanged, or None."""
```

- `None` or empty string → `None`.
- Otherwise `datetime.strptime(value, "%Y-%m-%d")`; on `ValueError` raise
  `HTTPException(400, f"unparsable {name} {value!r}, expected YYYY-MM-DD")`.

### 2b. Add a shared dependency-free function to read the range

```python
def parse_range(start: str | None, end: str | None) -> tuple[str | None, str | None]:
```

- Runs `parse_date_param` on both.
- If both are set and `start > end` (string comparison is correct for ISO
  dates) raise `HTTPException(400, "start must not be after end")`.

### 2c. Update the two routes

```python
@app.get("/api/stats")
def stats(start: str | None = None, end: str | None = None, conn=Depends(get_db)):
    start, end = parse_range(start, end)
    return queries.get_stats(conn, start, end)

@app.get("/api/machines/{machine_id}")
def machine_health(machine_id: int, start: str | None = None, end: str | None = None, conn=Depends(get_db)):
    start, end = parse_range(start, end)
    ... existing 404 logic, passing start, end to queries.get_machine_health
```

Leave `/api/machines`, `/api/drink-types`, and both POST routes unchanged.

### New query parameters (summary)

| Endpoint | Param | Format | Meaning |
|---|---|---|---|
| `GET /api/stats` | `start` | `YYYY-MM-DD`, optional | first day included |
| `GET /api/stats` | `end` | `YYYY-MM-DD`, optional | last day included |
| `GET /api/machines/{id}` | `start`, `end` | same | same |

Errors: bad format → 400 `unparsable start 'x', expected YYYY-MM-DD`;
`start` after `end` → 400 `start must not be after end`.

## Step 3 — Frontend

Files: `src/brewops/frontend/index.html`, `app.js`, `style.css`.

### 3a. `index.html` — filter bar

Insert a new `<div class="panel filter-bar" id="date-filter">` as the **first
child of `<section id="dashboard">`**, above `.stat-tiles`. Contents, in
this order, on one row (wraps on narrow screens):

1. `<label for="filter-start">From</label>` + `<input type="date" id="filter-start">`
2. `<label for="filter-end">To</label>` + `<input type="date" id="filter-end">`
3. Preset buttons (`type="button"`, class `preset`), with `data-days`:
   - `Last 7 days` (`data-days="7"`)
   - `Last 30 days` (`data-days="30"`)
   - `All time` (`data-days="all"`)
4. `<p id="filter-message" class="message" role="status"></p>` for errors.

No external resources (an existing test forbids them). No `<form>` element is
needed; changes apply as soon as either date input changes or a preset is
clicked.

Change tile labels (keep the element ids as they are):

- Give the `brews total` label element `id="total-label"`. JS sets its text
  (see 3b): `brews total` when unfiltered, `brews in range` when filtered.
- `brews on last active day` → give it `id="last-day-label"`. JS sets it to
  `brews on last active day` (unfiltered) or `brews on last day of range`
  (filtered).

### 3b. `app.js`

Add one module-level state object and use it everywhere:

```js
const filter = { start: "", end: "" };   // "" means unset

function rangeQuery() {
  const p = new URLSearchParams();
  if (filter.start) p.set("start", filter.start);
  if (filter.end) p.set("end", filter.end);
  const s = p.toString();
  return s ? `?${s}` : "";
}
```

Changes in `loadDashboard()` (`app.js:78`):

- `fetchJSON("/api/stats" + rangeQuery())`.
- Each machine call becomes `fetchJSON(`/api/machines/${m.id}${rangeQuery()}`)`.
- Set `total-label` and `last-day-label` text based on whether
  `filter.start || filter.end` is set.
- `brews-today` currently uses `per_day[last]`. With zero-filled data the last
  entry is the last day of the range (possibly 0), which matches the new label.
  Leave the logic as is.
- Pass `filtered` (boolean) to `renderMachineCards` so card wording can change
  (below).

Wire the controls in a new `setupFilter()`, called next to `setupForms()`:

- `change` on either date input: read both values into `filter`, then
  **validate on the client**: if both set and `start > end`, set
  `#filter-message` to `"From date must not be after To date."` (class
  `error`) and **do not** call the API. Otherwise clear the message and call
  `loadDashboard()`.
- Preset click: `all` → clear both inputs and `filter`. A number `N` → set
  `end` = today, `start` = today minus `N-1` days, both formatted
  `YYYY-MM-DD` in **local** time (do not use `toISOString()` on a plain
  `Date`; it is UTC. Reuse the local-time trick from `localNow()` at
  `app.js:94`, then `.slice(0, 10)`). Write both into the inputs and `filter`,
  then `loadDashboard()`.
- Mark the active preset button with class `active`; remove it whenever the
  user edits a date input manually.

Empty-state changes:

- `renderTimeline` (`app.js:29`): change the signature to
  `renderTimeline(perDay, filtered)` and update its one call in
  `loadDashboard()` to pass the same `filtered` boolean.
  - **Empty `perDay`** (`perDay.length === 0`). This happens when the DB is
    empty, when only one of `start`/`end` is set and nothing matches, or when
    the range is over 366 days (no zero-fill) and nothing matches. Today the
    function returns early and leaves a blank panel. Instead, draw a centered
    SVG `<text>` inside `#timeline` (`x="300" y="65" text-anchor="middle"`,
    class `timeline-empty`) reading `No brews in this range` when `filtered`,
    otherwise `No brews yet`. Then return. Because the SVG uses
    `preserveAspectRatio="none"`, the text may look stretched; add a
    `.timeline-empty` rule in `style.css` using the muted text colour already
    used by `.hint` and a small font size, and check it visually.
  - **All counts are 0** (a zero-filled range with no brews). `max` would be
    `0`, so `day.count / max` is `NaN` and every bar gets `height="NaN"`. Guard
    with `const max = Math.max(1, ...perDay.map(d => d.count))` (same as
    `renderDrinkBars`). Bars then render at height 0, the x-axis is still
    there, and hovering a bar shows e.g. `2026-06-03: 0 brews`. In this case
    also draw the same `.timeline-empty` text (`No brews in this range`),
    detected with `perDay.every(d => d.count === 0)`, so an all-empty chart
    is not mistaken for a broken one.
- After loading, if `stats.total_brews === 0` and a filter is set, put the text
  `No brews in this range.` into `#filter-message` (class `message`, not
  `error`); clear it otherwise. The timeline shows its own empty-state text
  (above), and the drink bars render with all-zero counts.
- `renderMachineCards`: when filtered, change wording:
  `${m.brew_count} brews` → `${m.brew_count} brews in range`;
  `no brews yet` → `no brews in range`;
  `no specialty yet` → `no specialty in range`;
  the `last never` text stays as is. Keep "Last maintenance" and
  "Recent errors" text unchanged (not filtered).

The form-submit path already calls `loadDashboard()` (`app.js:150`), so newly
logged brews respect the active filter automatically. Do not change it.

### 3c. `style.css`

Add, near the panel rules (`style.css:162`):

- `.filter-bar`: `display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem;`
  and make it span the full grid width. The dashboard is a bento grid
  (`style.css:120–160`); look at how `.stat-tiles` spans the row and give
  `.filter-bar` the same `grid-column`.
- `.filter-bar input[type="date"]`, `.preset`: reuse the existing
  input/button look from the form panels (copy those colour variables; do not
  invent new ones).
- `.preset.active`: filled background using the existing brass/accent variable
  used for the primary button.
- `.timeline-empty`: muted text colour (same as `.hint`), small font size,
  `fill` not `color` since it is SVG text.

Keep it visually consistent with the existing panels. No external fonts or
images.

## Step 4 — Docs

In `CLAUDE.md`, under "Conventions", add a bullet: `/api/stats` and
`/api/machines/{id}` accept optional inclusive `start`/`end` (`YYYY-MM-DD`)
query params; maintenance/error data is deliberately not date-filtered.

## Edge cases (each needs handling and a test)

| Case | Expected behaviour |
|---|---|
| No params | Identical to current output; `start`/`end` in `/api/stats` are `null`. |
| Only `start` | Everything from that day onward. No zero-fill. |
| Only `end` | Everything up to and including that day. No zero-fill. |
| `start == end` | Exactly one day, including brews at `23:59:59`. `per_day` has one entry. |
| `start` after `end` (API) | HTTP 400 `start must not be after end`. |
| `start` after `end` (UI) | Message shown, **no request sent**, previous data stays on screen. |
| Malformed date, e.g. `2026-13-45` or `06/01/2026` | HTTP 400 with the `unparsable ...` message. |
| Empty string param (`?start=`) | Treated as not set. |
| Empty range (no brews inside) | HTTP 200. `total_brews` 0; `per_drink` still lists **every** drink type with count 0; `per_day` is all-zero if zero-filled, else `[]`; machine cards show `brew_count` 0 and `null` last_brew/specialty/busiest_day. Must not crash or divide by zero in the frontend. |
| Days with no brews inside a full range | Present in `per_day` with `count: 0` (zero-fill). Days outside the range never appear. |
| Range in the future / `end` in the future | Allowed. Zero-filled future days show 0. |
| Very large range, e.g. `1900-01-01`..`2100-01-01` | Zero-fill is capped: if the range spans more than 366 days, skip zero-filling and return only days that have brews. Counts remain correct. |
| Drink with zero brews in range | Still appears in `per_drink` (date condition in the `LEFT JOIN ... ON`, not `WHERE`). |
| Machine with brews only outside range | Card shows 0 brews, but still shows its last maintenance and errors. |
| Logging a brew while a filter is active | Dashboard reloads with the filter still applied. |

## Step 5 — Tests

Add to the existing files; do not restructure them. Look at
`tests/test_db.py::test_stats_math` and `tests/test_api.py::test_stats` for how
fixtures (`conn`, `db`) and helpers (`request`) work, and reuse them.

`tests/test_db.py`:
- range with `start` only, `end` only, both; boundary days inclusive
  (including a `23:59:59` brew on `end`).
- empty range: all drinks present with count 0, `total_brews == 0`.
- zero-fill: gaps present with count 0; >366-day range is not filled.
- `get_machine_health` with a range: `brew_count`, `busiest_day`,
  `specialty` follow the range; `last_maintenance`/`recent_errors` do not.
- no-arg calls still return the old numbers.

`tests/test_api.py`:
- `GET /api/stats?start=...&end=...` returns filtered numbers and echoes
  `start`/`end`.
- `?start=2026-06-05&end=2026-06-01` → 400 with the "start must not be after
  end" message.
- `?start=nope` → 400.
- `GET /api/machines/1?start=...&end=...` filtered; unknown machine still 404.
- No params: the existing `test_stats` passes unchanged.

`tests/test_frontend.py`:
- the served `index.html` contains `id="filter-start"`, `id="filter-end"`, and
  three `.preset` buttons.
- the existing "no external resources" test still passes.

## Step 6 — Verify end to end

1. `uv run pytest` — everything green, including all pre-existing tests.
2. `uv run seed && uv run start`, open http://localhost:8123.
3. Check the API directly:
   - `curl 'localhost:8123/api/stats'` — same as before plus `start:null, end:null`.
   - `curl 'localhost:8123/api/stats?start=2026-06-01&end=2026-06-02'` —
     numbers match a manual count of the CSVs in `data/inbox/`.
   - `curl -i 'localhost:8123/api/stats?start=2026-06-05&end=2026-06-01'` — 400.
   - a range with no brews — 200, all drinks with 0.
4. In the browser:
   - Click `Last 7 days`, `Last 30 days`, `All time`: tiles, bars, timeline,
     and machine cards change; labels switch between "total" and "in range".
   - Set From after To: error message, no change to data.
   - Pick a range with no brews: "No brews in this range." under the filter
     bar, the timeline panel shows the same text (not a blank panel, no
     `NaN` attributes in the SVG), drink bars are all 0, no console errors.
     Repeat with only From set to a future date (empty `per_day`, no
     zero-fill): the timeline must still show the empty-state text.
   - With a range active, log a brew inside it via the form: numbers update
     and the filter stays.
   - Narrow the window to phone width: filter bar wraps, no horizontal scroll.
5. Confirm `git diff` touches only: `queries.py`, `main.py`, `index.html`,
   `app.js`, `style.css`, `CLAUDE.md`, and the three test files. No SQL
   outside `queries.py`.
