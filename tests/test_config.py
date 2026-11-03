import pytest

from app.config import ConfigError, Settings
from app.main import create_app


def test_from_env_reads_and_splits_rotating_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/app")
    monkeypatch.setenv("WEBHOOK_SIGNING_SECRET", "new-secret, old-secret")
    monkeypatch.setenv("COMPONENT", "webhooks")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "7")

    settings = Settings.from_env()

    assert settings.webhook_signing_secrets == ("new-secret", "old-secret")
    assert settings.component == "webhooks"
    assert settings.db_pool_max_size == 7
    assert settings.database_read_url is None


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        Settings.from_env()


def test_invalid_numbers_fail_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/app")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "lots")
    with pytest.raises(ConfigError, match="DB_POOL_MAX_SIZE"):
        Settings.from_env()


def test_webhook_pods_refuse_to_start_without_a_signing_secret() -> None:
    with pytest.raises(ConfigError, match="WEBHOOK_SIGNING_SECRET"):
        create_app(Settings(database_url="postgresql://db/app", component="webhooks"))


def test_api_pods_do_not_need_a_signing_secret() -> None:
    create_app(Settings(database_url="postgresql://db/app", component="api"))
