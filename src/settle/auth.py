"""Password hashing (scrypt, from the standard library) and signed access tokens (JWT)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt

from settle.db.models import UserRole

# scrypt parameters: N=2^14, r=8, p=1 is the widely recommended interactive-login setting.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_SALT_BYTES, _KEY_BYTES = 16, 32
_JWT_ALGORITHM = "HS256"
_ISSUER = "settle"
_CLOCK_SKEW_SECONDS = 60


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_KEY_BYTES
    )
    encode = base64.b64encode
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${encode(salt).decode()}${encode(key).decode()}"
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, key_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    expected = base64.b64decode(key_b64)
    actual = hashlib.scrypt(
        password.encode(),
        salt=base64.b64decode(salt_b64),
        n=int(n),
        r=int(r),
        p=int(p),
        dklen=len(expected),
    )
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: uuid.UUID
    role: UserRole


class InvalidToken(Exception):
    pass


def issue_token(
    user_id: uuid.UUID, role: UserRole, *, secret: str, now: datetime, ttl: timedelta
) -> str:
    claims = {
        "sub": str(user_id),
        "role": role.value,
        "iss": _ISSUER,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(claims, secret, algorithm=_JWT_ALGORITHM)


def read_token(token: str, *, secret: str, now: datetime) -> TokenClaims:
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[_JWT_ALGORITHM],
            issuer=_ISSUER,
            # Time checks use the application clock (below), not the library's wall clock.
            options={
                "require": ["sub", "role", "exp", "iat"],
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
        if int(claims["exp"]) <= now.timestamp():
            raise InvalidToken("Token has expired")
        if int(claims["iat"]) > now.timestamp() + _CLOCK_SKEW_SECONDS:
            raise InvalidToken("Token was issued in the future")
        return TokenClaims(user_id=uuid.UUID(claims["sub"]), role=UserRole(claims["role"]))
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise InvalidToken(str(exc)) from exc
