"""stocks 加上 market：區分上市（twse）、上櫃（tpex）、興櫃（emerging）

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04 16:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # create_all 建立、再標記為基準版 0001 的資料庫可能已經有這個欄位
    if 'market' in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('stocks')}:
        return
    # 舊資料是 NULL，每日排程發現沒有市場別時會重新抓股票清單補上
    op.add_column('stocks', sa.Column('market', sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column('stocks', 'market')
