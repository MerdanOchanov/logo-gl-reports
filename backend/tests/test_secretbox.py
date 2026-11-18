"""T7: хранение пароля подключения (DPAPI / plain1)."""
from __future__ import annotations

import logging
import sys

import pytest

from app import secretbox

win = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI только на Windows")
PASSWORDS = ["abc123", "Пароль-ёЁ-çğış", "p;a{s}s=word;{}=", "  пробелы  ", "x" * 500]


@win
@pytest.mark.parametrize("scope,tag", [("user", "dpapi1:u:"), ("machine", "dpapi1:m:")])
@pytest.mark.parametrize("pw", PASSWORDS)
def test_roundtrip(scope, tag, pw):
    s = secretbox.protect(pw, scope)
    assert s.startswith(tag)
    assert pw not in s
    assert secretbox.unprotect(s) == pw
    assert secretbox.describe(s)["kind"] == ("dpapi_user" if scope == "user" else "dpapi_machine")
    assert not secretbox.is_plain(s)


def test_empty():
    assert secretbox.protect("") == ""
    assert secretbox.protect("", "machine") == ""
    assert secretbox.unprotect("") == ""
    assert secretbox.describe("") == {"kind": "none"}
    assert not secretbox.is_plain("")


@win
def test_corrupt_base64():
    for bad in ("dpapi1:u:!!!не-base64", "dpapi1:m:AAAA", "dpapi1:u:" + "QQ==" * 4):
        with pytest.raises(secretbox.SecretError) as e:
            secretbox.unprotect(bad)
        assert "другой учётной записью Windows" in str(e.value)


def test_unknown_format():
    with pytest.raises(secretbox.SecretError):
        secretbox.unprotect("rot13:abc")


def test_plain_fallback_without_windll(monkeypatch, caplog):
    monkeypatch.setattr(secretbox, "_dpapi_available", lambda: False)
    pw = "СекретныйПароль;{}="
    with caplog.at_level(logging.WARNING, logger="buhlogo"):
        s = secretbox.protect(pw)
    assert s == "plain1:" + pw
    assert secretbox.is_plain(s) and secretbox.describe(s)["kind"] == "plain"
    assert secretbox.unprotect(s) == pw
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert pw not in caplog.text


def test_plain_fallback_on_failure(monkeypatch, caplog):
    monkeypatch.setattr(secretbox, "_dpapi_available", lambda: True)

    def boom(*a, **k):
        raise OSError("fail")
    monkeypatch.setattr(secretbox, "_dpapi", boom)
    pw = "TopSecret!"
    with caplog.at_level(logging.WARNING, logger="buhlogo"):
        assert secretbox.protect(pw) == "plain1:" + pw
    assert pw not in caplog.text


def test_dpapi_blob_without_dpapi(monkeypatch):
    monkeypatch.setattr(secretbox, "_dpapi_available", lambda: False)
    with pytest.raises(secretbox.SecretError):
        secretbox.unprotect("dpapi1:u:QUJD")


@win
def test_no_password_in_log(caplog):
    pw = "НикогдаВЛог123"
    with caplog.at_level(logging.DEBUG):
        s = secretbox.protect(pw)
        secretbox.unprotect(s)
        with pytest.raises(secretbox.SecretError) as e:
            secretbox.unprotect("dpapi1:u:QUJD")
    assert pw not in caplog.text and pw not in str(e.value)
