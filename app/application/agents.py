from typing import Protocol

from ..domain.models import AgentPlanProposal, AgentResultAnalysis, SourceAnalysis, WorkerResult


class PlanningAgent(Protocol):
    mode: str

    def propose(self, analysis: SourceAnalysis) -> AgentPlanProposal: ...


class ResultAnalysisAgent(Protocol):
    mode: str

    def analyze(self, analysis: SourceAnalysis, results: list[WorkerResult]) -> AgentResultAnalysis: ...


class FallbackPlanningAgent:
    mode = "fallback"

    def propose(self, analysis: SourceAnalysis) -> AgentPlanProposal:
        return AgentPlanProposal(summary="LLM planning unavailable; deterministic baseline used.")


class FallbackResultAnalysisAgent:
    mode = "fallback"

    def analyze(self, analysis: SourceAnalysis, results: list[WorkerResult]) -> AgentResultAnalysis:
        failures = [result for result in results if result.verdict == "fail"]
        return AgentResultAnalysis(
            summary=(f"{len(failures)} failed QC task(s); LLM result analysis unavailable."
                     if failures else "No failed QC tasks; LLM result analysis unavailable."),
        )
