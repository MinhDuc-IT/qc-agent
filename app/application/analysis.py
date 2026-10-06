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
    def __init__(self, org_policy_file: Path | None = None):
        self.org_policy_file = org_policy_file

    def analyze(self, workspace: Path, base_sha: str, head_sha: str) -> SourceAnalysis:
        repo_config = self._config_at_base(workspace, base_sha)
        return SourceAnalysis(
            changed_files=self._changed_files(workspace, base_sha, head_sha),
            diff_patch=self._diff_patch(workspace, base_sha, head_sha),
            projects=self._projects(workspace),
            config=self._merge_policy(self._org_policy(), repo_config),
        )

    def _changed_files(self, workspace: Path, base_sha: str, head_sha: str) -> list[str]:
        process = subprocess.run(
            ["git", "-c", f"safe.directory={workspace.as_posix()}", "diff", "--name-only", base_sha, head_sha],
            cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, text=True,
        )
        if process.returncode == 0:
            return [line.strip().replace("\\", "/") for line in process.stdout.splitlines() if line.strip()]
        return []

    def _diff_patch(self, workspace: Path, base_sha: str, head_sha: str) -> str:
        process = subprocess.run(
            ["git", "-c", f"safe.directory={workspace.as_posix()}", "diff", "--unified=3", base_sha, head_sha],
            cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        if process.returncode:
            return ""
        # Bound model input and avoid decoding failures; full diff remains available in Git.
        return process.stdout[:100_000].decode(errors="replace")

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

    def _config_at_base(self, workspace: Path, base_sha: str) -> dict[str, Any]:
        """Read trusted gate configuration from base_sha, never from PR head."""
        process = subprocess.run(
            ["git", "-c", f"safe.directory={workspace.as_posix()}", "show",
             f"{base_sha}:.agent-qc.yaml"],
            cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, text=True,
        )
        if process.returncode:
            return {}
        data = yaml.safe_load(process.stdout) or {}
        return data if isinstance(data, dict) else {}

    def _org_policy(self) -> dict[str, Any]:
        path = self.org_policy_file
        if path is None or not path.is_file():
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("Org policy must be a YAML mapping")
        return data

    @staticmethod
    def _merge_policy(org: dict[str, Any], repo: dict[str, Any]) -> dict[str, Any]:
        merged = dict(repo)
        org_required = set(org.get("required_capabilities", []))
        repo_required = set(repo.get("required_capabilities", []))
        merged["required_capabilities"] = sorted(org_required | repo_required)
        # These blocks affect execution/gating and therefore cannot be weakened by a PR.
        for protected in ("verdict", "target", "thresholds", "kill_switches", "rules"):
            if protected in org:
                merged[protected] = org[protected]
        return merged
