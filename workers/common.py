from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

from app.domain.models import ProjectDescriptor, WorkerResult, WorkerTask


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    ref: str


class AgentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    task: WorkerTask
    project: ProjectDescriptor
    source: SourceRef


@dataclass
class PreparedCommand:
    argv: list[str]
    cwd: Path
    environment: dict[str, str] = field(default_factory=dict)
    remove_environment: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


Prepare = Callable[[AgentRequest, str], PreparedCommand]
Parse = Callable[[AgentRequest, int, str, PreparedCommand], WorkerResult]


class CommandRuns:
    def __init__(self, name: str, executable: str, token: str, max_runtime: int,
                 allowed_root: Path | None, prepare: Prepare, parse: Parse) -> None:
        self.name, self.executable, self.token = name, executable, token
        self.max_runtime, self.allowed_root = max_runtime, allowed_root
        self.prepare, self.parse = prepare, parse
        self.states: dict[str, dict[str, Any]] = {}
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.lock = threading.Lock()

    def submit(self, request: AgentRequest) -> str:
        if request.source.type != "local_path":
            raise ValueError("local pilot only supports local_path sources")
        source = Path(request.source.ref).resolve()
        if not source.is_dir():
            raise ValueError("source workspace does not exist")
        if self.allowed_root and source != self.allowed_root and self.allowed_root not in source.parents:
            raise ValueError("source workspace is outside the configured allowed root")
        run_id = f"{self.name}_{uuid4().hex}"
        prepared = self.prepare(request, run_id)
        with self.lock:
            self.states[run_id] = {"run_id": run_id, "status": "queued"}
        threading.Thread(target=self._execute, args=(run_id, request, prepared), daemon=True).start()
        return run_id

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.lock:
            state = self.states.get(run_id)
            return dict(state) if state else None

    def cancel(self, run_id: str) -> bool:
        with self.lock:
            if run_id not in self.states:
                return False
            process = self.processes.get(run_id)
            self.states[run_id] = {"run_id": run_id, "status": "cancelled"}
        if process and process.poll() is None:
            process.terminate()
        return True

    def _execute(self, run_id: str, request: AgentRequest, prepared: PreparedCommand) -> None:
        try:
            if shutil.which(self.executable) is None:
                raise RuntimeError(f"required executable not found: {self.executable}")
            child_env = os.environ.copy()
            for name in prepared.remove_environment:
                child_env.pop(name, None)
            child_env.update(prepared.environment)
            process = subprocess.Popen(
                prepared.argv, cwd=prepared.cwd,
                env=child_env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                shell=False,
            )
            with self.lock:
                self.processes[run_id] = process
                self.states[run_id] = {"run_id": run_id, "status": "running"}
            try:
                output, _ = process.communicate(
                    timeout=min(request.task.timeout_seconds, self.max_runtime)
                )
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate()
                self._fail(run_id, "timed_out", f"worker timed out\n{output[-12000:]}")
                return
            with self.lock:
                if self.states[run_id]["status"] == "cancelled":
                    return
            result = self.parse(request, process.returncode, output, prepared)
            with self.lock:
                self.states[run_id] = {
                    "run_id": run_id, "status": "completed",
                    "result": result.model_dump(mode="json"),
                }
        except Exception as exc:
            self._fail(run_id, "failed", f"{type(exc).__name__}: {exc}")
        finally:
            with self.lock:
                self.processes.pop(run_id, None)

    def _fail(self, run_id: str, state: str, message: str) -> None:
        with self.lock:
            if self.states.get(run_id, {}).get("status") != "cancelled":
                self.states[run_id] = {"run_id": run_id, "status": state, "error": message}


def env_path(name: str) -> Path | None:
    value = os.getenv(name, "").strip()
    return Path(value).resolve() if value else None


def create_app(title: str, version: str, runs: CommandRuns) -> FastAPI:
    app = FastAPI(title=title, version=version)

    def authorize(value: str | None) -> None:
        if runs.token and value != f"Bearer {runs.token}":
            raise HTTPException(status_code=401, detail="invalid worker token")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "agent": runs.name, "version": version}

    @app.post("/v1/tasks", status_code=status.HTTP_202_ACCEPTED)
    def submit(request: AgentRequest, authorization: str | None = Header(default=None)):
        authorize(authorization)
        try:
            return {"run_id": runs.submit(request), "status": "queued"}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/tasks/{run_id}")
    def get(run_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        state = runs.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run not found")
        return state

    @app.delete("/v1/tasks/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
    def cancel(run_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        if not runs.cancel(run_id):
            raise HTTPException(status_code=404, detail="run not found")
        return Response(status_code=204)

    return app
