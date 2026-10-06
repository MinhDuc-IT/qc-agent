import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from .contracts import Invocation


@dataclass(frozen=True)
class ExecutionOutput:
    exit_code: int
    output: str


class ExecutionBackend(Protocol):
    def run(self, invocation: Invocation, timeout_seconds: int,
            environment: Mapping[str, str]) -> ExecutionOutput: ...


class LocalExecutionBackend:
    """Phase-1 backend. It must only be used for trusted pilot repositories."""

    def run(self, invocation: Invocation, timeout_seconds: int,
            environment: Mapping[str, str]) -> ExecutionOutput:
        process = subprocess.run(
            invocation.argv, cwd=invocation.cwd, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, **invocation.environment, **environment},
            shell=False, timeout=timeout_seconds, check=False,
        )
        return ExecutionOutput(process.returncode, process.stdout.decode(errors="replace"))


class DockerExecutionBackend:
    """Prepared sandbox backend; inactive until Docker and pinned images are configured."""

    def __init__(self, images: Mapping[str, str], cpu_limit: str = "1.0",
                 memory_limit: str = "1g", pids_limit: int = 256):
        self.images = dict(images)
        self.cpu_limit = cpu_limit
        self.memory_limit = memory_limit
        self.pids_limit = pids_limit

    def run(self, invocation: Invocation, timeout_seconds: int,
            environment: Mapping[str, str]) -> ExecutionOutput:
        executable = Path(invocation.argv[0]).name
        image = self.images.get(executable)
        if not image:
            raise ValueError(f"No pinned Docker image configured for executable: {executable}")
        workspace = invocation.cwd.resolve()
        mount = f"type=bind,source={workspace},target=/workspace"
        argv = [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--cpus", self.cpu_limit, "--memory", self.memory_limit,
            "--pids-limit", str(self.pids_limit),
            "--mount", mount, "--workdir", "/workspace",
        ]
        for name, value in {**invocation.environment, **environment}.items():
            argv.extend(["--env", f"{name}={value}"])
        argv.extend([image, *invocation.argv])
        process = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False,
            timeout=timeout_seconds, check=False,
        )
        return ExecutionOutput(process.returncode, process.stdout.decode(errors="replace"))
