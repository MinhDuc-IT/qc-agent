from pathlib import Path

from ...domain.models import ProjectDescriptor, WorkerResult, WorkerTask
from ..external_agents.runtime import ExternalAgentRuntime
from .runtime import RegisteredWorkerRuntime


class HybridWorkerRuntime:
    """Routes to specialized external agents first, with explicit tool fallback."""

    def __init__(self, external_agents: ExternalAgentRuntime, tools: RegisteredWorkerRuntime):
        self.external_agents = external_agents
        self.tools = tools

    def execute(self, task: WorkerTask, project: ProjectDescriptor, workspace: Path) -> WorkerResult:
        if task.execution_preference != "tool":
            external_result = self.external_agents.execute(task, project, workspace)
            if external_result is not None:
                if (external_result.execution_status == "completed"
                        or task.execution_preference == "external_agent"):
                    return external_result
                manifest = self.external_agents.registry.resolve(task)
                if manifest and not manifest.fallback_to_tools:
                    return external_result
        if task.execution_preference == "external_agent":
            return WorkerResult(
                task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                worker_kind="external_agent", execution_status="failed", verdict="unknown",
                output="No compatible external agent is registered",
            )
        return self.tools.execute(task, project, workspace)
