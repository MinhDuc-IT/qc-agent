from fnmatch import fnmatch
from typing import Any

from ..domain.capabilities import CATALOG
from ..domain.models import AgentPlanProposal, ExecutionPlan, SourceAnalysis, TaskTarget, WorkerTask


LANGUAGE_FILE_SUFFIXES = {
    "python": {".py"}, "javascript": {".js", ".jsx", ".ts", ".tsx"},
    "java": {".java"}, "kotlin": {".kt", ".kts"}, "go": {".go"},
    "rust": {".rs"}, "dotnet": {".cs", ".fs", ".vb"},
}


class CapabilityPlanner:
    """Deterministic baseline planner; an LLM planner can emit the same contract later."""

    def create_plan(self, run_id: str, analysis: SourceAnalysis,
                    requested_capabilities: list[str] | None = None,
                    trigger_type: str = "pull_request") -> ExecutionPlan:
        modes = self.capability_modes(analysis.config)
        required = self._required_capabilities(analysis.changed_files)
        policy_required = {item for item in analysis.config.get("required_capabilities", [])
                           if item in CATALOG}
        configured = {
            capability for capability, mode in modes.items()
            if mode == "always" or (mode == "affected" and capability in required)
            or (mode == "scheduled" and trigger_type == "schedule")
        }
        for capability, mode in modes.items():
            if mode == "disabled" or (mode == "scheduled" and trigger_type != "schedule"):
                required.discard(capability)
        required.update(policy_required)
        disabled = set(analysis.config.get("kill_switches", {}).get("capabilities", []))
        required.difference_update(disabled)
        configured.difference_update(disabled)
        requested = set(requested_capabilities or [])
        tasks: list[WorkerTask] = []
        for project in analysis.projects:
            capabilities = configured | required | requested
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
                        parameters=self._capability_parameters(analysis.config, capability),
                        timeout_seconds=descriptor.default_timeout,
                    ))
        return ExecutionPlan(run_id=run_id, tasks=self._deduplicate(tasks))

    @staticmethod
    def _required_capabilities(changed_files: list[str]) -> set[str]:
        """Platform baseline. LLM proposals are merged after this and cannot remove it."""
        selected = {"security.secrets"}
        rules = (
            (("**/*.py", "**/*.js", "**/*.ts", "**/*.tsx", "**/*.java", "**/*.go",
              "**/*.rs", "**/*.cs"), {"unit.run", "security.sast"}),
            (("**/integration/**", "**/integration_*", "**/*integration*"),
             {"integration.service"}),
            (("**/performance/**", "**/load/**", "**/*k6*"), {"performance.smoke"}),
            (("requirements*.txt", "**/requirements*.txt", "package-lock.json",
              "**/package-lock.json", "pom.xml", "**/pom.xml"), {"security.sca"}),
        )
        if not changed_files:
            return selected | {"unit.run", "security.sast"}
        for patterns, capabilities in rules:
            if any(any(fnmatch(path, pattern) for pattern in patterns) for path in changed_files):
                selected.update(capabilities)
        return selected

    def merge_agent_proposal(self, run_id: str, baseline: ExecutionPlan,
                             proposal: AgentPlanProposal) -> ExecutionPlan:
        tasks = list(baseline.tasks)
        for item in proposal.tasks:
            descriptor = CATALOG.get(item.capability)
            if descriptor is None:
                continue
            tasks.append(WorkerTask(
                run_id=run_id, capability=item.capability, objective=item.reason,
                target=TaskTarget(type=descriptor.target_types[0], project_id=item.project_id),
                execution_preference=item.execution_preference,
                scope=item.scope.model_dump(exclude_defaults=True),
                timeout_seconds=descriptor.default_timeout,
            ))
        return ExecutionPlan(run_id=run_id, tasks=self._deduplicate(tasks))

    def capability_modes(self, config: dict[str, Any]) -> dict[str, str]:
        selected: dict[str, str] = {}
        quality = config.get("quality")
        if isinstance(quality, dict):
            self._walk_quality("", quality, selected)
        # Compatibility with the initial demo configuration.
        workers = config.get("workers", {})
        if isinstance(workers, dict) and any(
            isinstance(value, dict) and value.get("enabled") for value in workers.values()
        ):
            selected.setdefault("unit.run", "always")
        return selected

    def _walk_quality(self, prefix: str, node: dict[str, Any],
                      selected: dict[str, str]) -> None:
        for key, value in node.items():
            name = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict) and ("enabled" in value or "mode" in value):
                raw = value.get("mode", value.get("enabled"))
                mode = {
                    True: "always", False: "disabled", "auto": "affected",
                    "always": "always", "affected": "affected",
                    "scheduled": "scheduled", "disabled": "disabled",
                }.get(raw)
                capability = self._canonical(name)
                if capability in CATALOG and mode is not None:
                    selected[capability] = mode
            elif isinstance(value, dict):
                self._walk_quality(name, value, selected)

    @staticmethod
    def _canonical(name: str) -> str:
        aliases = {
            "functional.unit": "unit.run",
            "functional.integration": "integration.service",
            "security.dependency": "security.sca",
            "security.secret": "security.secrets",
        }
        return aliases.get(name, name)

    def _capability_parameters(self, config: dict[str, Any], capability: str) -> dict[str, Any]:
        found: dict[str, Any] = {}

        def visit(prefix: str, node: dict[str, Any]) -> None:
            nonlocal found
            for key, value in node.items():
                name = f"{prefix}.{key}" if prefix else key
                if not isinstance(value, dict):
                    continue
                if self._canonical(name) == capability:
                    parameters = value.get("parameters", {})
                    if isinstance(parameters, dict):
                        found = dict(parameters)
                visit(name, value)

        quality = config.get("quality", {})
        if isinstance(quality, dict):
            visit("", quality)
        return found

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
    def validate(self, plan: ExecutionPlan, analysis: SourceAnalysis,
                 trigger_type: str = "pull_request") -> ExecutionPlan:
        project_ids = {project.id for project in analysis.projects}
        modes = CapabilityPlanner().capability_modes(analysis.config)
        policy_required = set(analysis.config.get("required_capabilities", []))
        validated: list[WorkerTask] = []
        for task in plan.tasks:
            if task.capability not in CATALOG:
                continue
            if task.target.project_id not in project_ids:
                continue
            mode = modes.get(task.capability)
            if task.capability not in policy_required:
                if mode == "disabled":
                    continue
                if mode == "scheduled" and trigger_type != "schedule":
                    continue
            if task.capability == "security.dast" and trigger_type != "schedule":
                continue
            switches = analysis.config.get("kill_switches", {})
            if task.capability in set(switches.get("capabilities", [])):
                continue
            if task.worker_id and task.worker_id in set(switches.get("implementations", [])):
                continue
            task.timeout_seconds = min(max(task.timeout_seconds, 1), 3600)
            task.parameters["trigger_type"] = trigger_type
            validated.append(task)
        plan.tasks = validated
        return plan
