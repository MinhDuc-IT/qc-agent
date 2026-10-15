"""Authenticated QC external worker consuming approved baselines from a trusted root."""
import hmac
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..pipeline import verify
from ..runner import HttpSuiteExecutor


class Target(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str
    ref: str | None = None
    project_id: str


class Parameters(BaseModel):
    model_config = ConfigDict(extra="ignore")
    baseline_version: str = Field(pattern=r"^[a-f0-9]{20}$")
    allow_mutations: bool = False


class Task(BaseModel):
    model_config = ConfigDict(extra="ignore")
    task_id: str
    run_id: str
    capability: str
    target: Target
    parameters: Parameters


class Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    task: Task


@dataclass
class Job:
    task: Task
    status: str = "queued"
    cancellation: Event = field(default_factory=Event)
    result: dict | None = None
    active: bool = True


def create_app(baseline_root=None, token=None, allowed_hosts=None, allow_mutations=None):
    root_value = baseline_root or os.getenv("TDA_BASELINE_ROOT")
    token = token or os.getenv("TDA_QC_TOKEN")
    if not root_value or not token:
        raise ValueError("TDA_BASELINE_ROOT and TDA_QC_TOKEN are required")
    root = Path(root_value).resolve()
    hosts = set(allowed_hosts or os.getenv("TDA_QC_ALLOWED_HOSTS", "127.0.0.1,localhost").split(","))
    mutation_policy = os.getenv("TDA_QC_ALLOW_MUTATIONS") == "1" if allow_mutations is None else allow_mutations
    app = FastAPI(title="Approved Baseline QC Worker", version="0.2.0")
    jobs, lock = {}, Lock()

    def authenticated(authorization: str | None = Header(default=None)):
        if not authorization or not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(status_code=401, detail="Service authentication required")

    def approved(version):
        baseline = (root / version).resolve()
        if not baseline.is_relative_to(root):
            raise ValueError("baseline escapes trusted root")
        manifest = verify(baseline)
        if manifest["schema_version"] != "2.0" or manifest["state"] != "approved" or manifest["baseline_version"] != version:
            raise ValueError("baseline must be an approved v2 artifact with matching version")
        return baseline

    def perform(identifier, baseline):
        job = jobs[identifier]
        with lock:
            if job.cancellation.is_set():
                job.status = "cancelled"
                job.active = False
                return
            job.status = "running"
        try:
            approved(job.task.parameters.baseline_version)
            cases = json.loads((baseline / "cases/cases.json").read_text(encoding="utf-8"))
            report = HttpSuiteExecutor(job.cancellation).execute(
                cases, job.task.target.ref,
                bool(mutation_policy and job.task.parameters.allow_mutations))
            failures = [result for result in report["results"] if result["status"] == "defect"]
            nonproduct = [result for result in report["results"] if result["status"] not in {"pass", "defect"}]
            verdict = "fail" if failures else "unknown" if nonproduct else "pass"
            findings = [{"id": "baseline-" + result["case_id"], "severity": "high", "category": "test-failure",
                         "title": "Approved baseline assertion failed", "message": "Test case " + result["case_id"] + " violates its oracle"}
                        for result in failures]
            normalized = {"schema_version": "1.0", "task_id": job.task.task_id, "run_id": job.task.run_id,
                          "capability": "baseline.http", "worker_id": "approved-baseline-worker",
                          "implementation": "test-design-agent@0.2.0", "worker_kind": "external_agent",
                          "execution_status": "completed" if not nonproduct else "failed", "verdict": verdict,
                          "summary": {"baseline_version": job.task.parameters.baseline_version,
                                      "results": report["results"]}, "findings": findings,
                          "metrics": {"cases_total": float(len(cases)), "cases_failed": float(len(failures))}}
            if nonproduct:
                normalized["error"] = {"code": "BASELINE_EXECUTION_BLOCKED", "message": "Baseline environment, fixture or execution failure",
                                       "category": "execution", "retryable": False}
            with lock:
                job.result = normalized
                job.status = "cancelled" if job.cancellation.is_set() else "completed"
        except Exception:
            with lock:
                job.status = "cancelled" if job.cancellation.is_set() else "failed"
        finally:
            with lock:
                job.active = False

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/v1/tasks", dependencies=[Depends(authenticated)])
    def submit(envelope: Envelope, background: BackgroundTasks):
        task = envelope.task
        if task.capability != "baseline.http" or task.target.type != "http_service":
            raise HTTPException(status_code=422, detail="Unsupported capability or target")
        parsed = urlsplit(task.target.ref or "")
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in hosts or parsed.username is not None or parsed.query or parsed.fragment:
            raise HTTPException(status_code=422, detail="Target host or URL is not allowed")
        try:
            baseline = approved(task.parameters.baseline_version)
        except (ValueError, OSError, KeyError):
            raise HTTPException(status_code=409, detail="Baseline unavailable, unapproved or invalid") from None
        with lock:
            if sum(job.active for job in jobs.values()) >= 2:
                raise HTTPException(status_code=429, detail="Worker capacity exhausted")
            if len(jobs) >= 100:
                completed = next((key for key, job in jobs.items() if not job.active), None)
                if completed:
                    del jobs[completed]
            identifier = uuid4().hex
            jobs[identifier] = Job(task=task)
        background.add_task(perform, identifier, baseline)
        return {"run_id": identifier, "status": "queued"}

    @app.get("/v1/tasks/{identifier}", dependencies=[Depends(authenticated)])
    def poll(identifier: str):
        with lock:
            job = jobs.get(identifier)
            if job is None:
                raise HTTPException(status_code=404, detail="Unknown task")
            result = {"run_id": identifier, "status": job.status}
            if job.status == "completed":
                result["result"] = job.result
            elif job.status == "failed":
                result["error"] = "Baseline worker failed; inspect trusted artifact and environment"
            return result

    @app.delete("/v1/tasks/{identifier}", dependencies=[Depends(authenticated)])
    def cancel(identifier: str):
        with lock:
            job = jobs.get(identifier)
            if job is None:
                raise HTTPException(status_code=404, detail="Unknown task")
            job.cancellation.set()
            if job.status in {"queued", "running"}:
                job.status = "cancelled"
            return {"run_id": identifier, "status": job.status}

    return app
