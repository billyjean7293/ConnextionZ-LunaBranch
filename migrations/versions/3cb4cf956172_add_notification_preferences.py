"""add notification preferences

Revision ID: 3cb4cf956172
Revises: 9cd2fcf2fef2
Create Date: 2026-09-01 07:41:10.297860
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa



revision: str = '3cb4cf956172'
down_revision: Union[str, None] = '9cd2fcf2fef2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notification_preferences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('collab_requests', sa.Boolean(), nullable=False),
        sa.Column('messages', sa.Boolean(), nullable=False),
        sa.Column('likes', sa.Boolean(), nullable=False),
        sa.Column('comments', sa.Boolean(), nullable=False),
        sa.Column('new_followers', sa.Boolean(), nullable=False),
        sa.Column('live_alerts', sa.Boolean(), nullable=False),
        sa.Column('trending_sounds', sa.Boolean(), nullable=False),
        sa.Column('product_updates', sa.Boolean(), nullable=False),
        sa.Column('email_digest', sa.String(length=20), nullable=False),
        sa.Column('quiet_hours', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_index(
        op.f('ix_notification_preferences_id'),
        'notification_preferences',
        ['id'],
        unique=False
    )

    op.create_index(
        op.f('ix_notification_preferences_user_id'),
        'notification_preferences',
        ['user_id'],
        unique=True
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_index(
        op.f('ix_notification_preferences_user_id'),
        table_name='notification_preferences'
    )

    op.drop_index(
        op.f('ix_notification_preferences_id'),
        table_name='notification_preferences'
    )

    op.drop_table('notification_preferences')
    # ### end Alembic commands ###
