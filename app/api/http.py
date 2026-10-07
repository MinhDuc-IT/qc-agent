import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
import yaml

from fastapi import FastAPI, Header, HTTPException, Request, status

from ..application.orchestrator import Orchestrator
from ..application.analysis import RepositoryAnalyzer
from ..application.agents import FallbackPlanningAgent, FallbackResultAnalysisAgent
from ..domain.models import (ManualRunRequest, QCRun, Trigger, TriggerContext,
                             normalize_pull_request)
from ..domain.capabilities import CATALOG
from ..infrastructure.github.client import GitHubClient
from ..infrastructure.github.webhook import verify_webhook_signature
from ..infrastructure.execution.runtime import RegisteredWorkerRuntime
from ..infrastructure.execution.backends import DockerExecutionBackend, LocalExecutionBackend
from ..infrastructure.execution.hybrid import HybridWorkerRuntime
from ..infrastructure.external_agents.registry import ExternalAgentRegistry
from ..infrastructure.external_agents.runtime import ExternalAgentRuntime
from ..infrastructure.persistence.sqlite import SqliteRunStore
from ..infrastructure.settings import get_settings
from ..infrastructure.source.git import GitRepositoryManager
from ..infrastructure.agents.openai import (OpenAIPlanningAgent, OpenAIResultAnalysisAgent,
                                            OpenAITriageAgent)
from ..infrastructure.target.local import LocalTargetProvisioner
from ..infrastructure.artifacts import LocalArtifactStore
from ..infrastructure.publishers import JiraPublisher, SlackPublisher

settings = get_settings()
store = SqliteRunStore(settings.agent_qc_data_dir)
github = GitHubClient(
    settings.github_app_id, settings.github_private_key_path,
    settings.agent_qc_dry_run, settings.agent_qc_publish_pr_review,
)
source = GitRepositoryManager(settings.agent_qc_workspace_dir, github, settings.agent_qc_dry_run)
external_agent_registry = ExternalAgentRegistry.from_yaml(settings.agent_qc_external_agents_file)
external_agent_runtime = ExternalAgentRuntime(external_agent_registry)


def _execution_backend():
    if settings.agent_qc_execution_backend == "local":
        return LocalExecutionBackend()
    if settings.agent_qc_execution_backend != "docker":
        raise ValueError("AGENT_QC_EXECUTION_BACKEND must be 'local' or 'docker'")
    path = settings.agent_qc_docker_images_file
    if path is None or not Path(path).is_file():
        raise ValueError("Docker backend requires AGENT_QC_DOCKER_IMAGES_FILE")
    images = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(images, dict):
        raise ValueError("Docker images file must be a mapping of executable to pinned image")
    return DockerExecutionBackend(images)


workers = HybridWorkerRuntime(
    external_agent_runtime, RegisteredWorkerRuntime(backend=_execution_backend())
)
if settings.agent_qc_agent_enabled and settings.openai_api_key:
    planning_agent = OpenAIPlanningAgent(settings.openai_api_key, settings.openai_model)
    result_agent = OpenAIResultAnalysisAgent(settings.openai_api_key, settings.openai_model)
    triage_agent = OpenAITriageAgent(settings.openai_api_key, settings.openai_model)
else:
    planning_agent = FallbackPlanningAgent()
    result_agent = FallbackResultAnalysisAgent()
    triage_agent = None
target_provisioner = LocalTargetProvisioner() if settings.agent_qc_local_target_enabled else None
artifact_store = LocalArtifactStore(settings.agent_qc_artifact_dir,
                                    settings.agent_qc_artifact_retention_days)
orchestrator = Orchestrator(store, github, source, workers, planning_agent, result_agent,
                            target_provisioner,
                            RepositoryAnalyzer(settings.agent_qc_org_policy_file),
                            triage_agent, settings.triage_min_conf,
                            artifact_store, (JiraPublisher(), SlackPublisher()),
                            settings.agent_qc_worker_concurrency)
queue_task: asyncio.Task | None = None
logger = logging.getLogger(__name__)


async def _queue_worker() -> None:
    while True:
        run_id = None
        try:
            run_id = await asyncio.to_thread(store.claim_next_run)
            if run_id is None:
                await asyncio.sleep(0.5)
                continue
            await orchestrator.execute(run_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # One broken repository, publisher, or cleanup operation must not
            # permanently stop delivery processing for every later webhook.
            logger.exception("Queue worker failed while processing run %s", run_id)
        finally:
            if run_id is not None:
                await asyncio.to_thread(store.complete_queued_run, run_id)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global queue_task
    artifact_store.prune()
    queue_task = asyncio.create_task(_queue_worker())
    try:
        yield
    finally:
        if queue_task is not None:
            queue_task.cancel()
            try:
                await queue_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="Agent-QC", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/agents")
def list_external_agents():
    return {"agents": [manifest.model_dump(mode="json")
                       for manifest in external_agent_registry.manifests]}


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str) -> QCRun:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.post("/api/v1/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_manual_run(
    request: ManualRunRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    expected = settings.agent_qc_manual_api_token
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Manual run authentication failed")
    allowed = {login.strip() for login in settings.agent_qc_manual_allowlist.split(",")
               if login.strip()}
    if request.actor.login not in allowed:
        raise HTTPException(status_code=403, detail="Actor is not allowed to create manual runs")
    unknown = set(request.requested_capabilities) - set(CATALOG)
    if unknown:
        raise HTTPException(status_code=422,
                            detail=f"Unknown capabilities: {sorted(unknown)}")
    context = TriggerContext(
        trigger=Trigger(type="manual", mode="manual", event="manual", action="requested",
                        delivery_id=f"manual-{uuid4().hex}", actor=request.actor),
        repository=request.repository, revision=request.revision,
        installation=request.installation,
    )
    run = QCRun(trigger_context=context,
                requested_capabilities=request.requested_capabilities)
    # Manual selection enters through the same repository config/planner pipeline. A later
    # policy merge may add mandatory capabilities but must never remove them.
    store.save_run(run)
    store.enqueue_run(run.run_id)
    return {"status": "queued", "run_id": run.run_id}


def _authorize_internal(request: ManualRunRequest, authorization: str | None) -> None:
    expected = settings.agent_qc_manual_api_token
    if not expected or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="Trigger authentication failed")
    allowed = {login.strip() for login in settings.agent_qc_manual_allowlist.split(",")
               if login.strip()}
    if request.actor.login not in allowed:
        raise HTTPException(status_code=403, detail="Actor is not allowlisted")


async def _enqueue_internal(request: ManualRunRequest, trigger_type: str) -> dict[str, str]:
    context = TriggerContext(
        trigger=Trigger(type=trigger_type, mode="auto", event=trigger_type,
                        action="requested", delivery_id=f"{trigger_type}-{uuid4().hex}",
                        actor=request.actor),
        repository=request.repository, revision=request.revision,
        installation=request.installation,
    )
    store.cancel_stale_runs(request.repository.full_name, request.revision.head_sha)
    run = QCRun(trigger_context=context, requested_capabilities=request.requested_capabilities)
    store.save_run(run)
    store.enqueue_run(run.run_id)
    return {"status": "queued", "run_id": run.run_id}


@app.post("/api/v1/triggers/schedule", status_code=status.HTTP_202_ACCEPTED)
async def schedule_run(request: ManualRunRequest,
                       authorization: str | None = Header(default=None)) -> dict[str, str]:
    if not settings.agent_qc_schedule_enabled:
        raise HTTPException(status_code=404, detail="Schedule trigger is disabled")
    _authorize_internal(request, authorization)
    return await _enqueue_internal(request, "schedule")


@app.post("/api/v1/triggers/deployment", status_code=status.HTTP_202_ACCEPTED)
async def deployment_run(request: ManualRunRequest,
                         authorization: str | None = Header(default=None)) -> dict[str, str]:
    if not settings.agent_qc_deployment_trigger_enabled:
        raise HTTPException(status_code=404, detail="Deployment trigger is disabled")
    _authorize_internal(request, authorization)
    return await _enqueue_internal(request, "deployment")


@app.post("/webhooks/github", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, str]:
    raw = await request.body()
    if not verify_webhook_signature(raw, x_hub_signature_256, settings.github_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if not x_github_delivery:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Delivery")
    if not store.claim_delivery(x_github_delivery):
        return {"status": "duplicate", "delivery_id": x_github_delivery}
    if x_github_event != "pull_request":
        return {"status": "ignored", "delivery_id": x_github_delivery}
    payload = json.loads(raw)
    if payload.get("action") not in {"opened", "synchronize", "reopened"}:
        return {"status": "ignored", "delivery_id": x_github_delivery}
    try:
        context = normalize_pull_request(payload, x_github_delivery)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid GitHub payload: {exc}") from exc
    run = QCRun(trigger_context=context)
    store.cancel_stale_runs(
        context.repository.full_name, context.revision.head_sha,
        context.pull_request.number if context.pull_request else None,
    )
    # Publish a queued Check Run before returning the webhook response so the
    # PR immediately shows that Agent-QC received the review request.
    run.check_run_id = await github.create_check(run)
    store.save_run(run)
    store.enqueue_run(run.run_id)
    return {"status": "queued", "run_id": run.run_id}
