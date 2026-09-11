from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    profile: Mapped["Profile"] = relationship(back_populates="user", cascade="all, delete-orphan")
    media: Mapped[list["Media"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)

    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)

    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    online: Mapped[bool] = mapped_column(Boolean, default=True)

    collab_status: Mapped[str | None] = mapped_column(String(120), nullable=True)
    collab_score: Mapped[float] = mapped_column(Float, default=0.0)
    collab_count: Mapped[int] = mapped_column(Integer, default=0)
    followers: Mapped[int] = mapped_column(Integer, default=0)
    following: Mapped[int] = mapped_column(Integer, default=0)

    open_to_collab: Mapped[bool] = mapped_column(Boolean, default=True)
    private_account: Mapped[bool] = mapped_column(Boolean, default=False)
    response_time: Mapped[str] = mapped_column(String(50), default="< 4 hours")

    user: Mapped[User] = relationship(back_populates="profile")
    posts: Mapped[list["Post"]] = relationship(back_populates="profile", cascade="all, delete-orphan")
    playlists: Mapped[list["Playlist"]] = relationship(back_populates="profile", cascade="all, delete-orphan")


class Follow(Base):
    __tablename__ = "follows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    follower_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    following_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("follower_id", "following_id", name="uq_follows_pair"),
    )


class Media(Base):
    __tablename__ = "media"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="media")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True)

    thumbnail: Mapped[str] = mapped_column(String(500), nullable=False)
    media_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"), nullable=True, index=True)
    thumbnail_media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"), nullable=True, index=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    audio: Mapped[str] = mapped_column(String(255), default="Original Sound")
    visibility: Mapped[str] = mapped_column(String(20), default="public")
    allow_comments: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_collabs: Mapped[bool] = mapped_column(Boolean, default=True)
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    collab_with: Mapped[str | None] = mapped_column(String(120), nullable=True)

    profile: Mapped[Profile] = relationship(back_populates="posts")


class PostLike(Base):
    __tablename__ = "post_likes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("post_id", "user_id", name="uq_post_likes_pair"),
    )

class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True
    )

    recipient_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )

    type: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        index=True
    )

    post_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    text: Mapped[str] = mapped_column(
        Text,
        nullable=False
    )

    read: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
        index=True
    )


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        index=True,
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    collab_requests: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    messages: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    likes: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    comments: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    new_followers: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    live_alerts: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    trending_sounds: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    product_updates: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    email_digest: Mapped[str] = mapped_column(
        String(20),
        default="weekly",
        nullable=False,
    )

    quiet_hours: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    
class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(150), nullable=False)
    cover: Mapped[str] = mapped_column(String(500), nullable=False)
    item_label: Mapped[str] = mapped_column(String(80), nullable=False)
    plays: Mapped[int] = mapped_column(Integer, default=0)

    profile: Mapped[Profile] = relationship(back_populates="playlists")


class Sound(Base):
    __tablename__ = "sounds"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    creator: Mapped[str] = mapped_column(String(120), nullable=False)
    creator_avatar: Mapped[str] = mapped_column(Text, nullable=False)
    artwork: Mapped[str] = mapped_column(Text, nullable=False)
    genre: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    total_plays: Mapped[int] = mapped_column(Integer, default=0)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    growth_pct: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[str] = mapped_column(String(20), default="0:30")
    bpm: Mapped[int] = mapped_column(Integer, default=0)
