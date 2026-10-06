from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4
from xml.etree import ElementTree

from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

from app.domain.models import ErrorDetail, Finding, ProjectDescriptor, WorkerResult, WorkerTask


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


class HerculesSettings:
    def __init__(self) -> None:
        self.executable = os.getenv("HERCULES_EXECUTABLE", "testzeus-hercules")
        self.model = os.getenv("HERCULES_LLM_MODEL", "gpt-4o-mini")
        self.api_key = os.getenv("HERCULES_LLM_API_KEY", "")
        self.base_url = os.getenv("HERCULES_LLM_BASE_URL", "https://api.openai.com/v1")
        self.auth_token = os.getenv("HERCULES_AGENT_TOKEN", "")
        self.max_scenarios = int(os.getenv("HERCULES_MAX_SCENARIOS", "3"))
        self.max_steps = int(os.getenv("HERCULES_MAX_STEPS", "15"))
        self.max_runtime = int(os.getenv("HERCULES_MAX_RUNTIME_SECONDS", "900"))
        self.work_root = Path(os.getenv("HERCULES_WORK_ROOT", ".agent-qc-hercules")).resolve()
        allowed = os.getenv("HERCULES_ALLOWED_SOURCE_ROOT", "").strip()
        self.allowed_source_root = Path(allowed).resolve() if allowed else None


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_" else "_" for char in value)
    return cleaned[:80] or "agent_qc_scenario"


def build_feature(task: WorkerTask) -> str:
    supplied = task.parameters.get("feature")
    if isinstance(supplied, str) and supplied.strip():
        return supplied.strip() + "\n"
    target_url = task.target.ref or task.parameters.get("base_url")
    if not target_url:
        raise ValueError("functional agent requires target.ref, parameters.base_url, or parameters.feature")
    parsed = urlparse(str(target_url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or "\n" in str(target_url):
        raise ValueError("functional target must be a valid http(s) URL")
    raw_scenarios = task.scope.get("scenarios") or [task.objective]
    scenarios = [" ".join(str(item).split()) for item in raw_scenarios if str(item).strip()]
    if not scenarios:
        raise ValueError("functional agent requires at least one scenario")
    lines = ["Feature: Agent-QC functional validation", ""]
    for index, scenario in enumerate(scenarios, 1):
        lines.extend([
            f"  Scenario: {_safe_name(scenario)}",
            f'    Given I open the page "{target_url}"',
            f"    When I perform this user objective: {scenario}",
            "    Then the objective should complete without an application error",
            "",
        ])
    return "\n".join(lines)


def build_command(settings: HerculesSettings, project_base: Path) -> list[str]:
    return [settings.executable, "--project-base", str(project_base)]


def _xml_totals(root: ElementTree.Element) -> dict[str, int]:
    suites = [root] if root.tag.endswith("testsuite") else list(root.iter("testsuite"))
    totals = {key: 0 for key in ("tests", "failures", "errors", "skipped")}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.attrib.get(key, 0))
    return totals


def parse_junit(output_dir: Path) -> tuple[dict[str, int], list[Finding]]:
    xml_files = sorted(output_dir.rglob("*.xml"), key=lambda path: path.stat().st_mtime)
    if not xml_files:
        raise FileNotFoundError("Hercules did not produce a JUnit XML result")
    root = ElementTree.parse(xml_files[-1]).getroot()
    totals = _xml_totals(root)
    findings: list[Finding] = []
    for case in root.iter("testcase"):
        problem = next((child for child in case if child.tag.endswith(("failure", "error"))), None)
        if problem is None:
            continue
        name = case.attrib.get("name", "Functional scenario")
        message = problem.attrib.get("message") or (problem.text or "Scenario failed").strip()
        fingerprint = hashlib.sha256(f"{name}:{message}".encode()).hexdigest()
        findings.append(Finding(
            id=f"finding_{fingerprint[:16]}", severity="high", category="functional",
            title=name, message=message[:4000], fingerprint=fingerprint,
        ))
    return totals, findings


class HerculesRuns:
    def __init__(self, settings: HerculesSettings) -> None:
        self.settings = settings
        self.states: dict[str, dict[str, Any]] = {}
        self.processes: dict[str, subprocess.Popen[str]] = {}
        self.lock = threading.Lock()

    def submit(self, request: AgentRequest) -> str:
        if request.task.capability not in {"functional.e2e", "functional.api"}:
            raise ValueError(f"unsupported capability: {request.task.capability}")
        scenarios = request.task.scope.get("scenarios") or []
        if len(scenarios) > self.settings.max_scenarios:
            raise ValueError(f"scenario limit exceeded ({len(scenarios)} > {self.settings.max_scenarios})")
        feature = build_feature(request.task)
        step_count = sum(
            line.lstrip().startswith(("Given ", "When ", "Then ", "And ", "But "))
            for line in feature.splitlines()
        )
        if step_count > self.settings.max_steps:
            raise ValueError(f"step limit exceeded ({step_count} > {self.settings.max_steps})")
        if request.source.type != "local_path":
            raise ValueError("only local_path sources are supported by the local pilot")
        source = Path(request.source.ref).resolve()
        if not source.is_dir():
            raise ValueError("source workspace does not exist")
        allowed = self.settings.allowed_source_root
        if allowed and source != allowed and allowed not in source.parents:
            raise ValueError("source workspace is outside HERCULES_ALLOWED_SOURCE_ROOT")
        run_id = f"hercules_{uuid4().hex}"
        with self.lock:
            self.states[run_id] = {"run_id": run_id, "status": "queued"}
        threading.Thread(target=self._execute, args=(run_id, request), daemon=True).start()
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

    def _execute(self, run_id: str, request: AgentRequest) -> None:
        task = request.task
        run_root = self.settings.work_root / run_id
        try:
            if not self.settings.api_key:
                raise RuntimeError("HERCULES_LLM_API_KEY is not configured")
            if shutil.which(self.settings.executable) is None:
                raise RuntimeError(f"Hercules executable not found: {self.settings.executable}")
            input_dir, output_dir, data_dir = (run_root / name for name in ("input", "output", "test_data"))
            for directory in (input_dir, output_dir, data_dir, run_root / "log_files", run_root / "proofs"):
                directory.mkdir(parents=True, exist_ok=True)
            (input_dir / "test.feature").write_text(build_feature(task), encoding="utf-8")
            (data_dir / "test_data.json").write_text("{}\n", encoding="utf-8")
            env = os.environ.copy()
            env.pop("OPENAI_API_KEY", None)
            env.pop("STRIX_LLM_API_KEY", None)
            env.update({
                "LLM_MODEL_NAME": self.settings.model,
                "LLM_MODEL_API_KEY": self.settings.api_key,
                "LLM_MODEL_BASE_URL": self.settings.base_url,
                "HEADLESS": "true", "AUTO_MODE": "1", "ENABLE_TELEMETRY": "0",
            })
            timeout = min(task.timeout_seconds, self.settings.max_runtime)
            process = subprocess.Popen(
                build_command(self.settings, run_root), cwd=Path(request.source.ref).resolve(),
                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", shell=False,
            )
            with self.lock:
                self.processes[run_id] = process
                self.states[run_id] = {"run_id": run_id, "status": "running"}
            try:
                output, _ = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate()
                self._failure(run_id, "timed_out", "HERCULES_TIMEOUT", output[-12000:])
                return
            with self.lock:
                if self.states[run_id]["status"] == "cancelled":
                    return
            try:
                totals, findings = parse_junit(output_dir)
                verdict = "fail" if totals["failures"] + totals["errors"] else "pass"
                result = WorkerResult(
                    task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                    worker_id="testzeus-hercules", implementation="testzeus-hercules@1.0.2",
                    worker_kind="external_agent", execution_status="completed", verdict=verdict,
                    exit_code=process.returncode, output=output[-12000:], summary=totals,
                    findings=findings,
                )
            except FileNotFoundError as exc:
                self._failure(run_id, "failed", "HERCULES_NO_RESULT", f"{exc}\n{output[-12000:]}")
                return
            with self.lock:
                self.states[run_id] = {
                    "run_id": run_id, "status": "completed",
                    "result": result.model_dump(mode="json"),
                }
        except Exception as exc:
            self._failure(run_id, "failed", "HERCULES_EXECUTION_ERROR", str(exc))
        finally:
            with self.lock:
                self.processes.pop(run_id, None)

    def _failure(self, run_id: str, state: str, code: str, message: str) -> None:
        with self.lock:
            if self.states.get(run_id, {}).get("status") == "cancelled":
                return
            self.states[run_id] = {
                "run_id": run_id, "status": state, "error": message,
                "error_detail": ErrorDetail(
                    code=code, message=message[:4000], category="execution", retryable=False,
                ).model_dump(mode="json"),
            }


settings = HerculesSettings()
runs = HerculesRuns(settings)
app = FastAPI(title="Agent-QC Functional Agent (TestZeus Hercules)", version="1.0.0")


def authorize(authorization: str | None) -> None:
    if settings.auth_token and authorization != f"Bearer {settings.auth_token}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid worker token")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "testzeus-hercules", "version": "1.0.2"}


@app.post("/v1/tasks", status_code=status.HTTP_202_ACCEPTED)
def create_task(request: AgentRequest, authorization: str | None = Header(default=None)) -> dict[str, str]:
    authorize(authorization)
    try:
        return {"run_id": runs.submit(request), "status": "queued"}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@app.get("/v1/tasks/{run_id}")
def get_task(run_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    authorize(authorization)
    state = runs.get(run_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return state


@app.delete("/v1/tasks/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_task(run_id: str, authorization: str | None = Header(default=None)) -> Response:
    authorize(authorization)
    if not runs.cancel(run_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
