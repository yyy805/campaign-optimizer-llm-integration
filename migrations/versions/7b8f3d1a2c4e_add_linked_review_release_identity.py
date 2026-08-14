'''add linked review revision and release identity'''

revision = '7b8f3d1a2c4e'
down_revision = 'da19a197a9f7'
branch_labels = None
depends_on = None

from alembic import op
import sqlalchemy as sa


def upgrade():
    # 移植性修正（2026-08-14 换库演练发现）：两库口味相反——
    # SQLite 不支持逐条 ALTER 约束，必须走 batch 拆表重建特招；
    # PG 上拆表手法会撞"被外键引用的约束不许拆"，必须逐条加列加约束。
    # 按方言分菜，终效相同。
    if op.get_bind().dialect.name == 'sqlite':
        with op.batch_alter_table('ontology_reviews', recreate='always') as batch:
            batch.add_column(sa.Column('parent_review_id', sa.String(length=128), nullable=True))
            batch.add_column(sa.Column('revision', sa.Integer(), nullable=False, server_default='0'))
            batch.add_column(sa.Column('rule_version', sa.String(length=64), nullable=False, server_default='legacy'))
            batch.add_column(sa.Column('engine_version', sa.String(length=64), nullable=False, server_default='legacy'))
            batch.add_column(sa.Column('schema_version', sa.String(length=64), nullable=False, server_default='legacy'))
            batch.add_column(sa.Column('source_commit', sa.String(length=40), nullable=False, server_default='0000000000000000000000000000000000000000'))
            batch.add_column(sa.Column('package_checksum', sa.String(length=64), nullable=False, server_default='0000000000000000000000000000000000000000000000000000000000000000'))
            batch.add_column(sa.Column('confidence_state_version', sa.String(length=128), nullable=False, server_default='legacy'))
            batch.create_check_constraint('ck_review_revision', 'revision >= 0')
            batch.create_unique_constraint('uq_review_revision', ['client_id', 'plan_id', 'revision'])
            batch.create_foreign_key(
                'fk_review_parent', 'ontology_reviews',
                ['client_id', 'parent_review_id'], ['client_id', 'review_id'],
            )
    else:
        op.add_column('ontology_reviews', sa.Column('parent_review_id', sa.String(length=128), nullable=True))
        op.add_column('ontology_reviews', sa.Column('revision', sa.Integer(), nullable=False, server_default='0'))
        op.add_column('ontology_reviews', sa.Column('rule_version', sa.String(length=64), nullable=False, server_default='legacy'))
        op.add_column('ontology_reviews', sa.Column('engine_version', sa.String(length=64), nullable=False, server_default='legacy'))
        op.add_column('ontology_reviews', sa.Column('schema_version', sa.String(length=64), nullable=False, server_default='legacy'))
        op.add_column('ontology_reviews', sa.Column('source_commit', sa.String(length=40), nullable=False, server_default='0000000000000000000000000000000000000000'))
        op.add_column('ontology_reviews', sa.Column('package_checksum', sa.String(length=64), nullable=False, server_default='0000000000000000000000000000000000000000000000000000000000000000'))
        op.add_column('ontology_reviews', sa.Column('confidence_state_version', sa.String(length=128), nullable=False, server_default='legacy'))
        op.create_check_constraint('ck_review_revision', 'ontology_reviews', condition='revision >= 0')
        op.create_unique_constraint('uq_review_revision', 'ontology_reviews', ['client_id', 'plan_id', 'revision'])
        op.create_foreign_key(
            'fk_review_parent', 'ontology_reviews', 'ontology_reviews',
            ['client_id', 'parent_review_id'], ['client_id', 'review_id'],
        )


def downgrade():
    raise RuntimeError('Production migrations are forward-only')
