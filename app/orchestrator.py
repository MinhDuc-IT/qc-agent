import asyncio
import shutil
import subprocess
from pathlib import Path

from .analyzer import RepositoryAnalyzer
from .config import Settings
from .contracts import QCRun
from .github import GitHubClient
from .planner import CapabilityPlanner, PolicyValidator
from .store import Store
from .verdict import VerdictEngine
from .workers import WorkerExecutor, WorkerRegistry


class Orchestrator:
    def __init__(self, settings: Settings, store: Store, github: GitHubClient):
        self.settings = settings
        self.store = store
        self.github = github
        self.analyzer = RepositoryAnalyzer()
        self.planner = CapabilityPlanner()
        self.policy = PolicyValidator()
        self.registry = WorkerRegistry()
        self.worker = WorkerExecutor()
        self.verdict = VerdictEngine()

    async def execute(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if not run:
            return
        workspace = (self.settings.agent_qc_workspace_dir / run.run_id).resolve()
        try:
            run.status = "preparing"
            run.check_run_id = await self.github.create_check(run)
            self.store.save_run(run)
            await self._prepare(run, workspace)
            ctx = run.trigger_context
            run.analysis = await asyncio.to_thread(
                self.analyzer.analyze, workspace, ctx.revision.base_sha, ctx.revision.head_sha
            )
            run.plan = self.policy.validate(
                self.planner.create_plan(run.run_id, run.analysis), run.analysis
            )
            run.status = "running"
            self.store.save_run(run)
            projects = {project.id: project for project in run.analysis.projects}
            completed: set[str] = set()
            pending = list(run.plan.tasks)
            while pending:
                ready = [task for task in pending if set(task.depends_on) <= completed]
                if not ready:
                    raise RuntimeError("Execution plan contains a dependency cycle")
                # A production queue can dispatch this ready batch in parallel.
                for task in ready:
                    project = projects[task.target.project_id]
                    implementation = self.registry.resolve(task, project)
                    result = await asyncio.to_thread(
                        self.worker.execute, task, project, workspace, implementation
                    )
                    run.results.append(result)
                    completed.add(task.task_id)
                    pending.remove(task)
                    self.store.save_run(run)
            run.aggregate = self.verdict.evaluate(run.results)
            run.result = run.results[0] if run.results else None
            run.status = "completed"
            run.verdict = run.aggregate.verdict
        except Exception as exc:
            run.status = "failed"
            run.verdict = "unknown"
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.store.save_run(run)
            await self.github.complete_check(run)
            if workspace.exists():
                shutil.rmtree(workspace)

    async def _prepare(self, run: QCRun, workspace: Path) -> None:
        ctx = run.trigger_context
        workspace.parent.mkdir(parents=True, exist_ok=True)
        clone_url = ctx.repository.clone_url
        if not self.settings.agent_qc_dry_run:
            token = await self.github.installation_token(ctx.installation.id)
            clone_url = clone_url.replace("https://", f"https://x-access-token:{token}@", 1)
        clone_args = ["git"]
        local_source = Path(clone_url)
        if local_source.exists():
            # Needed on shared/portable Windows volumes used by this local demo.
            clone_args += ["-c", f"safe.directory={(local_source / '.git').as_posix()}"]
        clone_args += ["clone", "--no-checkout", clone_url, str(workspace)]
        proc = await asyncio.to_thread(
            subprocess.run,
            clone_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode:
            raise RuntimeError(f"clone failed: {proc.stderr.decode(errors='replace')}")
        proc = await asyncio.to_thread(
            subprocess.run,
            ["git", "-c", f"safe.directory={workspace.as_posix()}",
             "checkout", "--detach", ctx.revision.head_sha],
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode:
            raise RuntimeError(f"checkout failed: {proc.stderr.decode(errors='replace')}")
