"""注册与密码策略校验。"""
from __future__ import annotations

import re
import secrets
import string

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")
DISPLAY_NAME_MAX = 64
PASSWORD_MIN = 8
PASSWORD_MAX = 128

COMMON_PASSWORDS = {
    "12345678",
    "123456789",
    "password",
    "password1",
    "qwerty123",
    "admin123",
    "abc12345",
    "11111111",
    "88888888",
    "iloveyou",
}


class AuthValidationError(ValueError):
    def __init__(self, field_errors: dict[str, str]):
        super().__init__("validation_error")
        self.field_errors = field_errors


def validate_username(username: str) -> str:
    value = (username or "").strip()
    if not USERNAME_RE.fullmatch(value):
        raise AuthValidationError(
            {
                "username": "3–32 位，以字母或数字开头，仅允许字母、数字、点、下划线、短横线"
            }
        )
    return value


def validate_display_name(display_name: str) -> str:
    value = (display_name or "").strip()
    if not value:
        raise AuthValidationError({"display_name": "显示名称不能为空"})
    if len(value) > DISPLAY_NAME_MAX:
        raise AuthValidationError({"display_name": f"显示名称不能超过 {DISPLAY_NAME_MAX} 个字符"})
    if any(ord(ch) < 32 for ch in value):
        raise AuthValidationError({"display_name": "显示名称不能包含控制字符"})
    return value


def validate_password(password: str, username: str) -> str:
    if not isinstance(password, str):
        raise AuthValidationError({"password": "密码格式非法"})
    if len(password) < PASSWORD_MIN or len(password) > PASSWORD_MAX:
        raise AuthValidationError(
            {"password": f"密码长度需为 {PASSWORD_MIN}–{PASSWORD_MAX} 个字符"}
        )
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise AuthValidationError({"password": "密码必须同时包含字母和数字"})
    if password.lower() in COMMON_PASSWORDS:
        raise AuthValidationError({"password": "密码过于常见，请更换"})
    if username and username.lower() in password.lower():
        raise AuthValidationError({"password": "密码不能包含用户名"})
    return password


def validate_password_confirmation(password: str, confirm_password: str) -> None:
    if password != confirm_password:
        raise AuthValidationError({"confirm_password": "两次输入的密码不一致"})


def validate_registration(
    *,
    username: str,
    display_name: str,
    password: str,
    confirm_password: str,
) -> tuple[str, str, str]:
    """一次性收集所有字段错误，便于前端逐项提示。"""
    errors: dict[str, str] = {}
    normalized_username = (username or "").strip()
    normalized_display = (display_name or "").strip()

    try:
        normalized_username = validate_username(username)
    except AuthValidationError as exc:
        errors.update(exc.field_errors)

    try:
        normalized_display = validate_display_name(display_name)
    except AuthValidationError as exc:
        errors.update(exc.field_errors)

    try:
        validate_password(password, normalized_username)
    except AuthValidationError as exc:
        errors.update(exc.field_errors)

    try:
        validate_password_confirmation(password, confirm_password)
    except AuthValidationError as exc:
        errors.update(exc.field_errors)

    if errors:
        raise AuthValidationError(errors)
    return normalized_username, normalized_display, password


def generate_user_id() -> str:
    return f"u_{secrets.token_hex(6)}"


def generate_temporary_password(length: int = 12) -> str:
    """生成满足策略的一次性临时密码（至少一个字母、一个数字）。"""
    alphabet = string.ascii_letters + string.digits
    while True:
        value = "Tmp" + "".join(secrets.choice(alphabet) for _ in range(length - 3))
        if re.search(r"[A-Za-z]", value) and re.search(r"\d", value):
            return value
