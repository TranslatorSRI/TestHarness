"""--trapi_version auto: each run follows its environment's TRAPI version."""

import logging

import httpx
import pytest

from test_harness.main import _trapi_change, _trapi_version, resolve_trapi_version
from test_harness.runner.smart_api_registry import registered_trapi_versions


def _hit(infores, version, *maturities):
    return {
        "info": {
            "x-translator": {"infores": infores, "component": "ARS"},
            "x-trapi": {"version": version},
        },
        "servers": [
            {"url": f"https://{m}.example", "x-maturity": m} for m in maturities
        ],
    }


HITS = [
    _hit("infores:ars", "1.6.0", "production", "testing"),
    _hit("infores:ars", "2.0.0", "development", "staging", "testing"),
    _hit("infores:aragorn", "1.6.0", "staging"),
]


def test_registered_versions_for_one_service_and_maturity():
    assert registered_trapi_versions("infores:ars", "production", HITS) == ["1.6.0"]
    assert registered_trapi_versions("infores:ars", "staging", HITS) == ["2.0.0"]
    # mid-rollout, registered twice: newest first
    assert registered_trapi_versions("infores:ars", "testing", HITS) == [
        "2.0.0",
        "1.6.0",
    ]
    assert registered_trapi_versions("infores:aragorn", "staging", HITS) == ["1.6.0"]
    assert registered_trapi_versions("infores:ars", "nowhere", HITS) == []


@pytest.fixture
def registry(monkeypatch):
    def fake(infores, maturity, hits=None):
        return registered_trapi_versions(infores, maturity, HITS)

    monkeypatch.setattr("test_harness.main.registered_trapi_versions", fake)


@pytest.mark.parametrize(
    "env, target, expected",
    [
        ("prod", None, "1.6.0"),
        ("ci", None, "2.0.0"),
        ("test", None, "2.0.0"),  # both registered: the newest
        ("ci", "aragorn", "1.6.0"),  # --target: that service's registration
        ("ci", "infores:aragorn", "1.6.0"),
    ],
)
def test_auto_follows_the_environment(registry, caplog, env, target, expected):
    caplog.set_level(logging.INFO)
    args = {"trapi_version": "auto", "target": target}
    assert resolve_trapi_version(args, env, logging.getLogger("t")) == expected
    assert f"TRAPI {expected}: what" in caplog.text


def test_auto_falls_back_to_the_default(registry, caplog):
    args = {"trapi_version": "auto", "target": "unknown-service"}
    assert resolve_trapi_version(args, "ci", logging.getLogger("t")) == "2.0.0"
    assert "isn't registered in SmartAPI for ci (staging)" in caplog.text


def test_auto_falls_back_when_smartapi_cant_be_read(monkeypatch, caplog):
    def down(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr("test_harness.main.registered_trapi_versions", down)
    args = {"trapi_version": "auto"}
    assert resolve_trapi_version(args, "ci", logging.getLogger("t")) == "2.0.0"
    assert "couldn't read the SmartAPI registry" in caplog.text


def test_unsupported_registered_versions_are_skipped(monkeypatch):
    monkeypatch.setattr(
        "test_harness.main.registered_trapi_versions",
        lambda infores, maturity: ["1.5.0", "1.6.0"],
    )
    args = {"trapi_version": "auto"}
    assert resolve_trapi_version(args, "ci", logging.getLogger("t")) == "1.6.0"


def test_a_chosen_version_is_used_as_given(monkeypatch):
    monkeypatch.setattr(
        "test_harness.main.registered_trapi_versions",
        lambda *a: pytest.fail("no lookup for an explicit version"),
    )
    args = {"trapi_version": "1.6.0"}
    assert resolve_trapi_version(args, "ci", logging.getLogger("t")) == "1.6.0"


def test_the_cli_accepts_auto():
    assert _trapi_version("auto") == "auto"
    assert _trapi_version("1.6.0") == "1.6.0"


def test_trapi_change():
    assert _trapi_change("1.6.0", "2.0.0") == ("1.6", "2.0")
    assert _trapi_change("2.0.0", "2.0.1") is None
    assert _trapi_change(None, "2.0.0") is None


def test_auto_falls_back_when_smartapi_answers_garbage(monkeypatch, caplog):
    def html(*args, **kwargs):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr("test_harness.main.registered_trapi_versions", html)
    args = {"trapi_version": "auto"}
    assert resolve_trapi_version(args, "ci", logging.getLogger("t")) == "2.0.0"
    assert "couldn't read the SmartAPI registry" in caplog.text
