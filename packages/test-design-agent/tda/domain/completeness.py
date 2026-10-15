"""Versioned contracts for independent design completeness evaluation."""
from typing import Literal
from pydantic import Field
from .contracts import Contract, Evidence


class CompletenessPolicy(Contract):
    critical_requirement: float = Field(default=1, ge=0, le=1)
    requirement: float = Field(default=.95, ge=0, le=1)
    scenario: float = Field(default=.90, ge=0, le=1)
    critical_high_risk: float = Field(default=1, ge=0, le=1)
    validity: float = Field(default=.95, ge=0, le=1)
    unresolved_critical_gaps: int = Field(default=0, ge=0)
    max_remediation_rounds: int = Field(default=3, ge=0, le=10)


class DesignScenario(Contract):
    id: str
    requirement_id: str
    criterion_ids: list[str] = Field(default_factory=list)
    risk_ids: list[str] = Field(default_factory=list)
    technique: str
    kind: str = 'functional'
    description: str
    applicability: Literal['APPLICABLE','NOT_APPLICABLE','NEEDS_REVIEW'] = 'APPLICABLE'
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)


class ScenarioInventory(Contract):
    scenarios: list[DesignScenario]
    limitations: list[str] = Field(default_factory=list)


class SemanticCoverage(Contract):
    scenario_id: str
    case_id: str
    verdict: Literal['COVERED','NOT_COVERED','NEEDS_REVIEW']
    explanation: str
    evidence: list[Evidence] = Field(default_factory=list)


class SemanticEvaluation(Contract):
    mappings: list[SemanticCoverage] = Field(default_factory=list)
    duplicate_groups: list[list[str]] = Field(default_factory=list)
    summary: str = ''
    case_checks: list['SemanticCaseCheck'] = Field(default_factory=list)
    duplicate_evidence: list['SemanticDuplicate'] = Field(default_factory=list)


class SemanticCaseCheck(Contract):
    case_id: str
    verdict: Literal['VALID','INVALID','NEEDS_REVIEW']
    explanation: str
    evidence: list[Evidence] = Field(default_factory=list)


class SemanticDuplicate(Contract):
    case_ids: list[str] = Field(min_length=2)
    explanation: str
    evidence: list[Evidence] = Field(default_factory=list)


SemanticEvaluation.model_rebuild()
