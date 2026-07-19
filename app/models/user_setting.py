"""UserSetting model — per-user preferences synced across devices.

Keyed by the normalized dashboard username (lowercased). Holds the things the
app lets a user change: their display name + avatar color, notification
preferences, and whether their plays are collected by the data engine.
"""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class UserSetting(TimestampMixin, Base):
    """A single user's synced preferences."""

    __tablename__ = "user_settings"

    # Normalized (lowercased) dashboard username — one row per user.
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    # Identity overrides (fall back to server defaults when null).
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    color: Mapped[str | None] = mapped_column(String(9), nullable=True)  # e.g. #8b5cf6

    # Notification preferences.
    notify_mentions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Data-engine collection opt-in (True = collect this user's plays).
    collect_plays: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<UserSetting(user={self.username})>"
