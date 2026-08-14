"""Harden plan review identifiers and external plan storage."""
from alembic import op
import sqlalchemy as sa

revision = "0003_plan_review_hardening"
down_revision = "0002_plan_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("idempotency_records") as batch:
        batch.alter_column("review_id", existing_type=sa.String(36), type_=sa.String(64), existing_nullable=False)
    with op.batch_alter_table("plan_reviews") as batch:
        batch.alter_column("plan_id", existing_type=sa.String(255), type_=sa.Text(), existing_nullable=False)
        batch.add_column(sa.Column("normalized_request_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("plan_reviews") as batch:
        batch.drop_column("normalized_request_json")
        batch.alter_column("plan_id", existing_type=sa.Text(), type_=sa.String(255), existing_nullable=False)
    with op.batch_alter_table("idempotency_records") as batch:
        batch.alter_column("review_id", existing_type=sa.String(64), type_=sa.String(36), existing_nullable=False)
