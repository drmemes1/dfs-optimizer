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


# ---------- Resend email transport ----------

def test_resend_is_preferred_when_configured(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_abc123")
    monkeypatch.setenv("RESEND_TO", "someone@example.com")
    monkeypatch.setenv("NTFY_TOPIC", "also-set")
    seen = {}
    out = notify.push("Lineups ready", "body text", priority="high",
                      post=lambda url, data, headers: seen.update(url=url, data=data, headers=headers))
    assert out["ok"] is True
    assert seen["url"] == "https://api.resend.com/emails"
    assert seen["headers"]["Authorization"] == "Bearer re_abc123"
    assert seen["headers"]["Content-Type"] == "application/json"
    import json
    body = json.loads(seen["data"])
    assert body["to"] == ["someone@example.com"]
    assert body["subject"] == "Lineups ready"
    assert body["text"] == "body text"


def test_resend_from_defaults_but_is_overridable(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setenv("RESEND_TO", "a@b.c")
    import json
    seen = {}
    notify.push("T", "b", post=lambda u, d, h: seen.update(d=d))
    assert json.loads(seen["d"])["from"] == notify.DEFAULT_FROM
    monkeypatch.setenv("RESEND_FROM", "alerts@mydomain.com")
    notify.push("T", "b", post=lambda u, d, h: seen.update(d=d))
    assert json.loads(seen["d"])["from"] == "alerts@mydomain.com"


def test_resend_without_a_recipient_is_not_configured(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.delenv("RESEND_TO", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    calls = []
    out = notify.push("T", "b", post=lambda *a: calls.append(a))
    assert out["ok"] is False
    assert "RESEND_TO" in out["error"]
    assert calls == []


def test_non_ascii_names_survive_an_email_body(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setenv("RESEND_TO", "a@b.c")
    import json
    seen = {}
    notify.push("Alert", "Nikola Jokić OUT", post=lambda u, d, h: seen.update(d=d))
    assert json.loads(seen["d"])["text"] == "Nikola Jokić OUT"


def test_configured_is_true_for_either_transport(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_TO", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert notify.configured() is False
    monkeypatch.setenv("NTFY_TOPIC", "t")
    assert notify.configured() is True
    monkeypatch.delenv("NTFY_TOPIC")
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setenv("RESEND_TO", "a@b.c")
    assert notify.configured() is True


def test_nothing_configured_says_so_and_is_distinguishable(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    out = notify.push("T", "b", post=lambda *a: None)
    assert out["ok"] is False
    assert out["unconfigured"] is True, "callers must tell 'switched off' from 'broken'"


def test_a_real_failure_is_not_marked_unconfigured(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "k")
    monkeypatch.setenv("RESEND_TO", "a@b.c")

    def boom(url, data, headers):
        raise OSError("resend unreachable")

    out = notify.push("T", "b", post=boom)
    assert out["ok"] is False
    assert out.get("unconfigured") is not True


def test_the_api_key_never_appears_in_an_error_string(monkeypatch):
    """M4: the error is f-string'd from the exception, and a malformed endpoint or a transport
    that echoes its request would otherwise put the secret into job_runs and the cron response."""
    monkeypatch.setenv("RESEND_API_KEY", "re_SUPERSECRET")
    monkeypatch.setenv("RESEND_TO", "a@b.c")

    def boom(url, data, headers):
        raise ValueError(f"unknown url type: {headers['Authorization']} {url}")

    out = notify.push("T", "b", post=boom)
    assert out["ok"] is False
    assert "re_SUPERSECRET" not in out["error"]


def test_the_ntfy_topic_never_appears_in_an_error_string(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("NTFY_TOPIC", "topic-is-the-secret")
    monkeypatch.setenv("NTFY_SERVER", "not-a-url")

    def boom(url, data, headers):
        raise ValueError(f"unknown url type: {url}")

    out = notify.push("T", "b", post=boom)
    assert out["ok"] is False
    assert "topic-is-the-secret" not in out["error"]


def test_a_short_topic_does_not_corrupt_the_error_message(monkeypatch):
    """Blind substring replacement of a 1-char secret mangles unrelated words."""
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.setenv("NTFY_TOPIC", "t")

    def boom(url, data, headers):
        raise OSError("connection refused")

    assert "connection refused" in notify.push("T", "b", post=boom)["error"]
