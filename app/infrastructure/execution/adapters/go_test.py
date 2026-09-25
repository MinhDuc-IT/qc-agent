from ....domain.models import ProjectDescriptor, WorkerTask
from .base import CommandAdapter


class GoTestAdapter(CommandAdapter):
    worker_id, implementation, capability = "go-test", "go-test", "functional.unit"
    languages, executable = ("go",), "go"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        return ("go", "test", "./...")
