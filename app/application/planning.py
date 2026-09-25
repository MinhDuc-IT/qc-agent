from typing import Any

from ..domain.capabilities import CATALOG
from ..domain.models import ExecutionPlan, SourceAnalysis, TaskTarget, WorkerTask


LANGUAGE_FILE_SUFFIXES = {
    "python": {".py"}, "javascript": {".js", ".jsx", ".ts", ".tsx"},
    "java": {".java"}, "kotlin": {".kt", ".kts"}, "go": {".go"},
    "rust": {".rs"}, "dotnet": {".cs", ".fs", ".vb"},
}


class CapabilityPlanner:
    """Deterministic baseline planner; an LLM planner can emit the same contract later."""

    def create_plan(self, run_id: str, analysis: SourceAnalysis) -> ExecutionPlan:
        configured = self._configured_capabilities(analysis.config)
        tasks: list[WorkerTask] = []
        for project in analysis.projects:
            capabilities = configured or {"functional.unit"}
            for capability in sorted(capabilities):
                descriptor = CATALOG.get(capability)
                if not descriptor:
                    continue
                if configured or self._project_changed(project.root, project.language, analysis.changed_files):
                    tasks.append(WorkerTask(
                        run_id=run_id,
                        capability=capability,
                        objective=f"Evaluate {capability} for project {project.id}",
                        target=TaskTarget(type=descriptor.target_types[0], project_id=project.id),
                        scope={"project_root": project.root},
                        timeout_seconds=descriptor.default_timeout,
                    ))
        return ExecutionPlan(tasks=self._deduplicate(tasks))

    def _configured_capabilities(self, config: dict[str, Any]) -> set[str]:
        selected: set[str] = set()
        quality = config.get("quality")
        if isinstance(quality, dict):
            self._walk_quality("", quality, selected)
        # Compatibility with the initial demo configuration.
        workers = config.get("workers", {})
        if isinstance(workers, dict) and any(
            isinstance(value, dict) and value.get("enabled") for value in workers.values()
        ):
            selected.add("functional.unit")
        return selected

    def _walk_quality(self, prefix: str, node: dict[str, Any], selected: set[str]) -> None:
        for key, value in node.items():
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict) and "enabled" in value:
                if value.get("enabled") in {True, "auto", "always"}:
                    capability = self._canonical(name)
                    if capability in CATALOG:
                        selected.add(capability)
            elif isinstance(value, dict):
                self._walk_quality(name, value, selected)

    @staticmethod
    def _canonical(name: str) -> str:
        aliases = {
            "functional.api_contract": "functional.contract",
            "experience.visual": "experience.visual_regression",
            "infrastructure.configuration": "infrastructure.iac",
        }
        return aliases.get(name, name)

    @staticmethod
    def _project_changed(root: str, language: str, changed_files: list[str]) -> bool:
        if not changed_files:
            return True
        prefix = "" if root == "." else root.rstrip("/") + "/"
        suffixes = LANGUAGE_FILE_SUFFIXES.get(language, set())
        return any(path.startswith(prefix) and (not suffixes or any(path.endswith(s) for s in suffixes))
                   for path in changed_files)

    @staticmethod
    def _deduplicate(tasks: list[WorkerTask]) -> list[WorkerTask]:
        result, seen = [], set()
        for task in tasks:
            key = (task.target.project_id, task.capability)
            if key not in seen:
                seen.add(key)
                result.append(task)
        return result


class PolicyValidator:
    def validate(self, plan: ExecutionPlan, analysis: SourceAnalysis) -> ExecutionPlan:
        project_ids = {project.id for project in analysis.projects}
        validated: list[WorkerTask] = []
        for task in plan.tasks:
            if task.capability not in CATALOG:
                continue
            if task.target.project_id not in project_ids:
                continue
            task.timeout_seconds = min(max(task.timeout_seconds, 1), 3600)
            validated.append(task)
        plan.tasks = validated
        return plan
