"""Human-readable test design, independent from any executable runner."""
from typing import Literal
from pydantic import Field, model_validator, field_validator
from .contracts import Contract, Evidence, ExecutableCase, KnowledgeModel, SourceBundle, TestKind, Parameter, RequirementNode
from .inventory import ProjectInventory
from .completeness import CompletenessPolicy, DesignScenario, SemanticEvaluation


Technique = Literal['happy_path','negative','boundary_value','equivalence_partitioning',
                    'decision_table','state_transition','permission_matrix','workflow','pairwise','risk_based']


class ExpectedResult(Contract):
    description: str = Field(min_length=1,max_length=4000)
    origin: Literal['specified','implementation_derived','maintainer_confirmed','unresolved'] = 'unresolved'
    evidence: list[Evidence] = Field(default_factory=list,max_length=20)
    expected_json: str | None = None
    assertion_kind: Literal['status','json_equals','json_contains','latency_ms','text_equals','text_contains',
        'return_equals','raises','exception_message','dom_visible','dom_text_equals','dom_text_contains',
        'dom_value_equals','latency_p95_ms','error_rate','ai_score'] | None = None
    pointer: str = ''


class DesignStep(Contract):
    action: str = Field(min_length=1,max_length=4000)
    expected_results: list[ExpectedResult] = Field(min_length=1,max_length=20)
    reusable_step_id: str | None = None


class DesignCase(Contract):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,120}$')
    revision: int = Field(default=1,ge=1)
    title: str = Field(min_length=1,max_length=500)
    objective: str = Field(default='',max_length=4000)
    kind: TestKind = 'functional'
    priority: Literal['P0','P1','P2'] = 'P1'
    technique: Technique = 'happy_path'
    requirement_ids: list[str] = Field(default_factory=list,max_length=100)
    criterion_ids: list[str] = Field(default_factory=list,max_length=100)
    inventory_ids: list[str] = Field(default_factory=list,max_length=100)
    branch_ids: list[str] = Field(default_factory=list,max_length=100)
    remediation_gap_ids: list[str] = Field(default_factory=list,max_length=100)
    preconditions: list[str] = Field(default_factory=list,max_length=30)
    test_data: list[Parameter] = Field(default_factory=list,max_length=100)
    steps: list[DesignStep] = Field(min_length=1,max_length=30)
    assumptions: list[str] = Field(default_factory=list,max_length=30)
    questions: list[str] = Field(default_factory=list,max_length=30)
    status: Literal['draft','needs_clarification','ready_for_review','approved','retired'] = 'draft'
    author: str = 'agent'
    human_edited: bool = False


class Criterion(Contract):
    id: str
    requirement_id: str
    text: str


class StrategyItem(Contract):
    requirement_id: str
    techniques: list[Technique] = Field(min_length=1)
    kinds: list[TestKind] = Field(min_length=1)
    rationale: str


class DesignStrategy(Contract):
    items: list[StrategyItem]
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class DesignBatch(Contract):
    cases: list[DesignCase] = Field(max_length=8)
    open_questions: list[str] = Field(default_factory=list)
    retire_case_ids: list[str] = Field(default_factory=list)


class WorkspaceFinding(Contract):
    id: str = ''
    case_ids: list[str] = Field(default_factory=list)
    severity: Literal['blocker','warning','info']
    category: Literal['clarity','evidence','coverage','duplicate','conflict','source_change','structure']
    message: str
    suggestion: str = ''
    evidence: list[Evidence] = Field(default_factory=list)


class DesignReview(Contract):
    findings: list[WorkspaceFinding]
    summary: str
    semantic_evaluation: SemanticEvaluation | None = None


class AssertionMapping(Contract):
    design_step: int = Field(ge=0)
    expected_result: int = Field(ge=0)
    executable_step: int = Field(ge=0)
    assertion_indexes: list[int] = Field(min_length=1)
    explanation: str = Field(min_length=1,max_length=2000)


class AutomationBinding(Contract):
    case_id: str
    case_revision: int
    design_sha256: str
    state: Literal['unbound','bound','validated'] = 'bound'
    executable: ExecutableCase
    reviewer: str | None = None
    validation_issues: list[str] = Field(default_factory=list)
    expected_result_mapping: list[AssertionMapping] = Field(default_factory=list)
    @model_validator(mode='after')
    def identity(self):
        if self.executable.id!=self.case_id:raise ValueError('Binding case ID mismatch')
        return self


class BindingBatch(Contract):
    bindings: list[AutomationBinding]
    limitations: list[str] = Field(default_factory=list)


class ReviewDecision(Contract):
    case_id: str | None = None
    finding_id: str | None = None
    action: str
    actor: str
    reason: str
    revision: int


class ReusableStep(Contract):
    id: str
    title: str
    action: str
    preconditions: list[str] = Field(default_factory=list)


class WorkspaceKnowledge(KnowledgeModel):
    requirements: list[RequirementNode] = Field(default_factory=list)


class TestWorkspace(Contract):
    schema_version: Literal['3.0'] = '3.0'
    project_id: str
    version: int = Field(default=1,ge=1)
    source_revision: str
    source_snapshots: list[str] = Field(default_factory=list)
    sources: SourceBundle
    knowledge: WorkspaceKnowledge
    criteria: list[Criterion]
    strategy: DesignStrategy
    cases: list[DesignCase]
    findings: list[WorkspaceFinding] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    decisions: list[ReviewDecision] = Field(default_factory=list)
    reusable_steps: list[ReusableStep] = Field(default_factory=list)
    bindings: dict[str,AutomationBinding] = Field(default_factory=dict)
    coverage: dict = Field(default_factory=dict)
    published_version: str | None = None
    published_workspace_version: int | None = None
    generation_job_id: str | None = None
    last_changes: dict = Field(default_factory=dict)
    proposals: list[DesignCase] = Field(default_factory=list)
    generation_diagnostic: dict | None = None
    inventory: ProjectInventory = Field(default_factory=ProjectInventory)
    completeness_policy: CompletenessPolicy = Field(default_factory=CompletenessPolicy)
    scenarios: list[DesignScenario] = Field(default_factory=list)
    scenario_input_sha256: str | None = None
    scenario_limitations: list[str] = Field(default_factory=list)
    semantic_evaluation: SemanticEvaluation | None = None
    semantic_input_sha256: str | None = None
    completeness: dict = Field(default_factory=dict)
    evaluation_history: list[dict] = Field(default_factory=list)
    design_method: str = 'legacy'
    investigation_survey: dict = Field(default_factory=dict)
    flow_modules: list[dict] = Field(default_factory=list)
    investigation_limitations: list[str] = Field(default_factory=list)
    automation_preparation: dict[str,dict] = Field(default_factory=dict)
    automation_job_diagnostic: dict | None = None
    @field_validator('knowledge',mode='before')
    @classmethod
    def accept_analysis_model(cls,value):
        return value.model_dump() if isinstance(value,KnowledgeModel) else value
