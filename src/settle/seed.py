"""Demo accounts and listings. Safe to run repeatedly; existing records are left alone."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from settle.auth import hash_password
from settle.db.models import Listing, User, UserRole
from settle.domain.policies import CancellationPolicy

DEMO_PASSWORD: Final = "settle-demo"

GUEST_EMAIL: Final = "ada.guest@example.com"
HOST_EMAIL: Final = "joao.host@example.com"
HOST_NG_EMAIL: Final = "chioma.host@example.com"
ADMIN_EMAIL: Final = "ops@example.com"


@dataclass(frozen=True, slots=True)
class SeedResult:
    guest: User
    host: User
    host_ng: User
    admin: User
    listing: Listing  # the Lisbon loft, moderate policy
    listings: list[Listing]


def _user(
    session: Session,
    *,
    email: str,
    name: str,
    role: UserRole,
    country: str,
    currency: str,
    payout_email: str | None = None,
    password_hash: str,
) -> User:
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            full_name=name,
            role=role,
            country=country,
            default_currency=currency,
            payout_email=payout_email,
            password_hash=password_hash,
        )
        session.add(user)
        session.flush()
    return user


def _listing(
    session: Session,
    host: User,
    *,
    title: str,
    city: str,
    country: str,
    price_minor: int,
    policy: CancellationPolicy,
) -> Listing:
    listing = session.scalar(select(Listing).where(Listing.title == title))
    if listing is None:
        listing = Listing(
            host_id=host.id,
            title=title,
            city=city,
            country=country,
            nightly_price_minor=price_minor,
            currency="USD",
            cancellation_policy=policy,
        )
        session.add(listing)
        session.flush()
    return listing


def seed_demo_data(session: Session, *, password: str = DEMO_PASSWORD) -> SeedResult:
    hashed = hash_password(password)
    guest = _user(
        session,
        email=GUEST_EMAIL,
        name="Ada Guest",
        role=UserRole.GUEST,
        country="US",
        currency="USD",
        password_hash=hashed,
    )
    host = _user(
        session,
        email=HOST_EMAIL,
        name="Joao Host",
        role=UserRole.HOST,
        country="PT",
        currency="EUR",
        payout_email="joao.host@paypal.example.com",
        password_hash=hashed,
    )
    host_ng = _user(
        session,
        email=HOST_NG_EMAIL,
        name="Chioma Host",
        role=UserRole.HOST,
        country="NG",
        currency="NGN",
        payout_email="chioma.host@paypal.example.com",
        password_hash=hashed,
    )
    admin = _user(
        session,
        email=ADMIN_EMAIL,
        name="Settle Operations",
        role=UserRole.ADMIN,
        country="US",
        currency="USD",
        password_hash=hashed,
    )
    listings = [
        _listing(
            session,
            host,
            title="Sunny loft in Alfama",
            city="Lisbon",
            country="PT",
            price_minor=12_500,
            policy=CancellationPolicy.MODERATE,
        ),
        _listing(
            session,
            host,
            title="Riverside studio near Belem",
            city="Lisbon",
            country="PT",
            price_minor=8_900,
            policy=CancellationPolicy.FLEXIBLE,
        ),
        _listing(
            session,
            host_ng,
            title="Lekki penthouse with ocean view",
            city="Lagos",
            country="NG",
            price_minor=21_000,
            policy=CancellationPolicy.STRICT,
        ),
    ]
    return SeedResult(guest, host, host_ng, admin, listings[0], listings)
