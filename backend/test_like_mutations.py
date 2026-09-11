"""Concurrency regression tests for post like mutations."""

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from backend.app.main import Mutation, Query
from backend.app.graphql_types import UpdateNotificationPreferencesInput
from backend.app.models import Base, Notification, NotificationPreference, Post, PostLike, Profile, User


class LikeMutationConcurrencyTests(unittest.TestCase):
    def test_concurrent_likes_from_one_user_are_idempotent(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "likes.db"
            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False, "timeout": 10},
            )
            Base.metadata.create_all(engine)
            with Session(engine) as session:
                user = User(email="race-user@example.test")
                session.add(user)
                session.flush()
                profile = Profile(
                    user_id=user.id,
                    username="race-user",
                    display_name="Race User",
                )
                post = Post(
                    profile=profile,
                    thumbnail="/media/post.jpg",
                    likes=0,
                )
                session.add(post)
                session.commit()
                user_id = user.id
                post_id = post.id

            read_barrier = threading.Barrier(2)
            synchronized_reads = 0
            read_lock = threading.Lock()

            @event.listens_for(engine, "before_cursor_execute")
            def synchronize_like_reads(connection, cursor, statement, parameters, context, executemany):
                nonlocal synchronized_reads
                normalized = statement.strip().upper()
                if normalized.startswith("SELECT") and "FROM POST_LIKES" in normalized:
                    with read_lock:
                        synchronized_reads += 1
                        should_wait = synchronized_reads <= 2
                    if should_wait:
                        read_barrier.wait(timeout=10)

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(context={"user_id": user_id})

            def like_post() -> object:
                return Mutation().like_post(str(post_id), info)

            try:
                with patch("backend.app.main.get_session", side_effect=get_test_session):
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        results = list(executor.map(lambda _: like_post(), range(2)))

                with Session(engine) as session:
                    saved_post = session.get(Post, post_id)
                    saved_likes = session.scalars(
                        select(PostLike).where(PostLike.post_id == post_id, PostLike.user_id == user_id)
                    ).all()
                    saved_notifications = session.scalars(
                        select(Notification).where(
                            Notification.actor_id == user_id,
                            Notification.post_id == post_id,
                            Notification.type == "like",
                        )
                    ).all()
            finally:
                event.remove(engine, "before_cursor_execute", synchronize_like_reads)
                engine.dispose()

            self.assertTrue(all(result.liked for result in results))
            self.assertEqual(saved_post.likes, 1)
            self.assertEqual(len(saved_likes), 1)
            self.assertEqual(len(saved_notifications), 0)

    def test_concurrent_likes_create_only_one_notification(self) -> None:
        """Concurrent duplicate likes should create only one like notification."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "like-notification-race.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False, "timeout": 10},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                owner = User(email="race-owner@example.test")
                liker = User(email="race-liker@example.test")

                session.add_all([owner, liker])
                session.flush()

                owner_profile = Profile(
                    user_id=owner.id,
                    username="race-owner",
                    display_name="Race Owner",
                )

                post = Post(
                    profile=owner_profile,
                    thumbnail="/media/post.jpg",
                    likes=0,
                )

                session.add(post)
                session.commit()

                owner_id = owner.id
                liker_id = liker.id
                post_id = post.id

            read_barrier = threading.Barrier(2)
            synchronized_reads = 0
            read_lock = threading.Lock()

            @event.listens_for(engine, "before_cursor_execute")
            def synchronize_like_reads(
                connection,
                cursor,
                statement,
                parameters,
                context,
                executemany,
            ):
                nonlocal synchronized_reads

                normalized = statement.strip().upper()

                if normalized.startswith("SELECT") and "FROM POST_LIKES" in normalized:
                    with read_lock:
                        synchronized_reads += 1
                        should_wait = synchronized_reads <= 2

                    if should_wait:
                        read_barrier.wait(timeout=10)

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": liker_id}
            )

            def like_post() -> object:
                return Mutation().like_post(
                    str(post_id),
                    info,
                )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        results = list(
                            executor.map(
                                lambda _: like_post(),
                                range(2),
                            )
                        )

                with Session(engine) as session:
                    notifications = session.scalars(
                        select(Notification).where(
                            Notification.recipient_id == owner_id,
                            Notification.actor_id == liker_id,
                            Notification.post_id == post_id,
                            Notification.type == "like",
                        )
                    ).all()

            finally:
                event.remove(
                    engine,
                    "before_cursor_execute",
                    synchronize_like_reads,
                )
                engine.dispose()

            self.assertTrue(all(result.liked for result in results))
            self.assertEqual(len(notifications), 1)

    def test_concurrent_likes_from_different_users_are_counted(self) -> None:
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "likes.db"
            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False, "timeout": 10},
            )
            Base.metadata.create_all(engine)
            with Session(engine) as session:
                user_one = User(email="user-one@example.test")
                user_two = User(email="user-two@example.test")
                session.add_all([user_one, user_two])
                session.flush()
                profile = Profile(
                    user_id=user_one.id,
                    username="profile-owner",
                    display_name="Profile Owner",
                )
                post = Post(
                    profile=profile,
                    thumbnail="/media/post.jpg",
                    likes=0,
                )
                session.add(post)
                session.commit()
                user_ids = [user_one.id, user_two.id]
                post_id = post.id

            read_barrier = threading.Barrier(2)
            synchronized_reads = 0
            read_lock = threading.Lock()

            @event.listens_for(engine, "before_cursor_execute")
            def synchronize_like_reads(connection, cursor, statement, parameters, context, executemany):
                nonlocal synchronized_reads
                normalized = statement.strip().upper()
                if normalized.startswith("SELECT") and "FROM POST_LIKES" in normalized:
                    with read_lock:
                        synchronized_reads += 1
                        should_wait = synchronized_reads <= 2
                    if should_wait:
                        read_barrier.wait(timeout=10)

            def get_test_session() -> Session:
                return Session(engine)

            def like_post(user_id: int) -> object:
                info = SimpleNamespace(context={"user_id": user_id})
                return Mutation().like_post(str(post_id), info)

            try:
                with patch("backend.app.main.get_session", side_effect=get_test_session):
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        results = list(executor.map(like_post, user_ids))

                with Session(engine) as session:
                    saved_post = session.get(Post, post_id)
                    saved_likes = session.scalars(
                        select(PostLike).where(PostLike.post_id == post_id)
                    ).all()
            finally:
                event.remove(engine, "before_cursor_execute", synchronize_like_reads)
                engine.dispose()

            self.assertTrue(all(result.liked for result in results))
            self.assertEqual(saved_post.likes, 2)
            self.assertEqual(len(saved_likes), 2)


    def test_like_creates_notification_for_post_owner(self) -> None:
        """Liking another user's post should create one unread like notification."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "notifications.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                # User who owns the post and should receive the notification.
                owner = User(email="owner@example.test")

                # User who likes the post and becomes the notification actor.
                liker = User(email="liker@example.test")

                session.add_all([owner, liker])
                session.flush()

                owner_profile = Profile(
                    user_id=owner.id,
                    username="post-owner",
                    display_name="Post Owner",
                )

                post = Post(
                    profile=owner_profile,
                    thumbnail="/media/post.jpg",
                    likes=0,
                )

                session.add(post)
                session.commit()

                owner_id = owner.id
                liker_id = liker.id
                post_id = post.id

            # Make like_post() use this test database instead of
            # the application's normal database.
            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": liker_id}
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    result = Mutation().like_post(
                        str(post_id),
                        info,
                    )

                with Session(engine) as session:
                    notification = session.scalar(
                        select(Notification).where(
                            Notification.recipient_id == owner_id,
                            Notification.actor_id == liker_id,
                            Notification.post_id == post_id,
                        )
                    )

            finally:
                engine.dispose()

            # Existing like behavior should still succeed.
            self.assertTrue(result.liked)

            # A notification should have been created.
            self.assertIsNotNone(notification)

            # Verify the notification contains the correct data.
            self.assertEqual(notification.recipient_id, owner_id)
            self.assertEqual(notification.actor_id, liker_id)
            self.assertEqual(notification.post_id, post_id)
            self.assertEqual(notification.type, "like")
            self.assertEqual(notification.text, "liked your post")
            self.assertFalse(notification.read)

    def test_like_notification_respects_disabled_preference(self) -> None:
        """A post owner with likes disabled should not receive a like notification."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "notifications-like-disabled.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                owner = User(email="owner-disabled@example.test")
                liker = User(email="liker-disabled@example.test")

                session.add_all([owner, liker])
                session.flush()

                owner_profile = Profile(
                    user_id=owner.id,
                    username="post-owner-disabled",
                    display_name="Post Owner Disabled",
                )

                post = Post(
                    profile=owner_profile,
                    thumbnail="/media/post.jpg",
                    likes=0,
                )

                session.add(post)
                session.flush()

                session.add(
                    NotificationPreference(
                        user_id=owner.id,
                        likes=False,
                    )
                )

                session.commit()

                owner_id = owner.id
                liker_id = liker.id
                post_id = post.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": liker_id}
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    result = Mutation().like_post(
                        str(post_id),
                        info,
                    )

                with Session(engine) as session:
                    notification = session.scalar(
                        select(Notification).where(
                            Notification.recipient_id == owner_id,
                            Notification.actor_id == liker_id,
                            Notification.post_id == post_id,
                            Notification.type == "like",
                        )
                    )

            finally:
                engine.dispose()

            self.assertTrue(result.liked)
            self.assertIsNone(notification)

    def test_mark_notification_read(self) -> None:
        """A user should be able to mark their own notification as read."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "notifications.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                user = User(email="reader@example.test")
                actor = User(email="actor@example.test")

                session.add_all([user, actor])
                session.flush()

                notification = Notification(
                    recipient_id=user.id,
                    actor_id=actor.id,
                    type="like",
                    text="liked your post",
                    read=False,
                )

                session.add(notification)
                session.commit()

                user_id = user.id
                notification_id = notification.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": user_id}
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    result = Mutation().mark_notification_read(
                        str(notification_id),
                        info,
                    )

                with Session(engine) as session:
                    saved_notification = session.get(
                        Notification,
                        notification_id,
                    )

            finally:
                engine.dispose()

            self.assertTrue(result)
            self.assertTrue(saved_notification.read)

    def test_mark_all_notifications_read(self) -> None:
        """A user should be able to mark all of their notifications as read."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "notifications.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                user = User(email="reader@example.test")
                actor = User(email="actor@example.test")

                session.add_all([user, actor])
                session.flush()

                notification_one = Notification(
                    recipient_id=user.id,
                    actor_id=actor.id,
                    type="like",
                    text="liked your post",
                    read=False,
                )

                notification_two = Notification(
                    recipient_id=user.id,
                    actor_id=actor.id,
                    type="follow",
                    text="started following you",
                    read=False,
                )

                session.add_all([
                    notification_one,
                    notification_two,
                ])
                session.commit()

                user_id = user.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": user_id}
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    result = Mutation().mark_all_notifications_read(info)

                with Session(engine) as session:
                    saved_notifications = session.scalars(
                        select(Notification).where(
                            Notification.recipient_id == user_id
                        )
                    ).all()

            finally:
                engine.dispose()

            self.assertTrue(result)
            self.assertEqual(len(saved_notifications), 2)
            self.assertTrue(
                all(notification.read for notification in saved_notifications)
            )
    def test_notifications_query_returns_current_users_notifications(self) -> None:
        """The notifications query should only return rows for the logged-in user."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "notifications-query.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                recipient = User(email="recipient@example.test")
                actor = User(email="actor@example.test")
                other_user = User(email="other@example.test")

                session.add_all([recipient, actor, other_user])
                session.flush()

                actor_profile = Profile(
                    user_id=actor.id,
                    username="actor-user",
                    display_name="Actor User",
                )

                session.add(actor_profile)
                session.flush()

                notification = Notification(
                    recipient_id=recipient.id,
                    actor_id=actor.id,
                    type="like",
                    text="liked your post",
                    read=False,
                )

                other_notification = Notification(
                    recipient_id=other_user.id,
                    actor_id=actor.id,
                    type="follow",
                    text="started following you",
                    read=False,
                )

                session.add_all([notification, other_notification])
                session.commit()

                recipient_id = recipient.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": recipient_id}
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    from backend.app.main import Query
                    results = Query().notifications(info)

            finally:
                engine.dispose()

            self.assertEqual(len(results), 1)

            returned = results[0]

            self.assertEqual(returned.type, "like")
            self.assertEqual(returned.actor, "actor-user")
            self.assertEqual(returned.text, "liked your post")
            self.assertFalse(returned.read)


    def test_user_cannot_mark_another_users_notification_read(self) -> None:
        """A user must not be able to update someone else's notification."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "notifications-auth.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                owner = User(email="owner@example.test")
                other_user = User(email="other@example.test")
                actor = User(email="actor@example.test")

                session.add_all([owner, other_user, actor])
                session.flush()

                notification = Notification(
                    recipient_id=owner.id,
                    actor_id=actor.id,
                    type="like",
                    text="liked your post",
                    read=False,
                )

                session.add(notification)
                session.commit()

                other_user_id = other_user.id
                notification_id = notification.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": other_user_id}
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    with self.assertRaises(Exception):
                        Mutation().mark_notification_read(
                            str(notification_id),
                            info,
                        )

                with Session(engine) as session:
                    saved_notification = session.get(
                        Notification,
                        notification_id,
                    )

            finally:
                engine.dispose()

            self.assertFalse(saved_notification.read)


    def test_follow_creates_notification_for_target_user(self) -> None:
        """Following another user should create one unread follow notification."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "follow-notification.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                follower_user = User(email="follower@example.test")
                target_user = User(email="target@example.test")

                session.add_all([follower_user, target_user])
                session.flush()

                follower_profile = Profile(
                    user_id=follower_user.id,
                    username="follower-user",
                    display_name="Follower User",
                )

                target_profile = Profile(
                    user_id=target_user.id,
                    username="target-user",
                    display_name="Target User",
                )

                session.add_all([follower_profile, target_profile])
                session.add(
                    NotificationPreference(
                        user_id=target_user.id,
                        new_followers=True,
                    )
                )
                session.commit()

                follower_user_id = follower_user.id
                target_user_id = target_user.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": follower_user_id}
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    result = Mutation().follow(
                        "target-user",
                        info,
                    )

                with Session(engine) as session:
                    notification = session.scalar(
                        select(Notification).where(
                            Notification.recipient_id == target_user_id,
                            Notification.actor_id == follower_user_id,
                            Notification.type == "follow",
                        )
                    )

            finally:
                engine.dispose()

            self.assertTrue(result.following)
            self.assertIsNotNone(notification)
            self.assertEqual(notification.recipient_id, target_user_id)
            self.assertEqual(notification.actor_id, follower_user_id)
            self.assertEqual(notification.type, "follow")
            self.assertEqual(notification.text, "started following you")
            self.assertIsNone(notification.post_id)
            self.assertFalse(notification.read)

    def test_follow_notification_respects_disabled_preference(self) -> None:
        """A user with new follower notifications disabled should not receive one."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "follow-notification-disabled.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                follower_user = User(email="follower-disabled@example.test")
                target_user = User(email="target-disabled@example.test")

                session.add_all([follower_user, target_user])
                session.flush()

                follower_profile = Profile(
                    user_id=follower_user.id,
                    username="follower-disabled",
                    display_name="Follower Disabled",
                )

                target_profile = Profile(
                    user_id=target_user.id,
                    username="target-disabled",
                    display_name="Target Disabled",
                )

                session.add_all([follower_profile, target_profile])

                session.add(
                    NotificationPreference(
                        user_id=target_user.id,
                        new_followers=False,
                    )
                )

                session.commit()

                follower_user_id = follower_user.id
                target_user_id = target_user.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": follower_user_id}
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    result = Mutation().follow(
                        "target-disabled",
                        info,
                    )

                with Session(engine) as session:
                    notification = session.scalar(
                        select(Notification).where(
                            Notification.recipient_id == target_user_id,
                            Notification.actor_id == follower_user_id,
                            Notification.type == "follow",
                        )
                    )

            finally:
                engine.dispose()

            self.assertTrue(result.following)
            self.assertIsNone(notification)


    def test_notifications_query_supports_pagination(self) -> None:
        """The notifications query should respect limit and offset."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "notifications-pagination.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                recipient = User(email="recipient-pagination@example.test")

                session.add(recipient)
                session.flush()

                notifications = [
                    Notification(
                        recipient_id=recipient.id,
                        type="system",
                        text=f"notification {index}",
                        read=False,
                    )
                    for index in range(5)
                ]

                session.add_all(notifications)
                session.commit()

                recipient_id = recipient.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": recipient_id}
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    from backend.app.main import Query

                    results = Query().notifications(
                        info,
                        limit=2,
                        offset=1,
                    )

            finally:
                engine.dispose()

            self.assertEqual(len(results), 2)

    def test_unread_notification_count(self) -> None:
        """The unread notification count should only count unread rows for the current user."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "notifications-unread-count.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                recipient = User(email="recipient-unread@example.test")
                other_user = User(email="other-unread@example.test")

                session.add_all([recipient, other_user])
                session.flush()

                session.add_all(
                    [
                        Notification(
                            recipient_id=recipient.id,
                            type="system",
                            text="unread one",
                            read=False,
                        ),
                        Notification(
                            recipient_id=recipient.id,
                            type="system",
                            text="unread two",
                            read=False,
                        ),
                        Notification(
                            recipient_id=recipient.id,
                            type="system",
                            text="already read",
                            read=True,
                        ),
                        Notification(
                            recipient_id=other_user.id,
                            type="system",
                            text="someone else's notification",
                            read=False,
                        ),
                    ]
                )

                session.commit()

                recipient_id = recipient.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": recipient_id}
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    from backend.app.main import Query

                    result = Query().unread_notification_count(info)

            finally:
                engine.dispose()

            self.assertEqual(result, 2)

    def test_notification_preferences_returns_defaults(self) -> None:
        """A user without saved preferences should receive the default settings."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "notification-preferences.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                user = User(email="preferences@example.test")
                session.add(user)
                session.commit()

                user_id = user.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": user_id}
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    result = Query().notification_preferences(info)

                with Session(engine) as session:
                    saved_preferences = session.scalar(
                        select(NotificationPreference).where(
                            NotificationPreference.user_id == user_id
                        )
                    )

            finally:
                engine.dispose()

            self.assertIsNotNone(saved_preferences)
            self.assertTrue(result.collab_requests)
            self.assertTrue(result.messages)
            self.assertTrue(result.likes)
            self.assertTrue(result.comments)
            self.assertFalse(result.new_followers)
            self.assertTrue(result.live_alerts)
            self.assertFalse(result.trending_sounds)
            self.assertFalse(result.product_updates)
            self.assertEqual(result.email_digest, "weekly")
            self.assertFalse(result.quiet_hours)

    def test_update_notification_preferences(self) -> None:
        """Updating one preference should preserve the other defaults."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "notification-preferences-update.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                user = User(email="preferences-update@example.test")
                session.add(user)
                session.commit()

                user_id = user.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": user_id}
            )

            input_data = UpdateNotificationPreferencesInput(
                likes=False,
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    result = Mutation().update_notification_preferences(
                        input_data,
                        info,
                    )

            finally:
                engine.dispose()

            self.assertFalse(result.likes)
            self.assertTrue(result.comments)
            self.assertTrue(result.messages)
            self.assertEqual(result.email_digest, "weekly")

    def test_update_notification_preferences_rejects_invalid_email_digest(self) -> None:
        """Invalid email digest values should be rejected."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "notification-preferences-invalid-digest.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                user = User(email="preferences-invalid@example.test")
                session.add(user)
                session.commit()

                user_id = user.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": user_id}
            )

            input_data = UpdateNotificationPreferencesInput(
                email_digest="monthly",
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    with self.assertRaises(Exception):
                        Mutation().update_notification_preferences(
                            input_data,
                            info,
                        )

            finally:
                engine.dispose()
            
    def test_update_notification_preferences_requires_authentication(self) -> None:
        """Logged-out users should not be able to update notification preferences."""

        info = SimpleNamespace(
            context={"user_id": None}
        )

        input_data = UpdateNotificationPreferencesInput(
            likes=False,
        )

        with self.assertRaises(Exception):
            Mutation().update_notification_preferences(
                input_data,
                info,
)

    def test_update_notification_preferences_only_changes_current_user(self) -> None:
        """Updating preferences should only affect the authenticated user."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "notification-preferences-isolation.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                user_one = User(email="preferences-one@example.test")
                user_two = User(email="preferences-two@example.test")

                session.add_all([user_one, user_two])
                session.flush()

                session.add_all(
                    [
                        NotificationPreference(
                            user_id=user_one.id,
                            likes=True,
                        ),
                        NotificationPreference(
                            user_id=user_two.id,
                            likes=True,
                        ),
                    ]
                )

                session.commit()

                user_one_id = user_one.id
                user_two_id = user_two.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": user_one_id}
            )

            input_data = UpdateNotificationPreferencesInput(
                likes=False,
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    Mutation().update_notification_preferences(
                        input_data,
                        info,
                    )

                with Session(engine) as session:
                    user_one_preferences = session.scalar(
                        select(NotificationPreference).where(
                            NotificationPreference.user_id == user_one_id
                        )
                    )

                    user_two_preferences = session.scalar(
                        select(NotificationPreference).where(
                            NotificationPreference.user_id == user_two_id
                        )
                    )

            finally:
                engine.dispose()

            self.assertFalse(user_one_preferences.likes)
            self.assertTrue(user_two_preferences.likes)

    def test_repeated_follow_creates_only_one_notification(self) -> None:
        """Following the same user twice should create only one notification."""

        with TemporaryDirectory() as directory:
            database_path = Path(directory) / "follow-notification-duplicate.db"

            engine = create_engine(
                f"sqlite:///{database_path}",
                connect_args={"check_same_thread": False},
            )

            Base.metadata.create_all(engine)

            with Session(engine) as session:
                follower_user = User(email="repeat-follower@example.test")
                target_user = User(email="repeat-target@example.test")

                session.add_all([follower_user, target_user])
                session.flush()

                follower_profile = Profile(
                    user_id=follower_user.id,
                    username="repeat-follower",
                    display_name="Repeat Follower",
                )

                target_profile = Profile(
                    user_id=target_user.id,
                    username="repeat-target",
                    display_name="Repeat Target",
                )

                session.add_all([follower_profile, target_profile])

                session.add(
                    NotificationPreference(
                        user_id=target_user.id,
                        new_followers=True,
                    )
                )

                session.commit()

                follower_user_id = follower_user.id
                target_user_id = target_user.id

            def get_test_session() -> Session:
                return Session(engine)

            info = SimpleNamespace(
                context={"user_id": follower_user_id}
            )

            try:
                with patch(
                    "backend.app.main.get_session",
                    side_effect=get_test_session,
                ):
                    first_result = Mutation().follow(
                        "repeat-target",
                        info,
                    )

                    second_result = Mutation().follow(
                        "repeat-target",
                        info,
                    )

                with Session(engine) as session:
                    notifications = session.scalars(
                        select(Notification).where(
                            Notification.recipient_id == target_user_id,
                            Notification.actor_id == follower_user_id,
                            Notification.type == "follow",
                        )
                    ).all()

            finally:
                engine.dispose()

            self.assertTrue(first_result.following)
            self.assertTrue(second_result.following)
            self.assertEqual(len(notifications), 1)

if __name__ == "__main__":
    unittest.main()