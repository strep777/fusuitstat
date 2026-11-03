"""安全响应头的回归测试（19 文档 SEC-08/10）。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_security_headers_on_api(client: TestClient) -> None:
    response = client.get("/api/v1/products")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    assert response.headers["Cross-Origin-Resource-Policy"] == "same-site"
    assert response.headers["X-Robots-Tag"] == "noindex, nofollow"
    assert response.headers["Cache-Control"] == "no-store"


def test_probe_does_not_leak(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert set(response.json()) == {"status", "time"}
