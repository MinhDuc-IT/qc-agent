from ....domain.models import ProjectDescriptor, WorkerTask
from .base import CommandAdapter


class PytestUnitAdapter(CommandAdapter):
    worker_id, implementation, capability = "python-pytest", "pytest", "unit.run"
    languages, executable = ("python",), "python"

    def parse_result(self,task,exit_code,output,cwd=None):
        result=super().parse_result(task,exit_code,output,cwd)
        if exit_code not in (0,1) or result.summary.get('errors'):
            result.execution_status='failed';result.verdict='unknown'
        return result

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        paths = task.scope.get("paths", [])
        return ("python", "-m", "pytest", *paths, "-q")


class PytestIntegrationAdapter(CommandAdapter):
    worker_id, implementation, capability = "python-pytest-integration", "pytest", "integration.service"
    languages, executable = ("python",), "python"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        paths = task.scope.get("paths", [])
        return ("python", "-m", "pytest", *paths, "-q", "-m", "integration")
