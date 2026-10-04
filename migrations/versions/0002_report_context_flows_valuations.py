"""reports 保留輸入資料與規則式判斷；新增三大法人與本益比資料表

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04 12:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('institutional',
    sa.Column('stock_id', sa.String(length=10), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('foreign_net', sa.BigInteger(), nullable=True),
    sa.Column('trust_net', sa.BigInteger(), nullable=True),
    sa.Column('dealer_net', sa.BigInteger(), nullable=True),
    sa.PrimaryKeyConstraint('stock_id', 'date')
    )
    op.create_table('valuations',
    sa.Column('stock_id', sa.String(length=10), nullable=False),
    sa.Column('date', sa.Date(), nullable=False),
    sa.Column('per', sa.Float(), nullable=True),
    sa.Column('pbr', sa.Float(), nullable=True),
    sa.Column('dividend_yield', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('stock_id', 'date')
    )
    with op.batch_alter_table('reports') as batch_op:
        batch_op.add_column(sa.Column('rule_action', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('rule_confidence', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('context', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('reports') as batch_op:
        batch_op.drop_column('context')
        batch_op.drop_column('rule_confidence')
        batch_op.drop_column('rule_action')
    op.drop_table('valuations')
    op.drop_table('institutional')
