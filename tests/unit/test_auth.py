from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from settle.auth import InvalidToken, hash_password, issue_token, read_token, verify_password
from settle.db.models import UserRole
from settle.db.session import normalize_url

NOW = datetime(2026, 11, 1, tzinfo=UTC)
SECRET = "test-secret-that-is-long-enough-for-hs256"


def test_password_round_trip() -> None:
    stored = hash_password("correct horse")
    assert stored.startswith("scrypt$")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong horse", stored)


def test_same_password_hashes_differently() -> None:
    assert hash_password("same") != hash_password("same")  # random salt


@pytest.mark.parametrize("stored", ["", "plain", "bcrypt$1$2$3$4$5"])
def test_malformed_hashes_never_verify(stored: str) -> None:
    assert not verify_password("anything", stored)


def test_token_round_trip() -> None:
    user_id = uuid.uuid4()
    token = issue_token(user_id, UserRole.HOST, secret=SECRET, now=NOW, ttl=timedelta(hours=1))
    claims = read_token(token, secret=SECRET, now=NOW + timedelta(minutes=59))
    assert claims.user_id == user_id
    assert claims.role is UserRole.HOST


def test_expired_token_is_rejected() -> None:
    token = issue_token(
        uuid.uuid4(), UserRole.GUEST, secret=SECRET, now=NOW, ttl=timedelta(hours=1)
    )
    with pytest.raises(InvalidToken):
        read_token(token, secret=SECRET, now=NOW + timedelta(hours=1))


def test_token_signed_with_another_secret_is_rejected() -> None:
    token = issue_token(
        uuid.uuid4(), UserRole.ADMIN, secret=SECRET, now=NOW, ttl=timedelta(hours=1)
    )
    with pytest.raises(InvalidToken):
        read_token(token, secret="a-different-secret-that-is-also-long-enough", now=NOW)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("sqlite:///./x.db", "sqlite:///./x.db"),
    ],
)
def test_hosting_database_urls_are_normalised(given: str, expected: str) -> None:
    assert normalize_url(given) == expected
