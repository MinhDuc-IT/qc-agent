import os
import time
from pathlib import Path
from pathlib import PurePosixPath

import httpx

from ...domain.models import ExternalAgentManifest, ProjectDescriptor, WorkerResult, WorkerTask


class HttpExternalAgentTransport:
    def __init__(self, poll_interval_seconds: float = 1.0):
        self.poll_interval_seconds = poll_interval_seconds

    def execute(self, manifest: ExternalAgentManifest, task: WorkerTask,
                project: ProjectDescriptor, workspace: Path) -> WorkerResult:
        source_ref = str(workspace)
        if manifest.source_root:
            source_ref = str(PurePosixPath(manifest.source_root) / workspace.name)
        payload = {
            "schema_version": "1.0",
            "task": task.model_dump(mode="json"),
            "project": project.model_dump(mode="json"),
            "source": {"type": "local_path", "ref": source_ref},
        }
        deadline = time.monotonic() + min(task.timeout_seconds, manifest.timeout_seconds)
        headers: dict[str, str] = {}
        if manifest.auth_secret_ref:
            prefix = "secret://"
            if not manifest.auth_secret_ref.startswith(prefix):
                raise ValueError("external agent auth_secret_ref must use secret://ENV_NAME")
            env_name = manifest.auth_secret_ref[len(prefix):]
            token = os.getenv(env_name)
            if not token:
                raise ValueError(f"external agent auth secret is not configured: {env_name}")
            headers["Authorization"] = f"Bearer {token}"
        with httpx.Client(base_url=manifest.endpoint.rstrip("/"), timeout=30,
                          headers=headers) as client:
            response = client.post("/v1/tasks", json=payload)
            response.raise_for_status()
            state = response.json()
            external_run_id = state["run_id"]
            while time.monotonic() < deadline:
                response = client.get(f"/v1/tasks/{external_run_id}")
                response.raise_for_status()
                state = response.json()
                status = state.get("status")
                if status == "completed":
                    result = WorkerResult.model_validate(state["result"])
                    result.task_id = task.task_id
                    result.run_id = task.run_id
                    result.capability = task.capability
                    result.worker_id = manifest.agent_id
                    result.implementation = f"external:{manifest.name}@{manifest.version}"
                    result.worker_kind = "external_agent"
                    return result
                if status in {"failed", "cancelled", "timed_out"}:
                    return WorkerResult(
                        task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                        worker_id=manifest.agent_id,
                        implementation=f"external:{manifest.name}@{manifest.version}",
                        worker_kind="external_agent", execution_status=status,
                        verdict="unknown", output=state.get("error", "External agent failed"),
                    )
                time.sleep(self.poll_interval_seconds)
            try:
                client.delete(f"/v1/tasks/{external_run_id}")
            finally:
                return WorkerResult(
                    task_id=task.task_id, run_id=task.run_id, capability=task.capability,
                    worker_id=manifest.agent_id,
                    implementation=f"external:{manifest.name}@{manifest.version}",
                    worker_kind="external_agent", execution_status="timed_out",
                    verdict="unknown", output="External agent exceeded its deadline",
                )
