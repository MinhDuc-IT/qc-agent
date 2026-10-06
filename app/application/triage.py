import hashlib
import json
import re
from typing import Protocol

from ..domain.models import DecisionLog, Finding, FindingTriage, WorkerResult


OBJECTIVE_EVIDENCE = {"confirmation_rerun_passed", "known_flaky_match"}


class TriageStore(Protocol):
    def get_flaky_test(self, fingerprint: str): ...
    def append_decision(self, decision: DecisionLog) -> None: ...


class TriageAgent(Protocol):
    model: str
    def classify(self, result: WorkerResult, finding: Finding,
                 evidence: list[str]) -> FindingTriage: ...


class TriageService:
    """Safe fallback triage. A model may propose values, but post-validation owns them."""

    def __init__(self, store: TriageStore, min_confidence: float = 0.9,
                 agent: TriageAgent | None = None):
        self.store = store
        self.min_confidence = min_confidence
        self.agent = agent

    def apply(self, run_id: str, results: list[WorkerResult],
              confirmation_evidence: dict[str, list[str]] | None = None) -> None:
        confirmation_evidence = confirmation_evidence or {}
        for result in results:
            for finding in result.findings:
                finding.fingerprint = finding.fingerprint or self.fingerprint(result, finding)
                known = self.store.get_flaky_test(finding.fingerprint)
                evidence = ["known_flaky_match"] if known else []
                evidence.extend(confirmation_evidence.get(finding.fingerprint, []))
                if self.agent is not None:
                    try:
                        proposed = self.agent.classify(result, finding, evidence)
                    except Exception:
                        proposed = self._fallback(bool(known))
                else:
                    proposed = self._fallback(bool(known))
                finding.triage = self._post_validate(proposed)
                payload = {
                    "finding": finding.model_dump(mode="json", exclude={"triage"}),
                    "triage": finding.triage.model_dump(mode="json"),
                }
                encoded = json.dumps(payload["finding"], sort_keys=True).encode()
                self.store.append_decision(DecisionLog(
                    run_id=run_id, component="triage",
                    rationale=finding.triage.rationale,
                    input_hash="sha256:" + hashlib.sha256(encoded).hexdigest(),
                    model=getattr(self.agent, "model", "deterministic-fallback"),
                    prompt_version="triage-v1",
                    output=payload,
                ))

    @staticmethod
    def _fallback(known: bool) -> FindingTriage:
                if known:
                    proposed = FindingTriage(
                        classification="flaky", confidence=1.0,
                        evidence_used=["known_flaky_match"],
                        rationale="Fingerprint matches reviewed flaky-test history.",
                        proposed_action="suppress",
                    )
                else:
                    proposed = FindingTriage(
                        classification="unknown", confidence=0.0, evidence_used=[],
                        rationale="No objective evidence is available.",
                        proposed_action="escalate_human",
                    )
                return proposed

    def _post_validate(self, triage: FindingTriage) -> FindingTriage:
        evidence = OBJECTIVE_EVIDENCE.intersection(triage.evidence_used)
        can_suppress = (triage.classification in {"flaky", "environment"}
                        and triage.confidence >= self.min_confidence and bool(evidence))
        if triage.proposed_action == "suppress" and not can_suppress:
            triage.proposed_action = "escalate_human"
            triage.rationale += " Suppression rejected by INV-4/INV-5."
        if triage.classification == "unknown":
            triage.proposed_action = "escalate_human"
        return triage

    @staticmethod
    def fingerprint(result: WorkerResult, finding: Finding) -> str:
        normalized = re.sub(r"\b\d+\b", "#", finding.message.lower())
        value = "\x1f".join((result.capability, finding.category, finding.title.lower(),
                              finding.path or "", normalized))
        return "sha256:" + hashlib.sha256(value.encode()).hexdigest()
