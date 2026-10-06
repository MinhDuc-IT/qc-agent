from __future__ import annotations

import os
from pathlib import Path

from app.domain.models import Finding, WorkerResult
from workers.common import (AgentRequest, CommandRuns, PreparedCommand, create_app,
                            env_path)


VERSION = "3.8.58"
EXECUTABLE = os.getenv("KEPLOY_EXECUTABLE", "keploy")


def prepare(request: AgentRequest, run_id: str) -> PreparedCommand:
    if request.task.capability not in {"integration.service", "contract.consumer_provider"}:
        raise ValueError(f"unsupported capability: {request.task.capability}")
    source = Path(request.source.ref).resolve()
    project = source if request.project.root == "." else source / request.project.root
    test_path = request.task.parameters.get("keploy_path", "keploy")
    if not isinstance(test_path, str) or Path(test_path).is_absolute() or ".." in Path(test_path).parts:
        raise ValueError("keploy_path must be repository-relative")
    tests = project / test_path
    if not tests.exists():
        raise ValueError(f"recorded Keploy tests not found: {test_path}")
    # Keploy 3.8 writes reports by default; the legacy --generateTestReport flag
    # was removed and makes current releases exit before replaying any tests.
    # --path is Keploy's config/project root; Keploy discovers its `keploy/`
    # data directory below that root. Passing the data directory itself makes
    # the CLI search for `keploy/keploy` and report no test sets.
    keploy_root = tests.parent
    argv = [EXECUTABLE, "test", "--path", str(keploy_root), "--disable-ansi"]
    command = request.task.parameters.get("sut_command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Keploy requires a reviewed sut_command from base-branch policy")
    argv.extend(["--command", command])
    # Explicitly prevent accidental use of Keploy Cloud/AI in the OSS replay worker.
    environment = {"KEPLOY_DISABLE_TELEMETRY": "true", "KEPLOY_API_KEY": ""}
    return PreparedCommand(argv=argv, cwd=project, environment=environment,
                           remove_environment=("OPENAI_API_KEY", "STRIX_LLM_API_KEY",
                                               "HERCULES_LLM_API_KEY"),
                           metadata={"test_path": str(tests),
                                     "keploy_root": str(keploy_root)})


def parse(request: AgentRequest, exit_code: int, output: str,
          prepared: PreparedCommand) -> WorkerResult:
    findings = []
    if exit_code != 0:
        findings.append(Finding(
            id=f"finding_{request.task.task_id[-16:]}", severity="high",
            category="integration", title="Keploy replay failed",
            message="One or more recorded API interactions did not match. See worker output/report.",
        ))
    reports = list(Path(prepared.metadata["test_path"]).rglob("reports/*.yaml"))
    return WorkerResult(
        task_id=request.task.task_id, run_id=request.task.run_id,
        capability=request.task.capability, worker_id="keploy-oss",
        implementation=f"keploy@{VERSION}", worker_kind="external_agent",
        execution_status="completed", verdict="pass" if exit_code == 0 else "fail",
        exit_code=exit_code, output=output[-12000:],
        summary={"reports": len(reports), "cloud_ai_enabled": False}, findings=findings,
    )


runs = CommandRuns(
    "keploy", EXECUTABLE, os.getenv("KEPLOY_AGENT_TOKEN", ""),
    int(os.getenv("KEPLOY_MAX_RUNTIME_SECONDS", "1200")),
    env_path("KEPLOY_ALLOWED_SOURCE_ROOT"), prepare, parse,
)
app = create_app("Agent-QC Integration Agent (Keploy OSS)", VERSION, runs)
