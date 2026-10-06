import shutil
import subprocess
from pathlib import Path

from ...domain.models import ErrorDetail, ProjectDescriptor, WorkerResult, WorkerTask
from .backends import ExecutionBackend, LocalExecutionBackend
from .registry import WorkerRegistry


class RegisteredWorkerRuntime:
    def __init__(self, registry: WorkerRegistry | None = None,
                 backend: ExecutionBackend | None = None):
        self.registry = registry or WorkerRegistry()
        self.backend = backend or LocalExecutionBackend()

    def execute(self, task: WorkerTask, project: ProjectDescriptor, workspace: Path) -> WorkerResult:
        adapter = self.registry.resolve(task, project)
        if adapter is None:
            return self._failed(task, None, None, "No compatible worker adapter",
                                "NO_COMPATIBLE_WORKER", "configuration", False)
        task.worker_id = adapter.worker_id
        task.implementation = adapter.implementation
        if shutil.which(adapter.executable) is None:
            return self._failed(
                task, adapter.worker_id, adapter.implementation,
                f"Required executable is unavailable: {adapter.executable}",
                "WORKER_START_FAILED", "execution", False,
            )
        try:
            adapter.validate(task, project)
            invocation = adapter.build_invocation(task, project, workspace)
            completed = self.backend.run(
                invocation, task.timeout_seconds,
                {"AGENT_QC_RUN_ID": task.run_id, "AGENT_QC_TASK_ID": task.task_id},
            )
            return adapter.parse_result(task, completed.exit_code, completed.output,
                                        invocation.cwd)
        except ValueError as exc:
            return self._failed(task, adapter.worker_id, adapter.implementation,
                                f"Invalid worker configuration: {exc}",
                                "INVALID_PROJECT_CONFIG", "configuration", False)
        except subprocess.TimeoutExpired as exc:
            output = ((exc.stdout or b"").decode(errors="replace")
                      if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
            return WorkerResult(
                task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                worker_id=adapter.worker_id, implementation=adapter.implementation,
                worker_kind="tool",
                execution_status="timed_out", verdict="unknown", output=output,
                error=ErrorDetail(code="WORKER_TIMEOUT", message="Worker timed out",
                                  category="execution", retryable=True),
            )
        except OSError as exc:
            return self._failed(task, adapter.worker_id, adapter.implementation, str(exc),
                                "WORKER_PROCESS_EXITED", "execution", True)

    @staticmethod
    def _skipped(task: WorkerTask, reason: str) -> WorkerResult:
        return WorkerResult(
            task_id=task.task_id, run_id=task.run_id, capability=task.capability,
            worker_kind="tool",
            execution_status="completed", verdict="skipped", output=reason,
            summary={"reason": reason},
        )

    @staticmethod
    def _failed(task: WorkerTask, worker_id: str | None, implementation: str | None,
                reason: str, code: str, category: str, retryable: bool) -> WorkerResult:
        return WorkerResult(
            task_id=task.task_id, run_id=task.run_id, capability=task.capability,
            worker_id=worker_id, implementation=implementation,
            worker_kind="tool",
            execution_status="failed", verdict="unknown", output=reason,
            error=ErrorDetail(code=code, message=reason, category=category,
                              retryable=retryable),
        )
