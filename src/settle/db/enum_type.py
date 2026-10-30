"""Store Python ``StrEnum`` members as their string values in a VARCHAR column."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum


def str_enum(enum_cls: type[StrEnum], *, length: int = 32) -> Enum:
    """A non-native enum column: portable across databases and cheap to extend."""
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )
