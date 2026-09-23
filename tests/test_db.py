import pytest

from brewops.db.connection import connect
from brewops.db.queries import (
    get_drink_types,
    get_machine_health,
    get_machines,
    get_stats,
    insert_brew,
    insert_maintenance,
)
from brewops.db.schema import init_db, reset_db


@pytest.fixture
def conn(tmp_path):
    conn = connect(tmp_path / "test.db")
    init_db(conn)
    yield conn
    conn.close()


def test_init_is_idempotent(conn):
    init_db(conn)
    init_db(conn)
    assert len(get_machines(conn)) == 4
    assert len(get_drink_types(conn)) == 6


def test_reference_data_seeded(conn):
    machines = {m["name"]: m for m in get_machines(conn)}
    assert "Bertha (3rd floor)" in machines
    assert machines["Old Faithful (2nd floor)"]["has_telemetry"] is False
    drinks = {d["name"] for d in get_drink_types(conn)}
    assert "espresso" in drinks


def test_stats_math(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.5, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-01 09:00:00", 26.0, 91.5, "csv")
    insert_brew(conn, 2, "latte", "2026-06-02 10:00:00", 44.0, 88.0, "csv")
    insert_brew(conn, 3, "lungo", "2026-06-02 11:00:00", 38.0, 90.0, "manual")
    conn.commit()

    stats = get_stats(conn)
    assert stats["total_brews"] == 4
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["espresso"] == 2
    assert per_drink["latte"] == 1
    assert per_drink["cappuccino"] == 0
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-06-01": 2, "2026-06-02": 2}


def test_machine_health(conn):
    insert_brew(conn, 4, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_maintenance(conn, 4, "descale", "2026-06-03 18:00:00", note="quarterly descale")
    insert_maintenance(conn, 4, "error", "2026-06-04 09:15:00", error_code="E42")
    conn.commit()

    health = get_machine_health(conn, 4)
    assert health["brew_count"] == 1
    assert health["last_brew"] == "2026-06-01 08:00:00"
    assert health["last_maintenance"]["type"] == "descale"
    assert health["recent_errors"][0]["error_code"] == "E42"


def test_machine_health_unknown_machine(conn):
    assert get_machine_health(conn, 999) is None


def test_reset_db_clears_events(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    conn.commit()
    reset_db(conn)
    assert get_stats(conn)["total_brews"] == 0
    assert len(get_machines(conn)) == 4


def test_stats_with_date_range(conn):
    insert_brew(conn, 1, "espresso", "2026-05-31 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-01 09:00:00", 26.0, 91.5, "csv")
    insert_brew(conn, 2, "latte", "2026-06-02 10:00:00", 44.0, 88.0, "csv")
    insert_brew(conn, 3, "lungo", "2026-06-03 11:00:00", 38.0, 90.0, "manual")
    conn.commit()

    stats = get_stats(conn, start="2026-06-01", end="2026-06-02")
    assert stats["total_brews"] == 3
    assert stats["start"] == "2026-06-01"
    assert stats["end"] == "2026-06-02"
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-06-01": 2, "2026-06-02": 1}


def test_stats_start_only(conn):
    insert_brew(conn, 1, "espresso", "2026-05-31 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 2, "latte", "2026-06-02 10:00:00", 44.0, 88.0, "csv")
    conn.commit()

    stats = get_stats(conn, start="2026-06-01")
    assert stats["total_brews"] == 2
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-06-01": 1, "2026-06-02": 1}


def test_stats_end_only(conn):
    insert_brew(conn, 1, "espresso", "2026-05-31 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 2, "latte", "2026-06-02 10:00:00", 44.0, 88.0, "csv")
    conn.commit()

    stats = get_stats(conn, end="2026-06-01")
    assert stats["total_brews"] == 2
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-05-31": 1, "2026-06-01": 1}


def test_stats_boundary_inclusive(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 23:59:59", 27.0, 92.0, "csv")
    insert_brew(conn, 2, "latte", "2026-06-02 00:00:00", 44.0, 88.0, "csv")
    conn.commit()

    stats = get_stats(conn, start="2026-06-01", end="2026-06-01")
    assert stats["total_brews"] == 1
    assert stats["per_day"][0]["day"] == "2026-06-01"
    assert stats["per_day"][0]["count"] == 1


def test_stats_empty_range(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 2, "latte", "2026-06-02 10:00:00", 44.0, 88.0, "csv")
    conn.commit()

    stats = get_stats(conn, start="2026-06-05", end="2026-06-10")
    assert stats["total_brews"] == 0
    assert all(d["count"] == 0 for d in stats["per_drink"])
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert all(c == 0 for c in per_day.values())
    assert len(per_day) == 6


def test_stats_zero_fill(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-03 09:00:00", 26.0, 91.5, "csv")
    conn.commit()

    stats = get_stats(conn, start="2026-06-01", end="2026-06-03")
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-06-01": 1, "2026-06-02": 0, "2026-06-03": 1}


def test_stats_zero_fill_large_range_not_filled(conn):
    insert_brew(conn, 1, "espresso", "1900-01-01 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2100-01-01 09:00:00", 26.0, 91.5, "csv")
    conn.commit()

    stats = get_stats(conn, start="1900-01-01", end="2100-01-01")
    assert len(stats["per_day"]) == 2
    assert stats["per_day"][0]["day"] == "1900-01-01"
    assert stats["per_day"][1]["day"] == "2100-01-01"


def test_stats_all_drinks_present_in_range(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    conn.commit()

    stats = get_stats(conn, start="2026-06-01", end="2026-06-02")
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["espresso"] == 1
    assert per_drink["cappuccino"] == 0


def test_machine_health_with_date_range(conn):
    insert_brew(conn, 4, "espresso", "2026-05-31 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 4, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 4, "espresso", "2026-06-02 09:00:00", 26.0, 91.5, "csv")
    insert_maintenance(conn, 4, "descale", "2026-06-03 18:00:00", note="quarterly descale")
    insert_maintenance(conn, 4, "error", "2026-06-04 09:15:00", error_code="E42")
    conn.commit()

    health = get_machine_health(conn, 4, start="2026-06-01", end="2026-06-02")
    assert health["brew_count"] == 2
    assert health["last_brew"] == "2026-06-02 09:00:00"
    assert health["last_maintenance"]["type"] == "descale"
    assert health["recent_errors"][0]["error_code"] == "E42"


def test_machine_health_maintenance_not_filtered(conn):
    insert_brew(conn, 4, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_maintenance(conn, 4, "descale", "2026-06-10 18:00:00", note="quarterly")
    conn.commit()

    health = get_machine_health(conn, 4, start="2026-06-01", end="2026-06-05")
    assert health["brew_count"] == 1
    assert health["last_maintenance"]["type"] == "descale"
