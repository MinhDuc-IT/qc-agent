import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import yaml

from tda.application.orchestrator import TestDesignOrchestrator as DesignOrchestrator
from tda.application.stages import RepairAgent
from tda.domain.contracts import (Assertion, CaseOracle, CriticReview, Evidence, ExecutableCase,
                                 FeatureNode, GeneratedBatch, GeneratedCase, HttpRequest,
                                 KnowledgeModel, OracleBatch, Parameter, PlannedScenario,
                                 RepairBatch, RequirementNode, RiskNode, SourceBundle,
                                 SourceDocument, StepOracle, TestPlan as Plan, WorkflowConfig)
from tda.domain.rules import TestValidator as Validator, validate_knowledge, path_matches
from tda.infrastructure.artifacts import LocalArtifactStore
from tda.infrastructure.baselines import BaselineRepository
from tda.infrastructure.openai_agent import AgentFailure, OpenAIStructuredAgent
from tda.infrastructure.sources import LocalSourceReader
from tda.pipeline import approve, dump, execute, qc_plan, verify
from tda.runner import HttpSuiteExecutor


KINDS = ["functional", "integration", "security", "performance", "ai_evaluation"]
TEXT = 'GET /sum returns HTTP 200 and JSON {"result":5}. GET /private returns HTTP 403. Each latency sample must be below 1000 milliseconds.'


@pytest.mark.parametrize("correction_valid", [True, False])
def test_critic_corrects_fabricated_quotes_or_blocks_draft(correction_valid):
    from tda.application.quality import TestCritic as Critic
    from tda.domain.contracts import CriticIssue

    class Agent:
        calls = []

        def propose(self, stage, instructions, context, output):
            self.calls.append(stage)
            corrected = stage == "critic.correction" and correction_valid
            if stage == "critic.correction":
                assert "evidence" in context["validation_feedback"]
                assert context["rejected_review"]["issues"]
            return CriticReview(recommendation="needs_changes", summary="structural review",
                issues=[CriticIssue(severity="blocker", case_ids=["case-functional"],
                    message="Oracle step count mismatch", evidence=[] if corrected else
                    [Evidence(source_id="prd:prd.md", quote="invented request in source")])])

    agent = Agent()
    review = Critic(agent).review(knowledge(), bundle(), [scenario()], [executable()],
                                 Validator().validate([executable()], bundle()), {}, None, [])
    assert agent.calls == ["critic", "critic.correction"]
    assert review.recommendation == "needs_changes"
    assert review.issues[0].severity == "blocker"
    assert review.issues[0].evidence == []
    assert ("step count" in review.issues[0].message) == correction_valid


def evidence():
    return Evidence(source_id="prd:prd.md", quote=TEXT)


def knowledge():
    return KnowledgeModel(
        requirements=[RequirementNode(id="R1", title="Sum", acceptance_criteria=[TEXT], evidence=[evidence()])],
        features=[FeatureNode(id="F1", title="Calculation", requirement_ids=["R1"], evidence=[evidence()])],
        risks=[RiskNode(id="K1", description="Wrong sum", severity="high", requirement_ids=["R1"], evidence=[evidence()])],
        dependencies=[], open_questions=[])


def scenario(kind="functional", identifier=None):
    return PlannedScenario(id=identifier or kind, title=kind, kind=kind, priority="P0",
                           requirement_ids=["R1"], feature_ids=["F1"], risk_ids=["K1"],
                           operation_ids=[], acceptance_criteria=[TEXT], evidence=[evidence()])


def request(path="/sum"):
    return HttpRequest(method="GET", path=path, query=[], body_json=None, auth="none")


def generated(kind="functional", invalid_path=False):
    return GeneratedCase(id="case-" + kind, scenario_id=kind, title=kind, kind=kind,
                         steps=[request("sum" if invalid_path else "/private" if kind == "security" else "/sum")],
                         cleanup=[], depends_on=[], repetitions=2 if kind == "performance" else 1, notes=[])


def oracle(case):
    assertions = [Assertion(kind="status", pointer="", expected_json="403" if case.kind == "security" else "200", evidence=[evidence()])]
    if case.kind != "security":
        assertions.append(Assertion(kind="json_equals", pointer="/result", expected_json="5", evidence=[evidence()]))
    if case.kind == "performance":
        assertions.append(Assertion(kind="latency_ms", pointer="", expected_json="1000", evidence=[evidence()]))
    return CaseOracle(case_id=case.id, steps=[StepOracle(step_index=0, assertions=assertions)], rationale="PRD")


def executable(kind="functional", invalid_path=False):
    case = generated(kind, invalid_path)
    return ExecutableCase(**case.model_dump(), oracle=oracle(case), requirement_ids=["R1"],
                          feature_ids=["F1"], risk_ids=["K1"], operation_ids=[])


def bundle():
    return SourceBundle(documents=[SourceDocument(id="prd:prd.md", kind="prd", path="prd.md", sha256="x", content=TEXT)], operations=[])


class ScriptedAgent:
    def __init__(self, kinds=None, invalid_path=False, blocker=False, no_oracle=False):
        self.kinds = kinds or KINDS
        self.invalid_path, self.blocker, self.no_oracle = invalid_path, blocker, no_oracle
        self.calls = []

    def propose(self, stage, instructions, context, output):
        self.calls.append(stage)
        if stage == "analysis":
            result = knowledge()
        elif stage == "planning":
            result = Plan(scenarios=[scenario(kind) for kind in self.kinds], rationale="risk-based", open_questions=[])
        elif stage.startswith("generation."):
            kind = stage.split(".")[1]
            result = GeneratedBatch(cases=[generated(kind, self.invalid_path)], open_questions=[])
        elif stage == "oracle":
            result = OracleBatch(oracles=[] if self.no_oracle else [oracle(GeneratedCase.model_validate(case))
                                for case in context["cases"]], open_questions=[])
        elif stage == "repair":
            result = RepairBatch(cases=[generated(case["kind"]) for case in context["cases"]], rationale="path fix")
        elif stage == "critic":
            result = CriticReview(recommendation="needs_changes" if self.blocker else "ready_for_human_review",
                                  summary="review", issues=[])
        else:
            raise AssertionError(stage)
        assert isinstance(result, output)
        return result


@pytest.fixture
def service():
    class Handler(BaseHTTPRequestHandler):
        broken = False

        def do_GET(self):
            self.send_response(403 if self.path == "/private" else 200)
            self.end_headers()
            self.wfile.write(json.dumps({"result": 6 if self.broken else 5}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", Handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run_workflow(tmp_path, agent, base_url=None, **overrides):
    (tmp_path / "prd.md").write_text(TEXT, encoding="utf-8")
    options = dict(project="demo", source_revision="revision-1", model="configured-model",
                   sources=[{"id": "prd", "kind": "prd", "paths": ["prd.md"]}],
                   kinds=agent.kinds, max_rounds=1)
    options.update(overrides)
    config = WorkflowConfig(**options)
    store = LocalArtifactStore(tmp_path / "run")
    workflow = DesignOrchestrator(agent, LocalSourceReader(), HttpSuiteExecutor(), store,
                                      BaselineRepository(tmp_path / "baselines"))
    return workflow.run(config, tmp_path, base_url), store


def test_all_specialized_stages_end_to_end_and_qc_export(tmp_path, service):
    agent = ScriptedAgent()
    result, store = run_workflow(tmp_path, agent, service[0])
    assert result["state"] == "ready_for_human_review"
    baseline = Path(result["baseline"])
    assert verify(baseline)["schema_version"] == "2.0"
    assert all("generation." + kind in agent.calls for kind in KINDS)
    assert (baseline / "ground_truth/oracles.json").is_file()
    approve(baseline, "reviewer", store.root / "execution_report.json")
    assert qc_plan(baseline, "backend", "run", "baseline")["tasks"][0]["implementation"] == "pytest"
    environment = dict(os.environ, TDA_BASE_URL=service[0])
    completed = subprocess.run([sys.executable, "-m", "pytest", str(baseline / "tests/test_baseline.py"),
                                "-q", "-p", "no:cacheprovider"], env=environment,
                               capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    service[1].broken = True
    failure = execute(baseline, service[0])
    assert not failure["passed"]
    assert any(case["status"] == "defect" for case in failure["results"])


def test_no_target_preserves_design_readiness_and_can_approve_without_execution(tmp_path, service):
    result, _ = run_workflow(tmp_path, ScriptedAgent(kinds=["functional"]))
    assert result["state"] == "ready_for_human_review"
    baseline = Path(result["baseline"])
    report = tmp_path / "reference.json"
    approve(baseline, "reviewer")
    assert verify(baseline)["state"] == "approved"


def test_product_defect_never_invokes_repair(tmp_path, service):
    service[1].broken = True
    agent = ScriptedAgent(kinds=["functional"])
    result, _ = run_workflow(tmp_path, agent, service[0])
    assert result["state"] == "ready_for_human_review"
    assert "repair" not in agent.calls
    approve(Path(result['baseline']),'reviewer')


def test_design_only_does_not_execute_application_even_with_a_target(tmp_path,service):
    result,store=run_workflow(tmp_path,ScriptedAgent(kinds=['functional']),service[0],reference_execution=False)
    assert result['state']=='ready_for_human_review'
    assert json.loads((store.root/'execution_report.json').read_text()) is None
    baseline=Path(result['baseline'])
    assert json.loads((baseline/'reference_execution.json').read_text()) is None
    approve(baseline,'reviewer')
    assert verify(baseline)['approval_policy']=='design-quality-v1'


def test_reference_environment_error_does_not_block_valid_design(tmp_path):
    result,store=run_workflow(tmp_path,ScriptedAgent(kinds=['functional']),'http://127.0.0.1:1')
    assert result['state']=='ready_for_human_review'
    report=json.loads((store.root/'execution_report.json').read_text())
    assert not report['passed'] and report['results'][0]['status']=='environment_error'
    approve(Path(result['baseline']),'reviewer')


def test_request_repair_keeps_oracle_and_revalidates(tmp_path, service):
    agent = ScriptedAgent(kinds=["functional"], invalid_path=True)
    result, _ = run_workflow(tmp_path, agent, service[0])
    assert result["state"] == "ready_for_human_review"
    assert agent.calls.count("repair") == 1
    cases = json.loads((Path(result["baseline"]) / "cases/cases.json").read_text())
    assert cases[0]["oracle"] == oracle(generated()).model_dump()


def test_missing_oracle_blocks_approval_and_exposes_gap(tmp_path, service):
    result, store = run_workflow(tmp_path, ScriptedAgent(kinds=["functional"], no_oracle=True), service[0])
    assert result["state"] == "needs_changes"
    assert any("oracle" in blocker for blocker in result["design_blockers"])
    with pytest.raises(ValueError):
        approve(Path(result["baseline"]), "reviewer", store.root / "execution_report.json")


def test_critic_can_block_but_cannot_approve(tmp_path, service):
    result, store = run_workflow(tmp_path, ScriptedAgent(kinds=["functional"], blocker=True), service[0])
    baseline = Path(result["baseline"])
    assert result["state"] == "needs_changes"
    assert verify(baseline)["state"] == "draft"
    with pytest.raises(ValueError, match="critic"):
        approve(baseline, "reviewer", store.root / "execution_report.json")


def test_hallucinated_quotes_and_code_only_expectations_rejected():
    model = knowledge()
    model.requirements[0].evidence[0].quote = "invented claim"
    with pytest.raises(ValueError, match="evidence"):
        validate_knowledge(model, bundle())
    sources = bundle()
    sources.documents[0].kind = "code"
    with pytest.raises(ValueError, match="normative"):
        validate_knowledge(knowledge(), sources)


def test_mislabeled_existing_test_evidence_is_implementation_through_whole_workflow(tmp_path, service):
    agent = ScriptedAgent(kinds=["functional"])
    result, _ = run_workflow(tmp_path, agent, service[0], sources=[
        {"id": "prd", "kind": "existing_tests", "paths": ["prd.md"]}])
    assert result["state"] == "ready_for_human_review"
    baseline = Path(result["baseline"])
    assert verify(baseline)["schema_version"] == "2.0"
    known = json.loads((baseline / "knowledge.json").read_text(encoding="utf-8"))
    plan = json.loads((baseline / "test_plan.json").read_text(encoding="utf-8"))
    cases = json.loads((baseline / "cases/cases.json").read_text(encoding="utf-8"))
    assert known["requirements"][0]["expectation_basis"] == "implementation"
    assert plan["scenarios"][0]["expectation_basis"] == "implementation"
    assert all(a["expectation_basis"] == "implementation" for a in cases[0]["oracle"]["steps"][0]["assertions"])
    assert cases[0]["oracle"]["steps"][0]["assertions"][1]["expected_json"] == "5"
    assert "analysis.correction" not in agent.calls


@pytest.mark.parametrize("valid_correction", [True, False])
def test_analyzer_correction_is_bounded_and_never_accepts_fabricated_quotes(valid_correction):
    from tda.application.stages import ProjectAnalyzer, AnalysisValidationError
    class Agent:
        def __init__(self):
            self.calls = []

        def propose(self, stage, instructions, context, output):
            self.calls.append(stage)
            result = knowledge()
            if stage == "analysis" or not valid_correction:
                result.requirements[0].evidence[0].quote = "fabricated quote"
            else:
                assert "evidence" in context["validation_feedback"]
            return result
    agent = Agent()
    analyzer = ProjectAnalyzer(agent)
    if valid_correction:
        assert analyzer.analyze(bundle(), "project", "sha").requirements[0].id == "R1"
    else:
        with pytest.raises(AnalysisValidationError) as error:
            analyzer.analyze(bundle(), "project", "sha")
        assert error.value.code == "ANALYSIS_VALIDATION"
        assert "Project Analyzer" in error.value.safe_message
    assert agent.calls == ["analysis", "analysis.correction"]


def test_deferred_runner_scope_does_not_block_http_baseline_but_is_visible(tmp_path, service):
    class Agent(ScriptedAgent):
        def propose(self, stage, instructions, context, output):
            result = super().propose(stage, instructions, context, output)
            if stage == "analysis":
                result.requirements.append(RequirementNode(id="R-browser", title="Click UI",
                    execution_scope="browser", scope_reason="Browser adapter is unavailable",
                    acceptance_criteria=[TEXT], evidence=[evidence()]))
                result.open_questions.append("Browser adapter is unavailable")
            return result
    result, _ = run_workflow(tmp_path, Agent(kinds=["functional"]), service[0])
    assert result["state"] == "ready_for_human_review"
    baseline = Path(result["baseline"])
    coverage = json.loads((baseline/'coverage.json').read_text(encoding='utf-8'))
    assert coverage['metrics']['requirement']['total'] == 1
    assert coverage['metrics']['requirement']['total_discovered'] == 2
    assert coverage['metrics']['requirement']['excluded'] == ['R-browser']
    gate = json.loads((baseline/'review_gate.json').read_text(encoding='utf-8'))
    assert gate['deferred_requirements'][0]['id'] == 'R-browser'
    assert gate['open_questions'] == ['Browser adapter is unavailable']
    assert not gate['design_blockers']


def test_explicit_blocking_question_still_blocks_approval(tmp_path, service):
    class Agent(ScriptedAgent):
        def propose(self, stage, instructions, context, output):
            result = super().propose(stage, instructions, context, output)
            if stage == 'analysis':
                result.blocking_questions = ['Unresolved HTTP acceptance contradiction']
            return result
    result, _ = run_workflow(tmp_path, Agent(kinds=['functional']), service[0])
    assert result['state'] == 'needs_changes'
    assert 'Unresolved HTTP acceptance contradiction' in result['design_blockers']


def test_dependency_and_k6_requirements_are_deferred_and_planner_cannot_target_them():
    from tda.application.stages import classify_runner_scope
    from tda.domain.rules import validate_plan
    sources = bundle()
    sources.documents[0].kind = 'code'
    sources.documents[0].path = 'requirements.txt'
    model = knowledge()
    model.requirements[0].expectation_basis = 'implementation'
    classify_runner_scope(model, sources)
    assert model.requirements[0].execution_scope == 'dependency'
    planned = scenario()
    planned.expectation_basis = 'implementation'
    with pytest.raises(ValueError, match='requirement scope'):
        validate_plan(Plan(scenarios=[planned], rationale='', open_questions=[]), model, sources, ['functional'])
    sources.documents[0].path = 'performance/k6.js'
    sources.documents[0].content = 'import http from "k6/http";'
    model.requirements[0].evidence[0].quote = sources.documents[0].content
    classify_runner_scope(model, sources)
    assert model.requirements[0].execution_scope == 'load'


def test_exception_unit_scope_with_existing_test_evidence():
    from tda.application.stages import classify_runner_scope
    sources = bundle()
    sources.documents[0].kind = 'existing_tests'
    sources.documents[0].path = 'test_calculator.py'
    sources.documents[0].content = 'with pytest.raises(ValueError): divide(1, 0)'
    model = knowledge()
    model.requirements[0].expectation_basis = 'implementation'
    model.requirements[0].evidence[0].quote = sources.documents[0].content
    classify_runner_scope(model, sources)
    assert model.requirements[0].execution_scope == 'unit'


def test_dom_error_requirement_with_server_exception_evidence_stays_browser():
    from tda.application.stages import classify_runner_scope
    sources=SourceBundle(documents=[SourceDocument(id='js',kind='code',path='static/app.js',sha256='x',content='error.textContent = requestError.message;'),
        SourceDocument(id='py',kind='code',path='calculator.py',sha256='x',content='raise ValueError("Cannot divide by zero")')],operations=[])
    model=knowledge();requirement=model.requirements[0];requirement.expectation_basis='implementation'
    requirement.evidence=[Evidence(source_id='js',quote=sources.documents[0].content),Evidence(source_id='py',quote=sources.documents[1].content)]
    classify_runner_scope(model,sources)
    assert requirement.execution_scope=='browser'
    validate_knowledge(model,SourceBundle(documents=sources.documents+bundle().documents,operations=[]))


def test_pure_function_exception_evidence_is_unit_even_if_model_calls_it_browser():
    from tda.application.stages import classify_runner_scope
    sources=SourceBundle(documents=[SourceDocument(id='py',kind='code',path='calculator.py',sha256='x',content='raise ValueError("Cannot divide by zero")')],operations=[])
    model=knowledge();requirement=model.requirements[0];requirement.expectation_basis='implementation';requirement.execution_scope='browser';requirement.scope_reason='UI contract'
    requirement.evidence=[Evidence(source_id='py',quote=sources.documents[0].content)]
    classify_runner_scope(model,sources)
    assert requirement.execution_scope=='unit'


def test_code_only_latency_scenario_is_rejected_before_generation():
    from tda.domain.rules import validate_plan
    sources = bundle()
    sources.documents[0].kind = 'code'
    model = knowledge()
    model.requirements[0].expectation_basis = 'implementation'
    planned = scenario('performance')
    planned.expectation_basis = 'implementation'
    with pytest.raises(ValueError, match='normative'):
        validate_plan(Plan(scenarios=[planned], rationale='', open_questions=[]), model, sources, ['performance'])


def test_only_deferred_requirements_cannot_approve_empty_http_suite(tmp_path, service):
    class Agent(ScriptedAgent):
        def propose(self, stage, instructions, context, output):
            if stage == 'planning':
                return Plan(scenarios=[], rationale='No HTTP requirements', open_questions=[])
            result = super().propose(stage, instructions, context, output)
            if stage == 'analysis':
                result.requirements[0].execution_scope = 'browser'
                result.requirements[0].scope_reason = 'Requires a browser adapter'
            return result
    result, _ = run_workflow(tmp_path, Agent(kinds=['functional']), service[0])
    assert result['state'] == 'needs_changes'
    assert 'No executable HTTP test cases were generated' in result['design_blockers']


def test_labeled_code_expectations_are_valid_but_cannot_claim_slo():
    sources = bundle()
    sources.documents[0].kind = "code"
    model = knowledge()
    model.requirements[0].expectation_basis = "implementation"
    validate_knowledge(model, sources)
    case = executable()
    for assertion in case.oracle.steps[0].assertions:
        assertion.expectation_basis = "implementation"
    assert Validator().validate([case], sources).valid_case_ids == [case.id]
    from tda.domain.contracts import ApiOperation
    sources.operations = [ApiOperation(id="documented", method="GET", path="/documented", source_id="prd:prd.md")]
    assert Validator().validate([case], sources).valid_case_ids == [case.id]
    case.oracle.steps[0].assertions[0].evidence[0].quote = "fabricated code"
    assert not Validator().validate([case], sources).valid_case_ids
    perf = executable("performance")
    for assertion in perf.oracle.steps[0].assertions:
        assertion.expectation_basis = "implementation"
    assert not Validator().validate([perf], sources).valid_case_ids


def test_validator_detects_cycle_and_requires_ai_ground_truth():
    first, second = executable(), executable("ai_evaluation")
    first.depends_on = [second.id]
    second.depends_on = [first.id]
    second.oracle.steps[0].assertions = second.oracle.steps[0].assertions[:1]
    report = Validator().validate([first, second], bundle())
    assert not report.valid_case_ids
    assert {issue.code for issue in report.issues}.issuperset({"DEPENDENCY_CYCLE", "GROUND_TRUTH_REQUIRED"})


def test_repair_cannot_change_identity_or_dependency():
    class BadRepair:
        def propose(self, stage, instructions, context, output):
            case = generated()
            case.depends_on = ["other"]
            return RepairBatch(cases=[case], rationale="unsafe change")
    with pytest.raises(ValueError, match="cannot change"):
        RepairAgent(BadRepair()).repair([executable()], [], bundle())


def test_source_reader_inventory_budget_and_credential_exclusion(tmp_path):
    spec = {"openapi": "3.0.3", "paths": {"/items/{id}": {"get": {"responses": {"200": {"description": "ok"}}}}}}
    (tmp_path / "api.yaml").write_text(yaml.safe_dump(spec))
    config = WorkflowConfig(project="demo", source_revision="x", model="model",
                            sources=[{"id": "api", "kind": "openapi", "paths": ["api.yaml"]}])
    sources = LocalSourceReader().collect(config, tmp_path)
    assert sources.operations[0].id == "GET /items/{id}"
    assert path_matches("/items/{id}", "/items/123")
    assert not path_matches("/items/{id}", "/items/123/extra")
    (tmp_path / ".env").write_text("SECRET=private")
    config.sources[0].paths = [".env"]
    with pytest.raises(ValueError, match="credential"):
        LocalSourceReader().collect(config, tmp_path)
    config.sources[0].paths = ["../outside"]
    with pytest.raises(ValueError, match="root"):
        LocalSourceReader().collect(config, tmp_path)


def test_openai_structured_call_and_budget(tmp_path):
    calls = []
    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_parsed=knowledge(), status="completed", id="response-1",
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20))
    client = SimpleNamespace(responses=SimpleNamespace(parse=parse))
    store = LocalArtifactStore(tmp_path / "run")
    provider = OpenAIStructuredAgent("test-key", "model", store, max_calls=1, client=client)
    assert provider.propose("analysis", "instructions", {}, KnowledgeModel) == knowledge()
    assert calls[0]["text_format"] is KnowledgeModel
    assert calls[0]["store"] is False
    with pytest.raises(AgentFailure, match="budget"):
        provider.propose("analysis", "instructions", {}, KnowledgeModel)
    assert len(calls) == 1


def test_provider_refusal_no_fallback_or_secret_logging(tmp_path):
    def parse(**kwargs):
        raise RuntimeError("test-key confidential source text")
    store = LocalArtifactStore(tmp_path / "run")
    provider = OpenAIStructuredAgent("test-key", "model", store,
                                    client=SimpleNamespace(responses=SimpleNamespace(parse=parse)))
    with pytest.raises(AgentFailure) as raised:
        provider.propose("analysis", "instructions", {}, KnowledgeModel)
    assert "test-key" not in str(raised.value)
    assert "confidential" not in (store.root / "events.jsonl").read_text()


def test_all_provider_output_schemas_are_closed_objects():
    from openai.lib._pydantic import to_strict_json_schema
    for model in (KnowledgeModel, Plan, GeneratedBatch, OracleBatch, RepairBatch, CriticReview):
        schema = to_strict_json_schema(model)
        def check(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    assert node.get("additionalProperties") is False
                    assert set(node["required"]) == set(node["properties"])
                for value in node.values():
                    check(value)
            elif isinstance(node, list):
                for value in node:
                    check(value)
        check(schema)


def test_coverage_gap_replans_additively_with_bounded_rounds(tmp_path, service):
    class GapAgent(ScriptedAgent):
        planning_round = 0

        def propose(self, stage, instructions, context, output):
            if stage == "analysis":
                model = knowledge()
                model.requirements.append(RequirementNode(id="R2", title="Second requirement",
                                                          acceptance_criteria=[TEXT], evidence=[evidence()]))
                return model
            if stage == "planning":
                self.planning_round += 1
                planned = scenario("functional", "first" if self.planning_round == 1 else "second")
                planned.requirement_ids = ["R1" if self.planning_round == 1 else "R2"]
                if self.planning_round == 2:
                    assert context["coverage_gaps"]["coverage"]["metrics"]["requirement"]["missing"] == ["R2"]
                    assert context["existing_scenarios"][0]["id"] == "first"
                return Plan(scenarios=[planned], rationale="fill gap", open_questions=[])
            if stage.startswith("generation."):
                case = generated()
                case.scenario_id = context["scenarios"][0]["id"]
                case.id = "case-" + case.scenario_id
                return GeneratedBatch(cases=[case], open_questions=[])
            return super().propose(stage, instructions, context, output)
    agent = GapAgent(kinds=["functional"])
    result, _ = run_workflow(tmp_path, agent, service[0], max_rounds=2)
    assert result["state"] == "ready_for_human_review"
    assert result["case_count"] == 2
    assert agent.planning_round == 2


def test_cleanup_runs_on_defect_and_dependents_are_blocked():
    class RecordingExecutor(HttpSuiteExecutor):
        def __init__(self):
            super().__init__()
            self.paths = []

        def _request(self, spec):
            self.paths.append(spec["path"])
            return 200, {"result": 6}, 1

    first, second = executable(), executable("integration")
    first.cleanup = [request("/cleanup")]
    second.depends_on = [first.id]
    executor = RecordingExecutor()
    results = executor.execute([first.model_dump(), second.model_dump()], "http://127.0.0.1:1")["results"]
    assert results[0]["status"] == "defect"
    assert results[1]["status"] == "blocked"
    assert executor.paths == ["/sum", "/cleanup"]


def test_cooperative_cancellation_stops_requests():
    from threading import Event
    cancellation = Event()
    cancellation.set()
    report = HttpSuiteExecutor(cancellation).execute([executable().model_dump()], "http://127.0.0.1:1")
    assert report["results"][0]["status"] == "environment_error"
    assert not report["passed"]


def test_non_json_response_cannot_satisfy_json_null_oracle(monkeypatch):
    class Response:
        code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            return b"not a JSON document"

    opener = SimpleNamespace(open=lambda *args, **kwargs: Response())
    monkeypatch.setattr("tda.runner.build_opener", lambda *args: opener)
    case = executable()
    assertion = case.oracle.steps[0].assertions[1]
    assertion.pointer, assertion.expected_json = "", "null"
    report = HttpSuiteExecutor().execute([case.model_dump()], "http://127.0.0.1:1")
    assert report["results"][0]["status"] == "defect"


def test_explicit_credential_scope_does_not_inherit_global_worker_token(monkeypatch):
    monkeypatch.setenv("TDA_AUTH_TOKEN", "global-worker-secret")
    case = executable()
    case.steps[0].auth = "bearer_env"
    report = HttpSuiteExecutor(auth_token=None).execute([case.model_dump()], "http://127.0.0.1:1")
    assert report["results"][0]["status"] == "blocked"
    assert "global-worker-secret" not in json.dumps(report)


def test_planner_corrects_invalid_reference_without_relaxing_validation():
    from tda.application.stages import TestPlanner, PlanValidationError
    class Planner:
        calls = []
        always_invalid = False
        def propose(self, stage, instructions, context, output):
            self.calls.append(stage)
            planned = scenario()
            if stage == "planning" or self.always_invalid:
                planned.operation_ids = ["GET /sum"]
            else:
                assert context["reference_catalog"]["operation_ids"] == []
                assert "operation references" in context["validation_feedback"]
            return Plan(scenarios=[planned], rationale="coverage", open_questions=[])
    config = WorkflowConfig(project="demo", source_revision="x", model="model", sources=[
        {"id": "prd", "kind": "prd", "paths": ["prd.md"]}])
    provider = Planner()
    result = TestPlanner(provider).plan(knowledge(), bundle(), config, [], None)
    assert result.scenarios[0].operation_ids == []
    assert provider.calls == ["planning", "planning.correction"]
    provider.always_invalid = True
    with pytest.raises(PlanValidationError) as failure:
        TestPlanner(provider).plan(knowledge(), bundle(), config, [], None)
    assert failure.value.code == "PLAN_VALIDATION"


def test_provider_saves_structured_proposal_for_semantic_failure_debugging(tmp_path):
    store = LocalArtifactStore(tmp_path / "run")
    client = SimpleNamespace(responses=SimpleNamespace(parse=lambda **kwargs:
        SimpleNamespace(output_parsed=Plan(scenarios=[scenario()], rationale="coverage", open_questions=[]),
                        status="completed", id="response-test", usage=None)))
    agent = OpenAIStructuredAgent("test-key", "test-model", store, client=client)
    agent.propose("planning", "instructions", {}, Plan)
    saved = json.loads((store.root / "llm_outputs/01-planning.json").read_text())
    assert saved["scenarios"][0]["id"] == "functional"
    assert "test-key" not in (store.root / "llm_outputs/01-planning.json").read_text()


def test_planner_retains_valid_batch_when_correction_only_drops_deferred_scenario():
    from tda.application.stages import TestPlanner
    model=knowledge()
    model.requirements.append(RequirementNode(id='D',title='Dependencies',execution_scope='dependency',
        scope_reason='No dependency adapter',acceptance_criteria=[TEXT],evidence=[evidence()]))
    invalid=scenario(identifier='dependency');invalid.requirement_ids=['D'];invalid.runner='unit'
    class Agent:
        calls=[]
        def propose(self,stage,instructions,context,output):
            self.calls.append(stage)
            if stage=='planning':return Plan(scenarios=[scenario(),invalid],rationale='initial',open_questions=[])
            assert context['existing_scenarios']==[]
            assert [item['id'] for item in context['preserved_scenarios']]==['functional']
            assert context['invalid_scenarios'][0]['deferred']
            return Plan(scenarios=[],rationale='drop unsupported dependency',open_questions=[])
    config=WorkflowConfig(project='demo',source_revision='x',model='fake',sources=[{'id':'prd','kind':'prd','paths':['prd.md']}],runners=['http','unit'])
    agent=Agent();result=TestPlanner(agent).plan(model,bundle(),config,[],None)
    assert result.scenarios==[scenario()]
    assert agent.calls==['planning','planning.correction']


@pytest.mark.parametrize('mode',['empty','alter_valid','partial'])
def test_planner_patch_preserves_valid_and_requires_in_scope_corrections(mode):
    from tda.application.stages import TestPlanner,PlanValidationError
    invalid=scenario(identifier='bad');invalid.operation_ids=['unknown']
    class Agent:
        def propose(self,stage,instructions,context,output):
            if stage=='planning':return Plan(scenarios=[scenario(),invalid],rationale='initial',open_questions=[])
            corrected=scenario(identifier='bad')
            if mode=='empty':items=[]
            elif mode=='alter_valid':
                changed=scenario();changed.priority='P2';items=[changed,corrected]
            else:items=[corrected]
            return Plan(scenarios=items,rationale='patch',open_questions=[])
    config=WorkflowConfig(project='demo',source_revision='x',model='fake',sources=[{'id':'prd','kind':'prd','paths':['prd.md']}])
    if mode=='partial':
        result=TestPlanner(Agent()).plan(knowledge(),bundle(),config,[],None)
        assert [item.id for item in result.scenarios]==['functional','bad']
    else:
        with pytest.raises(PlanValidationError):TestPlanner(Agent()).plan(knowledge(),bundle(),config,[],None)


def test_initial_empty_plan_with_runnable_requirements_is_rejected():
    from tda.application.stages import TestPlanner,PlanValidationError
    class Agent:
        calls=[]
        def propose(self,stage,*args):
            self.calls.append(stage);return Plan(scenarios=[],rationale='empty',open_questions=[])
    config=WorkflowConfig(project='demo',source_revision='x',model='fake',sources=[{'id':'prd','kind':'prd','paths':['prd.md']}])
    agent=Agent()
    with pytest.raises(PlanValidationError):TestPlanner(agent).plan(knowledge(),bundle(),config,[],None)
    assert agent.calls==['planning','planning.correction']


def test_coverage_followup_ignores_committed_scenarios_and_keeps_new_additions():
    from tda.application.stages import TestPlanner
    committed=scenario(identifier='old')
    class Agent:
        calls=[]
        def propose(self,stage,*args):
            self.calls.append(stage)
            repeated=committed.model_copy(deep=True);repeated.title='Changed by model'
            return Plan(scenarios=[repeated,scenario(identifier='new')],rationale='coverage followup',open_questions=[])
    config=WorkflowConfig(project='demo',source_revision='x',model='fake',sources=[{'id':'prd','kind':'prd','paths':['prd.md']}])
    agent=Agent();result=TestPlanner(agent).plan(knowledge(),bundle(),config,[committed],{'coverage':{}})
    assert [item.id for item in result.scenarios]==['new']
    assert committed.title==scenario().title
    assert agent.calls==['planning']


def test_coverage_followup_with_only_committed_ids_returns_no_additions():
    from tda.application.stages import TestPlanner
    committed=scenario()
    class Agent:
        def propose(self,*args):return Plan(scenarios=[committed.model_copy(deep=True)],rationale='no new coverage',open_questions=[])
    config=WorkflowConfig(project='demo',source_revision='x',model='fake',sources=[{'id':'prd','kind':'prd','paths':['prd.md']}])
    assert TestPlanner(Agent()).plan(knowledge(),bundle(),config,[committed],{}).scenarios==[]


def test_http_request_body_validation_and_runner_assertion_rejection():
    case=executable();case.steps[0].body_json='{"value":5}'
    assert Validator().validate([case],bundle()).issues==[]
    case.oracle.steps[0].assertions[1].kind='return_equals'
    assert any(issue.code=='ORACLE_EVIDENCE' for issue in Validator().validate([case],bundle()).issues)


def test_passing_subset_is_not_reported_as_passing_full_baseline(tmp_path,service):
    class Agent(ScriptedAgent):
        def propose(self,stage,instructions,context,output):
            if stage=='oracle.correction':
                return OracleBatch.model_validate({'oracles':context['rejected_oracles'],'open_questions':[]})
            result=super().propose(stage,instructions,context,output)
            if stage=='oracle':
                result.oracles[1].steps[0].assertions[0].expected_json='unknown status'
            return result
    result,store=run_workflow(tmp_path,Agent(kinds=['functional','integration']),service[0])
    report=json.loads((store.root/'execution_report.json').read_text(encoding='utf-8'))
    assert report['subset_passed'] and not report['passed']
    assert report['eligible_case_count']==1 and report['total_case_count']==2
    assert report['excluded_case_ids']==['case-integration']
    assert result['state']=='needs_changes'
