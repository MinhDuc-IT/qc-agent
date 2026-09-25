from ...domain.models import ProjectDescriptor, WorkerTask
from .adapters import (
    CargoTestAdapter, DotnetTestAdapter, EslintAdapter, GoTestAdapter,
    GradleTestAdapter, MavenTestAdapter, NpmTestAdapter,
    PytestIntegrationAdapter, PytestUnitAdapter, RuffAdapter,
    SemgrepAdapter, TrivyDependencyAdapter,
)
from .contracts import WorkerAdapter


DEFAULT_ADAPTERS: tuple[WorkerAdapter, ...] = (
    PytestUnitAdapter(), PytestIntegrationAdapter(), NpmTestAdapter(),
    MavenTestAdapter(), GradleTestAdapter(), GoTestAdapter(), CargoTestAdapter(),
    DotnetTestAdapter(), RuffAdapter(), EslintAdapter(), SemgrepAdapter(),
    TrivyDependencyAdapter(),
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

    def manifests(self) -> list[dict[str, object]]:
        return [
            {
                "worker_id": adapter.worker_id,
                "implementation": adapter.implementation,
                "capability": adapter.capability,
                "languages": list(adapter.languages),
            }
            for adapter in self.adapters
        ]
