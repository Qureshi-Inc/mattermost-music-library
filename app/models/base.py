"""SQLAlchemy base re-export and common model utilities."""

import uuid
from datetime import datetime

from sqlalchemy import String, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base

# Apply naming convention to the existing Base metadata
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
Base.metadata.naming_convention = convention


class UUIDString(TypeDecorator):
    """Platform-independent UUID type that stores as String(36).

    Works with both SQLite and PostgreSQL by storing UUIDs as their
    string representation.
    """

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001, ANN201
        if value is not None:
            if isinstance(value, uuid.UUID):
                return str(value)
            return str(value)
        return value

    def process_result_value(self, value, dialect):  # noqa: ANN001, ANN201
        if value is not None and not isinstance(value, uuid.UUID):
            return uuid.UUID(value)
        return value


class TimestampMixin:
    """Mixin that adds UUID primary key and timestamp columns to models."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDString(),
        primary_key=True,
        default=uuid.uuid4,
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


__all__ = ["Base", "TimestampMixin", "UUIDString"]
