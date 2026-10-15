import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TestKind = Literal["functional", "integration", "security", "performance", "ai_evaluation"]
RunnerKind = Literal["http", "unit", "browser", "k6", "ai"]
METHODS = {"get", "head", "post", "put", "patch", "delete", "options", "trace"}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(Contract):
    source_id: str
    quote: str = Field(min_length=1)


class SourceSpec(Contract):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    kind: Literal["code", "prd", "srs", "openapi", "db_schema", "existing_tests"]
    paths: list[str] = Field(min_length=1)


class SourceDocument(Contract):
    id: str
    kind: str
    path: str
    sha256: str
    content: str


class ApiOperation(Contract):
    id: str
    method: str
    path: str
    source_id: str


class SourceBundle(Contract):
    documents: list[SourceDocument]
    operations: list[ApiOperation]


class CoveragePolicy(Contract):
    requirement: float = Field(default=1.0, ge=0, le=1)
    feature: float = Field(default=1.0, ge=0, le=1)
    risk: float = Field(default=1.0, ge=0, le=1)
    api: float = Field(default=1.0, ge=0, le=1)


class WorkflowConfig(Contract):
    schema_version: Literal["2.0"] = "2.0"
    project: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    root: str = "."
    sources: list[SourceSpec] = Field(min_length=1)
    kinds: list[TestKind] = Field(default_factory=lambda: ["functional", "integration"])
    model: str = Field(min_length=1)
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = None
    max_calls: int = Field(default=24, ge=4, le=100)
    max_output_tokens: int = Field(default=8000, ge=1000, le=32000)
    max_rounds: int = Field(default=2, ge=1, le=5)
    max_repairs: int = Field(default=1, ge=0, le=3)
    max_cases: int = Field(default=50, ge=1, le=200)
    max_source_bytes: int = Field(default=160000, ge=1000, le=500000)
    coverage: CoveragePolicy = Field(default_factory=CoveragePolicy)
    runners: list[RunnerKind] = Field(default_factory=lambda: ["http"])
    reference_execution: bool = True

    @model_validator(mode="after")
    def uniqueness(self):
        ids = [source.id for source in self.sources]
        if len(ids) != len(set(ids)) or len(self.kinds) != len(set(self.kinds)) or not self.kinds:
            raise ValueError("source IDs and enabled test kinds must be unique and non-empty")
        if not self.runners or len(self.runners)!=len(set(self.runners)):
            raise ValueError('runners must be unique and non-empty')
        return self


class RequirementNode(Contract):
    category: Literal['functional','non_functional','business_rule','input_constraint','validation','error_handling','security','integration'] = 'functional'
    priority: Literal['P0','P1','P2'] = 'P1'
    clarity: Literal['CONFIRMED','AMBIGUOUS','INCOMPLETE'] = 'CONFIRMED'
    expectation_basis: Literal["specification", "implementation"] = "specification"
    execution_scope: Literal["http", "browser", "unit", "dependency", "load"] = "http"
    scope_reason: str | None = None
    id: str
    title: str
    acceptance_criteria: list[str]
    evidence: list[Evidence] = Field(min_length=1)


class FeatureNode(Contract):
    id: str
    title: str
    requirement_ids: list[str]
    evidence: list[Evidence] = Field(min_length=1)


class RiskNode(Contract):
    id: str
    description: str
    severity: Literal["critical", "high", "medium", "low"]
    requirement_ids: list[str]
    evidence: list[Evidence] = Field(min_length=1)


class DependencyNode(Contract):
    id: str
    description: str
    evidence: list[Evidence] = Field(min_length=1)


class KnowledgeModel(Contract):
    requirements: list[RequirementNode] = Field(min_length=1)
    features: list[FeatureNode]
    risks: list[RiskNode]
    dependencies: list[DependencyNode]
    open_questions: list[str]
    blocking_questions: list[str] = Field(default_factory=list)


class PlannedScenario(Contract):
    runner: RunnerKind = "http"
    expectation_basis: Literal["specification", "implementation"] = "specification"
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    title: str
    kind: TestKind
    priority: Literal["P0", "P1", "P2"]
    requirement_ids: list[str] = Field(min_length=1)
    feature_ids: list[str]
    risk_ids: list[str]
    operation_ids: list[str]
    acceptance_criteria: list[str] = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class TestPlan(Contract):
    scenarios: list[PlannedScenario]
    rationale: str
    open_questions: list[str]
    blocking_questions: list[str] = Field(default_factory=list)


class Parameter(Contract):
    name: str
    value: str


class HttpRequest(Contract):
    method: Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    query: list[Parameter]
    body_json: str | None
    auth: Literal["none", "bearer_env"]


class GeneratedCase(Contract):
    runner: RunnerKind = "http"
    id: str = Field(pattern=r"^[A-Za-z0-9_-]+$")
    scenario_id: str
    title: str
    kind: TestKind
    steps: list[HttpRequest] = Field(max_length=10)
    cleanup: list[HttpRequest] = Field(max_length=5)
    depends_on: list[str]
    repetitions: int = Field(ge=1, le=20)
    notes: list[str]
    unit: "UnitCall | None" = None
    browser: "BrowserPlan | None" = None
    load: "LoadPlan | None" = None
    grading: "AiRubric | None" = None


class UnitCall(Contract):
    language: Literal["python", "javascript"]
    module_path: str
    function: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    args_json: str
    kwargs_json: str


class BrowserAction(Contract):
    action: Literal["goto", "fill", "click", "select", "check", "observe"]
    path: str | None = None
    selector: str | None = None
    value: str | None = None


class BrowserPlan(Contract):
    actions: list[BrowserAction] = Field(min_length=1, max_length=20)
    auth: Literal["none", "bearer_env"] = "none"


class LoadPlan(Contract):
    vus: int = Field(ge=1, le=5)
    duration_seconds: int = Field(ge=1, le=15)


class AiRubric(Contract):
    rubric: str = Field(min_length=1, max_length=4000)
    response_pointer: str
    evidence: list[Evidence] = Field(min_length=1)


class AiGrade(Contract):
    score: float = Field(ge=0, le=1)
    reason: str


class GeneratedBatch(Contract):
    cases: list[GeneratedCase]
    open_questions: list[str]
    blocking_questions: list[str] = Field(default_factory=list)


class Assertion(Contract):
    expectation_basis: Literal["specification", "implementation"] = "specification"
    kind: Literal["status", "json_equals", "json_contains", "latency_ms", "text_equals", "text_contains",
                  "return_equals", "raises", "exception_message", "dom_visible", "dom_text_equals",
                  "dom_text_contains", "dom_value_equals", "latency_p95_ms", "error_rate", "ai_score"]
    pointer: str
    expected_json: str
    evidence: list[Evidence] = Field(min_length=1)


class StepOracle(Contract):
    step_index: int = Field(ge=0, le=19)
    assertions: list[Assertion] = Field(min_length=1)


class CaseOracle(Contract):
    case_id: str
    steps: list[StepOracle] = Field(min_length=1)
    rationale: str


class OracleBatch(Contract):
    oracles: list[CaseOracle]
    open_questions: list[str]
    blocking_questions: list[str] = Field(default_factory=list)


class ExecutableCase(GeneratedCase):
    oracle: CaseOracle
    requirement_ids: list[str]
    feature_ids: list[str]
    risk_ids: list[str]
    operation_ids: list[str]


class ValidationIssue(Contract):
    case_id: str
    code: str
    message: str
    repairable: bool


class ValidationReport(Contract):
    valid_case_ids: list[str]
    issues: list[ValidationIssue]


class RepairBatch(Contract):
    cases: list[GeneratedCase]
    rationale: str


class CriticIssue(Contract):
    severity: Literal["blocker", "warning", "info"]
    case_ids: list[str]
    message: str
    evidence: list[Evidence]
    suggested_action: Literal['request_changes','add_acceptance_criteria','review_blocker'] | None = None
    suggestion: str = Field(default='',max_length=2000)
    question: str = Field(default='',max_length=2000)


class CriticReview(Contract):
    recommendation: Literal["ready_for_human_review", "needs_changes"]
    summary: str
    issues: list[CriticIssue]


def validate_json(value: str):
    return json.loads(value, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


GeneratedCase.model_rebuild()
ExecutableCase.model_rebuild()
GeneratedBatch.model_rebuild()
RepairBatch.model_rebuild()
