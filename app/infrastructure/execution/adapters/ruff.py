from ....domain.models import ProjectDescriptor, WorkerTask
from .base import CommandAdapter


class RuffAdapter(CommandAdapter):
    worker_id, implementation, capability = "ruff-lint", "ruff", "static.lint"
    languages, executable = ("python",), "ruff"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        return ("ruff", "check", ".")
