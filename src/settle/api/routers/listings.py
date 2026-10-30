"""Public listing catalogue."""

from __future__ import annotations

import uuid

from fastapi import APIRouter
from sqlalchemy import select

from settle.api.deps import SessionDep
from settle.api.schemas import ListingOut, ProblemOut
from settle.api.serializers import listing_out
from settle.core.errors import NotFound
from settle.db.models import Listing

router = APIRouter(prefix="/v1/listings", tags=["listings"])


@router.get("", response_model=list[ListingOut])
def list_listings(session: SessionDep) -> list[ListingOut]:
    listings = session.scalars(select(Listing).order_by(Listing.created_at)).all()
    return [listing_out(session, listing) for listing in listings]


@router.get("/{listing_id}", response_model=ListingOut, responses={404: {"model": ProblemOut}})
def get_listing(listing_id: uuid.UUID, session: SessionDep) -> ListingOut:
    listing = session.get(Listing, listing_id)
    if listing is None:
        raise NotFound(f"Listing {listing_id} not found")
    return listing_out(session, listing)
