import os
import shutil
import subprocess
from pathlib import Path

from ...domain.models import ProjectDescriptor, WorkerResult, WorkerTask
from .registry import WorkerRegistry


class RegisteredWorkerRuntime:
    def __init__(self, registry: WorkerRegistry | None = None):
        self.registry = registry or WorkerRegistry()

    def execute(self, task: WorkerTask, project: ProjectDescriptor, workspace: Path) -> WorkerResult:
        adapter = self.registry.resolve(task, project)
        if adapter is None:
            return self._skipped(task, "No compatible worker adapter")
        task.worker_id = adapter.worker_id
        task.implementation = adapter.implementation
        if shutil.which(adapter.executable) is None:
            return self._skipped(task, f"Required executable is unavailable: {adapter.executable}")
        try:
            adapter.validate(task, project)
            invocation = adapter.build_invocation(task, project, workspace)
            process = subprocess.run(
                invocation.argv, cwd=invocation.cwd, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env={**os.environ, **invocation.environment,
                     "AGENT_QC_RUN_ID": task.run_id, "AGENT_QC_TASK_ID": task.task_id},
                shell=False, timeout=task.timeout_seconds, check=False,
            )
            output = process.stdout.decode(errors="replace")
            return adapter.parse_result(task, process.returncode, output)
        except ValueError as exc:
            return self._failed(task, adapter.worker_id, adapter.implementation,
                                f"Invalid worker configuration: {exc}")
        except subprocess.TimeoutExpired as exc:
            output = ((exc.stdout or b"").decode(errors="replace")
                      if isinstance(exc.stdout, bytes) else (exc.stdout or ""))
            return WorkerResult(
                task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                worker_id=adapter.worker_id, implementation=adapter.implementation,
                worker_kind="tool",
                execution_status="timed_out", verdict="unknown", output=output,
            )
        except OSError as exc:
            return self._failed(task, adapter.worker_id, adapter.implementation, str(exc))

    @staticmethod
    def _skipped(task: WorkerTask, reason: str) -> WorkerResult:
        return WorkerResult(
            task_id=task.task_id, run_id=task.run_id, capability=task.capability,
            worker_kind="tool",
            execution_status="completed", verdict="skipped", output=reason,
            summary={"reason": reason},
        )

    @staticmethod
    def _failed(task: WorkerTask, worker_id: str, implementation: str, reason: str) -> WorkerResult:
        return WorkerResult(
            task_id=task.task_id, run_id=task.run_id, capability=task.capability,
            worker_id=worker_id, implementation=implementation,
            worker_kind="tool",
            execution_status="failed", verdict="unknown", output=reason,
        )
