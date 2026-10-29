"""密码哈希：使用标准库 scrypt，不引入额外依赖。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache

_ALGORITHM = "scrypt"
_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16
_MAXMEM = 64 * 1024 * 1024

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 1024


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def hash_password(password: str) -> str:
    """生成 ``scrypt$n$r$p$salt$hash`` 格式的密码哈希。"""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少需要 {MIN_PASSWORD_LENGTH} 个字符")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError("密码过长")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_N,
        r=_R,
        p=_P,
        dklen=_DKLEN,
        maxmem=_MAXMEM,
    )
    return f"{_ALGORITHM}${_N}${_R}${_P}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str | None) -> bool:
    """恒定时间校验；格式错误或超长输入一律返回 False。"""
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    if len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        algorithm, n_text, r_text, p_text, salt_text, hash_text = encoded.split("$")
        if algorithm != _ALGORITHM:
            return False
        n, r, p = int(n_text), int(r_text), int(p_text)
        if n > 2**20 or r > 16 or p > 4:
            return False
        salt = _b64decode(salt_text)
        expected = _b64decode(hash_text)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=_MAXMEM,
        )
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(actual, expected)


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    """用户不存在时也执行一次校验，缩小用户名枚举的时序差异。"""
    return hash_password("dummy-password-for-timing")
