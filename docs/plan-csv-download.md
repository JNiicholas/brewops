# Plan: CSV download of brew events (ticket 002)

Ticket: `tickets/002-csv-export.md` — finance wants the consumption numbers in
Excel for the quarterly office-cost review ("Give them a way to download the
data as CSV").

This plan is written for an implementer with no prior context. Read `CLAUDE.md`
first. Follow its conventions, in particular:

- **All SQL lives in `src/brewops/db/queries.py`.** Never call `conn.execute`
  from `main.py` or anywhere else.
- **No new response models** and **no frameworks / build step / external
  resources** in the frontend (vanilla JS; an existing test forbids `http://`
  and `https://` strings in the frontend files).
- Timestamps are naive local-time strings `'YYYY-MM-DD HH:MM:SS'`.
- Show users a drink's `label`, not its `name`.

This feature builds on the date-range filter that already exists
(`docs/plan-date-filter.md`, already implemented). Reuse these, do not
re-create them:

- `_range_clause(start, end, column="timestamp")` in `queries.py`
- `parse_range(start, end)` and `parse_date_param(...)` in `src/brewops/api/main.py`
- the `filter` object and `rangeQuery()` in `src/brewops/frontend/app.js`

Do the steps in order. Run `uv run pytest` after each of steps 1, 2, 3 and 4.

## Decisions already made (do not re-open)

1. The CSV contains **raw brew events**, one row per brew. It is not a summary.
2. It follows the dashboard's date filter: optional `start` and `end`
   (`YYYY-MM-DD`), both **inclusive**. With no parameters it exports **all**
   brews.
3. **Brews only.** Maintenance events are not exported.
4. Columns, in this order: `timestamp, machine, drink, duration_s, temp_c, source`.
   - `machine` is the machine **name** (e.g. `Bertha (3rd floor)`).
   - `drink` is the drink **label** (display text), not the `name` key.
   - `duration_s` / `temp_c` are empty cells when the value is NULL (manual
     entries have no duration or temperature).
5. Rows are ordered by `timestamp`, then brew id, ascending.
6. The file is **UTF-8 with a BOM** so Excel opens it with correct characters
   without the import wizard. Lines end in `\r\n` (the `csv` module default).
7. There is **no zero-fill**. Days without brews simply have no rows (this is a
   list of events, not a per-day table). Do not add rows with zeros.
8. The export is a **new endpoint**. Do not change `/api/stats`,
   `/api/machines`, or the existing `POST /api/brews`.

## New endpoint (summary)

| Endpoint | Param | Format | Meaning |
|---|---|---|---|
| `GET /api/brews/export.csv` | `start` | `YYYY-MM-DD`, optional | first day included |
| `GET /api/brews/export.csv` | `end` | `YYYY-MM-DD`, optional | last day included |

Success: HTTP 200, `Content-Type: text/csv; charset=utf-8`,
`Content-Disposition: attachment; filename="<name>"`.

Errors (same as `/api/stats`, produced by the existing `parse_range`):
bad format → 400 `unparsable start 'x', expected YYYY-MM-DD`;
`start` after `end` → 400 `start must not be after end`.

Filename rules (only dates that were given, they are already validated):

| Params | Filename |
|---|---|
| none | `brews.csv` |
| start only | `brews_from_2026-06-01.csv` |
| end only | `brews_until_2026-06-02.csv` |
| both | `brews_2026-06-01_to_2026-06-02.csv` |

## Step 1 — `src/brewops/db/queries.py`

Add one function, `get_brew_events`, placed after `get_stats`:

```python
def get_brew_events(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
```

- Get the fragment with `clause, params = _range_clause(start, end, column="be.timestamp")`.
  The column MUST be `be.timestamp` (the table is aliased).
- Query (build with an f-string only for `{clause}`; pass dates as parameters):

  ```sql
  SELECT be.timestamp, m.name AS machine, dt.label AS drink,
         be.duration_s, be.temp_c, be.source
  FROM brew_events be
  JOIN machines m ON m.id = be.machine_id
  JOIN drink_types dt ON dt.name = be.drink_type
  WHERE 1=1{clause}
  ORDER BY be.timestamp, be.id
  ```
- Return `[dict(r) for r in conn.execute(sql, params)]`.
- `params` is a `list`; `conn.execute(sql, params)` accepts it.

Nothing else in this file changes.

## Step 2 — `src/brewops/api/main.py`

### 2a. Imports

Add `import csv` and `import io` with the other stdlib imports, and add
`Response` to the existing `from fastapi import ...` line.

### 2b. Add the route

Add it next to the other `/api/...` GET routes (before the static-files mount at
the bottom of the file; the mount at `/` must stay last).

```python
CSV_COLUMNS = ("timestamp", "machine", "drink", "duration_s", "temp_c", "source")

@app.get("/api/brews/export.csv")
def export_brews_csv(start: str | None = None, end: str | None = None, conn: sqlite3.Connection = Depends(get_db)):
    start, end = parse_range(start, end)
    rows = queries.get_brew_events(conn, start, end)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow(["" if row[c] is None else row[c] for c in CSV_COLUMNS])
    ...
```

Then build the response:

- Body: `("﻿" + buf.getvalue()).encode("utf-8")` (the BOM is the character
  `﻿` at the very start).
- Filename per the table above. Suggested logic:
  `if start and end: name = f"brews_{start}_to_{end}.csv"`,
  `elif start: name = f"brews_from_{start}.csv"`,
  `elif end: name = f"brews_until_{end}.csv"`, `else: name = "brews.csv"`.
- Return
  `Response(content=body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})`.

Use `csv.writer` (not manual string joining): machine names contain
parentheses and could in principle contain commas or quotes, and the module
quotes them correctly.

The route path `/api/brews/export.csv` does not clash with `POST /api/brews`
(different path and method).

## Step 3 — Frontend

Files: `src/brewops/frontend/index.html`, `app.js`, `style.css`.

### 3a. `index.html`

Inside `<div class="panel filter-bar" id="date-filter">`, add this link
**after the three `.preset` buttons and before** `<p id="filter-message" ...>`:

```html
<a id="export-csv" class="export-link" href="/api/brews/export.csv" download>Export CSV</a>
```

**Do NOT give it the class `preset`.** `setupFilter()` in `app.js` attaches a
click handler to every `.preset` element (`document.querySelectorAll(".preset")`)
that reads `data-days` and reloads the dashboard, which would break the link. An
existing test also expects exactly three `class="preset"` buttons. Use the class
`export-link` (as above).

### 3b. `app.js`

Add this at the end of `setupFilter()` (inside the function, after the preset
button wiring), or in a tiny new `setupExport()` called next to `setupFilter()`
at the bottom of the file. Either is fine; do not change anything else.

```js
const exportLink = document.getElementById("export-csv");
exportLink.addEventListener("click", (event) => {
  if (filter.start && filter.end && filter.start > filter.end) {
    event.preventDefault();
    const messageEl = document.getElementById("filter-message");
    messageEl.textContent = "From date must not be after To date.";
    messageEl.className = "message error";
    return;
  }
  exportLink.href = "/api/brews/export.csv" + rangeQuery();
});
```

Why at click time: when From is after To the existing change handler shows the
error but does not call `loadDashboard()`, so any href computed inside
`loadDashboard()` could be stale. Computing it on click always uses the current
`filter`, and the browser reads `href` after the click handler runs.

### 3c. `style.css`

Style the link like the preset buttons, next to the existing `.preset` rules
(the `.filter-bar` block). Add `.export-link` to the shared
`.filter-bar input[type="date"], .preset { ... }` selector list, or copy those
declarations, and also add:

```css
.export-link {
  display: inline-block;
  text-decoration: none;
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--brass-deep);
  cursor: pointer;
  margin-left: auto;   /* pushes it to the right of the filter bar row */
}
.export-link:hover { border-color: var(--brass); color: var(--brass); }
.export-link:focus-visible { outline: 3px solid rgb(185 129 47 / 0.5); outline-offset: 2px; }
```

Use only existing CSS variables (`--line`, `--brass`, `--brass-deep`,
`--radius-sm`). Visual result: a white, bordered, pill-like "Export CSV" control
that matches the "Last 7 days" buttons, on the same row as the filter (wraps
under it on narrow screens; no horizontal scroll). No icons, fonts or images
from outside the repo.

## Step 4 — Docs

In `CLAUDE.md`, under "Conventions", add a bullet: `GET /api/brews/export.csv`
downloads raw brew events (brews only, not maintenance) as UTF-8-with-BOM CSV
for Excel; it honours the same optional inclusive `start`/`end` (`YYYY-MM-DD`)
params, and days without brews produce no rows.

## Edge cases (each needs handling and a test)

| Case | Expected behaviour |
|---|---|
| No params | 200; every brew in the DB, header row first; filename `brews.csv`. |
| Only `start` | Everything from that day onward. |
| Only `end` | Everything up to and including that day. |
| `start == end` | Exactly that day, including a brew at `23:59:59`. |
| `start` after `end` (API) | HTTP 400 `start must not be after end`; no CSV body. |
| `start` after `end` (UI) | Click does nothing: no request, no download, message "From date must not be after To date." shown. |
| Malformed date, e.g. `2026-13-45`, `06/01/2026` | HTTP 400 with the `unparsable ...` message. |
| Empty string param (`?start=`) | Treated as not set. |
| **Empty range** (no brews inside) | HTTP 200 and a **valid CSV containing only the header row** (`timestamp,machine,drink,duration_s,temp_c,source`). Not a 404, not an empty file. |
| **Days with no brews** inside a range | No rows for those days. No zero rows. The file is a list of events. |
| Manual brew (no duration/temp) | `duration_s` and `temp_c` are empty cells, not `None` or `null` text. |
| Future `end` | Allowed (same as `/api/stats`); just returns whatever exists. |
| Very large range, e.g. `1900-01-01`..`2100-01-01` | Works; returns all brews. No cap needed. |
| Machine name with parentheses (`Bertha (3rd floor)`) | Appears intact; `csv.writer` quotes when needed. |

## Step 5 — Tests

Add to the existing files; do not restructure them. Look at
`tests/test_db.py::test_stats_with_date_range` and
`tests/test_api.py::test_stats_with_date_range` for fixtures (`conn`, `db`) and
the `request(app, "GET", "/path?query")` helper. (`tests/asgi_client.py`
already parses query strings; do not modify it.)

`tests/test_db.py`:
- `get_brew_events(conn)` with a few inserted brews: returns all, ordered by
  timestamp; each row has exactly the keys `timestamp, machine, drink,
  duration_s, temp_c, source`; `drink` is the label (e.g. `Espresso`, check
  with `get_drink_types`), `machine` is the machine name (check with
  `get_machines`).
- range with `start` only, `end` only, both; a `23:59:59` brew on `end` is
  included, a `00:00:00` brew on the next day is not.
- empty range returns `[]`.
- a brew inserted with `duration_s=None, temp_c=None` comes back with `None`
  for both.

`tests/test_api.py` (parse CSV with `csv.reader(io.StringIO(r.body.decode("utf-8-sig")))`;
do not use `r.text` for the content because it keeps the BOM character):
- `GET /api/brews/export.csv`: status 200, `content-type` starts with
  `text/csv`, `content-disposition` contains `attachment` and `brews.csv`,
  `r.body.startswith(b"\xef\xbb\xbf")`, first parsed row equals the header,
  number of data rows equals 3 (the `db` fixture has 3 brews).
- `?start=2026-06-02&end=2026-06-02`: one data row; filename contains
  `brews_2026-06-02_to_2026-06-02.csv`.
- empty range (`?start=2030-01-01&end=2030-01-02`): 200, only the header row.
- `?start=2026-06-05&end=2026-06-01` → 400 with `start must not be after end`.
- `?start=nope` → 400.
- a manual brew posted with `POST /api/brews` (no duration) exports with empty
  `duration_s` / `temp_c` cells.

`tests/test_frontend.py`:
- served `index.html` contains `id="export-csv"` and `href="/api/brews/export.csv"`.
- it still contains exactly three `class="preset"` occurrences.
- the existing "no external resources" test still passes.

## Step 6 — Verify end to end

1. `uv run pytest` — everything green, including all pre-existing tests.
2. `uv run seed && uv run start`, open http://localhost:8123.
3. Check the API directly:
   - `curl -i 'localhost:8123/api/brews/export.csv'` — 200, `text/csv`,
     `Content-Disposition: attachment; filename="brews.csv"`. The number of
     lines is `total_brews` from `curl localhost:8123/api/stats` plus 1
     (header).
   - `curl 'localhost:8123/api/brews/export.csv?start=2026-06-01&end=2026-06-02' | wc -l`
     equals `total_brews` for the same range in `/api/stats?start=2026-06-01&end=2026-06-02`, plus 1.
   - `curl -i 'localhost:8123/api/brews/export.csv?start=2026-06-05&end=2026-06-01'` — 400.
   - a range with no brews — 200 and exactly one line (the header).
   - `curl -s 'localhost:8123/api/brews/export.csv' | head -c 3 | xxd` shows `efbb bf` (BOM).
4. In the browser:
   - Click `Export CSV` with no filter: a file `brews.csv` downloads.
   - Click `Last 7 days`, then `Export CSV`: filename contains both dates and
     the file only has rows inside that range.
   - Set From after To, then click `Export CSV`: nothing downloads; the error
     message is visible.
   - Pick a range with no brews, click `Export CSV`: file has only the header.
   - Open a downloaded file in Excel or Numbers: six columns, readable
     characters, empty cells (not "None") for manual brews.
   - Narrow the window to phone width: the filter bar wraps, the link is
     still reachable, no horizontal scroll.
5. Confirm `git diff` touches only: `queries.py`, `main.py`, `index.html`,
   `app.js`, `style.css`, `CLAUDE.md`, and the three test files
   (`test_db.py`, `test_api.py`, `test_frontend.py`). No SQL outside
   `queries.py`.
