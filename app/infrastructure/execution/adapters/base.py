import re
from pathlib import Path

from ....domain.models import Finding, ProjectDescriptor, WorkerResult, WorkerTask
from ..contracts import Invocation


class CommandAdapter:
    worker_id = ""
    implementation = ""
    capability = ""
    languages: tuple[str, ...] = ()
    executable = ""

    def supports(self, task: WorkerTask, project: ProjectDescriptor) -> bool:
        return task.capability == self.capability and project.language in self.languages

    def validate(self, task: WorkerTask, project: ProjectDescriptor) -> None:
        root = task.scope.get("project_root", project.root)
        if not isinstance(root, str) or root.startswith(("/", "\\")) or ".." in Path(root).parts:
            raise ValueError("project_root must be a repository-relative path")

    def build_argv(self, task: WorkerTask, project: ProjectDescriptor) -> tuple[str, ...]:
        raise NotImplementedError

    def build_invocation(self, task: WorkerTask, project: ProjectDescriptor,
                         workspace: Path) -> Invocation:
        root = task.scope.get("project_root", project.root)
        cwd = workspace if root == "." else workspace / root
        return Invocation(argv=self.build_argv(task, project), cwd=cwd)

    def parse_result(self, task: WorkerTask, exit_code: int, output: str,
                     cwd: Path | None = None) -> WorkerResult:
        return WorkerResult(
            task_id=task.task_id, run_id=task.run_id, capability=task.capability,
            worker_id=self.worker_id, implementation=self.implementation,
            worker_kind="tool",
            execution_status="completed", verdict="pass" if exit_code == 0 else "fail",
            exit_code=exit_code, output=output, summary=self._parse_counts(output),
            findings=self._parse_findings(output),
        )

    @staticmethod
    def _parse_counts(output: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for key in ("passed", "failed", "skipped", "error", "errors"):
            matches = re.findall(rf"(\d+)\s+{key}\b", output, re.IGNORECASE)
            if matches:
                counts["errors" if key in {"error", "errors"} else key] = int(matches[-1])
        return counts

    @staticmethod
    def _parse_findings(output: str) -> list[Finding]:
        findings = []
        for path, line in re.findall(r"(?m)^([^\s:]+\.[A-Za-z]+):(\d+):\s*(?:AssertionError|error)", output):
            findings.append(Finding(
                id=f"finding-{len(findings)+1}", severity="high", category="test-failure",
                title="Automated quality check failure", message=f"Failure reported at {path}:{line}",
                path=path, start_line=int(line),
            ))
        return findings[:50]
