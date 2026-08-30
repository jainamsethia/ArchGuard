"""suppressions belong to a user

Suppressions were keyed by repository alone, so two accounts that had each
analysed the same public repository shared one set: either could read the
other's -- including the free-text reason -- and either could delete them.

Making the owner required is the half of that fix the database can enforce.
The queries are scoped in archguard/db/store.py, but a scoped query is a thing
somebody has to remember to write, and this is a thing they cannot forget.

Revision ID: b39a1665b2ed
Revises: 476c21b3f202
Create Date: 2026-08-30 15:05:31.595757
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b39a1665b2ed'
down_revision: str | Sequence[str] | None = '476c21b3f202'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # An ownerless row cannot be reached: every query filters on user_id, and
    # `user_id = X` never matches NULL, so nobody can list it, match it or
    # delete it. There should be none -- the routes wrote to a file, not to
    # this table -- but ALTER would fail on one, and a migration that dies
    # halfway through a deploy over rows nobody can see is a worse outcome than
    # removing them. Stated here rather than left implicit, because a DELETE
    # inside a migration deserves to be read.
    op.execute(sa.text("DELETE FROM suppressions WHERE user_id IS NULL"))

    op.alter_column('suppressions', 'user_id',
               existing_type=sa.INTEGER(),
               nullable=False)


def downgrade() -> None:
    """Downgrade schema.

    Only the constraint comes back. The rows deleted above do not, which is
    what makes this downgrade lossy in principle -- and harmless in practice,
    since anything it dropped was already unreachable.
    """
    op.alter_column('suppressions', 'user_id',
               existing_type=sa.INTEGER(),
               nullable=True)
