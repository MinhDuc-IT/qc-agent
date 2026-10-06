from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.domain.models import Finding, WorkerResult
from workers.common import (AgentRequest, CommandRuns, PreparedCommand, create_app,
                            env_path)


EXECUTABLE = os.getenv("K6_EXECUTABLE", "k6")
VERSION = "2.3.0"
WORK_ROOT = Path(os.getenv("K6_WORK_ROOT", ".agent-qc-k6")).resolve()
CAPS = {
    "performance.smoke": (2, 30), "performance.load": (50, 600),
    "performance.stress": (100, 900), "performance.soak": (25, 3600),
}
DEFAULTS = {
    "performance.smoke": (1, 10), "performance.load": (10, 60),
    "performance.stress": (30, 120), "performance.soak": (10, 600),
}


def _duration_seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)(s|m|h)", value)
    if not match:
        raise ValueError("duration must use <integer>s, <integer>m, or <integer>h")
    return int(match.group(1)) * {"s": 1, "m": 60, "h": 3600}[match.group(2)]


def _discover(project: Path, configured: object) -> Path | None:
    candidates = [configured] if isinstance(configured, str) else [
        ".agent-qc/k6.js", "performance/k6.js", "tests/performance/k6.js", "k6/script.js",
    ]
    for candidate in candidates:
        path = Path(str(candidate))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("k6 script_path must be repository-relative")
        resolved = project / path
        if resolved.is_file():
            return resolved
    if isinstance(configured, str):
        raise ValueError(f"configured k6 script does not exist: {configured}")
    return None


def prepare(request: AgentRequest, run_id: str) -> PreparedCommand:
    capability = request.task.capability
    if capability not in CAPS:
        raise ValueError(f"unsupported capability: {capability}")
    target = request.task.target.ref
    if not target or not str(target).startswith(("http://", "https://")):
        raise ValueError("k6 worker requires an http(s) target.ref")
    source = Path(request.source.ref).resolve()
    project = source if request.project.root == "." else source / request.project.root
    script = _discover(project, request.task.parameters.get("script_path"))
    run_root = WORK_ROOT / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    if script is None:
        script = run_root / "generated-smoke.js"
        script.write_text(
            "import http from 'k6/http';\nimport { check, sleep } from 'k6';\n"
            "export const options = { thresholds: { http_req_failed: ['rate<0.01'], "
            "http_req_duration: ['p(95)<500'] } };\n"
            "export default function () { const r=http.get(__ENV.BASE_URL); "
            "check(r, {'status below 500': x => x.status < 500}); sleep(1); }\n",
            encoding="utf-8",
        )
    default_vus, default_seconds = DEFAULTS[capability]
    max_vus, max_seconds = CAPS[capability]
    vus = int(request.task.parameters.get("vus", default_vus))
    duration = str(request.task.parameters.get("duration", f"{default_seconds}s"))
    if vus < 1 or vus > max_vus:
        raise ValueError(f"vus must be between 1 and {max_vus} for {capability}")
    if _duration_seconds(duration) > max_seconds:
        raise ValueError(f"duration exceeds {max_seconds}s budget for {capability}")
    result_path = run_root / "points.json"
    argv = [EXECUTABLE, "run", "--vus", str(vus), "--duration", duration,
            "--out", f"json={result_path}", str(script)]
    return PreparedCommand(argv=argv, cwd=project, environment={"BASE_URL": str(target)},
                           remove_environment=("OPENAI_API_KEY", "STRIX_LLM_API_KEY",
                                               "HERCULES_LLM_API_KEY"),
                           metadata={"result": str(result_path), "script": str(script),
                                     "vus": vus, "duration_seconds": _duration_seconds(duration)})


def parse(request: AgentRequest, exit_code: int, output: str,
          prepared: PreparedCommand) -> WorkerResult:
    durations, failed = [], []
    result_path = Path(prepared.metadata["result"])
    if result_path.is_file():
        for line in result_path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("type") != "Point":
                continue
            if item.get("metric") == "http_req_duration":
                durations.append(float(item["data"]["value"]))
            elif item.get("metric") == "http_req_failed":
                failed.append(float(item["data"]["value"]))
    durations.sort()
    p95 = durations[min(len(durations) - 1, int(len(durations) * .95))] if durations else 0.0
    error_rate = sum(failed) / len(failed) if failed else 0.0
    metrics = {"http_req_duration_p95_ms": p95, "http_req_failed_rate": error_rate}
    if exit_code != 0 and not durations:
        return WorkerResult(
            task_id=request.task.task_id, run_id=request.task.run_id,
            capability=request.task.capability, worker_id="performance-k6-agent",
            implementation=f"k6@{VERSION}", worker_kind="external_agent",
            execution_status="failed", verdict="unknown", exit_code=exit_code,
            output=output[-12000:], summary={"samples": 0}, metrics=metrics,
        )
    findings = []
    if exit_code != 0:
        findings.append(Finding(
            id=f"finding_{request.task.task_id[-16:]}", severity="high",
            category="performance", title="k6 performance threshold failed",
            message=f"p95={p95:.2f}ms; error_rate={error_rate:.4f}",
        ))
    return WorkerResult(
        task_id=request.task.task_id, run_id=request.task.run_id,
        capability=request.task.capability, worker_id="performance-k6-agent",
        implementation=f"k6@{VERSION}", worker_kind="external_agent", execution_status="completed",
        verdict="pass" if exit_code == 0 else "fail", exit_code=exit_code,
        output=output[-12000:], summary={
            "script": prepared.metadata["script"], "vus": prepared.metadata["vus"],
            "duration_seconds": prepared.metadata["duration_seconds"],
            "samples": len(durations),
        }, metrics=metrics, findings=findings,
    )


runs = CommandRuns(
    "k6", EXECUTABLE, os.getenv("K6_AGENT_TOKEN", ""),
    int(os.getenv("K6_MAX_RUNTIME_SECONDS", "3700")), env_path("K6_ALLOWED_SOURCE_ROOT"),
    prepare, parse,
)
app = create_app("Agent-QC Performance Agent (k6)", VERSION, runs)
