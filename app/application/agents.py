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
        return AgentPlanProposal(summary="Chưa có phân tích từ AI; test plan được lập theo bộ quy tắc mặc định.")


class FallbackResultAnalysisAgent:
    mode = "fallback"

    def analyze(self, analysis: SourceAnalysis, results: list[WorkerResult]) -> AgentResultAnalysis:
        failures = [result for result in results if result.verdict == "fail"]
        return AgentResultAnalysis(
            summary=(f"Có {len(failures)} test task không đạt. Chưa có phân tích root cause từ AI."
                     if failures else "Không có test task nào báo lỗi. Chưa có phân tích bổ sung từ AI."),
        )
