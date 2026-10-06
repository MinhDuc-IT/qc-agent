from ....domain.models import ProjectDescriptor, WorkerTask
from .base import CommandAdapter


class MavenTestAdapter(CommandAdapter):
    worker_id, implementation, capability = "jvm-maven-test", "maven", "unit.run"
    languages, executable = ("java",), "mvn"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        return ("mvn", "--batch-mode", "test")


class GradleTestAdapter(CommandAdapter):
    worker_id, implementation, capability = "jvm-gradle-test", "gradle", "unit.run"
    languages, executable = ("java", "kotlin"), "gradle"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        return ("gradle", "test")
