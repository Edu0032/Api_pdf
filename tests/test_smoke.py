from app.main import health


def test_health():
    r = health()
    assert r["ok"] is True
