import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.fernet import Fernet

from app.config import get_settings

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        _, salt, digest = hashed.split("$")
    except ValueError:
        return False
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), **_SCRYPT)
    return hmac.compare_digest(candidate.hex(), digest)


def create_token(username: str) -> str:
    settings = get_settings()
    payload = {"sub": username, "exp": datetime.now(UTC) + timedelta(hours=settings.jwt_expire_hours)}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> str | None:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        return None


def _fernet() -> Fernet:
    key = hashlib.sha256(get_settings().secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_json(data: Any) -> str | None:
    if data is None:
        return None
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str | None) -> Any:
    if not token:
        return None
    return json.loads(_fernet().decrypt(token.encode()))
