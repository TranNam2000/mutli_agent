"""Tests for core.config — env var helpers with defaults."""
import pytest

from core import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Clear MULTI_AGENT_* env vars so tests see defaults."""
    import os
    for k in list(os.environ):
        if k.startswith("MULTI_AGENT_"):
            monkeypatch.delenv(k, raising=False)


def test_defaults_honoured():
    assert config.get("MULTI_AGENT_LEARNING_MODE") == "propose"
    assert config.get_bool("MULTI_AGENT_AUTO_COMMIT") is True
    assert config.get_int("MULTI_AGENT_MAX_CONCURRENT") == 3


def test_bool_truthy_variants(monkeypatch):
    for val in ("1", "true", "True", "yes", "ON"):
        monkeypatch.setenv("MULTI_AGENT_DEBUG", val)
        assert config.get_bool("MULTI_AGENT_DEBUG") is True


def test_bool_falsy_variants(monkeypatch):
    for val in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("MULTI_AGENT_DEBUG", val)
        assert config.get_bool("MULTI_AGENT_DEBUG") is False


def test_get_int_clamps(monkeypatch):
    monkeypatch.setenv("MULTI_AGENT_MAX_CONCURRENT", "100")
    assert config.get_int("MULTI_AGENT_MAX_CONCURRENT", min_value=1, max_value=16) == 16
    monkeypatch.setenv("MULTI_AGENT_MAX_CONCURRENT", "-5")
    assert config.get_int("MULTI_AGENT_MAX_CONCURRENT", min_value=1, max_value=16) == 1


def test_get_int_falls_back_to_default_on_garbage(monkeypatch):
    monkeypatch.setenv("MULTI_AGENT_MAX_CONCURRENT", "NaN")
    assert config.get_int("MULTI_AGENT_MAX_CONCURRENT") == 3   # the registered default


def test_learning_mode_auto_legacy(monkeypatch):
    monkeypatch.setenv("MULTI_AGENT_LEARNING_AUTO", "1")
    assert config.get_learning_mode() == "auto"


def test_learning_mode_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("MULTI_AGENT_LEARNING_MODE", "weird")
    assert config.get_learning_mode() == "propose"
