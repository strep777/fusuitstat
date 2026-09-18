"""密钥派生、手机号规范化/HMAC 查找、PII 加密、令牌与常量时间比较。

安全约束（05/10 文档）：
- 手机号规范化为 E.164；保存随机化加密密文用于通知，另用独立密钥 HMAC 的
  ``phone_lookup`` 唯一索引查找；不可用普通 SHA256 对低熵手机号脱敏；
- 密钥有 ``key_version``，加密与查找密钥分离；
- 验证码使用服务端秘密参与的 HMAC，校验使用常量时间比较。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.platform.config import get_settings
from app.platform.errors import AppError

_password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)

# 中国大陆与北美常用区号；其他区号按 E.164 原样接受（C05 首发支持美国与中国）。
_CN_PREFIX = "+86"
_US_PREFIX = "+1"


def normalize_phone(raw: str) -> str:
    """把用户输入规范化为 E.164，并按区号做本地校验（C02/C24：中国大陆 + 美国首发）。

    接受 ``13800000000``、``+8613800000000``、``8613800000000``、``(415) 555-2671``、
    ``+1 415 555 2671``、``1 415 555 2671`` 等形式；无法判定时按默认区号补全。

    判定顺序按**国家码优先**，避免把 ``1`` 开头的美国号码当成中国大陆号码：

    1. 显式 ``+`` 前缀 → 直接用；
    2. 以 ``1`` 开头且第二位是 ``3``–``9``（中国大陆手机号段）且总长 11 → ``+86``；
    3. 以 ``1`` 开头且剩余 10 位是合法 NANP 号码 → ``+1``（例如 ``14155552671``）；
    4. 10 位且是合法 NANP 号码 → ``+1``；11 位且以 ``1`` 开头 → ``+86``（例如 ``13800000000``）。
    """
    settings = get_settings()
    text = (raw or "").strip()
    keep = "".join(ch for ch in text if ch.isdigit() or ch == "+")
    if not keep:
        raise AppError("VALIDATION_ERROR", "请填写手机号。", fields={"phone": "必填"})
    has_plus = keep.startswith("+")
    digits = keep.lstrip("+")
    if not digits.isdigit():
        raise AppError("VALIDATION_ERROR", "手机号只能包含数字。", fields={"phone": "格式不正确"})

    if has_plus:
        normalized = f"+{digits}"
    elif _is_cn_mobile(digits):
        normalized = f"{_CN_PREFIX}{digits}"
    elif digits.startswith("1") and _is_nanp(digits[1:]):
        normalized = f"{_US_PREFIX}{digits[1:]}"
    elif _is_nanp(digits):
        normalized = f"{_US_PREFIX}{digits}"
    elif len(digits) == 11 and digits.startswith("1"):
        normalized = f"{_CN_PREFIX}{digits}"
    elif digits.startswith("86") and len(digits) == 13:
        normalized = f"+{digits}"
    else:
        normalized = f"+{digits}"

    body = normalized[1:]
    if not (7 <= len(body) <= 15):
        raise AppError("VALIDATION_ERROR", "手机号位数不正确。", fields={"phone": "位数不正确"})

    region = _region_of(normalized)
    allowed = settings.allowed_sms_regions
    if allowed and region not in allowed:
        raise AppError(
            "VALIDATION_ERROR",
            f"当前只支持这些地区的手机号：{'、'.join(allowed)}。",
            fields={"phone": f"不支持的地区：{region}"},
        )
    if region == "CN" and not _is_cn_mobile(normalized[len(_CN_PREFIX) :]):
        raise AppError(
            "VALIDATION_ERROR", "中国大陆手机号格式不正确。", fields={"phone": "格式不正确"}
        )
    if region == "US" and not _is_nanp(normalized[len(_US_PREFIX) :]):
        raise AppError(
            "VALIDATION_ERROR", "美国手机号格式不正确。", fields={"phone": "格式不正确"}
        )
    return normalized


def _is_cn_mobile(digits: str) -> bool:
    """中国大陆手机号：11 位、以 ``1`` 开头，且号段符合已分配的移动前缀。

    第二位取 3/5/6/7/8/9；``14x`` 只在第三位为 5–9 时算移动号段（145/146–149）。
    这样 ``14155552671``（美国 ``1`` + 区号 ``415``）不会被误判成中国大陆号码。
    """
    if len(digits) != 11 or not digits.startswith("1"):
        return False
    second = digits[1]
    if second in "356789":
        return True
    return second == "4" and digits[2] in "56789"


def _is_nanp(digits: str) -> bool:
    """北美编号计划（NANP）：10 位，区号与局号首位均不能是 0 或 1。"""
    if len(digits) != 10 or not digits.isdigit():
        return False
    return digits[0] in "23456789" and digits[3] in "23456789"


def _region_of(phone_e164: str) -> str:
    """按 E.164 前缀判断地区；用于区号白名单与本地格式校验。"""
    if phone_e164.startswith(_CN_PREFIX):
        return "CN"
    if phone_e164.startswith(_US_PREFIX):
        return "US"
    return "OTHER"


def mask_phone(phone_e164: str) -> str:
    """脱敏展示。日志与后台列表只允许出现该形式（保留区号与末四位）。"""
    digits = phone_e164.lstrip("+")
    if len(digits) <= 7:
        return f"{phone_e164[:2]}****"
    country_len = 2 if digits.startswith("86") else 1
    country = f"+{digits[:country_len]}"
    local = digits[country_len:]
    if len(local) <= 4:
        return f"{country}****{local}"
    hidden = max(1, len(local) - 8)
    return f"{country}{local[:4]}{'*' * hidden}{local[-4:]}"


def phone_lookup(phone_e164: str) -> str:
    """独立查找密钥的 HMAC，用于唯一索引。"""
    key = get_settings().secret("phone_lookup_key").encode("utf-8")
    return hmac.new(key, phone_e164.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_token(token: str) -> str:
    """会话/一次性令牌哈希（令牌本身高熵，普通 SHA256 足够）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hmac_code(*parts: str, namespace: str = "otp") -> str:
    """验证码 HMAC：包含 challenge_id、用途与手机号查找值，不保存明文。"""
    key = get_settings().secret("otp_secret").encode("utf-8")
    message = "|".join([namespace, *parts]).encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _aead() -> tuple[AESGCM, str]:
    settings = get_settings()
    key_material = settings.secret("pii_encryption_key")
    key = hashlib.sha256(f"starium-pii::{key_material}".encode()).digest()
    return AESGCM(key), settings.pii_encryption_key_version


def encrypt_pii(plaintext: str) -> str:
    """AES-256-GCM 随机 nonce 加密，输出 ``v1:base64(nonce|ciphertext)``。"""
    aead, version = _aead()
    nonce = secrets.token_bytes(12)
    ciphertext = aead.encrypt(nonce, plaintext.encode("utf-8"), None)
    return f"{version}:{base64.urlsafe_b64encode(nonce + ciphertext).decode('ascii')}"


def decrypt_pii(payload: str) -> str:
    """解密。轮换时通过前缀 ``key_version`` 选择旧密钥（当前实现单版本）。"""
    if not payload:
        return ""
    try:
        version, encoded = payload.split(":", 1)
        _ = version
        raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
        aead, _current = _aead()
        return aead.decrypt(raw[:12], raw[12:], None).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - 解密失败一律视为不可用，不泄露细节
        raise AppError("INTERNAL_ERROR", "加密数据无法读取。") from exc


def hash_password(password: str) -> str:
    """员工后台口令：Argon2id。"""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:  # noqa: BLE001
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except Exception:  # noqa: BLE001
        return True


def new_csrf_secret() -> str:
    return secrets.token_urlsafe(32)


def issue_session_token() -> str:
    """至少 256 位随机不透明 session token。"""
    return secrets.token_urlsafe(32)
