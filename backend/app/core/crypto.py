"""API Key 的加密存储与脱敏。

用 Fernet（对称加密：AES-128-CBC + HMAC-SHA256 认证），主密钥来自环境变量
`API_KEY_ENCRYPTION_KEY`，**不进数据库**。

设计要点：
- 数据库里只存密文（`api_key_encrypted`）；
- 同时存一份脱敏串（`api_key_masked`），这样列表接口**不需要解密**就能展示，
  即使主密钥没配也能正常列出供应商；
- 明文只在真正要调模型时才解密，且绝不进任何响应体。
"""

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings
from app.core.exceptions import AppError


class EncryptionError(AppError):
    """加解密失败。"""

    status_code = 500
    code = "ENCRYPTION_ERROR"
    message = "密钥加解密失败"


def _fernet() -> Fernet:
    key = settings.api_key_encryption_key.strip()
    if not key:
        raise EncryptionError(
            "未配置 API_KEY_ENCRYPTION_KEY，无法加解密 API Key。"
            "可用 `python scripts/gen_encryption_key.py` 生成一把并写进 .env"
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise EncryptionError("API_KEY_ENCRYPTION_KEY 不是合法的 Fernet 密钥") from exc


def encrypt_secret(plain: str | None) -> str | None:
    """加密；空值原样返回 None。"""
    if not plain:
        return None
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(cipher: str | None) -> str | None:
    """解密；密文为空返回 None。"""
    if not cipher:
        return None
    try:
        return _fernet().decrypt(cipher.encode()).decode()
    except InvalidToken as exc:
        raise EncryptionError("API Key 解密失败，API_KEY_ENCRYPTION_KEY 可能已被更换") from exc


def mask_secret(plain: str | None) -> str:
    """脱敏：只留前 3 位与后 4 位，例如 ``sk-a***cdef``。

    脱敏结果在写入时就算好存库，读取接口因此不需要主密钥。
    """
    if not plain:
        return ""
    if len(plain) <= 8:
        return "*" * len(plain)
    return f"{plain[:3]}***{plain[-4:]}"
