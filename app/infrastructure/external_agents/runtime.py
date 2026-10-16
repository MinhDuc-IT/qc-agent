from pathlib import Path

import httpx

from ...domain.models import ProjectDescriptor, WorkerResult, WorkerTask
from .contracts import ExternalAgentTransport
from .http import HttpExternalAgentTransport
from .registry import ExternalAgentRegistry


class ExternalAgentRuntime:
    def __init__(self, registry: ExternalAgentRegistry,
                 http_transport: ExternalAgentTransport | None = None):
        self.registry = registry
        self.http_transport = http_transport or HttpExternalAgentTransport()

    def execute(self, task: WorkerTask, project: ProjectDescriptor,
                workspace: Path) -> WorkerResult | None:
        manifest = self.registry.resolve(task)
        if manifest is None:
            disabled=next((m for m in getattr(self.registry,'manifests',[]) if not m.enabled and task.capability in m.capabilities and task.target.type in m.supported_targets and (not task.worker_id or task.worker_id==m.agent_id)),None)
            if disabled:
                return WorkerResult(task_id=task.task_id,run_id=task.run_id,capability=task.capability,
                    worker_id=disabled.agent_id,worker_kind='external_agent',execution_status='failed',verdict='unknown',
                    summary={'configuration_blocked':True,'reason':disabled.blocked_reason or 'External worker disabled'},
                    output=disabled.blocked_reason or 'External worker disabled')
            return None
        try:
            return self.http_transport.execute(manifest, task, project, workspace)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            return WorkerResult(
                task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                worker_id=manifest.agent_id,
                implementation=f"external:{manifest.name}@{manifest.version}",
                worker_kind="external_agent", execution_status="failed",
                verdict="unknown", output=f"External agent transport failed: {exc}",
            )
