from ....domain.models import ProjectDescriptor, WorkerTask
from .base import CommandAdapter


class DotnetTestAdapter(CommandAdapter):
    worker_id, implementation, capability = "dotnet-test", "dotnet", "functional.unit"
    languages, executable = ("dotnet",), "dotnet"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        return ("dotnet", "test")
