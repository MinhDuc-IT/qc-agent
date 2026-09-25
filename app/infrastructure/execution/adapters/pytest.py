from ....domain.models import ProjectDescriptor, WorkerTask
from .base import CommandAdapter


class PytestUnitAdapter(CommandAdapter):
    worker_id, implementation, capability = "python-pytest", "pytest", "functional.unit"
    languages, executable = ("python",), "python"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        paths = task.scope.get("paths", [])
        return ("python", "-m", "pytest", *paths, "-q")


class PytestIntegrationAdapter(CommandAdapter):
    worker_id, implementation, capability = "python-pytest-integration", "pytest", "functional.integration"
    languages, executable = ("python",), "python"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        paths = task.scope.get("paths", [])
        return ("python", "-m", "pytest", *paths, "-q", "-m", "integration")
