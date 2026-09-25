import asyncio
import json

from fastapi import FastAPI, Header, HTTPException, Request, status

from ..application.orchestrator import Orchestrator
from ..domain.models import QCRun, normalize_pull_request
from ..infrastructure.github.client import GitHubClient
from ..infrastructure.github.webhook import verify_webhook_signature
from ..infrastructure.execution.workers import RegisteredWorkerRuntime
from ..infrastructure.persistence.sqlite import SqliteRunStore
from ..infrastructure.settings import get_settings
from ..infrastructure.source.git import GitRepositoryManager

settings = get_settings()
store = SqliteRunStore(settings.agent_qc_data_dir)
github = GitHubClient(settings.github_app_id, settings.github_private_key_path, settings.agent_qc_dry_run)
source = GitRepositoryManager(settings.agent_qc_workspace_dir, github, settings.agent_qc_dry_run)
workers = RegisteredWorkerRuntime()
orchestrator = Orchestrator(store, github, source, workers)
app = FastAPI(title="Agent-QC", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str) -> QCRun:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


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
    store.save_run(run)
    asyncio.create_task(orchestrator.execute(run.run_id))
    return {"status": "queued", "run_id": run.run_id}
