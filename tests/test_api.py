import pytest

from brewops.api.main import app
from brewops.db.connection import connect
from brewops.db.queries import insert_brew, insert_maintenance
from brewops.db.schema import init_db

from asgi_client import request


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "api-test.db"
    monkeypatch.setenv("BREWOPS_DB", str(path))
    conn = connect(path)
    init_db(conn)
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.5, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-01 09:00:00", 26.0, 91.5, "csv")
    insert_brew(conn, 2, "latte", "2026-06-02 10:00:00", 44.0, 88.0, "csv")
    insert_maintenance(conn, 1, "descale", "2026-06-03 18:00:00", note="quarterly")
    conn.commit()
    yield conn
    conn.close()


def test_stats(db):
    r = request(app, "GET", "/api/stats")
    assert r.status == 200
    stats = r.json()
    assert stats["total_brews"] == 3
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["espresso"] == 2
    assert {d["day"]: d["count"] for d in stats["per_day"]} == {
        "2026-06-01": 2,
        "2026-06-02": 1,
    }


def test_machines_list_and_health(db):
    r = request(app, "GET", "/api/machines")
    assert r.status == 200
    assert len(r.json()) == 4

    r = request(app, "GET", "/api/machines/1")
    assert r.status == 200
    health = r.json()
    assert health["brew_count"] == 2
    assert health["last_maintenance"]["type"] == "descale"


def test_machine_health_404(db):
    r = request(app, "GET", "/api/machines/999")
    assert r.status == 404


def test_drink_types(db):
    r = request(app, "GET", "/api/drink-types")
    assert r.status == 200
    assert {d["name"] for d in r.json()} >= {"espresso", "latte", "cappuccino"}


def test_post_brew_ok_and_visible_in_stats(db):
    r = request(app, "POST", "/api/brews", {
        "machine_id": 3,
        "drink_type": "lungo",
        "timestamp": "2026-06-05 14:30:00",
    })
    assert r.status == 200, r.text
    assert r.json()["status"] == "logged"

    row = db.execute("SELECT source, duration_s FROM brew_events WHERE machine_id = 3").fetchone()
    assert row["source"] == "manual"
    assert row["duration_s"] is None

    stats = request(app, "GET", "/api/stats").json()
    assert stats["total_brews"] == 4


def test_post_brew_accepts_html_form_timestamp(db):
    r = request(app, "POST", "/api/brews", {
        "machine_id": 1,
        "drink_type": "espresso",
        "timestamp": "2026-06-05T14:30",
    })
    assert r.status == 200, r.text
    row = db.execute(
        "SELECT timestamp FROM brew_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["timestamp"] == "2026-06-05 14:30:00"


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ({"machine_id": 99, "drink_type": "espresso", "timestamp": "2026-06-05 14:30:00"}, "unknown machine"),
        ({"machine_id": 1, "drink_type": "unicorn_frappe", "timestamp": "2026-06-05 14:30:00"}, "unknown drink"),
        ({"machine_id": 1, "drink_type": "espresso", "timestamp": "2099-01-01 00:00:00"}, "future"),
        ({"machine_id": 1, "drink_type": "espresso", "timestamp": "yesterday-ish"}, "unparsable"),
    ],
)
def test_post_brew_validation(db, payload, fragment):
    r = request(app, "POST", "/api/brews", payload)
    assert r.status == 400
    assert fragment in r.json()["detail"]


def test_post_maintenance(db):
    r = request(app, "POST", "/api/maintenance", {
        "machine_id": 2,
        "type": "descale",
        "timestamp": "2026-06-06 09:00:00",
        "note": "smelled funny",
    })
    assert r.status == 200
    r = request(app, "POST", "/api/maintenance", {
        "machine_id": 2,
        "type": "exploded",
        "timestamp": "2026-06-06 09:00:00",
    })
    assert r.status == 400


def test_stats_with_date_range(db):
    r = request(app, "GET", "/api/stats?start=2026-06-01&end=2026-06-02")
    assert r.status == 200
    stats = r.json()
    assert stats["total_brews"] == 3
    assert stats["start"] == "2026-06-01"
    assert stats["end"] == "2026-06-02"


def test_stats_with_start_only(db):
    r = request(app, "GET", "/api/stats?start=2026-06-02")
    assert r.status == 200
    stats = r.json()
    assert stats["total_brews"] == 1
    assert stats["start"] == "2026-06-02"
    assert stats["end"] is None


def test_stats_with_end_only(db):
    r = request(app, "GET", "/api/stats?end=2026-06-01")
    assert r.status == 200
    stats = r.json()
    assert stats["total_brews"] == 2


def test_stats_start_after_end_error(db):
    r = request(app, "GET", "/api/stats?start=2026-06-05&end=2026-06-01")
    assert r.status == 400
    assert "start must not be after end" in r.json()["detail"]


def test_stats_malformed_date(db):
    r = request(app, "GET", "/api/stats?start=06/01/2026")
    assert r.status == 400
    assert "unparsable start" in r.json()["detail"]
    assert "expected YYYY-MM-DD" in r.json()["detail"]


def test_stats_empty_string_param(db):
    r = request(app, "GET", "/api/stats?start=&end=2026-06-01")
    assert r.status == 200
    stats = r.json()
    assert stats["total_brews"] == 2
    assert stats["start"] is None


def test_machine_health_with_date_range(db):
    r = request(app, "GET", "/api/machines/1?start=2026-06-01&end=2026-06-01")
    assert r.status == 200
    health = r.json()
    assert health["brew_count"] == 2


def test_machine_health_start_after_end_error(db):
    r = request(app, "GET", "/api/machines/1?start=2026-06-05&end=2026-06-01")
    assert r.status == 400
    assert "start must not be after end" in r.json()["detail"]


def test_machine_health_unknown_machine_with_range(db):
    r = request(app, "GET", "/api/machines/999?start=2026-06-01&end=2026-06-02")
    assert r.status == 404


def test_export_brews_csv_basic(db):
    r = request(app, "GET", "/api/brews/export.csv")
    assert r.status == 200
    assert r.headers.get("content-type", "").startswith("text/csv")
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "brews.csv" in r.headers.get("content-disposition", "")
    assert r.body.startswith(b"\xef\xbb\xbf")

    import csv, io
    content = r.body.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    assert rows[0] == ["timestamp", "machine", "drink", "duration_s", "temp_c", "source"]
    assert len(rows) == 4


def test_export_brews_csv_with_range(db):
    r = request(app, "GET", "/api/brews/export.csv?start=2026-06-02&end=2026-06-02")
    assert r.status == 200
    assert "brews_2026-06-02_to_2026-06-02.csv" in r.headers.get("content-disposition", "")

    import csv, io
    content = r.body.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) == 2


def test_export_brews_csv_empty_range(db):
    r = request(app, "GET", "/api/brews/export.csv?start=2030-01-01&end=2030-01-02")
    assert r.status == 200

    import csv, io
    content = r.body.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0] == ["timestamp", "machine", "drink", "duration_s", "temp_c", "source"]


def test_export_brews_csv_reversed_range(db):
    r = request(app, "GET", "/api/brews/export.csv?start=2026-06-05&end=2026-06-01")
    assert r.status == 400
    assert "start must not be after end" in r.json()["detail"]


def test_export_brews_csv_malformed_date(db):
    r = request(app, "GET", "/api/brews/export.csv?start=nope")
    assert r.status == 400


def test_export_brews_csv_start_only(db):
    r = request(app, "GET", "/api/brews/export.csv?start=2026-06-02")
    assert r.status == 200
    assert "brews_from_2026-06-02.csv" in r.headers.get("content-disposition", "")


def test_export_brews_csv_end_only(db):
    r = request(app, "GET", "/api/brews/export.csv?end=2026-06-01")
    assert r.status == 200
    assert "brews_until_2026-06-01.csv" in r.headers.get("content-disposition", "")
