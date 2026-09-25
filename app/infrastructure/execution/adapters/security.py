from ....domain.models import ProjectDescriptor, WorkerTask
from .base import CommandAdapter


LANGUAGES = ("python", "javascript", "java", "kotlin", "go", "rust", "dotnet", "generic")


class SemgrepAdapter(CommandAdapter):
    worker_id, implementation, capability = "semgrep-sast", "semgrep", "security.sast"
    languages, executable = LANGUAGES, "semgrep"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        return ("semgrep", "scan", "--config", "auto", "--json")


class TrivyDependencyAdapter(CommandAdapter):
    worker_id, implementation, capability = "trivy-dependency", "trivy", "security.dependency"
    languages, executable = LANGUAGES, "trivy"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        return ("trivy", "fs", ".")
