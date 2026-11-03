"""开发环境密钥持久化：重启后仍能解密本地演示库的 PII/MFA 密文。"""

from __future__ import annotations

from app.platform.config import Settings


def test_development_secret_persists_in_data_dir(tmp_path):
    first = Settings(app_env="development", data_dir=tmp_path)
    value = first.secret("pii_encryption_key")
    path = tmp_path / ".dev-secrets.json"

    assert path.exists()
    assert len(value) >= 32
    assert value in path.read_text(encoding="utf-8")

    second = Settings(app_env="development", data_dir=tmp_path)
    assert second.secret("pii_encryption_key") == value


def test_test_environment_does_not_write_secret_file(tmp_path):
    settings = Settings(app_env="test", data_dir=tmp_path)
    assert len(settings.secret("session_secret")) >= 32
    assert not (tmp_path / ".dev-secrets.json").exists()
