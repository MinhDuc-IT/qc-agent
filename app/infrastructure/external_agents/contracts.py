from pathlib import Path
from typing import Protocol

from ...domain.models import ExternalAgentManifest, ProjectDescriptor, WorkerResult, WorkerTask


class ExternalAgentTransport(Protocol):
    def execute(self, manifest: ExternalAgentManifest, task: WorkerTask,
                project: ProjectDescriptor, workspace: Path) -> WorkerResult: ...
