from ...domain.capabilities import CATALOG
from ...domain.models import (ProjectDescriptor, WorkerManifest, WorkerManifestExecution,
                              WorkerRequirements, WorkerTask)
from .adapters import (
    CargoTestAdapter, DotnetTestAdapter, GoTestAdapter,
    GradleTestAdapter, MavenTestAdapter, NpmTestAdapter,
    PytestIntegrationAdapter, PytestUnitAdapter,
    GitleaksAdapter, SemgrepAdapter, TrivyDependencyAdapter,
)
from .contracts import WorkerAdapter


DEFAULT_ADAPTERS: tuple[WorkerAdapter, ...] = (
    PytestUnitAdapter(), PytestIntegrationAdapter(), NpmTestAdapter(),
    MavenTestAdapter(), GradleTestAdapter(), GoTestAdapter(), CargoTestAdapter(),
    DotnetTestAdapter(), SemgrepAdapter(),
    TrivyDependencyAdapter(), GitleaksAdapter(),
)


class WorkerRegistry:
    def __init__(self, adapters: tuple[WorkerAdapter, ...] = DEFAULT_ADAPTERS):
        self.adapters = adapters

    def resolve(self, task: WorkerTask, project: ProjectDescriptor) -> WorkerAdapter | None:
        candidates = [adapter for adapter in self.adapters if adapter.supports(task, project)]
        if project.build_system:
            exact = [adapter for adapter in candidates if adapter.implementation == project.build_system]
            if exact:
                return exact[0]
        return candidates[0] if candidates else None

    def manifests(self) -> list[WorkerManifest]:
        return [
            WorkerManifest(
                worker_id=adapter.worker_id,
                version="0.1.0",
                capabilities=[adapter.capability],
                supports_targets=list(CATALOG[adapter.capability].target_types),
                requires=WorkerRequirements(
                    target=CATALOG[adapter.capability].needs_running_target,
                    workspace=True,
                ),
                execution=WorkerManifestExecution(
                    adapter="cli", entrypoint=[adapter.executable]
                ),
            )
            for adapter in self.adapters
        ]
