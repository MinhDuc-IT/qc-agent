import os
import signal
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
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            invocation.argv, cwd=invocation.cwd, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, **invocation.environment, **environment},
            shell=False, creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        try:
            output, _ = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            self._terminate_tree(process)
            try:
                remainder, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                remainder, _ = process.communicate()
            partial = exc.output or b""
            if remainder and not partial.endswith(remainder):
                partial += remainder
            raise subprocess.TimeoutExpired(
                invocation.argv, timeout_seconds, output=partial
            ) from exc
        return ExecutionOutput(process.returncode, output.decode(errors="replace"))

    @staticmethod
    def _terminate_tree(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            # Semgrep launches semgrep-core. Killing only the Python/CLI wrapper
            # leaves the core process holding stdout open, so communicate()
            # never returns. taskkill /T terminates the complete descendant tree.
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, check=False,
            )
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


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
