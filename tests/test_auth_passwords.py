from __future__ import annotations

import pytest

from api_view.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    hash_password,
    verify_password,
)


def test_hash_and_verify_roundtrip():
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("scrypt$")
    assert verify_password("correct horse battery staple", encoded)
    assert not verify_password("wrong password", encoded)


def test_hash_is_salted():
    first = hash_password("same-password-123")
    second = hash_password("same-password-123")
    assert first != second
    assert verify_password("same-password-123", first)
    assert verify_password("same-password-123", second)


def test_short_or_malformed_inputs():
    with pytest.raises(ValueError):
        hash_password("short")
    with pytest.raises(ValueError):
        hash_password("x" * (MAX_PASSWORD_LENGTH + 1))


@pytest.mark.parametrize(
    "encoded",
    [
        None,
        "",
        "plain-text",
        "scrypt$bad",
        "scrypt$16384$8$1$not-base64$hash",
        "md5$1$2$3$c2FsdA==$aGFzaA==",
    ],
)
def test_verify_rejects_malformed_hash(encoded):
    assert not verify_password("anything", encoded)


def test_verify_rejects_oversized_password():
    encoded = hash_password("valid-password")
    assert not verify_password("x" * (MAX_PASSWORD_LENGTH + 1), encoded)
