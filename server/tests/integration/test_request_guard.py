"""请求守卫的行为测试：限额、UA、蜜罐、请求体、缓存与后台允许清单。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.platform.config import Settings
from app.platform.guard import RequestGuardMiddleware


def _guard_client(settings: Settings) -> TestClient:
    app = FastAPI()

    @app.get("/api/v1/ping")
    def ping() -> dict[str, str]:
        return {"ok": "1"}

    @app.post("/api/v1/ping")
    def echo() -> dict[str, str]:
        return {"ok": "1"}

    @app.get("/api/v1/products")
    def products() -> dict[str, str]:
        return {"items": "[]"}

    @app.get("/api/v1/admin/ping")
    def admin_ping() -> dict[str, str]:
        return {"ok": "1"}

    app.add_middleware(RequestGuardMiddleware, settings=settings)
    return TestClient(app)


@pytest.fixture()
def guard_settings() -> Settings:
    return Settings(
        app_env="test",
        security_guard_test_mode=True,
        rate_limit_global_per_minute=2,
        rate_limit_read_per_minute=2,
        rate_limit_write_per_minute=2,
        max_json_body_bytes=128,
        public_cache_seconds=15,
    )


def test_rate_limit_rejects_and_sets_headers(guard_settings: Settings) -> None:
    client = _guard_client(guard_settings)
    assert client.get("/api/v1/ping").status_code == 200
    assert client.get("/api/v1/ping").status_code == 200
    response = client.get("/api/v1/ping")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMITED"
    assert int(response.headers["Retry-After"]) >= 1
    assert response.headers["X-RateLimit-Limit"] == "2"
    assert response.headers["X-RateLimit-Remaining"] == "0"


def test_payload_limit_rejects_before_business(guard_settings: Settings) -> None:
    client = _guard_client(guard_settings)
    response = client.post("/api/v1/ping", content=b"x" * 256)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_crawler_and_empty_user_agent(guard_settings: Settings) -> None:
    guard_settings.crawler_block_enabled = True
    client = _guard_client(guard_settings)
    assert client.get("/api/v1/ping", headers={"User-Agent": "sqlmap/1.8"}).status_code == 403
    assert client.get("/api/v1/ping", headers={"User-Agent": ""}).status_code == 403
    assert client.get("/api/v1/ping", headers={"User-Agent": "Mozilla/5.0"}).status_code == 200


def test_honeypot_escalates(guard_settings: Settings) -> None:
    client = _guard_client(guard_settings)
    statuses = [client.get("/api/v1/_hp").status_code for _ in range(4)]
    assert statuses[:3] == [404, 404, 404]
    assert statuses[3] == 403
    assert client.get("/api/v1/_hp").json()["error"]["code"] == "ROBOT_DENIED"


def test_public_cache_only_without_cookie(guard_settings: Settings) -> None:
    client = _guard_client(guard_settings)
    anonymous = client.get("/api/v1/products")
    assert anonymous.headers["Cache-Control"].startswith("public")
    client.cookies.set("dsh_session", "fake")
    private = client.get("/api/v1/products")
    assert "public" not in private.headers.get("Cache-Control", "")


def test_admin_allowlist_blocks_unknown_client(guard_settings: Settings) -> None:
    guard_settings.admin_ip_allowlist = "10.0.0.0/8"
    client = _guard_client(guard_settings)
    response = client.get("/api/v1/admin/ping")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
