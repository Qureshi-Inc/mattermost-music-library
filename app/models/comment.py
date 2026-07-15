"""Comment model — stores user comments and reactions on tracks."""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Comment(TimestampMixin, Base):
    """A comment or reaction on a track."""

    __tablename__ = "comments"

    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    track_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    artist: Mapped[str] = mapped_column(String(512), nullable=False)
    text: Mapped[str] = mapped_column(String(1024), nullable=False)
    is_reaction: Mapped[bool] = mapped_column(default=False)

    def __repr__(self) -> str:
        return f"<Comment(user={self.username}, track={self.title[:30]}, text={self.text[:20]})>"
