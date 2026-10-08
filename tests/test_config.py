from dataclasses import replace

import pytest

from app.config import ConfigError, Settings


def base(**overrides) -> Settings:
    settings = replace(
        Settings.from_env(), app_env="production", github_webhook_secret="s3cr3t-value",
        api_admin_token="admin-token", github_token=None, github_write_enabled=False,
    )
    return replace(settings, **overrides)


def test_valid_production_settings_pass():
    base().validate()


@pytest.mark.parametrize("secret", ["", "change-me"])
def test_production_rejects_missing_or_placeholder_webhook_secret(secret):
    with pytest.raises(ConfigError, match="GITHUB_WEBHOOK_SECRET"):
        base(github_webhook_secret=secret).validate()


def test_production_requires_admin_token():
    with pytest.raises(ConfigError, match="API_ADMIN_TOKEN"):
        base(api_admin_token=None).validate()


def test_write_enabled_requires_token_in_any_env():
    with pytest.raises(ConfigError, match="GITHUB_TOKEN"):
        base(app_env="development", github_write_enabled=True).validate()


def test_development_allows_missing_secrets():
    base(app_env="development", github_webhook_secret="", api_admin_token=None).validate()
