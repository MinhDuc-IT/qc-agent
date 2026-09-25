from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Actor(BaseModel):
    id: str
    login: str


class Trigger(BaseModel):
    type: Literal["pull_request"] = "pull_request"
    mode: Literal["auto", "manual"] = "auto"
    provider: Literal["github"] = "github"
    event: str
    action: str
    delivery_id: str
    actor: Actor


class Repository(BaseModel):
    provider: Literal["github"] = "github"
    owner: str
    name: str
    full_name: str
    clone_url: str
    default_branch: str


class Revision(BaseModel):
    head_sha: str
    head_ref: str
    base_sha: str
    base_ref: str


class PullRequest(BaseModel):
    number: int
    url: str


class Installation(BaseModel):
    id: int


class TriggerContext(BaseModel):
    schema_version: str = "1.0"
    trigger: Trigger
    repository: Repository
    revision: Revision
    pull_request: PullRequest
    installation: Installation


class Finding(BaseModel):
    id: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    category: str
    title: str
    message: str
    path: str | None = None
    start_line: int | None = None


class ProjectDescriptor(BaseModel):
    id: str
    root: str = "."
    language: str
    build_system: str | None = None
    frameworks: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)


class SourceAnalysis(BaseModel):
    schema_version: str = "1.0"
    changed_files: list[str] = Field(default_factory=list)
    diff_patch: str = ""
    projects: list[ProjectDescriptor] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class TaskTarget(BaseModel):
    type: str = "source_tree"
    project_id: str
    ref: str | None = None


class WorkerTask(BaseModel):
    schema_version: str = "1.0"
    task_id: str = Field(default_factory=lambda: f"task_{uuid4().hex}")
    run_id: str
    capability: str
    objective: str
    target: TaskTarget
    worker_id: str | None = None
    implementation: str | None = None
    scope: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    timeout_seconds: int = 900


class ExecutionPlan(BaseModel):
    schema_version: str = "1.0"
    tasks: list[WorkerTask] = Field(default_factory=list)


class RiskAssessment(BaseModel):
    category: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    description: str
    evidence: list[str] = Field(default_factory=list)


class AgentTaskScope(BaseModel):
    paths: list[str] = Field(default_factory=list)
    scenarios: list[str] = Field(default_factory=list)


class AgentTaskProposal(BaseModel):
    project_id: str
    capability: str
    reason: str
    scope: AgentTaskScope = Field(default_factory=AgentTaskScope)


class AgentPlanProposal(BaseModel):
    risks: list[RiskAssessment] = Field(default_factory=list)
    tasks: list[AgentTaskProposal] = Field(default_factory=list)
    summary: str = ""


class RootCauseAnalysis(BaseModel):
    task_id: str
    root_cause: str
    confidence: float = Field(ge=0, le=1)
    remediation: str
    affected_files: list[str] = Field(default_factory=list)


class AgentResultAnalysis(BaseModel):
    summary: str
    root_causes: list[RootCauseAnalysis] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)


class WorkerResult(BaseModel):
    schema_version: str = "1.0"
    task_id: str
    run_id: str
    capability: str = "functional.unit"
    worker_id: str | None = None
    implementation: str | None = None
    execution_status: Literal["completed", "failed", "cancelled", "timed_out"]
    verdict: Literal["pass", "fail", "warning", "skipped", "unknown"]
    exit_code: int | None = None
    output: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)


class AggregateResult(BaseModel):
    schema_version: str = "1.0"
    verdict: Literal["pass", "fail", "warning", "skipped", "unknown"]
    tasks_total: int
    tasks_passed: int = 0
    tasks_failed: int = 0
    tasks_warning: int = 0
    tasks_skipped: int = 0
    execution_errors: int = 0


class QCRun(BaseModel):
    schema_version: str = "1.0"
    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex}")
    status: Literal["queued", "preparing", "running", "completed", "failed"] = "queued"
    verdict: Literal["pass", "fail", "warning", "skipped", "unknown"] | None = None
    trigger_context: TriggerContext
    check_run_id: int | None = None
    analysis: SourceAnalysis | None = None
    agent_mode: Literal["active", "fallback", "disabled"] = "disabled"
    agent_error: str | None = None
    agent_plan: AgentPlanProposal | None = None
    plan: ExecutionPlan | None = None
    results: list[WorkerResult] = Field(default_factory=list)
    aggregate: AggregateResult | None = None
    agent_result_analysis: AgentResultAnalysis | None = None
    # Backward-compatible alias for clients of the original single-worker demo.
    result: WorkerResult | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


def normalize_pull_request(payload: dict[str, Any], delivery_id: str) -> TriggerContext:
    repo = payload["repository"]
    pr = payload["pull_request"]
    owner = repo["owner"]["login"]
    sender = payload["sender"]
    return TriggerContext(
        trigger=Trigger(
            event="pull_request",
            action=payload["action"],
            delivery_id=delivery_id,
            actor=Actor(id=str(sender["id"]), login=sender["login"]),
        ),
        repository=Repository(
            owner=owner,
            name=repo["name"],
            full_name=repo["full_name"],
            clone_url=repo["clone_url"],
            default_branch=repo["default_branch"],
        ),
        revision=Revision(
            head_sha=pr["head"]["sha"],
            head_ref=pr["head"]["ref"],
            base_sha=pr["base"]["sha"],
            base_ref=pr["base"]["ref"],
        ),
        pull_request=PullRequest(number=pr["number"], url=pr["html_url"]),
        installation=Installation(id=payload["installation"]["id"]),
    )
