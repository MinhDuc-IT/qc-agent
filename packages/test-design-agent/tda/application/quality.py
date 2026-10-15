from . import prompts
from ..domain.contracts import CriticReview, CriticIssue
from ..domain.rules import check_evidence


class TestCritic:
    def __init__(self, agent):
        self.agent = agent

    def review(self, knowledge, sources, scenarios, cases, validation, coverage, execution, questions):
        context = {
            "knowledge": knowledge.model_dump(), "sources": sources.model_dump(),
            "plan": [scenario.model_dump() for scenario in scenarios],
            "cases": [case.model_dump() for case in cases], "validation": validation.model_dump(),
            "coverage": coverage, "execution": execution, "open_questions": sorted(set(questions))}
        known_ids = {case.id for case in cases}
        context["known_case_ids"] = sorted(known_ids)
        for attempt in range(2):
            review = self.agent.propose("critic" if attempt == 0 else "critic.correction",
                                        prompts.CRITIC, context, CriticReview)
            try:
                for issue in review.issues:
                    if not set(issue.case_ids).issubset(known_ids):
                        raise ValueError("critic referenced unknown case IDs")
                    check_evidence(issue.evidence, sources)
                return review
            except ValueError as error:
                if attempt:
                    # Keep the generated draft reviewable, but never accept an ungrounded review.
                    return CriticReview(
                        recommendation="needs_changes",
                        summary="Test Critic chưa hoàn tất review: trích dẫn hoặc case ID không hợp lệ.",
                        issues=[CriticIssue(severity="blocker", case_ids=[], evidence=[],
                            message="Cần review lại Test Critic; không được duyệt baseline khi review chưa hợp lệ.")])
                context["rejected_review"] = review.model_dump()
                context["validation_feedback"] = str(error)
                context["correction_instructions"] = (
                    "Use only exact quotations from supplied sources and IDs in known_case_ids. "
                    "Scenario IDs are not case IDs; correct the references without dropping findings. "
                    "For structural issues supported by validation/cases/execution, explain the "
                    "supplied report in message and use evidence=[] rather than invent source quotes. "
                    "Preserve grounded blockers; never weaken assertions or conceal validation issues.")
