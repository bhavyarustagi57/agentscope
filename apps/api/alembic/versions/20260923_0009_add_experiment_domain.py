"""Add reproducible A/B experiment domain."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260923_0009"
down_revision: str | None = "20260923_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "experiments",
        sa.Column("id", uuid, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(2_000)),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('draft', 'ready', 'running', 'completed', 'failed')",
            name="ck_experiments_status",
        ),
        sa.CheckConstraint(
            "(status = 'draft' AND ready_at IS NULL) OR "
            "(status <> 'draft' AND ready_at IS NOT NULL)",
            name="ck_experiments_ready_at",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_experiments_created_id", "experiments", ["created_at", "id"])
    op.create_index(
        "ix_experiments_status_created", "experiments", ["status", "created_at", "id"]
    )

    op.create_table(
        "experiment_variants",
        sa.Column("id", uuid, nullable=False),
        sa.Column("experiment_id", uuid, nullable=False),
        sa.Column("variant_key", sa.String(1), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("variant_key IN ('A', 'B')", name="ck_experiment_variants_key"),
        sa.CheckConstraint(
            "jsonb_typeof(provenance) = 'object'", name="ck_experiment_variants_provenance"
        ),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "experiment_id", "variant_key", name="uq_experiment_variants_key"
        ),
    )
    op.create_index(
        "ix_experiment_variants_experiment",
        "experiment_variants",
        ["experiment_id", "variant_key"],
    )

    op.create_table(
        "experiment_subjects",
        sa.Column("experiment_id", uuid, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("variant_key", sa.String(1), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("variant_key IN ('A', 'B')", name="ck_experiment_subjects_key"),
        sa.CheckConstraint(
            "position >= 0 AND position < 1000", name="ck_experiment_subjects_position"
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "variant_key"],
            ["experiment_variants.experiment_id", "experiment_variants.variant_key"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["trace_id"], ["traces.trace_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("experiment_id", "position", "variant_key"),
        sa.UniqueConstraint(
            "experiment_id", "trace_id", name="uq_experiment_subjects_trace"
        ),
    )
    op.create_index("ix_experiment_subjects_trace", "experiment_subjects", ["trace_id"])
    op.create_index(
        "ix_experiment_subjects_order",
        "experiment_subjects",
        ["experiment_id", "position", "variant_key"],
    )

    op.create_table(
        "experiment_evaluation_conditions",
        sa.Column("experiment_id", uuid, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("definition_id", uuid, nullable=False),
        sa.Column("definition_name", sa.String(200), nullable=False),
        sa.Column("evaluator_kind", sa.String(40), nullable=False),
        sa.Column("evaluator_config", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "position >= 0 AND position < 20", name="ck_experiment_conditions_position"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evaluator_config) = 'object'",
            name="ck_experiment_conditions_config",
        ),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["definition_id"], ["evaluation_definitions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("experiment_id", "position"),
        sa.UniqueConstraint(
            "experiment_id", "definition_id", name="uq_experiment_conditions_definition"
        ),
    )
    op.create_index(
        "ix_experiment_conditions_definition",
        "experiment_evaluation_conditions",
        ["definition_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_experiment_conditions_definition", table_name="experiment_evaluation_conditions"
    )
    op.drop_table("experiment_evaluation_conditions")
    op.drop_index("ix_experiment_subjects_order", table_name="experiment_subjects")
    op.drop_index("ix_experiment_subjects_trace", table_name="experiment_subjects")
    op.drop_table("experiment_subjects")
    op.drop_index("ix_experiment_variants_experiment", table_name="experiment_variants")
    op.drop_table("experiment_variants")
    op.drop_index("ix_experiments_status_created", table_name="experiments")
    op.drop_index("ix_experiments_created_id", table_name="experiments")
    op.drop_table("experiments")
