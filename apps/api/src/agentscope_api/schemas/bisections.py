from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class BisectionStatus(StrEnum):
    READY = "ready"


class BisectionSessionCreate(ContractModel):
    regression_check_id: UUID
    repository_path: Annotated[str, Field(min_length=1, max_length=2_048)]


class BisectionCommit(ContractModel):
    position: int
    commit_sha: str
    committed_at: datetime
    subject: str
    parent_shas: list[str]


class BisectionSessionSummary(ContractModel):
    id: UUID
    regression_check_id: UUID
    repository_path: str
    repository_root: str
    repository_common_dir: str
    repository_fingerprint: str
    repository_head_sha: str
    baseline_revision: str
    candidate_revision: str
    baseline_commit_sha: str
    candidate_commit_sha: str
    baseline_provenance: dict[str, object]
    candidate_provenance: dict[str, object]
    commit_count: int
    maximum_commit_count: int
    status: BisectionStatus
    created_at: datetime


class BisectionSession(BisectionSessionSummary):
    commits: list[BisectionCommit]


class BisectionSessionListParams(ContractModel):
    page_size: Annotated[int, Field(ge=1, le=100)] = 50
    offset: Annotated[int, Field(ge=0, le=100_000)] = 0
    regression_check_id: UUID | None = None


class BisectionSessionList(ContractModel):
    items: list[BisectionSessionSummary]
    has_more: bool
