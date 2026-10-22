from pathlib import Path

from ...domain.models import ProjectDescriptor, WorkerResult, WorkerTask
from ..external_agents.runtime import ExternalAgentRuntime
from .runtime import RegisteredWorkerRuntime


class HybridWorkerRuntime:
    """Routes to specialized external agents first, with explicit tool fallback."""

    def __init__(self, external_agents: ExternalAgentRuntime, tools: RegisteredWorkerRuntime, managed_baselines=None):
        self.external_agents = external_agents
        self.tools = tools
        self.managed_baselines = managed_baselines

    def execute(self, task: WorkerTask, project: ProjectDescriptor, workspace: Path) -> WorkerResult:
        if self.managed_baselines is not None and task.parameters.get('reviewed_cases'):
            return self.managed_baselines.execute_reviewed(task,project,workspace,self.external_agents)
        if self.managed_baselines is not None and task.parameters.get('generated_artifact'):
            return self.managed_baselines.execute_generated(task,project,workspace,self.external_agents)
        if self.managed_baselines is not None and task.capability in {"baseline.http", "baseline.repository"} and task.parameters.get("managed_project_id"):
            return self.managed_baselines.execute(task, project, workspace)
        if task.execution_preference != "tool":
            external_result = self.external_agents.execute(task, project, workspace)
            if external_result is not None:
                if external_result.summary.get('configuration_blocked'):return external_result
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
