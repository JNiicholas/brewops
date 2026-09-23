"""Read and write queries. All timestamps are naive local time strings."""

import sqlite3
from datetime import date, timedelta
from typing import Any


def get_machines(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, floor, has_telemetry FROM machines ORDER BY id")
    return [dict(r) | {"has_telemetry": bool(r["has_telemetry"])} for r in rows]


def get_machine(conn: sqlite3.Connection, machine_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, name, floor, has_telemetry FROM machines WHERE id = ?", (machine_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(row) | {"has_telemetry": bool(row["has_telemetry"])}


def get_drink_types(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, label FROM drink_types ORDER BY id")
    return [dict(r) for r in rows]


def drink_type_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM drink_types WHERE name = ?", (name,)).fetchone()
    return row is not None


def _range_clause(start: str | None, end: str | None, column: str = "timestamp") -> tuple[str, list[str]]:
    """Return (sql_fragment, params) restricting `column` to [start, end] by day.

    Fragment is '' or starts with ' AND '. Dates are 'YYYY-MM-DD' strings.
    end is inclusive of its whole day, so compare with DATE(column) <= end.
    """
    conditions = []
    params = []
    if start:
        conditions.append(f"{column} >= ?")
        params.append(start)
    if end:
        conditions.append(f"DATE({column}) <= ?")
        params.append(end)
    if not conditions:
        return ("", [])
    return (" AND " + " AND ".join(conditions), params)


def insert_brew(
    conn: sqlite3.Connection,
    machine_id: int,
    drink_type: str,
    timestamp: str,
    duration_s: float,
    temp_c: float,
    source: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO brew_events (machine_id, drink_type, timestamp, duration_s, temp_c, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (machine_id, drink_type, timestamp, duration_s, temp_c, source),
    )
    return cur.lastrowid


def insert_maintenance(
    conn: sqlite3.Connection,
    machine_id: int,
    type: str,
    timestamp: str,
    note: str | None = None,
    error_code: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO maintenance_events (machine_id, type, timestamp, note, error_code)
        VALUES (?, ?, ?, ?, ?)
        """,
        (machine_id, type, timestamp, note, error_code),
    )
    return cur.lastrowid


def get_stats(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Dashboard numbers: totals, per-drink, per-day."""
    total_clause, total_params = _range_clause(start, end)
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM brew_events WHERE 1=1{total_clause}",
        total_params
    ).fetchone()["n"]

    drink_clause, drink_params = _range_clause(start, end, column="be.timestamp")
    per_drink = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT dt.name, dt.label, COUNT(be.id) AS count
            FROM drink_types dt
            LEFT JOIN brew_events be ON be.drink_type = dt.name{drink_clause}
            GROUP BY dt.id
            ORDER BY dt.id
            """,
            drink_params
        )
    ]

    day_clause, day_params = _range_clause(start, end)
    per_day = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT DATE(timestamp) AS day, COUNT(*) AS count
            FROM brew_events
            WHERE 1=1{day_clause}
            GROUP BY DATE(timestamp)
            ORDER BY day
            """,
            day_params
        )
    ]

    # Zero-fill per_day if both start and end are set
    if start and end and start <= end:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        date_range = end_date - start_date
        if date_range.days <= 366:
            per_day_dict = {item["day"]: item["count"] for item in per_day}
            filled_per_day = []
            current = start_date
            while current <= end_date:
                day_str = current.isoformat()
                filled_per_day.append({
                    "day": day_str,
                    "count": per_day_dict.get(day_str, 0)
                })
                current += timedelta(days=1)
            per_day = filled_per_day

    return {"total_brews": total, "per_drink": per_drink, "per_day": per_day, "start": start, "end": end}


def get_machine_health(conn: sqlite3.Connection, machine_id: int, start: str | None = None, end: str | None = None) -> dict[str, Any] | None:
    """Machine card: brew activity plus maintenance history."""
    machine = get_machine(conn, machine_id)
    if machine is None:
        return None

    brew_clause, brew_params = _range_clause(start, end)
    brews = conn.execute(
        f"""
        SELECT COUNT(*) AS count, MAX(timestamp) AS last_brew
        FROM brew_events WHERE machine_id = ?{brew_clause}
        """,
        (machine_id,) + tuple(brew_params),
    ).fetchone()

    last_maintenance = conn.execute(
        """
        SELECT type, timestamp, note, error_code
        FROM maintenance_events
        WHERE machine_id = ? AND type != 'error'
        ORDER BY timestamp DESC LIMIT 1
        """,
        (machine_id,),
    ).fetchone()
    recent_errors = [
        dict(r)
        for r in conn.execute(
            """
            SELECT timestamp, error_code, note
            FROM maintenance_events
            WHERE machine_id = ? AND type = 'error'
            ORDER BY timestamp DESC LIMIT 5
            """,
            (machine_id,),
        )
    ]

    spec_clause, spec_params = _range_clause(start, end, column="b.timestamp")
    specialty = conn.execute(
        f"""
        SELECT dt.label, COUNT(*) AS count
        FROM brew_events b
        JOIN drink_types dt ON dt.name = b.drink_type
        WHERE b.machine_id = ?{spec_clause}
        GROUP BY b.drink_type
        ORDER BY COUNT(*) DESC, dt.name
        LIMIT 1
        """,
        (machine_id,) + tuple(spec_params),
    ).fetchone()

    busy_clause, busy_params = _range_clause(start, end)
    busiest_day = conn.execute(
        f"""
        SELECT DATE(timestamp) AS day, COUNT(*) AS count
        FROM brew_events
        WHERE machine_id = ?{busy_clause}
        GROUP BY day
        ORDER BY count DESC, day
        LIMIT 1
        """,
        (machine_id,) + tuple(busy_params),
    ).fetchone()

    return machine | {
        "brew_count": brews["count"],
        "last_brew": brews["last_brew"],
        "last_maintenance": dict(last_maintenance) if last_maintenance else None,
        "recent_errors": recent_errors,
        "specialty": specialty["label"] if specialty else None,
        "specialty_count": specialty["count"] if specialty else None,
        "busiest_day": busiest_day["day"] if busiest_day else None,
        "busiest_day_count": busiest_day["count"] if busiest_day else None,
    }
