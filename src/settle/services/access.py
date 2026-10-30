"""Who may see or act on what. One place, so every endpoint applies the same rules."""

from __future__ import annotations

from settle.core.errors import Forbidden
from settle.db.models import Booking, User, UserRole
from settle.db.models.trust import DisputeParty


def can_view_booking(user: User, booking: Booking) -> bool:
    return user.role is UserRole.ADMIN or user.id in {booking.guest_id, booking.host_id}


def ensure_can_view(user: User, booking: Booking) -> None:
    if not can_view_booking(user, booking):
        # Same message as "not found" would leak nothing either; we prefer an explicit 403.
        raise Forbidden("You do not have access to this booking")


def ensure_guest_of(user: User, booking: Booking) -> None:
    if user.id != booking.guest_id:
        raise Forbidden("Only the guest who made this booking can do that")


def party_of(user: User, booking: Booking) -> DisputeParty:
    if user.id == booking.guest_id:
        return DisputeParty.GUEST
    if user.id == booking.host_id:
        return DisputeParty.HOST
    raise Forbidden("Only the guest or the host can take part in this dispute")
