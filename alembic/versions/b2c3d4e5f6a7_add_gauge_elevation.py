"""Add elevation and drainage_area columns to gauge table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-03 18:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('gauge', schema=None) as batch_op:
        batch_op.add_column(sa.Column('elevation', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('drainage_area', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('gauge', schema=None) as batch_op:
        batch_op.drop_column('drainage_area')
        batch_op.drop_column('elevation')
