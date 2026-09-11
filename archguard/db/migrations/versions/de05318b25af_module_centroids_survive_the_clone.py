"""module centroids survive the clone

Layer 3 measures semantic drift against the centroid a previous run stored.
That centroid lived in a SQLite file inside the analysed repository, which is a
throwaway clone deleted when the job finishes -- so the baseline never survived
to be compared against, and the layer reported "no prior baseline" on every
hosted analysis however many times a repository had been scanned.

Keyed by repository, exactly as `file_hashes` already is and for the same
reason. The vector is raw little-endian float32 bytes, which is the form
`EmbeddingCache` already reads and writes.

Revision ID: de05318b25af
Revises: b39a1665b2ed
Create Date: 2026-09-11 12:05:41.468128
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'de05318b25af'
down_revision: str | Sequence[str] | None = 'b39a1665b2ed'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('module_centroids',
    sa.Column('repository_id', sa.Integer(), nullable=False),
    sa.Column('module_name', sa.Text(), nullable=False),
    sa.Column('centroid', sa.LargeBinary(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('repository_id', 'module_name')
    )


def downgrade() -> None:
    op.drop_table('module_centroids')
