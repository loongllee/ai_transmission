"""安全工具：密码哈希、JWT、Key 加密、内部 API Token 生成。"""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

import jwt
from cryptography.fernet import Fernet

from .config import settings

# ---------------------------------------------------------------------------
# 密码哈希（stdlib PBKDF2-HMAC-SHA256，无需额外原生依赖）
# 生产可替换为 bcrypt / argon2。
# ---------------------------------------------------------------------------
_PBKDF2_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT（网页会话）
# ---------------------------------------------------------------------------
def create_access_token(user_id: int, role: str, username: str) -> str:
    now = datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "role": role,
        "username": username,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------------------
# 平台内部 API Token：用户只看明文一次，库里只存哈希
# ---------------------------------------------------------------------------
def generate_api_token() -> Tuple[str, str, str]:
    """返回 (明文 token, sha256 哈希, 展示前缀)。"""
    plaintext = settings.api_token_prefix + secrets.token_hex(24)
    token_hash = hash_api_token(plaintext)
    display = plaintext[: len(settings.api_token_prefix) + 6] + "…"
    return plaintext, token_hash, display


def hash_api_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 真实供应商 Key 加密（Fernet，密钥由 ENCRYPTION_SECRET 派生）
# ---------------------------------------------------------------------------
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.encryption_secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
