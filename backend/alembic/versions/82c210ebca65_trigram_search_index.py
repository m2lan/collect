"""trigram search index

Revision ID: 82c210ebca65
Revises: 2be2bb0248d8
Create Date: 2026-10-22 02:26:13.749744

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '82c210ebca65'
down_revision: Union[str, Sequence[str], None] = '2be2bb0248d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """PostgreSQL 下为标题建立 pg_trgm 索引，加速 ILIKE 全文搜索（pg_trgm 为可信扩展，库所有者即可创建）。"""
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE INDEX IF NOT EXISTS ix_lot_title_trgm ON lot USING gin (title gin_trgm_ops)")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_lot_title_trgm")
