from __future__ import annotations

import strawberry


@strawberry.type
class ContentItem:
    id: str
    thumbnail: str
    caption: str
    views: int
    likes: int
    is_liked: bool = strawberry.field(name="isLiked", default=False)
    media_url: str | None = strawberry.field(name="mediaUrl", default=None)
    collab_with: str | None = strawberry.field(name="collabWith")
    hashtags: list[str] = strawberry.field(default_factory=list)
    audio: str = "Original Sound"
    visibility: str = "public"
    allow_comments: bool = strawberry.field(name="allowComments", default=True)
    allow_collabs: bool = strawberry.field(name="allowCollabs", default=True)
    duration_sec: float = strawberry.field(name="durationSec", default=0.0)
    comments: int = 0
    shares: int = 0
    saves: int = 0


@strawberry.type
class LikeResult:
    liked: bool
    likes: int

@strawberry.type
class NotificationItem:
    """
    Notification data returned by the GraphQL API.

    This shape intentionally matches the fields expected by the frontend
    notification store.
    """

    id: str

    # The kind of event that created the notification.
    # Examples: "like", "comment", "follow", "mention"
    type: str

    # Username/handle of the user who caused the notification.
    # This can be None for system or milestone notifications.
    actor: str | None

    # Human-readable text displayed in the notification list.
    # Example: "liked your post"
    text: str

    # Related post, if the notification belongs to a post.
    post_id: str | None = strawberry.field(
        name="postId",
        default=None,
    )

    # Unix timestamp in milliseconds so it matches the frontend's
    # existing AppNotification.createdAt field.
    created_at: int = strawberry.field(
        name="createdAt",
    )

    # True once the user has read/opened the notification.
    read: bool

@strawberry.type
class Playlist:
    id: str
    title: str
    cover: str
    item_label: str = strawberry.field(name="itemLabel")
    plays: int


@strawberry.type
class ProfileSummary:
    id: str
    username: str
    display_name: str = strawberry.field(name="displayName")
    avatar_url: str = strawberry.field(name="avatarUrl")
    avatar_color: str = strawberry.field(name="avatarColor")
    verified: bool = False
    collab_score: float = strawberry.field(name="collabScore", default=0.0)
    collab_count: int = strawberry.field(name="collabCount", default=0)
    followers: int = 0
    following: int = 0
    open_to_collab: bool = strawberry.field(name="openToCollab", default=True)
    private_account: bool = strawberry.field(name="privateAccount", default=False)
    is_following: bool = strawberry.field(name="isFollowing", default=False)


@strawberry.type
class FollowResult:
    following: bool
    followers: int
    following_count: int = strawberry.field(name="followingCount")


@strawberry.type
class FeedItem:
    id: str
    thumbnail: str
    media_url: str | None = strawberry.field(name="mediaUrl", default=None)
    caption: str
    views: int
    likes: int
    is_liked: bool = strawberry.field(name="isLiked", default=False)
    collab_with: str | None = strawberry.field(name="collabWith", default=None)
    hashtags: list[str] = strawberry.field(default_factory=list)
    audio: str = "Original Sound"
    visibility: str = "public"
    allow_comments: bool = strawberry.field(name="allowComments", default=True)
    allow_collabs: bool = strawberry.field(name="allowCollabs", default=True)
    duration_sec: float = strawberry.field(name="durationSec", default=0.0)
    comments: int = 0
    shares: int = 0
    saves: int = 0
    creator: ProfileSummary


@strawberry.type
class FeedPage:
    items: list[FeedItem]
    next_cursor: str | None = strawberry.field(name="nextCursor", default=None)


@strawberry.type
class PostPage:
    items: list[ContentItem]
    next_cursor: str | None = strawberry.field(name="nextCursor", default=None)


@strawberry.type
class ProfilePage:
    profiles: list[ProfileSummary]
    next_cursor: str | None = strawberry.field(name="nextCursor", default=None)


@strawberry.type
class HashtagResult:
    tag: str
    posts: int
    views: int


@strawberry.type
class SoundResult:
    id: str
    title: str
    creator: str
    creator_avatar: str = strawberry.field(name="creatorAvatar")
    artwork: str
    genre: str
    video_count: int = strawberry.field(name="videoCount")
    total_plays: int = strawberry.field(name="totalPlays")
    rank: int
    growth_pct: int = strawberry.field(name="growthPct")
    duration: str
    bpm: int
@strawberry.type


class NotificationPreferences:
    collab_requests: bool = strawberry.field(name="collabRequests")
    messages: bool
    likes: bool
    comments: bool
    new_followers: bool = strawberry.field(name="newFollowers")
    live_alerts: bool = strawberry.field(name="liveAlerts")
    trending_sounds: bool = strawberry.field(name="trendingSounds")
    product_updates: bool = strawberry.field(name="productUpdates")
    email_digest: str = strawberry.field(name="emailDigest")
    quiet_hours: bool = strawberry.field(name="quietHours")


@strawberry.input
class UpdateNotificationPreferencesInput:
    collab_requests: bool | None = strawberry.field(name="collabRequests", default=None)
    messages: bool | None = None
    likes: bool | None = None
    comments: bool | None = None
    new_followers: bool | None = strawberry.field(name="newFollowers", default=None)
    live_alerts: bool | None = strawberry.field(name="liveAlerts", default=None)
    trending_sounds: bool | None = strawberry.field(name="trendingSounds", default=None)
    product_updates: bool | None = strawberry.field(name="productUpdates", default=None)
    email_digest: str | None = strawberry.field(name="emailDigest", default=None)
    quiet_hours: bool | None = strawberry.field(name="quietHours", default=None)

@strawberry.input
class PostInput:
    media_id: strawberry.ID = strawberry.field(name="mediaId")
    thumbnail_media_id: strawberry.ID = strawberry.field(name="thumbnailMediaId")
    caption: str | None = None
    collab_with: str | None = strawberry.field(name="collabWith", default=None)
    hashtags: list[str] = strawberry.field(default_factory=list)
    audio: str = "Original Sound"
    visibility: str = "public"
    allow_comments: bool = strawberry.field(name="allowComments", default=True)
    allow_collabs: bool = strawberry.field(name="allowCollabs", default=True)
    duration_sec: float = strawberry.field(name="durationSec", default=0.0)


@strawberry.input
class PlaylistInput:
    title: str
    cover: str
    item_label: str = strawberry.field(name="itemLabel")


@strawberry.input
class UpdatePostInput:
    caption: str | None = None
    collab_with: str | None = strawberry.field(name="collabWith", default=None)
    hashtags: list[str] | None = None
    audio: str | None = None
    visibility: str | None = None
    allow_comments: bool | None = strawberry.field(name="allowComments", default=None)
    allow_collabs: bool | None = strawberry.field(name="allowCollabs", default=None)
    duration_sec: float | None = strawberry.field(name="durationSec", default=None)


@strawberry.input
class UpdatePlaylistInput:
    title: str | None = None
    cover: str | None = None
    item_label: str | None = strawberry.field(name="itemLabel", default=None)


@strawberry.type
class Profile:
    id: str
    username: str
    display_name: str = strawberry.field(name="displayName")
    avatar_url: str = strawberry.field(name="avatarUrl")
    avatar_color: str = strawberry.field(name="avatarColor")
    bio: str | None = None
    location: str | None = None
    website: str | None = None
    verified: bool = False
    online: bool = True
    collab_status: str | None = strawberry.field(name="collabStatus")
    collab_score: float = strawberry.field(name="collabScore")
    collab_count: int = strawberry.field(name="collabCount")
    followers: int = 0
    following: int = 0
    open_to_collab: bool = strawberry.field(name="openToCollab")
    private_account: bool = strawberry.field(name="privateAccount", default=False)
    response_time: str = strawberry.field(name="responseTime")
    posts: list[ContentItem]
    posts_page: PostPage = strawberry.field(name="postsPage")
    playlists: list[Playlist]
    is_following: bool = strawberry.field(name="isFollowing", default=False)


@strawberry.input
class UpdateProfileInput:
    username: str | None = None
    display_name: str | None = strawberry.field(name="displayName", default=None)
    bio: str | None = None
    location: str | None = None
    website: str | None = None
    avatar_url: str | None = strawberry.field(name="avatarUrl", default=None)
    avatar_color: str | None = strawberry.field(name="avatarColor", default=None)
    collab_status: str | None = strawberry.field(name="collabStatus", default=None)
    open_to_collab: bool | None = strawberry.field(name="openToCollab", default=None)
    private_account: bool | None = strawberry.field(name="privateAccount", default=None)