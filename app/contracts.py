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


class WorkerResult(BaseModel):
    schema_version: str = "1.0"
    task_id: str
    run_id: str
    execution_status: Literal["completed", "failed", "cancelled", "timed_out"]
    verdict: Literal["pass", "fail", "warning", "skipped", "unknown"]
    exit_code: int | None = None
    output: str = ""
    findings: list[Finding] = Field(default_factory=list)


class QCRun(BaseModel):
    schema_version: str = "1.0"
    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex}")
    status: Literal["queued", "preparing", "running", "completed", "failed"] = "queued"
    verdict: Literal["pass", "fail", "unknown"] | None = None
    trigger_context: TriggerContext
    check_run_id: int | None = None
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

