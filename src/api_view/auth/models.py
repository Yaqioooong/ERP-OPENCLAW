"""认证领域模型。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    username: str
    roles: tuple[str, ...] = ()
    display_name: str = ""
    must_change_password: bool = False

    @property
    def role_set(self) -> set[str]:
        return set(self.roles)

    def as_public_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name or self.username,
            "roles": list(self.roles),
            "must_change_password": self.must_change_password,
        }
