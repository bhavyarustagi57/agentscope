from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from agentscope_api.database import Base


class HumanReferenceSetRecord(Base):
    __tablename__ = "human_reference_sets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'labeling', 'frozen')", name="ck_reference_sets_status"
        ),
        CheckConstraint(
            "(status = 'frozen' AND frozen_at IS NOT NULL) OR "
            "(status <> 'frozen' AND frozen_at IS NULL)",
            name="ck_reference_sets_frozen_at",
        ),
        CheckConstraint(
            "frozen_at IS NULL OR frozen_at >= created_at", name="ck_reference_sets_time"
        ),
        Index("ix_reference_sets_created_id", "created_at", "id"),
        Index("ix_reference_sets_status_created", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(2_000))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HumanReferenceSubjectRecord(Base):
    __tablename__ = "human_reference_subjects"
    __table_args__ = (
        UniqueConstraint("reference_set_id", "position", name="uq_reference_subjects_position"),
        CheckConstraint("position >= 0 AND position < 500", name="ck_reference_subjects_position"),
        CheckConstraint(
            "reference_label IS NULL OR reference_label IN ('passed', 'failed')",
            name="ck_reference_subjects_label",
        ),
        CheckConstraint(
            "(reference_label IS NULL AND reference_rationale IS NULL AND "
            "reference_annotator_id IS NULL AND reference_labeled_at IS NULL) OR "
            "(reference_label IS NOT NULL AND reference_annotator_id IS NOT NULL AND "
            "reference_labeled_at IS NOT NULL)",
            name="ck_reference_subjects_label_metadata",
        ),
        Index("ix_reference_subjects_trace_id", "trace_id"),
        Index("ix_reference_subjects_set_label", "reference_set_id", "reference_label"),
    )

    reference_set_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("human_reference_sets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    trace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("traces.trace_id", ondelete="RESTRICT"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer)
    reference_label: Mapped[str | None] = mapped_column(String(20))
    reference_rationale: Mapped[str | None] = mapped_column(String(4_000))
    reference_annotator_id: Mapped[str | None] = mapped_column(String(100))
    reference_labeled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HumanAnnotationRecord(Base):
    __tablename__ = "human_annotations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["reference_set_id", "trace_id"],
            ["human_reference_subjects.reference_set_id", "human_reference_subjects.trace_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "reference_set_id", "trace_id", "annotator_id", name="uq_human_annotations_identity"
        ),
        CheckConstraint("label IN ('passed', 'failed')", name="ck_human_annotations_label"),
        CheckConstraint("source = 'manual'", name="ck_human_annotations_source"),
        Index("ix_human_annotations_set_created", "reference_set_id", "created_at", "id"),
        Index("ix_human_annotations_annotator", "annotator_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    reference_set_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    annotator_id: Mapped[str] = mapped_column(String(100))
    label: Mapped[str] = mapped_column(String(20))
    rationale: Mapped[str | None] = mapped_column(String(4_000))
    source: Mapped[str] = mapped_column(String(20), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CalibrationStudyRecord(Base):
    __tablename__ = "calibration_studies"
    __table_args__ = (
        CheckConstraint("status = 'draft'", name="ck_calibration_studies_status"),
        Index("ix_calibration_studies_created_id", "created_at", "id"),
        Index("ix_calibration_studies_reference_set", "reference_set_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(2_000))
    reference_set_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("human_reference_sets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CalibrationStudySubjectRecord(Base):
    __tablename__ = "calibration_study_subjects"
    __table_args__ = (
        UniqueConstraint("study_id", "position", name="uq_calibration_study_subjects_position"),
        CheckConstraint("position >= 0 AND position < 500", name="ck_study_subjects_position"),
        CheckConstraint("reference_label IN ('passed', 'failed')", name="ck_study_subjects_label"),
        Index("ix_calibration_study_subjects_trace_id", "trace_id"),
    )

    study_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("calibration_studies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    trace_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("traces.trace_id", ondelete="RESTRICT"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer)
    reference_label: Mapped[str] = mapped_column(String(20))
