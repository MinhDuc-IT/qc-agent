from __future__ import annotations

import json
import os
from pathlib import Path

from app.domain.models import Finding, WorkerResult
from workers.common import (AgentRequest, CommandRuns, PreparedCommand, create_app,
                            env_path)


EXECUTABLE = os.getenv("STRIX_EXECUTABLE", "strix")
VERSION = "1.4.0"
MAX_BUDGET = float(os.getenv("STRIX_MAX_BUDGET_USD", "5"))
MODEL = os.getenv("STRIX_LLM_MODEL", "openai/gpt-5-mini")
API_KEY = os.getenv("STRIX_LLM_API_KEY", "")


def prepare(request: AgentRequest, run_id: str) -> PreparedCommand:
    if request.task.capability != "security.dast":
        raise ValueError(f"unsupported capability: {request.task.capability}")
    if request.task.parameters.get("trigger_type") != "schedule":
        raise ValueError("Strix is restricted to scheduled/nightly runs")
    if not API_KEY:
        raise ValueError("STRIX_LLM_API_KEY is not configured")
    requested = float(request.task.parameters.get("max_budget_usd", MAX_BUDGET))
    if requested <= 0 or requested > MAX_BUDGET:
        raise ValueError(f"max_budget_usd must be > 0 and <= {MAX_BUDGET}")
    source = Path(request.source.ref).resolve()
    project = source if request.project.root == "." else source / request.project.root
    argv = [EXECUTABLE, "-n", "--target", str(project)]
    target = request.task.target.ref
    if target and str(target).startswith(("http://", "https://")):
        argv.extend(["--target", str(target)])
    argv.extend(["--scan-mode", "standard", "--scope-mode", "full",
                 "--max-budget", str(requested)])
    environment = {
        "STRIX_LLM": MODEL, "LLM_API_KEY": API_KEY, "STRIX_TELEMETRY": "0",
    }
    return PreparedCommand(argv=argv, cwd=project, environment=environment,
                           remove_environment=("OPENAI_API_KEY", "HERCULES_LLM_API_KEY"),
                           metadata={"budget": requested, "before": {
                               str(path.resolve()) for path in project.glob("strix_runs/*")
                           }})


def _new_run(project: Path, before: set[str]) -> Path | None:
    candidates = [path for path in project.glob("strix_runs/*")
                  if str(path.resolve()) not in before and path.is_dir()]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def parse(request: AgentRequest, exit_code: int, output: str,
          prepared: PreparedCommand) -> WorkerResult:
    run_dir = _new_run(prepared.cwd, prepared.metadata["before"])
    run_status, cost = "unknown", 0.0
    findings = []
    if run_dir:
        run_json = run_dir / "run.json"
        if run_json.is_file():
            payload = json.loads(run_json.read_text(encoding="utf-8"))
            run_status = str(payload.get("status", "unknown"))
            cost = float(payload.get("llm_usage", {}).get("cost", 0) or 0)
        vulnerability_dir = run_dir / "vulnerabilities"
        for path in vulnerability_dir.glob("*") if vulnerability_dir.is_dir() else []:
            if not path.is_file():
                continue
            message = path.read_text(encoding="utf-8", errors="replace")[:4000]
            findings.append(Finding(
                id=f"finding_strix_{len(findings)+1}", severity="high",
                category="security.dast", title=path.stem, message=message,
            ))
    budget_stopped = run_status == "stopped"
    verdict = "fail" if findings or exit_code == 2 else ("warning" if budget_stopped else "pass")
    execution_status = "completed" if exit_code in {0, 2} else "failed"
    return WorkerResult(
        task_id=request.task.task_id, run_id=request.task.run_id,
        capability=request.task.capability, worker_id="strix-nightly",
        implementation=f"strix@{VERSION}", worker_kind="external_agent",
        execution_status=execution_status, verdict=verdict, exit_code=exit_code,
        output=output[-12000:], summary={
            "scan_mode": "standard", "run_status": run_status,
            "max_budget_usd": prepared.metadata["budget"], "cost_usd": cost,
            "budget_stopped": budget_stopped,
        }, metrics={"llm_cost_usd": cost}, findings=findings[:200],
    )


runs = CommandRuns(
    "strix", EXECUTABLE, os.getenv("STRIX_AGENT_TOKEN", ""),
    int(os.getenv("STRIX_MAX_RUNTIME_SECONDS", "7200")), env_path("STRIX_ALLOWED_SOURCE_ROOT"),
    prepare, parse,
)
app = create_app("Agent-QC Nightly Security Agent (Strix)", VERSION, runs)
