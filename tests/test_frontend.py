"""Frontend-adjacent checks: the app serves its own static files."""

import pytest

from brewops.db.connection import connect
from brewops.db.schema import init_db

from asgi_client import request


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "static-test.db"
    monkeypatch.setenv("BREWOPS_DB", str(path))
    conn = connect(path)
    init_db(conn)
    conn.commit()
    yield conn
    conn.close()


def get_app():
    # imported lazily so the static mount reflects the frontend directory
    from brewops.api.main import app
    return app


def test_index_served(db):
    r = request(get_app(), "GET", "/")
    assert r.status == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Log a brew" in r.text
    assert "Log maintenance" in r.text


def test_static_assets_served(db):
    js = request(get_app(), "GET", "/app.js")
    assert js.status == 200
    assert "loadDashboard" in js.text
    css = request(get_app(), "GET", "/style.css")
    assert css.status == 200
    assert "bar-fill" in css.text


def test_frontend_has_no_external_resources():
    # the SVG namespace is an identifier, not a fetched resource
    allowed = ("http://www.w3.org/2000/svg", "http://localhost")
    from brewops.api.main import FRONTEND_DIR
    for f in FRONTEND_DIR.iterdir():
        content = f.read_text(encoding="utf-8").lower()
        for a in allowed:
            content = content.replace(a, "")
        assert "http://" not in content, f.name
        assert "https://" not in content, f.name


def test_filter_bar_in_html(db):
    r = request(get_app(), "GET", "/")
    assert r.status == 200
    assert 'id="filter-start"' in r.text
    assert 'id="filter-end"' in r.text
    assert 'class="preset"' in r.text
    assert 'data-days="7"' in r.text
    assert 'data-days="30"' in r.text
    assert 'data-days="all"' in r.text
    assert 'id="filter-message"' in r.text
    assert 'id="total-label"' in r.text
    assert 'id="last-day-label"' in r.text


def test_export_csv_link_in_html(db):
    r = request(get_app(), "GET", "/")
    assert r.status == 200
    assert 'id="export-csv"' in r.text
    assert 'href="/api/brews/export.csv"' in r.text
    assert r.text.count('class="preset"') == 3
