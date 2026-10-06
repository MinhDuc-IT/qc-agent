import asyncio
import hashlib
import json
from uuid import uuid4

from ..domain.models import AgentPlanProposal, AgentResultAnalysis, DecisionLog, RunComparison
from ..domain.verdict import VerdictEngine
from ..domain.capabilities import CATALOG
from .agents import PlanningAgent, ResultAnalysisAgent
from .analysis import RepositoryAnalyzer
from .planning import CapabilityPlanner, PolicyValidator
from .triage import TriageService
from .ports import CheckPublisher, RunStore, SourceManager, TargetProvisioner, WorkerRuntime


class Orchestrator:
    def __init__(self, store: RunStore, checks: CheckPublisher, source: SourceManager,
                 workers: WorkerRuntime, planning_agent: PlanningAgent,
                 result_agent: ResultAnalysisAgent,
                 target_provisioner: TargetProvisioner | None = None,
                 analyzer: RepositoryAnalyzer | None = None,
                 triage_agent=None, triage_min_confidence: float = 0.9,
                 artifact_store=None, side_publishers=(), worker_concurrency: int = 4):
        self.store = store
        self.checks = checks
        self.source = source
        self.workers = workers
        self.planning_agent = planning_agent
        self.result_agent = result_agent
        self.target_provisioner = target_provisioner
        self.analyzer = analyzer or RepositoryAnalyzer()
        self.planner = CapabilityPlanner()
        self.policy = PolicyValidator()
        self.verdict = VerdictEngine()
        self.triage = TriageService(store, triage_min_confidence, triage_agent)
        self.artifact_store = artifact_store
        self.side_publishers = side_publishers
        self.worker_slots = asyncio.Semaphore(max(1, worker_concurrency))

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
            if self._cancelled(run_id):
                return
            ctx = run.trigger_context
            run.analysis = await asyncio.to_thread(
                self.analyzer.analyze, workspace, ctx.revision.base_sha, ctx.revision.head_sha
            )
            baseline = self.planner.create_plan(
                run.run_id, run.analysis, run.requested_capabilities
            )
            run.agent_mode = self.planning_agent.mode
            try:
                run.agent_plan = await asyncio.to_thread(self.planning_agent.propose, run.analysis)
            except Exception as exc:
                run.agent_mode = "fallback"
                run.agent_error = f"Planning agent failed: {type(exc).__name__}: {exc}"
                run.agent_plan = AgentPlanProposal(summary="Agent failed; deterministic baseline used.")
            planner_input = json.dumps(run.analysis.model_dump(mode="json"), sort_keys=True).encode()
            self.store.append_decision(DecisionLog(
                run_id=run.run_id, component="planner", rationale=run.agent_plan.summary,
                input_hash="sha256:" + hashlib.sha256(planner_input).hexdigest(),
                model=getattr(self.planning_agent, "model", "deterministic-fallback"),
                prompt_version="planner-v1",
                output=run.agent_plan.model_dump(mode="json"),
            ))
            proposed = self.planner.merge_agent_proposal(run.run_id, baseline, run.agent_plan)
            run.plan = self.policy.validate(
                proposed, run.analysis, run.trigger_context.trigger.type
            )
            target_tasks = [task for task in run.plan.tasks
                            if CATALOG[task.capability].needs_running_target]
            if target_tasks:
                if self.target_provisioner is None:
                    raise RuntimeError("TARGET_UNAVAILABLE: target provisioner is disabled")
                target = await asyncio.to_thread(
                    self.target_provisioner.provision, run, run.analysis, workspace
                )
                for task in target_tasks:
                    if task.target.type in {target.type, "http_service", "web_app", "ai_service"}:
                        task.target.ref = target.ref
            run.status = "running"
            self.store.save_run(run)
            projects = {project.id: project for project in run.analysis.projects}
            completed: set[str] = set()
            pending = list(run.plan.tasks)
            while pending:
                if self._cancelled(run_id):
                    return
                ready = [task for task in pending if set(task.depends_on) <= completed]
                if not ready:
                    raise RuntimeError("Execution plan contains a dependency cycle")
                executions = [self._execute_with_infra_retry(
                    task, projects[task.target.project_id], workspace
                ) for task in ready]
                batch_results = await asyncio.gather(*executions)
                for task, result in zip(ready, batch_results):
                    run.results.append(result)
                    completed.add(task.task_id)
                    pending.remove(task)
                    self.store.save_run(run)
            confirmation_evidence = await self._run_confirmations(
                run, projects, workspace
            )
            self.triage.apply(run.run_id, run.results, confirmation_evidence)
            run.aggregate = self.verdict.evaluate(run.results)
            if hasattr(self.store, "save_comparison"):
                self.store.save_comparison(RunComparison(
                    run_id=run.run_id,
                    verdict_raw=run.aggregate.verdict_raw or run.aggregate.verdict,
                    verdict_triaged=run.aggregate.verdict_triaged or run.aggregate.verdict,
                ))
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
            if self.artifact_store is not None and run.agent_result_analysis is not None:
                self.artifact_store.save_self_heal_suggestion(
                    run.run_id, run.agent_result_analysis
                )
            for publisher in self.side_publishers:
                publisher.publish(run)
            run.result = run.results[0] if run.results else None
            run.status = "completed"
            run.verdict = (run.aggregate.verdict_raw if run.mode == "observe"
                           else run.aggregate.verdict_triaged)
        except Exception as exc:
            run.status = "failed"
            run.verdict = "unknown"
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            current = self.store.get_run(run.run_id)
            if current is not None and current.status == "cancelled":
                run = current
            else:
                self.store.save_run(run)
            await self.checks.complete_check(run)
            if self.target_provisioner is not None:
                self.target_provisioner.cleanup(run.run_id)
            if workspace is not None:
                self.source.cleanup(workspace)

    def _cancelled(self, run_id: str) -> bool:
        current = self.store.get_run(run_id)
        return current is not None and current.status == "cancelled"

    async def _execute_with_infra_retry(self, task, project, workspace):
        max_attempts = 2
        result = None
        for attempt in range(1, max_attempts + 1):
            task.parameters["attempt"] = attempt
            async with self.worker_slots:
                result = await asyncio.to_thread(
                    self.workers.execute, task, project, workspace
                )
            if (result.execution_status == "completed" or result.error is None
                    or not result.error.retryable):
                return result
        return result

    async def _run_confirmations(self, run, projects, workspace) -> dict[str, list[str]]:
        eligible = {"functional.e2e", "performance.smoke", "performance.load",
                    "performance.stress", "performance.soak"}
        task_by_id = {task.task_id: task for task in run.plan.tasks}
        evidence: dict[str, list[str]] = {}
        for result in list(run.results):
            if result.verdict != "fail" or result.capability not in eligible:
                continue
            original = task_by_id.get(result.task_id)
            if original is None:
                continue
            for finding in result.findings:
                fingerprint = self.triage.fingerprint(result, finding)
                finding.fingerprint = fingerprint
                confirmation = original.model_copy(deep=True)
                confirmation.task_id = f"task_{uuid4().hex}"
                confirmation.purpose = "confirmation"
                confirmation.scope["finding_fingerprint"] = fingerprint
                confirmation_result = await self._execute_with_infra_retry(
                    confirmation, projects[confirmation.target.project_id], workspace
                )
                confirmation_result.summary["execution_purpose"] = "confirmation"
                run.results.append(confirmation_result)
                self.store.save_run(run)
                if confirmation_result.execution_status == "completed" and confirmation_result.verdict == "pass":
                    evidence[fingerprint] = ["confirmation_rerun_passed"]
        return evidence
