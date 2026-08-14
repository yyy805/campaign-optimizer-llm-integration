"""Create immutable review and idempotency storage."""
from alembic import op
import sqlalchemy as sa

revision = "0001_review_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("schema_version", sa.String(30), nullable=False),
        sa.Column("tenant", sa.String(100), nullable=False),
        sa.Column("client_id", sa.String(100), nullable=False),
        sa.Column("entity_json", sa.Text(), nullable=False),
        sa.Column("original_request_json", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("disposition", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("matched_rules_json", sa.Text(), nullable=False),
        sa.Column("winner_rule", sa.String(20), nullable=True),
        sa.Column("suppressed_rules_json", sa.Text(), nullable=False),
        sa.Column("action_json", sa.Text(), nullable=True),
        sa.Column("rule_evaluations_json", sa.Text(), nullable=False),
        sa.Column("guardrail_evaluations_json", sa.Text(), nullable=False),
        sa.Column("evidence_refs_json", sa.Text(), nullable=False),
        sa.Column("evidence_status", sa.String(30), nullable=False),
        sa.Column("ontology_version", sa.String(100), nullable=False),
        sa.Column("ontology_checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("principal_id", sa.String(100), nullable=False),
        sa.Column("request_id", sa.String(100), nullable=False),
        sa.Column("record_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reviews_tenant_created", "reviews", ["tenant", "created_at"])
    op.create_index("ix_reviews_filters", "reviews", ["tenant", "client_id", "outcome", "status"])
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("principal_id", sa.String(100), nullable=False),
        sa.Column("endpoint", sa.String(200), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("review_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("principal_id", "endpoint", "idempotency_key", name="uq_idempotency_scope"),
    )
    op.create_index("ix_idempotency_created", "idempotency_records", ["created_at"])


def downgrade() -> None:
    op.drop_table("idempotency_records")
    op.drop_table("reviews")

