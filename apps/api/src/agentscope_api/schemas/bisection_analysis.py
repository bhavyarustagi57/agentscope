from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from agentscope_api.schemas.bisection_execution import ProbeConfiguration


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class BisectionAnalysisCreate(ContractModel):
    configuration: ProbeConfiguration


class BisectionAnalysisStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"
    ATTRIBUTED = "attributed"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"


class BisectionAnalysisStep(ContractModel):
    sequence_number: int
    good_position_before: int
    good_commit_sha_before: str
    bad_position_before: int
    bad_commit_sha_before: str
    selected_position: int
    selected_commit_sha: str
    evidence_source: Literal["reused", "requested"]
    execution_run_id: UUID
    observed_outcome: Literal["pass", "regression", "indeterminate", "execution_failed"]
    decision: Literal[
        "confirm_candidate",
        "advance_good",
        "retreat_bad",
        "skip_indeterminate",
        "skip_execution_failed",
    ]
    good_position_after: int
    good_commit_sha_after: str
    bad_position_after: int
    bad_commit_sha_after: str
    created_at: datetime


class BisectionAnalysis(ContractModel):
    id: UUID
    session_id: UUID
    status: BisectionAnalysisStatus
    configuration_schema_version: Literal["1"]
    configuration: ProbeConfiguration
    repository_fingerprint: str
    baseline_commit_sha: str
    candidate_commit_sha: str
    total_commit_count: int
    current_interval_size: int
    good_position: int
    good_commit_sha: str
    bad_position: int
    bad_commit_sha: str
    step_count: int
    reused_evidence_count: int
    new_evidence_count: int
    usable_evidence_count: int
    indeterminate_count: int
    execution_failure_count: int
    final_good_commit_sha: str | None
    final_good_position: int | None
    final_bad_commit_sha: str | None
    final_bad_position: int | None
    final_good_snapshot: dict[str, object] | None
    final_bad_snapshot: dict[str, object] | None
    terminal_reason: str | None
    terminal_message: str | None
    created_at: datetime
    queued_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    steps: list[BisectionAnalysisStep] = Field(default_factory=list)


class BisectionAnalysisListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0
    status: BisectionAnalysisStatus | None = None


class BisectionAnalysisList(ContractModel):
    items: list[BisectionAnalysis]
    has_more: bool


class BisectionAnalysisAccepted(ContractModel):
    analysis_id: UUID
    status: Literal["queued"] = "queued"
    queue_delivery: Literal["enqueued", "deferred"]
