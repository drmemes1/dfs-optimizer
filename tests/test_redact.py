"""Credentials must never reach job_runs or an HTTP response. The spec promises a test asserts
this; this is it, plus the scrubber it relies on."""
import pytest

from dfs import redact

SECRETS = ["RG_PASS", "RG_COOKIE", "RESEND_API_KEY", "NTFY_TOPIC"]


@pytest.mark.parametrize("var", SECRETS)
def test_every_secret_env_var_is_scrubbed(monkeypatch, var):
    monkeypatch.setenv(var, "sentinel-value-12345")
    assert "sentinel-value-12345" not in redact.scrub("boom: sentinel-value-12345 happened")


def test_all_known_secret_vars_are_covered():
    assert set(SECRETS) <= set(redact.SECRET_ENV_VARS)


def test_unset_variables_are_ignored(monkeypatch):
    for v in redact.SECRET_ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    assert redact.scrub("nothing to hide") == "nothing to hide"


def test_short_values_are_left_alone_so_messages_are_not_corrupted(monkeypatch):
    monkeypatch.setenv("NTFY_TOPIC", "t")
    assert redact.scrub("connection refused") == "connection refused"


def test_multiple_secrets_are_all_removed(monkeypatch):
    monkeypatch.setenv("RG_PASS", "password-aaaaaaa")
    monkeypatch.setenv("RESEND_API_KEY", "re_bbbbbbbbbbbb")
    out = redact.scrub("password-aaaaaaa and re_bbbbbbbbbbbb")
    assert "password-aaaaaaa" not in out and "re_bbbbbbbbbbbb" not in out
