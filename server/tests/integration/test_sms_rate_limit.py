"""短信验证码多维限流的回归（10 文档 / NFR-06）。

审计关注「不能为了跑通测试而关掉校验」。e2e 需要大量登录，因此测试环境的 IP 限额
会被放宽（见 `e2e/playwright.config.ts` 的 webServer env）；本文件在**服务端**把
限流行为固定住，保证放宽的只是测试环境的配置值，不是校验逻辑本身。
"""
from __future__ import annotations

BASE = "/api/v1"
ORIGIN = "http://127.0.0.1:5173"


def _anonymous_csrf(client) -> str:
    response = client.get(f"{BASE}/session/csrf", headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _challenge(client, token: str, phone: str, *, purpose: str = "login"):
    return client.post(
        f"{BASE}/auth/sms/challenges",
        json={"phone": phone, "purpose": purpose},
        headers={"Origin": ORIGIN, "X-CSRF-Token": token},
    )


def test_same_phone_cannot_request_twice_within_resend_window(client, settings):
    """同手机号在 `otp_resend_seconds` 内只能请求一次，第二次必须 429 且给重试秒数。"""
    token = _anonymous_csrf(client)
    phone = "+8613800009001"

    first = _challenge(client, token, phone)
    assert first.status_code == 200, first.text
    assert "dev_hint" in first.json(), "非生产环境应返回 dev_hint 供联调"

    second = _challenge(client, token, phone)
    assert second.status_code == 429, second.text
    assert second.json()["error"]["code"] == "SMS_RATE_LIMITED"
    # 限流响应在响应体里给出可重试信息，前端不猜。
    assert settings.otp_resend_seconds > 0
    message = second.json()["error"]["message"]
    assert "秒" in message


def test_same_ip_hourly_limit_is_enforced(client, settings):
    """同 IP 一小时内超过 `otp_ip_hourly_limit` 次后必须被拒（即使手机号各不相同）。"""
    token = _anonymous_csrf(client)
    limit = settings.otp_ip_hourly_limit
    assert 0 < limit < 50, "默认 IP 限额应是一个有限的小值，便于验证"

    accepted = 0
    for index in range(limit + 2):
        response = _challenge(client, token, f"+861380001{index:04d}")
        if response.status_code == 200:
            accepted += 1
            continue
        assert response.status_code == 429, response.text
        assert response.json()["error"]["code"] == "SMS_RATE_LIMITED"
        break

    assert accepted <= limit, "不得超过 IP 小时限额"
    assert accepted >= 1, "限额内至少应放行一次"


def test_daily_send_budget_blocks_further_sends(client, settings, monkeypatch):
    """每日短信发送预算耗尽后必须拒绝新挑战，不能无限发送。

    预算熔断与用户限流是两件事：前者是工作室的成本熔断（``SMS_BUDGET_EXHAUSTED``，
    503 暂时不可用），后者是防刷限流（``SMS_RATE_LIMITED``，429）。两者都必须拒绝新发送。
    """
    monkeypatch.setattr(settings, "otp_daily_budget", 2, raising=False)
    monkeypatch.setattr(settings, "otp_phone_hourly_limit", 100, raising=False)
    monkeypatch.setattr(settings, "otp_phone_daily_limit", 100, raising=False)
    monkeypatch.setattr(settings, "otp_ip_hourly_limit", 100, raising=False)

    token = _anonymous_csrf(client)
    statuses: list[int] = []
    last_code = ""
    for index in range(3):
        response = _challenge(client, token, f"+861380002{index:04d}")
        statuses.append(response.status_code)
        if response.status_code != 200:
            last_code = response.json()["error"]["code"]

    assert 200 in statuses, f"预算内应有成功发送：{statuses}"
    assert statuses[-1] != 200, f"超出每日预算必须被拒：{statuses}"
    assert last_code == "SMS_BUDGET_EXHAUSTED", last_code
