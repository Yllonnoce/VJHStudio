"""phase7

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-22 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("catalog_models", schema=None) as batch_op:
        batch_op.add_column(sa.Column("constraints_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("constraints_updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("catalog_models", schema=None) as batch_op:
        batch_op.drop_column("constraints_updated_at")
        batch_op.drop_column("constraints_json")
