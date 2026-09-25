import subprocess
from pathlib import Path
from typing import Any

import yaml

from ..domain.models import ProjectDescriptor, SourceAnalysis


PROJECT_MARKERS = {
    "pyproject.toml": ("python", "python", ["pytest"]),
    "requirements.txt": ("python", "pip", ["pytest"]),
    "package.json": ("javascript", "npm", []),
    "pom.xml": ("java", "maven", ["junit"]),
    "build.gradle": ("java", "gradle", ["junit"]),
    "build.gradle.kts": ("kotlin", "gradle", ["junit"]),
    "go.mod": ("go", "go", ["go-test"]),
    "Cargo.toml": ("rust", "cargo", ["cargo-test"]),
}


class RepositoryAnalyzer:
    def analyze(self, workspace: Path, base_sha: str, head_sha: str) -> SourceAnalysis:
        return SourceAnalysis(
            changed_files=self._changed_files(workspace, base_sha, head_sha),
            projects=self._projects(workspace),
            config=self._config(workspace),
        )

    def _changed_files(self, workspace: Path, base_sha: str, head_sha: str) -> list[str]:
        process = subprocess.run(
            ["git", "-c", f"safe.directory={workspace.as_posix()}", "diff", "--name-only", base_sha, head_sha],
            cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True,
        )
        if process.returncode == 0:
            return [line.strip().replace("\\", "/") for line in process.stdout.splitlines() if line.strip()]
        return []

    def _projects(self, workspace: Path) -> list[ProjectDescriptor]:
        found: dict[tuple[str, str], ProjectDescriptor] = {}
        ignored = {".git", "node_modules", ".venv", "venv", "target", "dist", "build"}
        for path in workspace.rglob("*"):
            if not path.is_file() or any(part in ignored for part in path.relative_to(workspace).parts):
                continue
            marker = PROJECT_MARKERS.get(path.name)
            if not marker and path.suffix not in {".csproj", ".sln"}:
                continue
            if marker:
                language, build_system, frameworks = marker
            else:
                language, build_system, frameworks = "dotnet", "dotnet", ["dotnet-test"]
            root = path.parent.relative_to(workspace).as_posix() or "."
            key = (root, language)
            project = found.get(key)
            if project is None:
                project = ProjectDescriptor(
                    id=(root.replace("/", "-").replace(".", "root") + "-" + language),
                    root=root, language=language, build_system=build_system, frameworks=list(frameworks),
                )
                found[key] = project
            project.manifests.append(path.relative_to(workspace).as_posix())
        if not found and any(workspace.glob("test_*.py")):
            found[(".", "python")] = ProjectDescriptor(
                id="root-python", language="python", build_system="python", frameworks=["pytest"]
            )
        if not found:
            found[(".", "generic")] = ProjectDescriptor(id="root-generic", language="generic")
        return list(found.values())

    def _config(self, workspace: Path) -> dict[str, Any]:
        path = workspace / ".agent-qc.yaml"
        if not path.exists():
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
