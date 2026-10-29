from __future__ import annotations

import pytest

from api_view.thread_ownership import (
    ThreadAccessError,
    ThreadOwnershipStore,
)
from tests.fakes import FakeRedis


def test_claim_and_verify():
    store = ThreadOwnershipStore(FakeRedis())
    store.claim("thread-1", "u_a")
    store.verify("thread-1", "u_a")
    assert store.get_owner("thread-1") == "u_a"


def test_other_user_is_denied():
    store = ThreadOwnershipStore(FakeRedis())
    store.claim("thread-1", "u_a")
    with pytest.raises(ThreadAccessError):
        store.verify("thread-1", "u_b")


def test_unowned_thread_is_denied_not_claimed():
    store = ThreadOwnershipStore(FakeRedis())
    with pytest.raises(ThreadAccessError):
        store.verify("legacy", "u_a")
    assert store.get_owner("legacy") is None


def test_claim_does_not_overwrite_existing_owner():
    store = ThreadOwnershipStore(FakeRedis())
    store.claim("thread-1", "u_a")
    with pytest.raises(ThreadAccessError):
        store.claim("thread-1", "u_b")
    assert store.get_owner("thread-1") == "u_a"
