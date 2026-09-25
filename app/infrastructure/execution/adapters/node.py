from ....domain.models import ProjectDescriptor, WorkerTask
from .base import CommandAdapter


class NpmTestAdapter(CommandAdapter):
    worker_id, implementation, capability = "node-npm-test", "npm", "functional.unit"
    languages, executable = ("javascript",), "npm"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        return ("npm", "test", "--", "--runInBand")


class EslintAdapter(CommandAdapter):
    worker_id, implementation, capability = "eslint-lint", "eslint", "static.lint"
    languages, executable = ("javascript",), "npx"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        return ("npx", "--no-install", "eslint", ".")
