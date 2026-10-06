import os
import subprocess
import time
from pathlib import Path
from threading import Lock
from typing import Mapping

import httpx

from ...domain.models import ProvisionedTarget, QCRun, SourceAnalysis


class LocalTargetProvisioner:
    """Runs a trusted base-sha target declaration directly on the Windows pilot host."""

    def __init__(self, secrets: Mapping[str, str] | None = None):
        self.secrets = secrets or os.environ
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = Lock()

    def provision(self, run: QCRun, analysis: SourceAnalysis,
                  workspace: Path) -> ProvisionedTarget:
        config = analysis.config.get("target")
        if not isinstance(config, dict):
            raise TargetUnavailable("Trusted base config does not define target")
        command = config.get("command")
        healthcheck = config.get("healthcheck")
        base_url = config.get("base_url")
        if not (isinstance(command, list) and command
                and all(isinstance(item, str) and item for item in command)):
            raise TargetUnavailable("target.command must be a non-empty argv list")
        if not isinstance(healthcheck, str) or not isinstance(base_url, str):
            raise TargetUnavailable("target.healthcheck and target.base_url are required")
        environment = self._resolve_environment(config.get("environment", {}))
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            command, cwd=workspace, env={**os.environ, **environment}, shell=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, creationflags=flags,
        )
        with self._lock:
            self._processes[run.run_id] = process
        timeout = min(max(int(config.get("startup_timeout_seconds", 60)), 1), 600)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise TargetUnavailable(f"Target exited with code {process.returncode}")
                try:
                    response = httpx.get(healthcheck, timeout=2)
                    if response.is_success:
                        return ProvisionedTarget(
                            type=str(config.get("type", "http_service")), ref=base_url,
                            healthcheck_url=healthcheck, process_id=process.pid,
                        )
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
        except Exception:
            self.cleanup(run.run_id)
            raise
        self.cleanup(run.run_id)
        raise TargetUnavailable("Target healthcheck timed out")

    def cleanup(self, run_id: str) -> None:
        with self._lock:
            process = self._processes.pop(run_id, None)
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _resolve_environment(self, config: object) -> dict[str, str]:
        if not isinstance(config, dict):
            raise TargetUnavailable("target.environment must be a mapping")
        resolved: dict[str, str] = {}
        for name, reference in config.items():
            if not isinstance(name, str) or not isinstance(reference, str):
                raise TargetUnavailable("Target environment entries must be strings")
            if not reference.startswith("secret://"):
                raise TargetUnavailable(f"Target environment {name} must use secret://")
            secret_name = reference.removeprefix("secret://")
            value = self.secrets.get(secret_name)
            if value is None:
                raise TargetUnavailable(f"Secret reference is unavailable: {secret_name}")
            resolved[name] = value
        return resolved


class TargetUnavailable(RuntimeError):
    pass
