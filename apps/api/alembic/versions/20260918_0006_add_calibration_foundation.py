"""Add human reference and calibration study foundation."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260918_0006"
down_revision: str | None = "20260918_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "human_reference_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2_000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
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
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft', 'labeling', 'frozen')", name="ck_reference_sets_status"
        ),
        sa.CheckConstraint(
            "(status = 'frozen' AND frozen_at IS NOT NULL) OR "
            "(status <> 'frozen' AND frozen_at IS NULL)",
            name="ck_reference_sets_frozen_at",
        ),
        sa.CheckConstraint(
            "frozen_at IS NULL OR frozen_at >= created_at", name="ck_reference_sets_time"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reference_sets_created_id", "human_reference_sets", ["created_at", "id"])
    op.create_index(
        "ix_reference_sets_status_created", "human_reference_sets", ["status", "created_at"]
    )

    op.create_table(
        "human_reference_subjects",
        sa.Column("reference_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("reference_label", sa.String(length=20), nullable=True),
        sa.Column("reference_rationale", sa.String(length=4_000), nullable=True),
        sa.Column("reference_annotator_id", sa.String(length=100), nullable=True),
        sa.Column("reference_labeled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "position >= 0 AND position < 500", name="ck_reference_subjects_position"
        ),
        sa.CheckConstraint(
            "reference_label IS NULL OR reference_label IN ('passed', 'failed')",
            name="ck_reference_subjects_label",
        ),
        sa.CheckConstraint(
            "(reference_label IS NULL AND reference_rationale IS NULL AND "
            "reference_annotator_id IS NULL AND reference_labeled_at IS NULL) OR "
            "(reference_label IS NOT NULL AND reference_annotator_id IS NOT NULL AND "
            "reference_labeled_at IS NOT NULL)",
            name="ck_reference_subjects_label_metadata",
        ),
        sa.ForeignKeyConstraint(
            ["reference_set_id"], ["human_reference_sets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["trace_id"], ["traces.trace_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("reference_set_id", "trace_id"),
        sa.UniqueConstraint("reference_set_id", "position", name="uq_reference_subjects_position"),
    )
    op.create_index("ix_reference_subjects_trace_id", "human_reference_subjects", ["trace_id"])
    op.create_index(
        "ix_reference_subjects_set_label",
        "human_reference_subjects",
        ["reference_set_id", "reference_label"],
    )

    op.create_table(
        "human_annotations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reference_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("annotator_id", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=20), nullable=False),
        sa.Column("rationale", sa.String(length=4_000), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
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
        sa.CheckConstraint("label IN ('passed', 'failed')", name="ck_human_annotations_label"),
        sa.CheckConstraint("source = 'manual'", name="ck_human_annotations_source"),
        sa.ForeignKeyConstraint(
            ["reference_set_id", "trace_id"],
            ["human_reference_subjects.reference_set_id", "human_reference_subjects.trace_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "reference_set_id", "trace_id", "annotator_id", name="uq_human_annotations_identity"
        ),
    )
    op.create_index(
        "ix_human_annotations_set_created",
        "human_annotations",
        ["reference_set_id", "created_at", "id"],
    )
    op.create_index("ix_human_annotations_annotator", "human_annotations", ["annotator_id"])

    op.create_table(
        "calibration_studies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2_000), nullable=True),
        sa.Column("reference_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status = 'draft'", name="ck_calibration_studies_status"),
        sa.ForeignKeyConstraint(
            ["reference_set_id"], ["human_reference_sets.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_calibration_studies_created_id", "calibration_studies", ["created_at", "id"]
    )
    op.create_index(
        "ix_calibration_studies_reference_set",
        "calibration_studies",
        ["reference_set_id", "created_at"],
    )

    op.create_table(
        "calibration_study_subjects",
        sa.Column("study_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("reference_label", sa.String(length=20), nullable=False),
        sa.CheckConstraint("position >= 0 AND position < 500", name="ck_study_subjects_position"),
        sa.CheckConstraint(
            "reference_label IN ('passed', 'failed')", name="ck_study_subjects_label"
        ),
        sa.ForeignKeyConstraint(["study_id"], ["calibration_studies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trace_id"], ["traces.trace_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("study_id", "trace_id"),
        sa.UniqueConstraint("study_id", "position", name="uq_calibration_study_subjects_position"),
    )
    op.create_index(
        "ix_calibration_study_subjects_trace_id", "calibration_study_subjects", ["trace_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_calibration_study_subjects_trace_id", table_name="calibration_study_subjects")
    op.drop_table("calibration_study_subjects")
    op.drop_index("ix_calibration_studies_reference_set", table_name="calibration_studies")
    op.drop_index("ix_calibration_studies_created_id", table_name="calibration_studies")
    op.drop_table("calibration_studies")
    op.drop_index("ix_human_annotations_annotator", table_name="human_annotations")
    op.drop_index("ix_human_annotations_set_created", table_name="human_annotations")
    op.drop_table("human_annotations")
    op.drop_index("ix_reference_subjects_set_label", table_name="human_reference_subjects")
    op.drop_index("ix_reference_subjects_trace_id", table_name="human_reference_subjects")
    op.drop_table("human_reference_subjects")
    op.drop_index("ix_reference_sets_status_created", table_name="human_reference_sets")
    op.drop_index("ix_reference_sets_created_id", table_name="human_reference_sets")
    op.drop_table("human_reference_sets")
