from __future__ import annotations

import os
import re
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

import strawberry
from graphql import GraphQLError
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import String, and_, cast, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from strawberry.fastapi import GraphQLRouter
from strawberry.types import Info
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from backend.app.auth import delete_user_account, get_user_profile, AuthContext
from backend.app.auth_routes import create_auth_router
from backend.app.database import get_session, run_migrations
from backend.app.models import Follow, Media as DbMedia, Notification, NotificationPreference, Playlist as DbPlaylist, Post as DbPost, PostLike, Profile as DbProfile, Sound as DbSound, User
from backend.app.media import AVATAR_TYPES, MAX_AVATAR_BYTES, MAX_MEDIA_BYTES, MEDIA_ROOT, MEDIA_TYPES, store_upload
from backend.app.media_routes import create_media_router
from backend.app.profile_validation import (
    validate_avatar_url,
    validate_bio,
    validate_display_name,
    validate_username,
    validate_website,
)
from backend.app.graphql_types import (
    ContentItem, FeedItem, FeedPage, FollowResult, HashtagResult, LikeResult, NotificationItem,
    NotificationPreferences, Playlist, PlaylistInput, PostInput, PostPage, Profile, ProfilePage,
    ProfileSummary, SoundResult, UpdateNotificationPreferencesInput, UpdatePlaylistInput,
    UpdatePostInput, UpdateProfileInput,
)
from backend.app.seed import seed_database
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("connextionz.api")


def api_error(message: str, code: str, status_code: int) -> GraphQLError:
    return GraphQLError(message, extensions={"code": code, "statusCode": status_code})


def parse_int_id(raw_id: object, field_name: str = "ID") -> int:
    if raw_id is None:
        raise api_error(f"{field_name} is required", "VALIDATION_ERROR", 400)

    value = str(raw_id).strip()
    if not value or not re.fullmatch(r"\d+", value):
        raise api_error(f"Invalid {field_name}: expected a numeric ID", "VALIDATION_ERROR", 400)

    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise api_error(f"Invalid {field_name}: expected a numeric ID", "VALIDATION_ERROR", 400) from exc


def sound_to_graphql(sound: DbSound) -> SoundResult:
    return SoundResult(
        id=sound.id, title=sound.title, creator=sound.creator,
        creator_avatar=sound.creator_avatar, artwork=sound.artwork,
        genre=sound.genre, video_count=sound.video_count,
        total_plays=sound.total_plays, rank=sound.rank,
        growth_pct=sound.growth_pct, duration=sound.duration, bpm=sound.bpm,
    )


def is_post_liked(session, user_id: int | None, post_id: int) -> bool:
    if user_id is None:
        return False
    return session.execute(
        select(PostLike.id).where(PostLike.post_id == post_id, PostLike.user_id == user_id)
    ).first() is not None


def refresh_post_like_count(session, post_id: int) -> int:
    post = session.execute(select(DbPost).where(DbPost.id == post_id)).scalar_one_or_none()
    if post is None:
        return 0

    count = session.execute(
        select(func.count(PostLike.id)).where(PostLike.post_id == post_id)
    ).scalar_one() or 0
    post.likes = int(count)
    session.flush()
    return post.likes


def post_to_graphql(
    post: DbPost,
    viewer_id: int | None = None,
    liked_post_ids: set[int] | None = None,
) -> ContentItem:
    if liked_post_ids is None:
        with get_session() as session:
            liked = is_post_liked(session, viewer_id, post.id)
    else:
        liked = post.id in liked_post_ids
    return ContentItem(
        id=str(post.id), thumbnail=post.thumbnail, media_url=post.media_url,
        caption=post.caption or "", views=post.views, likes=post.likes,
        is_liked=liked, collab_with=post.collab_with, hashtags=post.hashtags or [],
        audio=post.audio or "Original Sound", visibility=post.visibility or "public",
        allow_comments=post.allow_comments if post.allow_comments is not None else True,
        allow_collabs=post.allow_collabs if post.allow_collabs is not None else True,
        duration_sec=post.duration_sec or 0.0, comments=post.comments or 0,
        shares=post.shares or 0, saves=post.saves or 0,
    )


def liked_post_ids(session, viewer_id: int | None, posts: list[DbPost]) -> set[int]:
    if viewer_id is None or not posts:
        return set()
    return set(session.scalars(
        select(PostLike.post_id).where(
            PostLike.user_id == viewer_id,
            PostLike.post_id.in_([post.id for post in posts]),
        )
    ).all())


def profile_to_graphql(
    profile: DbProfile,
    is_following: bool = False,
    viewer_id: int | None = None,
    liked_post_ids: set[int] | None = None,
) -> Profile:
    all_posts = [
        post_to_graphql(post, viewer_id=viewer_id, liked_post_ids=liked_post_ids)
        for post in profile.posts
    ]
    page_size = 12
    recent_posts = all_posts[:page_size]
    playlists = [
        Playlist(
            id=str(item.id),
            title=item.title,
            cover=item.cover,
            item_label=item.item_label,
            plays=item.plays,
        )
        for item in profile.playlists
    ]

    return Profile(
        id=str(profile.id),
        username=profile.username,
        display_name=profile.display_name,
        avatar_url=profile.avatar_url or "",
        avatar_color=profile.avatar_color or "#00AEEF",
        bio=profile.bio,
        location=profile.location,
        website=profile.website,
        verified=profile.verified,
        online=profile.online,
        collab_status=profile.collab_status,
        collab_score=profile.collab_score,
        collab_count=profile.collab_count,
        followers=profile.followers,
        following=profile.following,
        open_to_collab=profile.open_to_collab,
        private_account=profile.private_account,
        response_time=profile.response_time,
        posts=recent_posts,
        posts_page=PostPage(items=recent_posts, next_cursor=str(page_size) if len(all_posts) > page_size else None),
        playlists=playlists,
        is_following=is_following,
    )


def load_profile_from_db(username: str | None = None) -> DbProfile | None:
    with get_session() as session:
        stmt = select(DbProfile).options(
            selectinload(DbProfile.posts),
            selectinload(DbProfile.playlists),
        )
        if username is not None:
            stmt = stmt.where(DbProfile.username == username)
        profile = session.execute(stmt).scalars().first()
        if profile is None:
            return None

        return profile


def owner_profile(session, info: Info) -> DbProfile:
    user_id = info.context.get("user_id")
    if user_id is None:
        raise api_error("Must be logged in", "UNAUTHENTICATED", 401)
    profile = session.execute(
        select(DbProfile).where(DbProfile.user_id == user_id)
    ).scalar_one_or_none()
    if profile is None:
        raise api_error("Profile not found", "NOT_FOUND", 404)
    return profile


def owned_media(session, user_id: int, media_id: object, field_name: str) -> DbMedia:
    record_id = parse_int_id(media_id, field_name)
    media = session.execute(
        select(DbMedia).where(DbMedia.id == record_id, DbMedia.user_id == user_id)
    ).scalar_one_or_none()
    if media is None:
        raise api_error("Media not found", "NOT_FOUND", 404)
    return media


def profile_summary(profile: DbProfile, is_following: bool = False) -> ProfileSummary:
    return ProfileSummary(
        id=str(profile.id), username=profile.username,
        display_name=profile.display_name, avatar_url=profile.avatar_url or "",
        avatar_color=profile.avatar_color or "#00AEEF", verified=profile.verified,
        collab_score=profile.collab_score, collab_count=profile.collab_count,
        followers=profile.followers, following=profile.following,
        open_to_collab=profile.open_to_collab,
        private_account=profile.private_account,
        is_following=is_following,
    )


def find_profile(session, identifier: str) -> DbProfile | None:
    identifier = identifier.strip()
    profile = session.execute(
        select(DbProfile).where(DbProfile.username == identifier)
    ).scalar_one_or_none()
    if profile is not None or not identifier.isdigit():
        return profile
    try:
        numeric_id = int(identifier)
    except (TypeError, ValueError):
        return profile
    return session.execute(
        select(DbProfile).where(DbProfile.id == numeric_id)
    ).scalar_one_or_none()


def is_following_profile(session, viewer_id: int, target_user_id: int) -> bool:
    return session.execute(
        select(Follow.id).where(
            Follow.follower_id == viewer_id,
            Follow.following_id == target_user_id,
        )
    ).first() is not None


def can_view_profile(session, viewer_id: int | None, profile: DbProfile) -> bool:
    if not profile.private_account:
        return True
    if viewer_id is None:
        return False
    if viewer_id == profile.user_id:
        return True
    return is_following_profile(session, viewer_id, profile.user_id)


def profile_visibility_filter(viewer_id: int | None):
    visibility = [DbProfile.private_account.is_(False)]
    if viewer_id is not None:
        visibility.append(DbProfile.user_id == viewer_id)
        visibility.append(
            select(Follow.id).where(
                Follow.follower_id == viewer_id,
                Follow.following_id == DbProfile.user_id,
            ).exists()
        )
    return or_(*visibility)


def feed_item(
    post: DbPost,
    profile: DbProfile,
    viewer_id: int | None = None,
    liked_post_ids: set[int] | None = None,
) -> FeedItem:
    if liked_post_ids is None:
        with get_session() as session:
            liked = is_post_liked(session, viewer_id, post.id)
    else:
        liked = post.id in liked_post_ids
    return FeedItem(
        id=str(post.id), thumbnail=post.thumbnail, media_url=post.media_url,
        caption=post.caption or "", views=post.views, likes=post.likes,
        is_liked=liked, collab_with=post.collab_with, hashtags=post.hashtags or [],
        audio=post.audio or "Original Sound", visibility=post.visibility or "public",
        allow_comments=post.allow_comments if post.allow_comments is not None else True,
        allow_collabs=post.allow_collabs if post.allow_collabs is not None else True,
        duration_sec=post.duration_sec or 0.0, comments=post.comments or 0,
        shares=post.shares or 0, saves=post.saves or 0,
        creator=profile_summary(profile),
    )


def visible_post_filter(viewer_id: int | None):
    """Build the SQL predicate for posts the viewer is allowed to see."""
    if viewer_id is None:
        return and_(
            DbPost.visibility == "public",
            DbProfile.private_account.is_(False),
        )

    follows_author = select(Follow.id).where(
        Follow.follower_id == viewer_id,
        Follow.following_id == DbProfile.user_id,
    ).exists()

    return or_(
        and_(DbPost.visibility == "public", DbProfile.private_account.is_(False)),
        and_(DbPost.visibility == "public", DbProfile.user_id == viewer_id),
        and_(DbPost.visibility == "public", DbProfile.private_account.is_(True), follows_author),
        and_(DbPost.visibility == "followers", follows_author),
        and_(DbPost.visibility == "followers", DbProfile.user_id == viewer_id),
    )


@strawberry.type
class Query:
    @strawberry.field
    def me(self, info: Info) -> Profile | None:
        """Return the logged-in user's profile, or None if not authenticated."""
        user_id = info.context.get("user_id")
        if user_id is None:
            return None

        profile = get_user_profile(user_id)
        if profile is None:
            return None
        return profile_to_graphql(profile)

    @strawberry.field
    def notifications(
    self,
    info: Info,
    limit: int = 20,
    offset: int = 0,
) -> list[NotificationItem]:
        """
        Return notifications for the currently logged-in user,
        ordered from newest to oldest.
        """

        user_id = info.context.get("user_id")

        if user_id is None:
            raise api_error(
                "Must be logged in to view notifications",
                "UNAUTHENTICATED",
                401,
            )

        with get_session() as session:
            rows = session.execute(
                select(Notification)
                .where(Notification.recipient_id == user_id)
                .order_by(Notification.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).scalars().all()

            results: list[NotificationItem] = []

            for notification in rows:
                actor_username = None

                # System notifications may not have an actor.
                # For user-generated notifications, retrieve the actor's
                # username so the frontend can display their handle.
                if notification.actor_id is not None:
                    actor_profile = session.execute(
                        select(DbProfile).where(
                            DbProfile.user_id == notification.actor_id
                        )
                    ).scalar_one_or_none()

                    if actor_profile is not None:
                        actor_username = actor_profile.username

                results.append(
                    NotificationItem(
                        id=str(notification.id),
                        type=notification.type,
                        actor=actor_username,
                        text=notification.text,
                        post_id=(
                            str(notification.post_id)
                            if notification.post_id is not None
                            else None
                        ),
                        # JavaScript timestamps use milliseconds, while
                        # Python datetime.timestamp() returns seconds.
                        created_at=int(
                            notification.created_at.timestamp() * 1000
                        ),
                        read=notification.read,
                    )
                )

            return results

    @strawberry.field(name="unreadNotificationCount")
    def unread_notification_count(self, info: Info) -> int:
        """Return the number of unread notifications for the current user."""

        user_id = info.context.get("user_id")

        if user_id is None:
            raise api_error(
                "Must be logged in to view notifications",
                "UNAUTHENTICATED",
                401,
            )

        with get_session() as session:
            count = session.execute(
                select(func.count(Notification.id)).where(
                    Notification.recipient_id == user_id,
                    Notification.read.is_(False),
                )
            ).scalar_one()

            return int(count or 0)

    @strawberry.field(name="notificationPreferences")
    def notification_preferences(self, info: Info) -> NotificationPreferences:
        """Return notification preferences for the current user."""

        user_id = info.context.get("user_id")

        if user_id is None:
            raise api_error(
                "Must be logged in to view notification preferences",
                "UNAUTHENTICATED",
                401,
            )

        with get_session() as session:
            preferences = session.execute(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == user_id
                )
            ).scalar_one_or_none()

            if preferences is None:
                preferences = NotificationPreference(user_id=user_id)
                session.add(preferences)
                session.commit()
                session.refresh(preferences)

            return NotificationPreferences(
                collab_requests=preferences.collab_requests,
                messages=preferences.messages,
                likes=preferences.likes,
                comments=preferences.comments,
                new_followers=preferences.new_followers,
                live_alerts=preferences.live_alerts,
                trending_sounds=preferences.trending_sounds,
                product_updates=preferences.product_updates,
                email_digest=preferences.email_digest,
                quiet_hours=preferences.quiet_hours,
            )
    
    @strawberry.field
    def profile(self, username: str, info: Info) -> Profile | None:
        """Fetch any user's public profile by username or numeric profile ID."""
        with get_session() as session:
            profile = find_profile(session, username)
            if profile is None:
                return None
            profile = session.execute(
                select(DbProfile)
                .options(selectinload(DbProfile.posts), selectinload(DbProfile.playlists))
                .where(DbProfile.id == profile.id)
            ).scalar_one()
            user_id = info.context.get("user_id")
            if not can_view_profile(session, user_id, profile):
                return None
            is_following = False
            if user_id is not None and user_id != profile.user_id:
                is_following = is_following_profile(session, user_id, profile.user_id)
            liked_ids = liked_post_ids(session, user_id, list(profile.posts))
            return profile_to_graphql(
                profile,
                is_following=is_following,
                viewer_id=user_id,
                liked_post_ids=liked_ids,
            )

    @strawberry.field(name="searchProfiles")
    def search_profiles(self, query: str, info: Info, limit: int = 20) -> list[ProfileSummary]:
        """Search public profiles by username or display name."""
        terms = [term for term in query.strip().split() if term]
        if not terms:
            return []
        search = "%" + "%".join(terms) + "%"
        page_size = max(1, min(limit, 50))
        user_id = info.context.get("user_id")
        with get_session() as session:
            profiles = session.execute(
                select(DbProfile)
                .where(or_(
                    func.lower(DbProfile.username).like(search.lower()),
                    func.lower(DbProfile.display_name).like(search.lower()),
                ))
                .where(profile_visibility_filter(user_id))
                .order_by(DbProfile.followers.desc(), DbProfile.username)
                .limit(page_size)
            ).scalars().all()
            return [profile_summary(profile) for profile in profiles]

    @strawberry.field(name="suggestedProfiles")
    def suggested_profiles(self, info: Info, limit: int = 6) -> list[ProfileSummary]:
        """Return public profiles ranked for collaboration discovery."""
        page_size = max(1, min(limit, 20))
        user_id = info.context.get("user_id")
        with get_session() as session:
            profiles = session.execute(
                select(DbProfile)
                .where(DbProfile.open_to_collab.is_(True))
                .where(profile_visibility_filter(user_id))
                .order_by(DbProfile.collab_score.desc(), DbProfile.followers.desc())
                .limit(page_size)
            ).scalars().all()
            return [profile_summary(profile) for profile in profiles]

    @strawberry.field(name="searchPosts")
    def search_posts(self, info: Info, query: str, limit: int = 20) -> list[FeedItem]:
        """Search published posts by caption, hashtag, audio, or creator."""
        terms = [term for term in query.strip().split() if term]
        if not terms:
            return []
        page_size = max(1, min(limit, 50))
        with get_session() as session:
            filters = []
            for term in terms:
                pattern = f"%{term}%"
                filters.append(or_(
                    DbPost.caption.ilike(pattern),
                    DbPost.audio.ilike(pattern),
                    cast(DbPost.hashtags, String).ilike(pattern),
                    DbProfile.username.ilike(pattern),
                    DbProfile.display_name.ilike(pattern),
                ))
            rows = session.execute(
                select(DbPost, DbProfile)
                .join(DbProfile, DbPost.profile_id == DbProfile.id)
                .where(*filters)
                .order_by(DbPost.views.desc(), DbPost.id.desc())
                .limit(page_size)
            ).all()
            viewer_id = info.context.get("user_id")
            liked_ids = liked_post_ids(session, viewer_id, [post for post, _ in rows])
            return [
                feed_item(post, profile, viewer_id=viewer_id, liked_post_ids=liked_ids)
                for post, profile in rows
            ]

    @strawberry.field(name="searchHashtags")
    def search_hashtags(self, query: str, limit: int = 20) -> list[HashtagResult]:
        """Search hashtags stored on published posts."""
        term = query.strip().lstrip("#").lower()
        if not term:
            return []
        page_size = max(1, min(limit, 50))
        counts: dict[str, tuple[int, int]] = {}
        with get_session() as session:
            posts = session.execute(select(DbPost.hashtags, DbPost.views)).all()
        for hashtags, views in posts:
            for value in hashtags or []:
                tag = str(value).strip().lstrip("#").lower()
                if tag and term in tag:
                    post_count, view_count = counts.get(tag, (0, 0))
                    counts[tag] = (post_count + 1, view_count + (views or 0))
        return [
            HashtagResult(tag=tag, posts=post_count, views=view_count)
            for tag, (post_count, view_count) in sorted(
                counts.items(), key=lambda item: (-item[1][1], item[0])
            )[:page_size]
        ]

    @strawberry.field(name="trendingSounds")
    def trending_sounds(self, genre: str | None = None, limit: int = 50) -> list[SoundResult]:
        page_size = max(1, min(limit, 100))
        with get_session() as session:
            statement = select(DbSound).order_by(DbSound.rank, DbSound.total_plays.desc())
            if genre and genre != "All":
                statement = statement.where(DbSound.genre == genre)
            sounds = session.execute(statement.limit(page_size)).scalars().all()
            return [sound_to_graphql(sound) for sound in sounds]

    @strawberry.field(name="myPosts")
    def my_posts(self, info: Info) -> list[ContentItem]:
        return self.my_posts_page(info=info).items

    @strawberry.field(name="myPostsPage")
    def my_posts_page(self, info: Info, after: str | None = None, limit: int = 12) -> PostPage:
        user_id = info.context.get("user_id")
        if user_id is None:
            return PostPage(items=[], next_cursor=None)
        page_size = max(1, min(limit, 50))
        offset = 0
        if after:
            try:
                offset = max(0, int(after))
            except ValueError as error:
                raise api_error("Invalid post cursor", "VALIDATION_ERROR", 400) from error
        with get_session() as session:
            profile = session.execute(
                select(DbProfile).where(DbProfile.user_id == user_id)
            ).scalar_one_or_none()
            if profile is None:
                return PostPage(items=[], next_cursor=None)
            rows = list(profile.posts)
            ordered = sorted(rows, key=lambda post: post.id, reverse=True)
            page = ordered[offset:offset + page_size]
            has_more = offset + page_size < len(ordered)
            liked_ids = liked_post_ids(session, user_id, page)
            return PostPage(
                items=[
                    post_to_graphql(post, viewer_id=user_id, liked_post_ids=liked_ids)
                    for post in page
                ],
                next_cursor=str(offset + page_size) if has_more else None,
            )

    @strawberry.field(name="profilePostsPage")
    def profile_posts_page(self, username: str, info: Info, after: str | None = None, limit: int = 12) -> PostPage:
        viewer_id = info.context.get("user_id")
        with get_session() as session:
            profile = find_profile(session, username)
            if profile is None:
                return PostPage(items=[], next_cursor=None)
            if not can_view_profile(session, viewer_id, profile):
                return PostPage(items=[], next_cursor=None)
            page_size = max(1, min(limit, 50))
            offset = 0
            if after:
                try:
                    offset = max(0, int(after))
                except ValueError as error:
                    raise api_error("Invalid post cursor", "VALIDATION_ERROR", 400) from error

            can_view_all = viewer_id == profile.user_id
            statement = select(DbPost).where(DbPost.profile_id == profile.id)
            if not can_view_all:
                statement = statement.join(DbProfile).where(visible_post_filter(viewer_id))
            ordered = session.execute(
                statement.order_by(DbPost.id.desc()).offset(offset).limit(page_size + 1)
            ).scalars().all()
            has_more = len(ordered) > page_size
            page = ordered[:page_size]
            liked_ids = liked_post_ids(session, viewer_id, page)
            return PostPage(
                items=[
                    post_to_graphql(post, viewer_id=viewer_id, liked_post_ids=liked_ids)
                    for post in page
                ],
                next_cursor=str(offset + page_size) if has_more else None,
            )

    @strawberry.field(name="myPlaylists")
    def my_playlists(self, info: Info) -> list[Playlist]:
        user_id = info.context.get("user_id")
        if user_id is None:
            return []
        with get_session() as session:
            profile = session.execute(
                select(DbProfile).where(DbProfile.user_id == user_id)
            ).scalar_one_or_none()
            return [
                Playlist(
                    id=str(item.id), title=item.title, cover=item.cover,
                    item_label=item.item_label, plays=item.plays,
                )
                for item in profile.playlists
            ] if profile else []

    @strawberry.field
    def followers(self, username: str, info: Info) -> list[ProfileSummary]:
        with get_session() as session:
            profile = find_profile(session, username)
            if profile is None:
                return []
            if not can_view_profile(session, info.context.get("user_id"), profile):
                return []
            profiles = session.execute(
                select(DbProfile)
                .join(Follow, Follow.follower_id == DbProfile.user_id)
                .where(Follow.following_id == profile.user_id)
            ).scalars().all()
            return [profile_summary(item) for item in profiles]

    @strawberry.field
    def following(self, username: str, info: Info) -> list[ProfileSummary]:
        with get_session() as session:
            profile = find_profile(session, username)
            if profile is None:
                return []
            if not can_view_profile(session, info.context.get("user_id"), profile):
                return []
            profiles = session.execute(
                select(DbProfile)
                .join(Follow, Follow.following_id == DbProfile.user_id)
                .where(Follow.follower_id == profile.user_id)
            ).scalars().all()
            return [profile_summary(item) for item in profiles]

    @strawberry.field(name="myFollowers")
    def my_followers(self, info: Info) -> list[ProfileSummary]:
        user_id = info.context.get("user_id")
        if user_id is None:
            return []
        with get_session() as session:
            profiles = session.execute(
                select(DbProfile)
                .join(Follow, Follow.follower_id == DbProfile.user_id)
                .where(Follow.following_id == user_id)
            ).scalars().all()
            return [profile_summary(item) for item in profiles]

    @strawberry.field(name="myFollowing")
    def my_following(self, info: Info) -> list[ProfileSummary]:
        user_id = info.context.get("user_id")
        if user_id is None:
            return []
        with get_session() as session:
            profiles = session.execute(
                select(DbProfile)
                .join(Follow, Follow.following_id == DbProfile.user_id)
                .where(Follow.follower_id == user_id)
            ).scalars().all()
            return [profile_summary(item) for item in profiles]

    @strawberry.field(name="myFollowersPage")
    def my_followers_page(self, info: Info, after: str | None = None, limit: int = 20) -> ProfilePage:
        user_id = info.context.get("user_id")
        return Query._follow_page(user_id, True, after, limit, viewer_id=user_id)

    @strawberry.field(name="myFollowingPage")
    def my_following_page(self, info: Info, after: str | None = None, limit: int = 20) -> ProfilePage:
        user_id = info.context.get("user_id")
        return Query._follow_page(user_id, False, after, limit, viewer_id=user_id)

    @strawberry.field(name="followersPage")
    def followers_page(self, username: str, info: Info, after: str | None = None, limit: int = 20) -> ProfilePage:
        with get_session() as session:
            profile = find_profile(session, username)
            if profile is None:
                return ProfilePage(profiles=[], next_cursor=None)
            if not can_view_profile(session, info.context.get("user_id"), profile):
                return ProfilePage(profiles=[], next_cursor=None)
            user_id = profile.user_id
        return Query._follow_page(user_id, True, after, limit, viewer_id=info.context.get("user_id"))

    @strawberry.field(name="followingPage")
    def following_page(self, username: str, info: Info, after: str | None = None, limit: int = 20) -> ProfilePage:
        with get_session() as session:
            profile = find_profile(session, username)
            if profile is None:
                return ProfilePage(profiles=[], next_cursor=None)
            if not can_view_profile(session, info.context.get("user_id"), profile):
                return ProfilePage(profiles=[], next_cursor=None)
            user_id = profile.user_id
        return Query._follow_page(user_id, False, after, limit, viewer_id=info.context.get("user_id"))

    @staticmethod
    def _follow_page(
        user_id: int | None, incoming: bool, after: str | None, limit: int, viewer_id: int | None = None,
    ) -> ProfilePage:
        if user_id is None:
            return ProfilePage(profiles=[], next_cursor=None)
        page_size = max(1, min(limit, 100))
        try:
            offset = max(0, int(after or "0"))
        except ValueError as error:
            raise api_error("Invalid follow cursor", "VALIDATION_ERROR", 400) from error
        with get_session() as session:
            join_column = Follow.follower_id if incoming else Follow.following_id
            owner_column = Follow.following_id if incoming else Follow.follower_id
            rows = session.execute(
                select(DbProfile)
                .join(Follow, join_column == DbProfile.user_id)
                .where(owner_column == user_id)
                .order_by(Follow.id)
                .offset(offset)
                .limit(page_size + 1)
            ).scalars().all()
            has_more = len(rows) > page_size
            rows = rows[:page_size]
            following_ids: set[int] = set()
            if viewer_id is not None and rows:
                following_ids = set(session.execute(
                    select(Follow.following_id).where(
                        Follow.follower_id == viewer_id,
                        Follow.following_id.in_([row.user_id for row in rows]),
                    )
                ).scalars().all())
            return ProfilePage(
                profiles=[
                    profile_summary(profile, is_following=profile.user_id in following_ids)
                    for profile in rows
                ],
                next_cursor=str(offset + page_size) if has_more else None,
            )

    @strawberry.field
    def feed(self, info: Info, cursor: str | None = None, limit: int = 10) -> FeedPage:
        page_size = max(1, min(limit, 50))
        offset = 0
        if cursor:
            try:
                offset = max(0, int(cursor))
            except ValueError:
                raise api_error("Invalid feed cursor", "VALIDATION_ERROR", 400)

        with get_session() as session:
            viewer_id = info.context.get("user_id")
            rows = session.execute(
                select(DbPost, DbProfile)
                .join(DbProfile, DbPost.profile_id == DbProfile.id)
                .where(visible_post_filter(viewer_id))
                .order_by(DbPost.id.desc())
                .offset(offset)
                .limit(page_size + 1)
            ).all()
            has_more = len(rows) > page_size
            rows = rows[:page_size]
            liked_ids = liked_post_ids(session, viewer_id, [post for post, _ in rows])
            return FeedPage(
                items=[
                    feed_item(post, profile, viewer_id=viewer_id, liked_post_ids=liked_ids)
                    for post, profile in rows
                ],
                next_cursor=str(offset + page_size) if has_more else None,
            )


@strawberry.type
class Mutation:
    def _delete_account(self, info: Info) -> bool:
        user_id = info.context.get("user_id")
        if user_id is None:
            raise api_error("Must be logged in", "UNAUTHENTICATED", 401)
        if not delete_user_account(user_id):
            raise api_error("Account not found", "NOT_FOUND", 404)

        request = info.context.get("request")
        if request is not None:
            request.session.clear()
        return True

    @strawberry.mutation(name="deleteAccount")
    def delete_account(self, info: Info) -> bool:
        return self._delete_account(info)

    @strawberry.mutation(name="deleteProfile")
    def delete_profile(self, info: Info) -> bool:
        return self._delete_account(info)

    @strawberry.field
    def update_profile(self, input: UpdateProfileInput, info: Info) -> Profile | None:
        """Update the logged-in user's profile. Only the owner can edit."""
        user_id = info.context.get("user_id")
        if user_id is None:
            raise api_error("Must be logged in to update profile", "UNAUTHENTICATED", 401)

        with get_session() as session:
            profile = session.execute(
                select(DbProfile).where(DbProfile.user_id == user_id)
            ).scalar_one_or_none()
            if profile is None:
                raise api_error("Profile not found", "NOT_FOUND", 404)

            if input.username is not None:
                try:
                    username = validate_username(input.username)
                except ValueError as exc:
                    raise api_error(str(exc), "VALIDATION_ERROR", 400) from exc
                taken = session.execute(
                    select(DbProfile).where(
                        DbProfile.username == username,
                        DbProfile.id != profile.id,
                    )
                ).scalar_one_or_none()
                if taken is not None:
                    raise api_error("That username is already taken.", "CONFLICT", 409)
                profile.username = username
            if input.display_name is not None:
                try:
                    profile.display_name = validate_display_name(input.display_name)
                except ValueError as exc:
                    raise api_error(str(exc), "VALIDATION_ERROR", 400) from exc
            if input.bio is not None:
                try:
                    profile.bio = validate_bio(input.bio)
                except ValueError as exc:
                    raise api_error(str(exc), "VALIDATION_ERROR", 400) from exc
            if input.location is not None:
                profile.location = input.location
            if input.website is not None:
                try:
                    profile.website = validate_website(input.website)
                except ValueError as exc:
                    raise api_error(str(exc), "VALIDATION_ERROR", 400) from exc
            if input.avatar_url is not None:
                try:
                    profile.avatar_url = validate_avatar_url(input.avatar_url)
                except ValueError as exc:
                    raise api_error(str(exc), "VALIDATION_ERROR", 400) from exc
            if input.avatar_color is not None:
                profile.avatar_color = input.avatar_color
            if input.collab_status is not None:
                profile.collab_status = input.collab_status
            if input.open_to_collab is not None:
                profile.open_to_collab = input.open_to_collab
            if input.private_account is not None:
                profile.private_account = input.private_account

            session.commit()
            session.refresh(profile)
            return profile_to_graphql(profile)

    @strawberry.mutation(name="likePost")
    def like_post(self, id: strawberry.ID, info: Info) -> LikeResult:
        user_id = info.context.get("user_id")
        if user_id is None:
            raise api_error("Must be logged in to like a post", "UNAUTHENTICATED", 401)

        post_id = parse_int_id(id, "Post ID")
        with get_session() as session:
            post = session.execute(select(DbPost).where(DbPost.id == post_id)).scalar_one_or_none()
            if post is None:
                raise api_error("Post not found", "NOT_FOUND", 404)

            existing = session.execute(
                select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == user_id)
            ).scalar_one_or_none()
            if existing is None:
                session.add(PostLike(post_id=post_id, user_id=user_id))
                try:
                    session.flush()
                except IntegrityError:
                    session.rollback()
                else:
                    session.execute(
                        update(DbPost)
                        .where(DbPost.id == post_id)
                        .values(likes=DbPost.likes + 1)
                    )

                    # Find the profile that owns the post.
                    # We need this so we know which user should receive
                    # the like notification.
                    post_owner = session.execute(
                        select(DbProfile).where(
                            DbProfile.id == post.profile_id
                        )
                    ).scalar_one_or_none()

                    # Only create a notification when someone likes
                    # another user's post. Users should not receive
                    # notifications for liking their own posts.
                    if post_owner is not None and post_owner.user_id != user_id:
                        preferences = session.execute(
                            select(NotificationPreference).where(
                                NotificationPreference.user_id == post_owner.user_id
                            )
                        ).scalar_one_or_none()

                        likes_enabled = (
                            preferences.likes
                            if preferences is not None
                            else True
                        )

                        if likes_enabled:
                            session.add(
                                Notification(
                                    recipient_id=post_owner.user_id,
                                    actor_id=user_id,
                                    type="like",
                                    post_id=post_id,
                                    text="liked your post",
                                    read=False,
                                )
                            )

                session.commit()

            session.refresh(post)
            current_count = post.likes
            return LikeResult(liked=True, likes=current_count)

    @strawberry.mutation(name="unlikePost")
    def unlike_post(self, id: strawberry.ID, info: Info) -> LikeResult:
        user_id = info.context.get("user_id")
        if user_id is None:
            raise api_error("Must be logged in to unlike a post", "UNAUTHENTICATED", 401)

        post_id = parse_int_id(id, "Post ID")
        with get_session() as session:
            post = session.execute(select(DbPost).where(DbPost.id == post_id)).scalar_one_or_none()
            if post is None:
                raise api_error("Post not found", "NOT_FOUND", 404)

            existing = session.execute(
                select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == user_id)
            ).scalar_one_or_none()
            if existing is not None:
                session.delete(existing)
                session.commit()

            current_count = refresh_post_like_count(session, post_id)
            session.commit()
            session.refresh(post)
            return LikeResult(liked=False, likes=current_count)

    @strawberry.mutation(name="markNotificationRead")
    def mark_notification_read(
        self,
        id: strawberry.ID,
        info: Info,
    ) -> bool:
        """
        Mark one notification as read.

        A user can only mark their own notifications as read.
        """

        user_id = info.context.get("user_id")

        if user_id is None:
            raise api_error(
                "Must be logged in to update notifications",
                "UNAUTHENTICATED",
                401,
            )

        notification_id = parse_int_id(id, "Notification ID")

        with get_session() as session:
            notification = session.execute(
                select(Notification).where(
                    Notification.id == notification_id,
                    Notification.recipient_id == user_id,
                )
            ).scalar_one_or_none()

            if notification is None:
                raise api_error(
                    "Notification not found",
                    "NOT_FOUND",
                    404,
                )

            notification.read = True
            session.commit()

            return True

    @strawberry.mutation(name="markAllNotificationsRead")
    def mark_all_notifications_read(self, info: Info) -> bool:
        """
        Mark every notification belonging to the logged-in user as read.
        """

        user_id = info.context.get("user_id")

        if user_id is None:
            raise api_error(
                "Must be logged in to update notifications",
                "UNAUTHENTICATED",
                401,
            )

        with get_session() as session:
            notifications = session.execute(
                select(Notification).where(
                    Notification.recipient_id == user_id,
                    Notification.read.is_(False),
                )
            ).scalars().all()

            for notification in notifications:
                notification.read = True

            session.commit()

            return True

    @strawberry.mutation(name="updateNotificationPreferences")
    def update_notification_preferences(
        self,
        input: UpdateNotificationPreferencesInput,
        info: Info,
    ) -> NotificationPreferences:
        """Update notification preferences for the current user."""

        user_id = info.context.get("user_id")

        if user_id is None:
            raise api_error(
                "Must be logged in to update notification preferences",
                "UNAUTHENTICATED",
                401,
            )

        if (
            input.email_digest is not None
            and input.email_digest not in {"off", "daily", "weekly"}
        ):
            raise api_error(
                "emailDigest must be one of: off, daily, weekly",
                "VALIDATION_ERROR",
                400,
            )

        with get_session() as session:
            preferences = session.execute(
                select(NotificationPreference).where(
                    NotificationPreference.user_id == user_id
                )
            ).scalar_one_or_none()

            if preferences is None:
                preferences = NotificationPreference(user_id=user_id)
                session.add(preferences)
            if input.collab_requests is not None:
                preferences.collab_requests = input.collab_requests
            if input.messages is not None:
                preferences.messages = input.messages
            if input.likes is not None:
                preferences.likes = input.likes
            if input.comments is not None:
                preferences.comments = input.comments
            if input.new_followers is not None:
                preferences.new_followers = input.new_followers
            if input.live_alerts is not None:
                preferences.live_alerts = input.live_alerts
            if input.trending_sounds is not None:
                preferences.trending_sounds = input.trending_sounds
            if input.product_updates is not None:
                preferences.product_updates = input.product_updates
            if input.email_digest is not None:
                preferences.email_digest = input.email_digest
            if input.quiet_hours is not None:
                preferences.quiet_hours = input.quiet_hours
            session.commit()
            session.refresh(preferences)

            return NotificationPreferences(
                collab_requests=preferences.collab_requests,
                messages=preferences.messages,
                likes=preferences.likes,
                comments=preferences.comments,
                new_followers=preferences.new_followers,
                live_alerts=preferences.live_alerts,
                trending_sounds=preferences.trending_sounds,
                product_updates=preferences.product_updates,
                email_digest=preferences.email_digest,
                quiet_hours=preferences.quiet_hours,
            )

    @strawberry.mutation
    def follow(self, username: str, info: Info) -> FollowResult:
        with get_session() as session:
            follower = owner_profile(session, info)
            target = find_profile(session, username)
            if target is None:
                raise api_error("Profile not found", "NOT_FOUND", 404)
            if target.user_id == follower.user_id:
                raise api_error("You cannot follow yourself", "VALIDATION_ERROR", 400)

            existing = session.execute(
                select(Follow).where(
                    Follow.follower_id == follower.user_id,
                    Follow.following_id == target.user_id,
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(Follow(
                    follower_id=follower.user_id,
                    following_id=target.user_id,
                ))

                follower.following += 1
                target.followers += 1

                # Check whether the target user wants new-follower notifications.
                preferences = session.execute(
                    select(NotificationPreference).where(
                        NotificationPreference.user_id == target.user_id
                    )
                ).scalar_one_or_none()

                new_followers_enabled = (
                    preferences.new_followers
                    if preferences is not None
                    else False
                )

                if new_followers_enabled:
                    session.add(
                        Notification(
                            recipient_id=target.user_id,
                            actor_id=follower.user_id,
                            type="follow",
                            post_id=None,
                            text="started following you",
                            read=False,
                        )
                    )

                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    raise api_error("That follow already exists", "CONFLICT", 409)
            return FollowResult(
                following=True, followers=target.followers,
                following_count=follower.following,
            )

    @strawberry.mutation
    def unfollow(self, username: str, info: Info) -> FollowResult:
        with get_session() as session:
            follower = owner_profile(session, info)
            target = find_profile(session, username)
            if target is None:
                raise api_error("Profile not found", "NOT_FOUND", 404)

            existing = session.execute(
                select(Follow).where(
                    Follow.follower_id == follower.user_id,
                    Follow.following_id == target.user_id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                session.delete(existing)
                follower.following = max(0, follower.following - 1)
                target.followers = max(0, target.followers - 1)
                session.commit()
            return FollowResult(
                following=False, followers=target.followers,
                following_count=follower.following,
            )

    @strawberry.mutation(name="createPost")
    def create_post(self, input: PostInput, info: Info) -> ContentItem:
        with get_session() as session:
            profile = owner_profile(session, info)
            media = owned_media(session, profile.user_id, input.media_id, "Media ID")
            thumbnail_media = owned_media(session, profile.user_id, input.thumbnail_media_id, "Thumbnail media ID")
            if not thumbnail_media.content_type.startswith("image/"):
                raise api_error("Thumbnail must be an image", "VALIDATION_ERROR", 400)
            post = DbPost(
                profile_id=profile.id,
                thumbnail=thumbnail_media.url,
                media_url=media.url,
                media_id=media.id,
                thumbnail_media_id=thumbnail_media.id,
                caption=input.caption,
                collab_with=input.collab_with,
                hashtags=input.hashtags,
                audio=input.audio,
                visibility=input.visibility,
                allow_comments=input.allow_comments,
                allow_collabs=input.allow_collabs,
                duration_sec=input.duration_sec,
            )
            session.add(post)
            session.commit()
            session.refresh(post)
            return post_to_graphql(post, viewer_id=profile.user_id)

    @strawberry.mutation(name="updatePost")
    def update_post(self, id: strawberry.ID, input: UpdatePostInput, info: Info) -> ContentItem:
        with get_session() as session:
            profile = owner_profile(session, info)
            post_id = parse_int_id(id, "Post ID")
            post = session.execute(
                select(DbPost).where(DbPost.id == post_id, DbPost.profile_id == profile.id)
            ).scalar_one_or_none()
            if post is None:
                raise api_error("Post not found", "NOT_FOUND", 404)
            if input.caption is not None:
                post.caption = input.caption
            if input.collab_with is not None:
                post.collab_with = input.collab_with
            if input.hashtags is not None:
                post.hashtags = input.hashtags
            if input.audio is not None:
                post.audio = input.audio
            if input.visibility is not None:
                post.visibility = input.visibility
            if input.allow_comments is not None:
                post.allow_comments = input.allow_comments
            if input.allow_collabs is not None:
                post.allow_collabs = input.allow_collabs
            if input.duration_sec is not None:
                post.duration_sec = input.duration_sec
            session.commit()
            session.refresh(post)
            return post_to_graphql(post, viewer_id=profile.user_id)

    @strawberry.mutation(name="deletePost")
    def delete_post(self, id: strawberry.ID, info: Info) -> bool:
        with get_session() as session:
            profile = owner_profile(session, info)
            post_id = parse_int_id(id, "Post ID")
            post = session.execute(
                select(DbPost).where(DbPost.id == post_id, DbPost.profile_id == profile.id)
            ).scalar_one_or_none()
            if post is None:
                return False
            session.delete(post)
            session.commit()
            return True

    @strawberry.mutation(name="createPlaylist")
    def create_playlist(self, input: PlaylistInput, info: Info) -> Playlist:
        with get_session() as session:
            profile = owner_profile(session, info)
            playlist = DbPlaylist(
                profile_id=profile.id, title=input.title, cover=input.cover,
                item_label=input.item_label,
            )
            session.add(playlist)
            session.commit()
            session.refresh(playlist)
            return Playlist(
                id=str(playlist.id), title=playlist.title, cover=playlist.cover,
                item_label=playlist.item_label, plays=playlist.plays,
            )

    @strawberry.mutation(name="updatePlaylist")
    def update_playlist(self, id: strawberry.ID, input: UpdatePlaylistInput, info: Info) -> Playlist:
        with get_session() as session:
            profile = owner_profile(session, info)
            playlist_id = parse_int_id(id, "Playlist ID")
            playlist = session.execute(
                select(DbPlaylist).where(
                    DbPlaylist.id == playlist_id, DbPlaylist.profile_id == profile.id
                )
            ).scalar_one_or_none()
            if playlist is None:
                raise api_error("Playlist not found", "NOT_FOUND", 404)
            if input.title is not None:
                playlist.title = input.title
            if input.cover is not None:
                playlist.cover = input.cover
            if input.item_label is not None:
                playlist.item_label = input.item_label
            session.commit()
            session.refresh(playlist)
            return Playlist(
                id=str(playlist.id), title=playlist.title, cover=playlist.cover,
                item_label=playlist.item_label, plays=playlist.plays,
            )

    @strawberry.mutation(name="deletePlaylist")
    def delete_playlist(self, id: strawberry.ID, info: Info) -> bool:
        with get_session() as session:
            profile = owner_profile(session, info)
            playlist_id = parse_int_id(id, "Playlist ID")
            playlist = session.execute(
                select(DbPlaylist).where(
                    DbPlaylist.id == playlist_id, DbPlaylist.profile_id == profile.id
                )
            ).scalar_one_or_none()
            if playlist is None:
                return False
            session.delete(playlist)
            session.commit()
            return True


schema = strawberry.Schema(query=Query, mutation=Mutation)


def get_context_for_request(request: Request):
    """Extract user_id from session and pass to GraphQL context."""
    user_id = request.session.get("user_id")
    return {"user_id": user_id, "request": request}


graphql_app = GraphQLRouter(schema, context_getter=get_context_for_request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    seed_database()
    yield


app = FastAPI(title="ConnextionZ Profile API", lifespan=lifespan)

rate_limit_enabled = os.getenv("RATE_LIMIT_ENABLED", "true").lower() not in {"0", "false", "no"}
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["120/minute"],
    enabled=rate_limit_enabled,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

environment = os.getenv("ENVIRONMENT", "development").lower()
session_secret = os.getenv("SESSION_SECRET")
if not session_secret:
    if environment == "production":
        raise RuntimeError("SESSION_SECRET must be set in production")
    session_secret = "local-development-session-secret"

session_cookie_secure = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
session_same_site = os.getenv("SESSION_COOKIE_SAMESITE", "lax").lower()
if session_same_site not in {"lax", "strict", "none"}:
    raise RuntimeError("SESSION_COOKIE_SAMESITE must be lax, strict, or none")
if session_same_site == "none" and not session_cookie_secure:
    raise RuntimeError("SESSION_COOKIE_SECURE must be true when SESSION_COOKIE_SAMESITE is none")

session_max_age = int(os.getenv("SESSION_MAX_AGE", str(60 * 60 * 24 * 7)))

app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    session_cookie="connextionz_session",
    max_age=session_max_age,
    same_site=session_same_site,
    https_only=session_cookie_secure,
)

cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()]
if not cors_origins:
    if environment == "production":
        raise RuntimeError("CORS_ORIGINS must be set in production")
    cors_origins = [
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5174", "http://127.0.0.1:5174",
        "http://localhost:5175", "http://127.0.0.1:5175",
        "http://localhost:3000", "http://127.0.0.1:3000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

allowed_hosts = [host.strip() for host in os.getenv("ALLOWED_HOSTS", "").split(",") if host.strip()]
if allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

if os.getenv("REQUIRE_HTTPS", "false").lower() in {"1", "true", "yes"}:
    app.add_middleware(HTTPSRedirectMiddleware)

app.add_middleware(SlowAPIMiddleware)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "request method=%s path=%s status=%s duration_ms=%.1f client=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        request.client.host if request.client else "unknown",
    )
    return response

app.include_router(graphql_app, prefix="/graphql")
app.include_router(create_media_router(limiter))


app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")
app.include_router(create_auth_router(limiter))


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
