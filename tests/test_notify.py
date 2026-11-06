import pytest

from dfs import notify


def test_no_topic_configured_returns_failure_without_calling_out(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    calls = []
    out = notify.push("Title", "body", post=lambda *a, **k: calls.append(a))
    assert out["ok"] is False
    assert "NTFY_TOPIC" in out["error"]
    assert calls == []


def test_posts_to_the_topic_with_title_and_priority(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "secret-topic")
    seen = {}

    def fake_post(url, data, headers):
        seen.update(url=url, data=data, headers=headers)

    out = notify.push("Lineups ready", "4 flags", priority="high", tags="warning", post=fake_post)
    assert out["ok"] is True
    assert seen["url"] == "https://ntfy.sh/secret-topic"
    assert seen["headers"]["Title"] == "Lineups ready"
    assert seen["headers"]["Priority"] == "high"
    assert seen["headers"]["Tags"] == "warning"
    assert seen["data"] == b"4 flags"


def test_non_ascii_body_is_utf8_encoded(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    seen = {}
    notify.push("Alert", "Nikola Jokic OUT", post=lambda u, d, h: seen.update(data=d))
    assert seen["data"] == "Nikola Jokic OUT".encode("utf-8")


def test_transport_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")

    def boom(url, data, headers):
        raise OSError("connection refused")

    out = notify.push("T", "b", post=boom)
    assert out["ok"] is False
    assert "connection refused" in out["error"]


def test_custom_server_is_honored(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    monkeypatch.setenv("NTFY_SERVER", "https://ntfy.example.com/")
    seen = {}
    notify.push("T", "b", post=lambda u, d, h: seen.update(url=u))
    assert seen["url"] == "https://ntfy.example.com/t"
