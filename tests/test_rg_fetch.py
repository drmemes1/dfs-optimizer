"""No network. A fake opener stands in for RotoGrinders so the login contract is pinned
without contacting the site."""
from datetime import datetime, timedelta, timezone

import pytest

from dfs.sources import rg_fetch

NOW = datetime(2026, 11, 5, 18, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, body, url):
        self._body = body.encode("utf-8")
        self._url = url
        self.headers = {}
        self.status = 200

    def read(self):
        return self._body

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Records requests; sets a cookie on a successful sign-in POST."""

    def __init__(self, good_credentials=True, table_rows=60):
        self.good = good_credentials
        self.rows = table_rows
        self.requests = []
        self.cookies = []

    def open(self, req, timeout=None):
        url = req.full_url
        self.requests.append((req.get_method(), url, req.data))
        if url.endswith("/sign-in"):
            if self.good:
                self.cookies = ["rguid=abc123", "rgref=x"]
                return FakeResponse("<html>home</html>", "https://rotogrinders.com/")
            return FakeResponse("<html>Invalid username or password</html>",
                                "https://rotogrinders.com/sign-in")
        rows = "".join(f"<tr><td>P{i}</td>{'<td>1</td>' * 46}</tr>" for i in range(self.rows))
        head = "<thead><tr>" + "".join(f"<th>C{i}</th>" for i in range(47)) + "</tr></thead>"
        return FakeResponse(f"<html><table>{head}{rows}</table></html>",
                            "https://rotogrinders.com/projected-stats/nba?site=draftkings")


def test_login_posts_username_and_password_to_sign_in(monkeypatch):
    monkeypatch.setenv("RG_USER", "someuser")
    monkeypatch.setenv("RG_PASS", "somepass")
    fake = FakeOpener()
    cookie = rg_fetch.login(opener_factory=lambda: fake)
    method, url, data = fake.requests[0]
    assert method == "POST"
    assert url == "https://rotogrinders.com/sign-in"
    assert b"username=someuser" in data and b"password=somepass" in data
    assert "rguid=" in cookie


def test_login_without_credentials_raises_without_contacting_the_site(monkeypatch):
    monkeypatch.delenv("RG_USER", raising=False)
    monkeypatch.delenv("RG_PASS", raising=False)
    fake = FakeOpener()
    with pytest.raises(rg_fetch.RGNotConnected, match="RG_USER"):
        rg_fetch.login(opener_factory=lambda: fake)
    assert fake.requests == []


def test_login_with_bad_credentials_raises_and_never_echoes_the_password(monkeypatch):
    monkeypatch.setenv("RG_USER", "someuser")
    monkeypatch.setenv("RG_PASS", "hunter2-secret")
    fake = FakeOpener(good_credentials=False)
    with pytest.raises(rg_fetch.RGNotConnected) as exc:
        rg_fetch.login(opener_factory=lambda: fake)
    assert "hunter2-secret" not in str(exc.value)


def test_backoff_refuses_to_attempt_after_repeated_failures(monkeypatch):
    """A revoked password must not be retried every five minutes all evening."""
    monkeypatch.setenv("RG_USER", "u")
    monkeypatch.setenv("RG_PASS", "p")
    monkeypatch.delenv("RG_COOKIE", raising=False)
    state = {"cookie": None, "acquired_at": None,
             "failures": rg_fetch.MAX_LOGIN_FAILURES,
             "last_failure_at": NOW - timedelta(minutes=5)}
    attempts = []

    with pytest.raises(rg_fetch.RGNotConnected, match="cooling off"):
        rg_fetch.session_cookie(_FakeConn(state), login_fn=lambda: attempts.append(1), now=NOW)
    assert attempts == []


def test_backoff_expires_after_the_cooldown(monkeypatch):
    monkeypatch.delenv("RG_COOKIE", raising=False)
    state = {"cookie": None, "acquired_at": None,
             "failures": rg_fetch.MAX_LOGIN_FAILURES,
             "last_failure_at": NOW - timedelta(minutes=rg_fetch.FAILURE_COOLDOWN_MINUTES + 1)}
    out = rg_fetch.session_cookie(_FakeConn(state), login_fn=lambda: "rguid=new", now=NOW)
    assert out == "rguid=new"
    assert state["failures"] == 0


def test_cached_cookie_is_reused_without_logging_in(monkeypatch):
    monkeypatch.delenv("RG_COOKIE", raising=False)
    state = {"cookie": "rguid=cached", "acquired_at": NOW - timedelta(days=2),
             "failures": 0, "last_failure_at": None}
    logins = []
    out = rg_fetch.session_cookie(_FakeConn(state), login_fn=lambda: logins.append(1), now=NOW)
    assert out == "rguid=cached"
    assert logins == []


def test_missing_cookie_triggers_exactly_one_login_and_is_stored(monkeypatch):
    monkeypatch.delenv("RG_COOKIE", raising=False)
    state = {"cookie": None, "acquired_at": None, "failures": 0, "last_failure_at": None}
    logins = []

    def fake_login():
        logins.append(1)
        return "rguid=fresh"

    out = rg_fetch.session_cookie(_FakeConn(state), login_fn=fake_login, now=NOW)
    assert out == "rguid=fresh"
    assert logins == [1]
    assert state["cookie"] == "rguid=fresh"


def test_rg_cookie_env_var_still_overrides_the_cached_session(monkeypatch):
    monkeypatch.setenv("RG_COOKIE", "rguid=manual")
    state = {"cookie": "rguid=cached", "acquired_at": NOW, "failures": 0, "last_failure_at": None}
    assert rg_fetch.session_cookie(_FakeConn(state), login_fn=lambda: "x", now=NOW) == "rguid=manual"


class _FakeConn:
    """Minimal stand-in for a psycopg connection over the single rg_session row."""

    def __init__(self, state):
        self.state = state

    def cursor(self):
        return _FakeCursor(self.state)

    def commit(self):
        pass


class _FakeCursor:
    def __init__(self, state):
        self.state = state
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split()).lower()
        if s.startswith("select"):
            self._last = dict(self.state)
        elif "failures = failures + 1" in s:
            self.state["failures"] = self.state.get("failures", 0) + 1
            self.state["last_failure_at"] = params[0] if params else None
        elif "failures = 0" in s:
            self.state.update(cookie=params[0], acquired_at=params[1] if len(params) > 1 else None,
                              failures=0, last_failure_at=None)
        elif "cookie = null" in s:
            self.state["cookie"] = None

    def fetchone(self):
        return self._last
