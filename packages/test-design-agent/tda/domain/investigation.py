"""Contracts for a tool-using, flow-oriented test design agent."""
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Evidence,RequirementNode
from .workspace import DesignCase,WorkspaceFinding


class ToolRequest(Contract):
    name: Literal['list_files','search','read_file','find_references']
    path: str = ''
    query: str = ''
    start_line: int = Field(default=1,ge=1)
    end_line: int = Field(default=120,ge=1)


class ModuleScope(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    title: str
    objective: str
    entry_paths: list[str] = Field(min_length=1,max_length=12)
    priority: Literal['P0','P1','P2'] = 'P1'


class ProjectSurvey(Contract):
    modules: list[ModuleScope] = Field(max_length=30)
    exclusions: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class FlowNode(Contract):
    role: Literal['ui','api','service','model','validation','authorization','database','dependency','existing_test']
    description: str
    evidence: list[Evidence] = Field(min_length=1,max_length=6)


class Behavior(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,120}$')
    description: str
    requirement_ids: list[str] = Field(default_factory=list)
    criterion_ids: list[str] = Field(default_factory=list)
    technique: str
    basis: Literal['specified','implementation_derived','missing_implementation','conflict','unclear']
    expected_behavior: str
    evidence: list[Evidence] = Field(min_length=1,max_length=8)


class ModuleStudy(Contract):
    module_id: str
    scope_fit: Literal['matched','mismatch','unclear'] = 'matched'
    scope_rationale: str = ''
    summary: str
    requirements: list[RequirementNode] = Field(max_length=20)
    flow: list[FlowNode] = Field(max_length=30)
    behaviors: list[Behavior] = Field(max_length=40)
    questions: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)


class InquiryTurn(Contract):
    notes: str = Field(default='',max_length=4000)
    requests: list[ToolRequest] = Field(default_factory=list,max_length=3)
    study: ModuleStudy | None = None

    @model_validator(mode='after')
    def one_action(self):
        if bool(self.requests)==bool(self.study):raise ValueError('Request tools OR finish a grounded study')
        return self


class ProposedBehaviorLink(Contract):
    behavior_id: str
    case_ids: list[str]


class FlowCaseBatch(Contract):
    cases: list[DesignCase] = Field(max_length=8)
    behavior_links: list[ProposedBehaviorLink] = Field(default_factory=list,max_length=100)


class VerifiedLink(Contract):
    behavior_id: str
    case_id: str
    step_indexes: list[int] = Field(min_length=1,max_length=30)
    criterion_ids: list[str] = Field(default_factory=list)
    verdict: Literal['verified','not_verified','needs_review']
    explanation: str
    evidence: list[Evidence] = Field(min_length=1,max_length=8)


class DuplicateGroup(Contract):
    canonical_case_id: str
    duplicate_case_ids: list[str] = Field(min_length=1,max_length=20)
    explanation: str
    evidence: list[Evidence] = Field(min_length=1,max_length=8)


class ModuleReview(Contract):
    findings: list[WorkspaceFinding] = Field(default_factory=list)
    links: list[VerifiedLink] = Field(default_factory=list)
    missing_behaviors: list[Behavior] = Field(default_factory=list,max_length=12)
    questions: list[str] = Field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = Field(default_factory=list,max_length=20)


class ModuleResult(Contract):
    scope: ModuleScope
    study: ModuleStudy | None = None
    case_ids: list[str] = Field(default_factory=list)
    links: list[VerifiedLink] = Field(default_factory=list)
    proposed_links: dict[str,list[str]] = Field(default_factory=dict)
    status: Literal['pending','investigating','draft','reviewed','partial'] = 'pending'
    notes: str = ''
    diagnostic: str | None = None
    read_paths: list[str] = Field(default_factory=list)
    investigation_checkpoint: dict | None = None
    review_checkpoint: dict = Field(default_factory=dict)
    investigation_feedback: list[dict] = Field(default_factory=list)
