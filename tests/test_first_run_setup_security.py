from __future__ import annotations

import importlib

import pytest
from fastapi import HTTPException


app_module = importlib.import_module("throughline_api.app")


class _Client:
    def __init__(self, host: str):
        self.host = host


class _Request:
    def __init__(self, host: str, headers: dict[str, str] | None = None):
        self.client = _Client(host)
        self.headers = headers or {}


def test_non_loopback_setup_needs_token_even_when_deployment_says_local(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "local")
    monkeypatch.delenv("THROUGHLINE_REMOTE_SETUP_TOKEN", raising=False)

    with pytest.raises(HTTPException) as raised:
        app_module._require_remote_setup_authority(_Request("192.168.1.42"))

    assert raised.value.status_code == 403
    assert "setup token" in str(raised.value.detail).lower()


def test_non_loopback_setup_refuses_wrong_token(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "local")
    monkeypatch.setenv("THROUGHLINE_REMOTE_SETUP_TOKEN", "correct-token")

    with pytest.raises(HTTPException) as raised:
        app_module._require_remote_setup_authority(
            _Request("172.17.0.1"), body_token="wrong-token"
        )

    assert raised.value.status_code == 403


def test_non_loopback_setup_accepts_operator_token_from_body(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_DEPLOYMENT", "local")
    monkeypatch.setenv("THROUGHLINE_REMOTE_SETUP_TOKEN", "correct-token")

    app_module._require_remote_setup_authority(
        _Request("172.17.0.1"), body_token="correct-token"
    )


def test_non_loopback_setup_still_accepts_legacy_header_token(monkeypatch):
    monkeypatch.setenv("THROUGHLINE_REMOTE_SETUP_TOKEN", "correct-token")

    app_module._require_remote_setup_authority(
        _Request(
            "10.0.0.8",
            headers={"x-throughline-setup-token": "correct-token"},
        )
    )


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_true_loopback_setup_needs_no_token(monkeypatch, host):
    monkeypatch.delenv("THROUGHLINE_REMOTE_SETUP_TOKEN", raising=False)
    app_module._require_remote_setup_authority(_Request(host))
