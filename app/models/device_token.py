"""DeviceToken model — stores FCM registration tokens for push notifications."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DeviceToken(TimestampMixin, Base):
    """An FCM device token registered by a user's app install."""

    __tablename__ = "device_tokens"

    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)

    def __repr__(self) -> str:
        return f"<DeviceToken(user={self.username}, token={self.token[:16]}...)>"
