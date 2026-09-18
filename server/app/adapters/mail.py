"""邮件适配器：sandbox / SMTP。

C27：邮件为必选通知通道。开发环境写入 ``storage/sandbox/mail.log``；
生产必须配置 SMTP，否则启动拒绝。
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from typing import Protocol

from app.adapters.base import GatewayError, GatewayNotConfigured, GatewayResult
from app.platform.config import Settings, get_settings
from app.platform.ids import now_ms


class MailGateway(Protocol):
    name: str

    def send(self, *, to: str, subject: str, body: str) -> GatewayResult: ...


class SandboxMailGateway:
    name = "sandbox"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._path = settings.storage_root / "sandbox" / "mail.log"

    def send(self, *, to: str, subject: str, body: str) -> GatewayResult:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        stamp = now_ms()
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(f"--- {stamp} to={to} subject={subject} ---\n{body}\n")
        return GatewayResult(provider=self.name, message_id=f"mail-{stamp}", detail={"sandbox": "true"})


class SmtpMailGateway:
    name = "smtp"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, *, to: str, subject: str, body: str) -> GatewayResult:
        host = self._settings.smtp_host
        if not host:
            raise GatewayNotConfigured("smtp")
        message = EmailMessage()
        message["From"] = self._settings.sms_sender_email
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(host, self._settings.smtp_port, timeout=10) as client:
                if self._settings.smtp_starttls:
                    client.starttls()
                if self._settings.smtp_username:
                    client.login(self._settings.smtp_username, self._settings.smtp_password)
                client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise GatewayError(f"smtp failed: {type(exc).__name__}") from exc
        return GatewayResult(provider=self.name, message_id=f"smtp-{now_ms()}")


_gateway: MailGateway | None = None


def get_gateway() -> MailGateway:
    global _gateway
    if _gateway is None:
        settings = get_settings()
        if settings.app_env in ("development", "test"):
            _gateway = SandboxMailGateway(settings)
        elif settings.smtp_host:
            _gateway = SmtpMailGateway(settings)
        else:
            raise GatewayNotConfigured("smtp")
    return _gateway


def reset_gateway() -> None:
    global _gateway
    _gateway = None
