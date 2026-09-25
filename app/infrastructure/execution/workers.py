import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ...domain.models import Finding, ProjectDescriptor, WorkerResult, WorkerTask


@dataclass(frozen=True)
class WorkerImplementation:
    worker_id: str
    implementation: str
    capability: str
    languages: tuple[str, ...]
    executable: str
    command: tuple[str, ...]


IMPLEMENTATIONS = [
    WorkerImplementation("python-pytest", "pytest", "functional.unit", ("python",), "python", ("python", "-m", "pytest", "-q")),
    WorkerImplementation("python-pytest-integration", "pytest", "functional.integration", ("python",), "python", ("python", "-m", "pytest", "-q", "-m", "integration")),
    WorkerImplementation("node-npm-test", "npm", "functional.unit", ("javascript",), "npm", ("npm", "test", "--", "--runInBand")),
    WorkerImplementation("jvm-maven-test", "maven", "functional.unit", ("java",), "mvn", ("mvn", "--batch-mode", "test")),
    WorkerImplementation("jvm-gradle-test", "gradle", "functional.unit", ("java", "kotlin"), "gradle", ("gradle", "test")),
    WorkerImplementation("go-test", "go-test", "functional.unit", ("go",), "go", ("go", "test", "./...")),
    WorkerImplementation("rust-cargo-test", "cargo", "functional.unit", ("rust",), "cargo", ("cargo", "test")),
    WorkerImplementation("dotnet-test", "dotnet", "functional.unit", ("dotnet",), "dotnet", ("dotnet", "test")),
    WorkerImplementation("ruff-lint", "ruff", "static.lint", ("python",), "ruff", ("ruff", "check", ".")),
    WorkerImplementation("eslint-lint", "eslint", "static.lint", ("javascript",), "npx", ("npx", "--no-install", "eslint", ".")),
    WorkerImplementation("semgrep-sast", "semgrep", "security.sast", ("python", "javascript", "java", "kotlin", "go", "rust", "dotnet", "generic"), "semgrep", ("semgrep", "scan", "--config", "auto", "--json")),
    WorkerImplementation("trivy-dependency", "trivy", "security.dependency", ("python", "javascript", "java", "kotlin", "go", "rust", "dotnet", "generic"), "trivy", ("trivy", "fs", ".")),
]


class WorkerRegistry:
    def resolve(self, task: WorkerTask, project: ProjectDescriptor) -> WorkerImplementation | None:
        candidates = [item for item in IMPLEMENTATIONS
                      if item.capability == task.capability and project.language in item.languages]
        if project.build_system:
            exact = [item for item in candidates if item.implementation == project.build_system]
            if exact:
                return exact[0]
        return candidates[0] if candidates else None


class WorkerExecutor:
    def execute(self, task: WorkerTask, project: ProjectDescriptor, workspace: Path,
                implementation: WorkerImplementation | None) -> WorkerResult:
        if implementation is None:
            return self._skipped(task, "No compatible worker implementation")
        task.worker_id = implementation.worker_id
        task.implementation = implementation.implementation
        command = list(implementation.command)
        executable = command[0]
        if shutil.which(executable) is None:
            return self._skipped(task, f"Required executable is unavailable: {executable}")
        cwd = workspace if project.root == "." else workspace / project.root
        try:
            process = subprocess.run(
                command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env={**os.environ, "AGENT_QC_RUN_ID": task.run_id, "AGENT_QC_TASK_ID": task.task_id},
                shell=False, timeout=task.timeout_seconds, check=False,
            )
            output = process.stdout.decode(errors="replace")
            verdict = "pass" if process.returncode == 0 else "fail"
            return WorkerResult(
                task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                worker_id=implementation.worker_id, implementation=implementation.implementation,
                execution_status="completed", verdict=verdict, exit_code=process.returncode,
                output=output, summary=self._parse_counts(output), findings=self._parse_findings(output),
            )
        except subprocess.TimeoutExpired as exc:
            return WorkerResult(
                task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                worker_id=implementation.worker_id, implementation=implementation.implementation,
                execution_status="timed_out", verdict="unknown",
                output=(exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or ""),
            )
        except OSError as exc:
            return WorkerResult(
                task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                worker_id=implementation.worker_id, implementation=implementation.implementation,
                execution_status="failed", verdict="unknown", output=str(exc),
            )

    @staticmethod
    def _skipped(task: WorkerTask, reason: str) -> WorkerResult:
        return WorkerResult(task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                            execution_status="completed", verdict="skipped", output=reason,
                            summary={"reason": reason})

    @staticmethod
    def _parse_counts(output: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key in ("passed", "failed", "skipped", "error", "errors"):
            matches = re.findall(rf"(\d+)\s+{key}\b", output, re.IGNORECASE)
            if matches:
                counts["errors" if key in {"error", "errors"} else key] = int(matches[-1])
        return counts

    @staticmethod
    def _parse_findings(output: str) -> list[Finding]:
        findings = []
        for path, line in re.findall(r"(?m)^([^\s:]+\.py):(\d+):\s*(?:AssertionError|error)", output):
            findings.append(Finding(id=f"finding-{len(findings)+1}", severity="high",
                                    category="test-failure", title="Automated test failure",
                                    message=f"Failure reported at {path}:{line}", path=path,
                                    start_line=int(line)))
        return findings[:50]


class RegisteredWorkerRuntime:
    """Infrastructure facade hiding registry and process execution from the use case."""

    def __init__(self, registry: WorkerRegistry | None = None, executor: WorkerExecutor | None = None):
        self.registry = registry or WorkerRegistry()
        self.executor = executor or WorkerExecutor()

    def execute(self, task: WorkerTask, project: ProjectDescriptor, workspace: Path) -> WorkerResult:
        implementation = self.registry.resolve(task, project)
        return self.executor.execute(task, project, workspace, implementation)
