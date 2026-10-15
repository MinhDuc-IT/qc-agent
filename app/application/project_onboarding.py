import re
import json
from pathlib import Path

from ..domain.models import Repository, TaskTarget, WorkerResult, WorkerTask


class ProjectOnboarding:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def descriptor(payload):
        full_name = payload["full_name"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
            raise ValueError("Invalid GitHub repository name")
        owner, name = full_name.split("/")
        return Repository(owner=owner, name=name, full_name=full_name,
                          clone_url=f"https://github.com/{full_name}.git",
                          default_branch=payload.get("default_branch") or "main")

    def register_repository(self, installation_id, payload, reason="installation"):
        repository = self.descriptor(payload)
        project = self.store.register(int(installation_id), int(payload["id"]), repository)
        return self.store.get_project(project.project_id)

    def installation_event(self, event, payload):
        installation_id = int(payload["installation"]["id"])
        action = payload.get("action")
        if event == "installation" and action in {"deleted", "suspend"}:
            self.store.disable(installation_id)
            return []
        if event == "installation_repositories" and action == "removed":
            self.store.disable(installation_id, {int(repo["id"]) for repo in payload.get("repositories_removed", [])})
            return []
        repositories = payload.get("repositories_added", []) if event == "installation_repositories" else payload.get("repositories", [])
        if action not in {"created", "added", "unsuspend"}:
            return []
        return [self.register_repository(installation_id, repo).project_id for repo in repositories]

    def push(self, payload):
        return None

    def ensure_pull_request(self, payload):
        repository = payload["repository"]
        if "id" not in repository:
            return None  # Compatibility for old local fixtures without a real GitHub repository ID.
        iid = int(payload["installation"]["id"])
        project = self.store.find_project(iid, repository_id=int(repository["id"]))
        if project and project.enabled and project.repository.full_name != repository["full_name"]:
            return self.store.register(iid, project.repository_id, self.descriptor(repository))
        return project if project else self.register_repository(iid, repository, "first_pull_request")


class ManagedBaselineBinding:
    """Pins the server registry baseline to a QC run, independent of PR-controlled files."""

    def __init__(self, store, target_profiles):
        self.store, self.target_profiles = store, target_profiles

    def prepare(self, run):
        ctx = run.trigger_context
        project = self.store.find_project(ctx.installation.id, full_name=ctx.repository.full_name)
        if not project or not project.enabled:
            return
        needs_service=True
        baseline=self.store.get_baseline(project.project_id,project.active_baseline) if project.active_baseline else None
        if baseline:
            try:
                cases=json.loads((Path(baseline.baseline_dir)/'cases/cases.json').read_text(encoding='utf-8'))
                needs_service=any(case.get('runner','http')!='unit' for case in cases)
            except (OSError,ValueError):pass
        if needs_service and not run.analysis.config.get("target") and project.target_profile:
            target = self.target_profiles.materialize(project.target_profile)
            if target is not None:
                run.analysis.config["target"] = target
        run.managed_baseline = {"project_id": project.project_id, "version": project.active_baseline,
                                "repository_id": project.repository_id,
                                "status": project.state, "message": project.message, "allow_mutations": project.allow_mutations}

    def attach(self, run):
        binding = run.managed_baseline
        if binding is None:
            return
        if not binding["version"]:
            run.results.append(WorkerResult(
                task_id="baseline-readiness-" + run.run_id, run_id=run.run_id, capability="baseline.http",
                worker_id="managed-baseline", worker_kind="managed_agent", execution_status="completed",
                verdict="unknown", output="Baseline chưa được duyệt: " + binding["message"],
                summary={"onboarding_status": binding["status"], "project_id": binding["project_id"]}))
            return
        projects = run.analysis.projects
        selected = projects[0]
        needs_service=True
        baseline=self.store.get_baseline(binding['project_id'],binding['version'])
        if baseline:
            try:
                cases=json.loads((Path(baseline.baseline_dir)/'cases/cases.json').read_text(encoding='utf-8'))
                needs_service=any(case.get('runner','http')!='unit' for case in cases)
            except (OSError,ValueError):pass
        # One managed HTTP suite per repository, not one duplicated task per language manifest.
        run.plan.tasks = [task for task in run.plan.tasks if task.capability not in {"baseline.http", "baseline.repository"}]
        run.plan.tasks.append(WorkerTask(
            run_id=run.run_id, capability="baseline.http" if needs_service else 'baseline.repository', objective="Kiểm tra PR bằng baseline đã được review.",
            target=TaskTarget(type="http_service" if needs_service else 'repository', project_id=selected.id), timeout_seconds=600,
            parameters={"managed_project_id": binding["project_id"], "baseline_version": binding["version"],
                        "installation_id": run.trigger_context.installation.id,
                        "allow_mutations": binding["allow_mutations"],
                        "repository": run.trigger_context.repository.full_name}))
