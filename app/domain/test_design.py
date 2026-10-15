from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CaseReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    case_id: str = Field(min_length=1,max_length=120)
    action: Literal['request_changes','add_acceptance_criteria','review_blocker','supplement','manual_edit','add_case','delete_case']
    feedback: str = Field(default='',max_length=8000)
    acceptance_criteria: str = Field(default='',max_length=8000)
    case_data: dict[str, Any] | None = None
    scenario_data: dict[str, Any] | None = None

    @model_validator(mode='after')
    def meaningful(self):
        if self.action in ('manual_edit','add_case'):
            if not self.case_data:raise ValueError('Test case content is required')
            if self.action=='add_case' and not self.scenario_data:raise ValueError('Scenario is required for a new case')
        if self.action=='add_acceptance_criteria':
            if not self.acceptance_criteria.strip():raise ValueError('Acceptance criteria must be non-empty')
        elif not self.feedback.strip():raise ValueError('Review feedback must be non-empty')
        return self

from .models import Repository


class DesignProject(BaseModel):
    project_id: str = Field(default_factory=lambda: "project_" + uuid4().hex)
    installation_id: int
    repository_id: int
    repository: Repository
    enabled: bool = True
    state: str = "not_started"
    message: str = "Chưa có bộ test. Chọn tài liệu nếu cần, rồi bấm Sinh test để bắt đầu."
    current_job_id: str | None = None
    source_revision: str | None = None
    active_baseline: str | None = None
    requirements_text: str = ""
    source_paths: list[str] = Field(default_factory=list)
    uploaded_document_ids: list[str] = Field(default_factory=list)
    target_profile: dict[str, Any] = Field(default_factory=dict)
    allow_mutations: bool = False
    discovered_sources: list[dict[str, Any]] = Field(default_factory=list)


class DesignJob(BaseModel):
    job_id: str = Field(default_factory=lambda: "design_" + uuid4().hex)
    project_id: str
    revision: str | None = None
    reason: str
    status: str = "queued"
    attempts: int = 0
    baseline_version: str | None = None
    artifact_dir: str | None = None
    error: str | None = None
    error_message: str | None = None
    review_request: CaseReviewRequest | None = None
    review_baseline: str | None = None
    review_actor: str | None = None
    workspace_request: dict[str,Any] | None = None


class ManagedBaseline(BaseModel):
    project_id: str
    version: str
    job_id: str
    source_revision: str
    state: Literal["draft", "approved", "rejected"] = "draft"
    baseline_dir: str
    report_path: str | None = None
    blockers: list[str] = Field(default_factory=list)
    reviewer: str | None = None


def can_request_case_review(project,baseline,current_job,current_baseline=None):
    if not project or not project.enabled or not baseline or baseline.state=='rejected':return False
    if current_job and current_job.status in ('queued','running'):return False
    if baseline.state=='approved':return baseline.version==project.active_baseline
    if baseline.job_id==project.current_job_id:return True
    if not current_job or current_job.review_baseline!=baseline.version:return False
    if current_job.status=='failed' and current_job.baseline_version is None:return True
    return bool(current_job.status=='completed' and current_baseline and current_baseline.state=='draft'
                and current_baseline.version==current_job.baseline_version)


class ProjectSetup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirements_text: str = Field(default="", max_length=50000)
    source_paths: list[str] = Field(default_factory=list, max_length=100)
    uploaded_document_ids: list[str] = Field(default_factory=list, max_length=50)
    fastapi_module: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$")
    allow_mutations: bool = False
    target_command: list[str] | None = Field(default=None, min_length=1, max_length=30)
    healthcheck_path: str = Field(default="/health", pattern=r"^/[A-Za-z0-9_./-]*$")


class BrowserSession(BaseModel):
    user_id: int
    login: str
    csrf: str
    expires_at: float
