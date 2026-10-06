from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Actor(BaseModel):
    id: str
    login: str


class Trigger(BaseModel):
    type: Literal["pull_request", "manual", "schedule", "deployment"] = "pull_request"
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
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    trigger: Trigger
    repository: Repository
    revision: Revision
    pull_request: PullRequest | None = None
    installation: Installation


class Finding(BaseModel):
    id: str
    severity: Literal["critical", "high", "medium", "low", "info"]
    category: str
    title: str
    message: str
    path: str | None = None
    start_line: int | None = None
    fingerprint: str | None = None
    triage: "FindingTriage | None" = None


class FindingTriage(BaseModel):
    classification: Literal["real_bug", "flaky", "environment", "unknown"]
    confidence: float = Field(ge=0, le=1)
    evidence_used: list[str] = Field(default_factory=list)
    rationale: str
    duplicate_of: str | None = None
    proposed_action: Literal["report", "suppress", "escalate_human"]


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
    type: str = "repository"
    project_id: str
    ref: str | None = None


class ProvisionedTarget(BaseModel):
    type: str
    ref: str
    healthcheck_url: str
    process_id: int | None = None


class WorkerTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.1"
    task_id: str = Field(default_factory=lambda: f"task_{uuid4().hex}")
    run_id: str
    capability: str
    objective: str
    target: TaskTarget
    worker_id: str | None = None
    implementation: str | None = None
    execution_preference: Literal["auto", "external_agent", "tool"] = "auto"
    scope: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    timeout_seconds: int = 900
    purpose: Literal["primary", "confirmation"] = "primary"


class ExecutionPlan(BaseModel):
    schema_version: str = "1.1"
    run_id: str | None = None
    profile: Literal["smoke", "regression", "full"] = "smoke"
    risk_flags: list[str] = Field(default_factory=list)
    rationale: str = ""
    confidence: float = Field(default=1.0, ge=0, le=1)
    planner_version: str = "rules-v1"
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
    execution_preference: Literal["auto", "external_agent", "tool"] = "auto"
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
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    task_id: str
    run_id: str
    capability: str = "unit.run"
    worker_id: str | None = None
    implementation: str | None = None
    worker_kind: Literal["external_agent", "tool", "managed_agent"] | None = None
    execution_status: Literal["completed", "failed", "cancelled", "timed_out"]
    verdict: Literal["pass", "fail", "warning", "skipped", "unknown"]
    exit_code: int | None = None
    output: str = ""
    summary: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    error: "ErrorDetail | None" = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    category: Literal["authentication", "authorization", "webhook", "repository",
                      "configuration", "planning", "execution", "target",
                      "infrastructure", "internal"]
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class AggregateResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.1"
    verdict: Literal["pass", "fail", "warning", "skipped", "unknown"]
    tasks_total: int
    tasks_passed: int = 0
    tasks_failed: int = 0
    tasks_warning: int = 0
    tasks_skipped: int = 0
    execution_errors: int = 0
    verdict_raw: Literal["pass", "fail", "warning", "skipped", "unknown"] | None = None
    verdict_triaged: Literal["pass", "fail", "warning", "skipped", "unknown"] | None = None


class WorkerManifestExecution(BaseModel):
    adapter: Literal["cli", "http", "managed_agent"]
    entrypoint: list[str] = Field(default_factory=list)


class WorkerRequirements(BaseModel):
    target: bool = False
    workspace: bool = True
    device: str | None = None


class WorkerManifest(BaseModel):
    schema_version: str = "1.1"
    worker_id: str
    version: str
    capabilities: list[str]
    supports_targets: list[str]
    requires: WorkerRequirements = Field(default_factory=WorkerRequirements)
    task_schema: str = "1.x"
    result_schema: str = "1.0"
    execution: WorkerManifestExecution


class ExternalAgentManifest(BaseModel):
    schema_version: str = "1.0"
    agent_id: str
    name: str
    version: str
    capabilities: list[str]
    supported_targets: list[str]
    transport: Literal["http"] = "http"
    endpoint: str
    # Optional path at which the orchestrator workspace root is mounted in a
    # remote/container worker. The checkout directory name is appended.
    source_root: str | None = None
    auth_secret_ref: str | None = None
    timeout_seconds: int = 1800
    fallback_to_tools: bool = True


class ExternalAgentRun(BaseModel):
    agent_id: str
    external_run_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled", "timed_out"]


class QCRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.1"
    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex}")
    status: Literal["queued", "preparing", "running", "completed", "failed", "cancelled"] = "queued"
    verdict: Literal["pass", "fail", "warning", "skipped", "unknown"] | None = None
    mode: Literal["observe", "enforce"] = "observe"
    requested_capabilities: list[str] = Field(default_factory=list)
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


class DecisionLog(BaseModel):
    decision_id: str = Field(default_factory=lambda: f"decision_{uuid4().hex}")
    run_id: str
    component: Literal["planner", "triage"]
    rationale: str
    input_hash: str
    model: str
    prompt_version: str
    output: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)


class FlakyTestRecord(BaseModel):
    fingerprint: str
    occurrences: int = 1
    last_seen_at: datetime = Field(default_factory=utc_now)
    evidence: list[str] = Field(default_factory=list)


class RunComparison(BaseModel):
    run_id: str
    verdict_raw: str
    verdict_triaged: str
    human_verdict: str | None = None


class Artifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: f"artifact_{uuid4().hex}")
    run_id: str
    task_id: str | None = None
    type: str
    name: str
    storage_ref: str
    retention_until: datetime


class ManualRunRequest(BaseModel):
    repository: Repository
    revision: Revision
    installation: Installation
    requested_capabilities: list[str] = Field(default_factory=list)
    actor: Actor


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
