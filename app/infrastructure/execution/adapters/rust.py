from ....domain.models import ProjectDescriptor, WorkerTask
from .base import CommandAdapter


class CargoTestAdapter(CommandAdapter):
    worker_id, implementation, capability = "rust-cargo-test", "cargo", "functional.unit"
    languages, executable = ("rust",), "cargo"

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        return ("cargo", "test")
