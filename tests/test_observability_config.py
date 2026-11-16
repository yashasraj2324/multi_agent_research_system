from research_system.observability.config import ObservabilityConfig


def test_logfire_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SEND_TO_LOGFIRE", raising=False)
    assert ObservabilityConfig().send_to_logfire is False


def test_logfire_can_be_enabled_by_env(monkeypatch):
    monkeypatch.setenv("SEND_TO_LOGFIRE", "true")
    assert ObservabilityConfig().send_to_logfire is True
