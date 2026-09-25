import asyncio

from ..domain.models import AgentPlanProposal, AgentResultAnalysis
from ..domain.verdict import VerdictEngine
from .agents import PlanningAgent, ResultAnalysisAgent
from .analysis import RepositoryAnalyzer
from .planning import CapabilityPlanner, PolicyValidator
from .ports import CheckPublisher, RunStore, SourceManager, WorkerRuntime


class Orchestrator:
    def __init__(self, store: RunStore, checks: CheckPublisher, source: SourceManager,
                 workers: WorkerRuntime, planning_agent: PlanningAgent,
                 result_agent: ResultAnalysisAgent):
        self.store = store
        self.checks = checks
        self.source = source
        self.workers = workers
        self.planning_agent = planning_agent
        self.result_agent = result_agent
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
            baseline = self.planner.create_plan(run.run_id, run.analysis)
            run.agent_mode = self.planning_agent.mode
            try:
                run.agent_plan = await asyncio.to_thread(self.planning_agent.propose, run.analysis)
            except Exception as exc:
                run.agent_mode = "fallback"
                run.agent_error = f"Planning agent failed: {type(exc).__name__}: {exc}"
                run.agent_plan = AgentPlanProposal(summary="Agent failed; deterministic baseline used.")
            proposed = self.planner.merge_agent_proposal(run.run_id, baseline, run.agent_plan)
            run.plan = self.policy.validate(proposed, run.analysis)
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
            try:
                run.agent_result_analysis = await asyncio.to_thread(
                    self.result_agent.analyze, run.analysis, run.results
                )
            except Exception as exc:
                run.agent_mode = "fallback"
                detail = f"Result agent failed: {type(exc).__name__}: {exc}"
                run.agent_error = f"{run.agent_error}; {detail}" if run.agent_error else detail
                run.agent_result_analysis = AgentResultAnalysis(
                    summary="Agent result analysis failed; deterministic verdict preserved."
                )
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
