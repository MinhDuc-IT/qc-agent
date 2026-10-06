from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ...domain.models import ProjectDescriptor, WorkerResult, WorkerTask


@dataclass(frozen=True)
class Invocation:
    argv: tuple[str, ...]
    cwd: Path
    environment: dict[str, str] = field(default_factory=dict)


class WorkerAdapter(Protocol):
    worker_id: str
    implementation: str
    capability: str
    languages: tuple[str, ...]
    executable: str

    def supports(self, task: WorkerTask, project: ProjectDescriptor) -> bool: ...
    def validate(self, task: WorkerTask, project: ProjectDescriptor) -> None: ...
    def build_invocation(self, task: WorkerTask, project: ProjectDescriptor,
                         workspace: Path) -> Invocation: ...
    def parse_result(self, task: WorkerTask, exit_code: int, output: str,
                     cwd: Path | None = None) -> WorkerResult: ...
