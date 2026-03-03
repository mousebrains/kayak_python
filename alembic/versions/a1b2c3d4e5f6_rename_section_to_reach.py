"""Rename section to reach

Revision ID: a1b2c3d4e5f6
Revises: 58db118f1478
Create Date: 2026-03-03 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '58db118f1478'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename tables
    op.rename_table('section', 'reach')
    op.rename_table('section_state', 'reach_state')
    op.rename_table('section_class', 'reach_class')
    op.rename_table('section_level', 'reach_level')
    op.rename_table('section_guidebook', 'reach_guidebook')

    # Rename section_id columns to reach_id using batch mode (required for SQLite)
    with op.batch_alter_table('reach_state', schema=None) as batch_op:
        batch_op.alter_column('section_id', new_column_name='reach_id')

    with op.batch_alter_table('reach_class', schema=None) as batch_op:
        batch_op.alter_column('section_id', new_column_name='reach_id')

    with op.batch_alter_table('reach_level', schema=None) as batch_op:
        batch_op.alter_column('section_id', new_column_name='reach_id')

    with op.batch_alter_table('reach_guidebook', schema=None) as batch_op:
        batch_op.alter_column('section_id', new_column_name='reach_id')

    # Add new columns to reach
    with op.batch_alter_table('reach', schema=None) as batch_op:
        batch_op.add_column(sa.Column('river', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('max_gradient', sa.Float(), nullable=True))

    # Make name nullable (was NOT NULL, now nullable for AW-only reaches)
    with op.batch_alter_table('reach', schema=None) as batch_op:
        batch_op.alter_column('name', existing_type=sa.String(64), nullable=True)


def downgrade() -> None:
    # Make name non-nullable again
    with op.batch_alter_table('reach', schema=None) as batch_op:
        batch_op.alter_column('name', existing_type=sa.String(64), nullable=False)

    # Remove new columns
    with op.batch_alter_table('reach', schema=None) as batch_op:
        batch_op.drop_column('max_gradient')
        batch_op.drop_column('river')

    # Rename reach_id columns back to section_id
    with op.batch_alter_table('reach_guidebook', schema=None) as batch_op:
        batch_op.alter_column('reach_id', new_column_name='section_id')

    with op.batch_alter_table('reach_level', schema=None) as batch_op:
        batch_op.alter_column('reach_id', new_column_name='section_id')

    with op.batch_alter_table('reach_class', schema=None) as batch_op:
        batch_op.alter_column('reach_id', new_column_name='section_id')

    with op.batch_alter_table('reach_state', schema=None) as batch_op:
        batch_op.alter_column('reach_id', new_column_name='section_id')

    # Rename tables back
    op.rename_table('reach_guidebook', 'section_guidebook')
    op.rename_table('reach_level', 'section_level')
    op.rename_table('reach_class', 'section_class')
    op.rename_table('reach_state', 'section_state')
    op.rename_table('reach', 'section')
