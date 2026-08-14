"""Create plan-review exchange storage."""
from alembic import op
import sqlalchemy as sa

revision = "0002_plan_reviews"
down_revision = "0001_review_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_reviews",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("plan_id", sa.String(255), nullable=False),
        sa.Column("tenant", sa.String(100), nullable=False),
        sa.Column("client_id", sa.String(100), nullable=False),
        sa.Column("original_request_json", sa.Text(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("ontology_version", sa.String(100), nullable=False),
        sa.Column("ontology_checksum", sa.String(64), nullable=False),
        sa.Column("principal_id", sa.String(100), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_plan_reviews_plan_id", "plan_reviews", ["plan_id"])
    op.create_index("ix_plan_reviews_tenant_created", "plan_reviews", ["tenant", "created_at"])


def downgrade() -> None:
    op.drop_table("plan_reviews")
