from . import prompts
from .ports import AgentFailure
from pathlib import PurePosixPath
from ..domain.contracts import (ExecutableCase, GeneratedBatch, KnowledgeModel, OracleBatch,
                                RepairBatch, TestPlan)
from ..domain.rules import unique, validate_knowledge, validate_plan, normalize_evidence_quotes, check_evidence
from .design_validation import generated_shape_feedback, normalize_string_oracles, assertion_fingerprints, preserves_browser_inputs


class AnalysisValidationError(ValueError):
    code = "ANALYSIS_VALIDATION"

    def __init__(self, reason):
        self.safe_message = "Project Analyzer trả về phân tích chưa hợp lệ: " + reason
        super().__init__(self.safe_message)


def classify_implementation_expectations(items, sources):
    """Downgrade code-only evidence; never elevate it to a business specification."""
    documents = {document.id: document for document in sources.documents}
    for item in items:
        if item.expectation_basis != "specification" or not item.evidence:
            continue
        if all(claim.source_id in documents and claim.quote.strip()
               and claim.quote in documents[claim.source_id].content
               and documents[claim.source_id].kind in {"code", "db_schema", "existing_tests"}
               for claim in item.evidence):
            item.expectation_basis = "implementation"


def classify_runner_scope(knowledge, sources):
    documents = {document.id: document for document in sources.documents}
    for requirement in knowledge.requirements:
        if requirement.expectation_basis != "implementation":
            continue
        claims = [documents.get(claim.source_id) for claim in requirement.evidence]
        if not claims or any(document is None for document in claims):
            continue
        names = [PurePosixPath(document.path).name.lower() for document in claims]
        if all(name in {"requirements.txt", "pyproject.toml", "package.json", "pom.xml", "go.mod", "cargo.toml"}
               for name in names):
            requirement.execution_scope = "dependency"
            requirement.scope_reason = "Dependency/build metadata needs a dependency adapter, not an HTTP assertion."
        elif all('k6/' in document.content or 'from "k6"' in document.content or "from 'k6'" in document.content for document in claims):
            requirement.execution_scope = "load"
            requirement.scope_reason = "Existing k6 benchmark is a load-runner source, not a mandatory HTTP latency SLO."
        elif any(document.kind=='code' and document.path.endswith(('.js','.mjs','.cjs')) and
                 any(token in claim.quote for token in ('document.querySelector','addEventListener(','.textContent','event.preventDefault','event.submitter'))
                 for claim,document in zip(requirement.evidence,claims)):
            requirement.execution_scope = "browser"
            requirement.scope_reason = "DOM interaction requires a browser adapter, even when server exceptions are supporting evidence."
        elif any('raise ValueError' in claim.quote or 'pytest.raises(' in claim.quote for claim in requirement.evidence) and all(
                document.kind in {"code", "existing_tests"} and document.path.endswith('.py') and not any(token in document.content for token in ('FastAPI(', '@app.', 'Flask('))
                for document in claims):
            requirement.execution_scope = "unit"
            requirement.scope_reason = "A Python exception is observable at function level; its HTTP mapping is not evidenced."


class PlanValidationError(ValueError):
    code = "PLAN_VALIDATION"

    def __init__(self, reason):
        self.safe_message = "Test Planner trả về plan chưa hợp lệ: " + reason
        super().__init__(self.safe_message)


class ProjectAnalyzer:
    def __init__(self, agent):
        self.agent = agent

    def analyze(self, sources, project, revision):
        context = {"project": project, "revision": revision, "sources": sources.model_dump()}
        for attempt in range(2):
            try:
                knowledge = self.agent.propose("analysis" if attempt == 0 else "analysis.correction",
                                               prompts.ANALYZE + '\nPhân loại requirement category: functional, non_functional, business_rule, input_constraint, validation, error_handling, security, integration. Mỗi requirement có priority và clarity CONFIRMED/AMBIGUOUS/INCOMPLETE. Thiếu thông tin không tự xác nhận hoặc suy đoán acceptance criteria. Keep output concise: short exact quotes, no whole-file evidence; return complete JSON.', context, KnowledgeModel)
            except AgentFailure as error:
                if attempt or error.error_kind not in ('validation','incomplete'):raise
                context['output_validation_feedback']=error.validation_errors
                context['correction_instructions']='Previous output could not be parsed. Return complete compact JSON with short exact quotes and all grounded requirement groups.'
                continue
            for node in knowledge.requirements+knowledge.features+knowledge.risks:
                normalize_evidence_quotes(node.evidence,sources)
            classify_implementation_expectations(knowledge.requirements, sources)
            classify_runner_scope(knowledge, sources)
            try:
                validate_knowledge(knowledge, sources)
                return knowledge
            except ValueError as error:
                if attempt:
                    raise AnalysisValidationError(str(error)) from None
                context["rejected_knowledge"] = knowledge.model_dump()
                context["validation_feedback"] = str(error)
                documents={document.id:document for document in sources.documents}
                invalid=[]
                for node in knowledge.requirements+knowledge.features+knowledge.risks:
                    for claim in node.evidence:
                        try:check_evidence([claim],sources)
                        except ValueError:
                            document=documents.get(claim.source_id)
                            invalid.append({'node_id':node.id,'rejected_quote':claim.quote,'source_id':claim.source_id,
                                'actual_source':document.content if document else None})
                context['invalid_evidence_details']=invalid
                context["correction_instructions"] = (
                    "Correct invalid references/evidence using only supplied sources. "
                    "Use invalid_evidence_details to fix each rejected quote AND any acceptance criterion contradicted by its actual_source. Preserve correct nodes. "
                    "Quotes must match exactly. Existing tests are implementation, never specification. "
                    "Do not invent requirements or remove grounded findings just to pass validation.")


class TestPlanner:
    def __init__(self, agent):
        self.agent = agent

    def plan(self, knowledge, sources, config, existing, gaps):
        context = {
            "knowledge": knowledge.model_dump(), "sources": sources.model_dump(),
            "enabled_kinds": config.kinds, "existing_scenarios": [s.model_dump() for s in existing],
            "coverage_gaps": gaps, "remaining_case_budget": config.max_cases - len(existing),
            "enabled_runners": config.runners,
            "requirement_scopes": {requirement.id:requirement.execution_scope for requirement in knowledge.requirements},
            "reference_catalog": {"requirement_ids": [r.id for r in knowledge.requirements
                if r.execution_scope in runnable_scopes(config.runners)],
                                  "feature_ids": [f.id for f in knowledge.features],
                                  "risk_ids": [r.id for r in knowledge.risks],
                                  "operation_ids": [o.id for o in sources.operations]}}
        preserved = {}
        required_corrections = set()
        committed_ids={scenario.id for scenario in existing}
        for attempt in range(2):
            plan = self.agent.propose("planning" if attempt == 0 else "planning.correction",
                                       prompts.PLAN + ("\nThis call corrects the rejected current batch, not coverage replanning. "
                                       "The server retains preserved_scenarios. Return only corrections to invalid "
                                       "in-scope scenarios, with their original IDs. Omit deferred scenarios. "
                                       "Never assume rejected scenarios are committed or have generated tests."
                                       if attempt else ""), context, TestPlan)
            # Coverage follow-up sometimes repeats the committed plan. Those
            # scenarios already have generated cases and must neither be replaced
            # nor mistaken for invalid additions requiring correction.
            if committed_ids:
                repeated=[scenario.id for scenario in plan.scenarios if scenario.id in committed_ids]
                if repeated:context['ignored_committed_scenario_ids']=sorted(set(repeated))
                plan.scenarios=[scenario for scenario in plan.scenarios if scenario.id not in committed_ids]
            classify_implementation_expectations(plan.scenarios, sources)
            try:
                if attempt:
                    unique(plan.scenarios, 'scenario')
                    replacements = {scenario.id: scenario for scenario in plan.scenarios}
                    for identifier, original in preserved.items():
                        if identifier in replacements and replacements[identifier] != original:
                            raise ValueError('correction must not change preserved valid scenarios')
                    if required_corrections - replacements.keys():
                        missing=sorted(required_corrections-replacements.keys())
                        raise ValueError('Bản correction còn thiếu scenario trong scope: '+', '.join(missing))
                    plan.scenarios = list(preserved.values()) + [scenario for scenario in plan.scenarios if scenario.id not in preserved]
                validate_plan(plan, knowledge, sources, config.kinds, config.runners)
                if not existing and not plan.scenarios and any(requirement.execution_scope in runnable_scopes(config.runners) for requirement in knowledge.requirements):
                    raise ValueError('initial plan omitted all runnable requirements')
                if {s.id for s in existing}.intersection(s.id for s in plan.scenarios):
                    raise ValueError("replanning may only add new scenario IDs")
                if len(existing) + len(plan.scenarios) > config.max_cases:
                    raise ValueError("planner exceeded scenario budget")
                return plan
            except ValueError as error:
                if attempt:
                    raise PlanValidationError(str(error)) from None
                # The rejected batch is not committed to existing_scenarios. Keep
                # its valid entries here so a partial correction cannot erase them.
                counts = {scenario.id: sum(other.id == scenario.id for other in plan.scenarios) for scenario in plan.scenarios}
                requirements = {requirement.id: requirement for requirement in knowledge.requirements}
                invalid = []
                for scenario in plan.scenarios:
                    try:
                        validate_plan(TestPlan(scenarios=[scenario], rationale=plan.rationale, open_questions=[]),
                                      knowledge, sources, config.kinds, config.runners)
                        if counts[scenario.id] != 1 or scenario.id in {item.id for item in existing}:
                            raise ValueError('duplicate scenario ID')
                        preserved[scenario.id] = scenario.model_copy(deep=True)
                    except ValueError as scenario_error:
                        deferred = bool(scenario.requirement_ids) and all(identifier in requirements and
                            requirements[identifier].execution_scope not in runnable_scopes(config.runners)
                            for identifier in scenario.requirement_ids)
                        invalid.append({'scenario': scenario.model_dump(), 'reason': str(scenario_error), 'deferred': deferred})
                        if not deferred:required_corrections.add(scenario.id)
                context["rejected_plan"] = plan.model_dump()
                context["validation_feedback"] = str(error)
                context['preserved_scenarios'] = [scenario.model_dump() for scenario in preserved.values()]
                context['invalid_scenarios'] = invalid
                context["correction_instructions"] = (
                    "Correct only the invalid plan; never change requirements or invent evidence. "
                    "Use references from reference_catalog only. If operation_ids catalog is empty, "
                    "every scenario.operation_ids must be []. HTTP paths are not catalog IDs. "
                    "Label code-derived scenarios expectation_basis=implementation; specification "
                    "scenarios must quote PRD/SRS/OpenAPI. "
                    "This is correction of the rejected current batch, NOT coverage replanning. "
                    "preserved_scenarios are retained by the system; do not change them. "
                    "Return corrected invalid in-scope scenarios using their same IDs. "
                    "Omit deferred scenarios; never map dependency metadata to unit tests. "
                    "existing_scenarios contains only batches committed before this call. "
                    "Do not assume the rejected batch has already generated tests.")


class GenerationValidationError(ValueError):
    code='GENERATION_VALIDATION'
    def __init__(self,reason):
        self.safe_message='Generator trả về test case chưa hợp lệ: '+reason
        super().__init__(self.safe_message)


class SpecializedGenerator:
    def __init__(self, agent, kind):
        self.agent, self.kind = agent, kind

    def generate(self, scenarios, knowledge, sources, existing, allow_correction=True):
        batch = self.agent.propose(f"generation.{self.kind}", prompts.GENERATE.format(kind=self.kind), {
            "scenarios": [s.model_dump() for s in scenarios], "knowledge": knowledge.model_dump(),
            "sources": sources.model_dump(), "existing_cases": [c.model_dump() for c in existing]}, GeneratedBatch)
        scenario_ids = {s.id for s in scenarios}
        old_ids = {c.id for c in existing}
        runners = {scenario.id: scenario.runner for scenario in scenarios}
        for attempt in range(2):
            try:
                unique(batch.cases,'generated case')
                if any(case.scenario_id not in scenario_ids or case.kind!=self.kind or case.id in old_ids for case in batch.cases):
                    raise ValueError('Scenario ID, test kind hoặc case ID không đúng batch được giao.')
                if any(case.runner!=runners[case.scenario_id] for case in batch.cases):
                    raise ValueError('Runner không khớp scenario được giao.')
                break
            except ValueError as error:
                if attempt or not allow_correction:raise GenerationValidationError(str(error)) from None
                batch=self.agent.propose(f'generation.{self.kind}.identity_correction',prompts.GENERATE.format(kind=self.kind)+
                    '\nReject this uncommitted batch and generate ONLY supplied scenarios. Historical review '
                    'IDs are not current IDs. Respect assigned kind, runner and new-case-ID constraints.',
                    {'scenarios':[scenario.model_dump() for scenario in scenarios],'knowledge':knowledge.model_dump(),
                     'sources':sources.model_dump(),'existing_cases':[case.model_dump() for case in existing],
                     'rejected_batch':batch.model_dump(),'validation_feedback':str(error)},GeneratedBatch)
        invalid={case.id:generated_shape_feedback(case) for case in batch.cases if generated_shape_feedback(case)}
        if invalid and allow_correction:
            originals={case.id:case for case in batch.cases}
            correction=self.agent.propose(f'generation.{self.kind}.correction',prompts.GENERATE.format(kind=self.kind)+
                '\nCorrect only structural errors for these case IDs before execution. Preserve scenario_id, runner, kind, '
                'dependencies, cleanup and existing request/action steps. Add missing requests from supplied source only. '
                'No expected results or observed product output is supplied.',
                {'cases':[originals[identifier].model_dump() for identifier in invalid], 'validation_feedback':invalid,
                 'scenarios':[scenario.model_dump() for scenario in scenarios], 'sources':sources.model_dump()},GeneratedBatch)
            unique(correction.cases,'corrected case')
            if any(case.id not in invalid for case in correction.cases):raise ValueError('generator correction changed case IDs')
            replacements={}
            for case in correction.cases:
                old=originals[case.id]
                fields=('scenario_id','runner','kind','depends_on','cleanup','repetitions')
                if any(getattr(old,field)!=getattr(case,field) for field in fields) or old.steps and old.steps!=case.steps or not preserves_browser_inputs(old.browser,case.browser):
                    raise ValueError('generator correction changed preserved case inputs')
                replacements[case.id]=case
            batch.cases=[replacements.get(case.id,case) for case in batch.cases]
            batch.open_questions.extend(correction.open_questions)
            batch.blocking_questions.extend(correction.blocking_questions)
        missing = scenario_ids - {case.scenario_id for case in batch.cases}
        batch.open_questions.extend(f"Scenario {identifier} has no generated case" for identifier in sorted(missing))
        return batch


class GeneratorRegistry:
    def __init__(self, agent):
        self.generators = {kind: SpecializedGenerator(agent, kind) for kind in (
            "functional", "integration", "security", "performance", "ai_evaluation")}

    def resolve(self, kind):
        return self.generators[kind]


class OracleEngine:
    def __init__(self, agent):
        self.agent = agent

    def attach(self, generated, scenarios, sources, allow_correction=True):
        batch = self.agent.propose("oracle", prompts.ORACLE, {
            "cases": [case.model_dump() for case in generated],
            "scenarios": [scenario.model_dump() for scenario in scenarios],
            "sources": [doc.model_dump() for doc in sources.documents]}, OracleBatch)
        normalize_string_oracles(batch.oracles)
        classify_implementation_expectations(
            [assertion for oracle in batch.oracles for step in oracle.steps for assertion in step.assertions], sources)
        ids = [oracle.case_id for oracle in batch.oracles]
        if len(ids) != len(set(ids)) or not set(ids).issubset(case.id for case in generated):
            raise ValueError("oracle returned duplicate or unknown case IDs")
        lookup = {oracle.case_id: oracle for oracle in batch.oracles}
        scenario_lookup = {scenario.id: scenario for scenario in scenarios}
        cases = []
        for case in generated:
            if case.id not in lookup:
                batch.open_questions.append(f"Missing evidenced oracle for {case.id}")
                continue
            scenario = scenario_lookup[case.scenario_id]
            cases.append(ExecutableCase(**case.model_dump(), oracle=lookup[case.id],
                                        requirement_ids=scenario.requirement_ids,
                                        feature_ids=scenario.feature_ids, risk_ids=scenario.risk_ids,
                                        operation_ids=scenario.operation_ids))
        if allow_correction and cases:
            from ..domain.rules import TestValidator
            codes={'ORACLE_STEPS','ORACLE_EVIDENCE','STATUS_ORACLE','SLO_REQUIRED','SLO_STEPS','UNIT_ORACLE'}
            issues=[issue for issue in TestValidator().validate(cases,sources).issues if issue.code in codes]
            invalid={issue.case_id for issue in issues}
            if invalid:
                rejected=[oracle for oracle in batch.oracles if oracle.case_id in invalid]
                correction=self.agent.propose('oracle.correction',prompts.ORACLE+
                    '\nCorrect structure/encoding before execution using only source evidence. '
                    'Keep every existing valid JSON assertion, its expected value, selector, evidence and basis. '
                    'You may relocate assertions to their correct action index and add missing sourced assertions. '
                    'Do not weaken or remove assertions. Never invent HTTP 422/500, body or framework defaults. '
                    'If a source does not establish an expectation, omit that case oracle and explain the gap.',
                    {'cases':[case.model_dump() for case in generated if case.id in invalid],
                     'rejected_oracles':[oracle.model_dump() for oracle in rejected],
                     'validation_feedback':[issue.model_dump() for issue in issues],
                     'sources':sources.model_dump()},OracleBatch)
                normalize_string_oracles(correction.oracles)
                classify_implementation_expectations([a for o in correction.oracles for s in o.steps for a in s.assertions],sources)
                corrected_ids=[oracle.case_id for oracle in correction.oracles]
                if len(corrected_ids)!=len(set(corrected_ids)) or not set(corrected_ids).issubset(invalid):
                    raise ValueError('oracle correction returned duplicate or unknown case IDs')
                original_claims=assertion_fingerprints(rejected);new_claims=assertion_fingerprints(correction.oracles)
                replacements={oracle.case_id:oracle for oracle in correction.oracles if not (original_claims[oracle.case_id]-new_claims[oracle.case_id])}
                for identifier in set(corrected_ids)-replacements.keys():
                    batch.open_questions.append('Không áp dụng bản sửa oracle của '+identifier+' vì đã thay đổi hoặc bỏ điều kiện kiểm tra được giữ nguyên.')
                for case in cases:
                    if case.id in replacements:case.oracle=replacements[case.id]
                batch.oracles=[replacements.get(oracle.case_id,oracle) for oracle in batch.oracles]
                batch.open_questions.extend(correction.open_questions)
                batch.blocking_questions.extend(correction.blocking_questions)
        return cases, batch


class RepairAgent:
    def __init__(self, agent):
        self.agent = agent

    def repair(self, cases, issues, sources):
        batch = self.agent.propose("repair", prompts.REPAIR, {
            "cases": [case.model_dump() for case in cases], "issues": [i.model_dump() for i in issues],
            "sources": sources.model_dump()}, RepairBatch)
        if {case.id for case in batch.cases} != {case.id for case in cases} or len(batch.cases) != len(cases):
            raise ValueError("repair must preserve exactly the requested case IDs")
        originals = {case.id: case for case in cases}
        replacements = []
        for repaired in batch.cases:
            original = originals[repaired.id]
            if (repaired.scenario_id, repaired.kind, repaired.depends_on, repaired.repetitions,
                len(repaired.steps), repaired.cleanup, repaired.runner, repaired.unit, repaired.browser, repaired.load, repaired.grading) != (
                original.scenario_id, original.kind, original.depends_on, original.repetitions,
                len(original.steps), original.cleanup, original.runner, original.unit, original.browser, original.load, original.grading):
                raise ValueError("repair cannot change scenario, kind, dependencies, repetition, cleanup or step count")
            replacements.append(ExecutableCase(**repaired.model_dump(), oracle=original.oracle,
                                                requirement_ids=original.requirement_ids,
                                                feature_ids=original.feature_ids, risk_ids=original.risk_ids,
                                                operation_ids=original.operation_ids))
        return replacements


def runnable_scopes(runners):
    scopes={'http'} if any(runner in runners for runner in ('http','ai','k6')) else set()
    for runner,scope in [('unit','unit'),('browser','browser'),('k6','load')]:
        if runner in runners:scopes.add(scope)
    return scopes
