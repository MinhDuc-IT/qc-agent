import asyncio

from ..domain.verdict import VerdictEngine
from .analysis import RepositoryAnalyzer
from .planning import CapabilityPlanner, PolicyValidator
from .ports import CheckPublisher, RunStore, SourceManager, WorkerRuntime


class Orchestrator:
    def __init__(self, store: RunStore, checks: CheckPublisher, source: SourceManager,
                 workers: WorkerRuntime):
        self.store = store
        self.checks = checks
        self.source = source
        self.workers = workers
        self.analyzer = RepositoryAnalyzer()
        self.planner = CapabilityPlanner()
        self.policy = PolicyValidator()
        self.verdict = VerdictEngine()

    async def execute(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if not run:
            return
        workspace = None
        try:
            run.status = "preparing"
            run.check_run_id = await self.checks.create_check(run)
            self.store.save_run(run)
            workspace = await self.source.prepare(run)
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
                    result = await asyncio.to_thread(
                        self.workers.execute, task, project, workspace
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
            await self.checks.complete_check(run)
            if workspace is not None:
                self.source.cleanup(workspace)
