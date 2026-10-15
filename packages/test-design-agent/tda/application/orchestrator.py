from .coverage import CoverageAnalyzer
from .ports import ArtifactStore, BaselinePublisher, SourceReader, StructuredAgent, TestExecutor
from .stages import GeneratorRegistry, OracleEngine, ProjectAnalyzer, RepairAgent, TestPlanner, runnable_scopes
from .quality import TestCritic
from ..domain.contracts import TestPlan, WorkflowConfig
from ..domain.rules import TestValidator
import json


class TestDesignOrchestrator:
    def __init__(self, agent: StructuredAgent, reader: SourceReader, executor: TestExecutor,
                 store: ArtifactStore, baselines: BaselinePublisher):
        self.agent, self.reader, self.executor = agent, reader, executor
        self.store, self.baselines = store, baselines
        self.analyzer, self.planner = ProjectAnalyzer(agent), TestPlanner(agent)
        self.generators, self.oracle, self.repair = GeneratorRegistry(agent), OracleEngine(agent), RepairAgent(agent)
        self.validator, self.coverage = TestValidator(), CoverageAnalyzer()
        self.critic = TestCritic(agent)

    def run(self, config: WorkflowConfig, config_directory, base_url=None, allow_mutations=False, case_review=None):
        try:
            if case_review:return self._review_case(config,config_directory,case_review)
            return self._run(config, config_directory, base_url, allow_mutations)
        except Exception as error:
            diagnostic = {"error_type": type(error).__name__}
            if getattr(error, "code", None) in {"PLAN_VALIDATION", "ANALYSIS_VALIDATION", "GENERATION_VALIDATION", "CASE_REVIEW_VALIDATION"}:
                diagnostic.update(error_code=error.code, message=error.safe_message)
            self.store.event("workflow", "failed", diagnostic)
            self.store.write("run_status.json", {"state": "failed", **diagnostic})
            raise

    def _run(self, config, directory, base_url, allow_mutations):
        self.store.write("config.json", config.model_dump())
        self.store.event("sources", "started", {})
        sources = self.reader.collect(config, directory)
        self.store.write("sources.json", sources.model_dump())
        self.store.event("sources", "completed", {"documents": len(sources.documents)})
        knowledge = self.analyzer.analyze(sources, config.project, config.source_revision)
        self.store.write("knowledge.json", knowledge.model_dump())
        scenarios, cases, generated = [], [], []
        questions = list(knowledge.open_questions)
        blocking_questions = list(knowledge.blocking_questions)
        gap_context = None
        for round_number in range(config.max_rounds):
            plan = self.planner.plan(knowledge, sources, config, scenarios, gap_context)
            if len(scenarios) + len(plan.scenarios) > config.max_cases:
                raise ValueError("planner exceeded scenario budget")
            scenarios.extend(plan.scenarios)
            questions.extend(plan.open_questions)
            blocking_questions.extend(plan.blocking_questions)
            new_generated = []
            for kind in config.kinds:
                selected = [scenario for scenario in plan.scenarios if scenario.kind == kind]
                if not selected:
                    continue
                batch = self.generators.resolve(kind).generate(selected, knowledge, sources, generated + new_generated,
                                                               allow_correction=config.max_repairs>0)
                new_generated.extend(batch.cases)
                questions.extend(batch.open_questions)
                blocking_questions.extend(batch.blocking_questions)
                self.store.write(f"rounds/{round_number}/generator-{kind}.json", batch.model_dump())
            if len(generated) + len(new_generated) > config.max_cases:
                raise ValueError("generators exceeded case budget")
            generated.extend(new_generated)
            if new_generated:
                attached, oracle_batch = self.oracle.attach(new_generated, scenarios, sources, allow_correction=config.max_repairs>0)
                cases.extend(attached)
                questions.extend(oracle_batch.open_questions)
                blocking_questions.extend(oracle_batch.blocking_questions)
                self.store.write(f"rounds/{round_number}/oracles.json", oracle_batch.model_dump())
            validation = self.validator.validate(cases, sources)
            for repair_number in range(config.max_repairs):
                repairable_ids = {issue.case_id for issue in validation.issues if issue.repairable}
                selected = [case for case in cases if case.id in repairable_ids]
                if not selected:
                    break
                replacements = self.repair.repair(selected, [i for i in validation.issues if i.case_id in repairable_ids], sources)
                lookup = {case.id: case for case in replacements}
                cases = [lookup.get(case.id, case) for case in cases]
                self.store.write(f"rounds/{round_number}/repair-{repair_number}.json", [c.model_dump() for c in replacements])
                validation = self.validator.validate(cases, sources)
            valid_ids = set(validation.valid_case_ids)
            execution = None if not config.reference_execution or not base_url and not (cases and all(case.runner=='unit' for case in cases)) else self.executor.execute(
                [case.model_dump() for case in cases if case.id in valid_ids], base_url, allow_mutations)
            if execution is not None:
                execution['subset_passed']=execution['passed']
                execution['total_case_count']=len(cases)
                execution['eligible_case_count']=len(valid_ids)
                execution['excluded_case_ids']=[case.id for case in cases if case.id not in valid_ids]
                execution['passed']=bool(cases) and execution['passed'] and len(valid_ids)==len(cases)
            coverage = self.coverage.analyze(knowledge, sources, cases, validation, execution, config.coverage, runnable_scopes(config.runners))
            self.store.write(f"rounds/{round_number}/plan.json", plan.model_dump())
            self.store.write(f"rounds/{round_number}/validation.json", validation.model_dump())
            self.store.write(f"rounds/{round_number}/execution.json", execution)
            self.store.write(f"rounds/{round_number}/coverage.json", coverage)
            self.store.event("coverage", "completed", {"round": round_number,
                                                        "design_satisfied": coverage["design_satisfied"]})
            if coverage["design_satisfied"] or not plan.scenarios or len(generated) >= config.max_cases:
                break
            gap_context = {"coverage": {key:value for key,value in coverage.items() if key!='execution_satisfied'}, "validation_issues": validation.model_dump(),
                           "open_questions": questions}
        return self._publish(config,knowledge,sources,scenarios,cases,validation,coverage,execution,questions,blocking_questions)

    def _review_case(self,config,directory,request):
        from ..domain.contracts import KnowledgeModel,SourceBundle,ExecutableCase,GeneratedBatch
        from ..domain.rules import validate_plan,validate_knowledge
        from .stages import classify_runner_scope,classify_implementation_expectations
        from .design_validation import generated_shape_feedback
        previous=request['artifacts'];knowledge=KnowledgeModel.model_validate(previous['knowledge.json'])
        old_sources=SourceBundle.model_validate(previous['sources.json'])
        fresh=self.reader.collect(config,directory);by_path={doc.path:doc for doc in fresh.documents}
        if any(doc.path not in by_path or by_path[doc.path].content!=doc.content for doc in old_sources.documents):
            raise CaseReviewValidationError('Source của baseline đã thay đổi hoặc không còn được chọn; cần sinh lại baseline từ source hiện tại.')
        documents=[doc.model_copy(deep=True) for doc in old_sources.documents]
        paths={doc.path for doc in documents};ids={doc.id for doc in documents}
        for doc in fresh.documents:
            if doc.path in paths:continue
            doc=doc.model_copy(deep=True)
            if doc.id in ids:doc.id='review-source-'+doc.sha256[:16]
            ids.add(doc.id);documents.append(doc)
        sources=SourceBundle(documents=documents,operations=old_sources.operations)
        classify_runner_scope(knowledge,sources);validate_knowledge(knowledge,sources)
        plan=TestPlan.model_validate(previous['test_plan.json']);scenarios=list(plan.scenarios)
        cases=[ExecutableCase.model_validate(case) for case in previous['cases/cases.json']]
        original=next((case for case in cases if case.id==request['case_id']),None)
        scenario=next((item for item in scenarios if original and item.id==original.scenario_id),None)
        self.store.write('config.json',config.model_dump());self.store.write('sources.json',sources.model_dump());self.store.write('knowledge.json',knowledge.model_dump())
        questions=list(previous['review_gate.json'].get('open_questions',[]));blocking=list(previous['review_gate.json'].get('blocking_questions',[]))
        action=request['action']
        if action=='supplement':
            plan=self.planner.plan(knowledge,sources,config,scenarios,{'requested_additions':request['feedback'],
                'existing_cases':[case.model_dump() for case in cases],
                'instruction':'Chỉ thêm scenario chưa có, không thay case hiện tại; không lặp lại cùng steps/input/oracle.'})
            if len(cases)+len(plan.scenarios)>config.max_cases:raise CaseReviewValidationError('Bổ sung vượt giới hạn số test case.')
            new_scenarios=plan.scenarios
            additions=[]
            for kind in config.kinds:
                selected=[item for item in new_scenarios if item.kind==kind]
                if selected:
                    batch=self.generators.resolve(kind).generate(selected,knowledge,sources,cases+additions,allow_correction=config.max_repairs>0)
                    additions.extend(batch.cases);questions.extend(batch.open_questions);blocking.extend(batch.blocking_questions)
            if not additions:raise CaseReviewValidationError('Chưa tìm được case mới có căn cứ để bổ sung. Bộ hiện tại được giữ nguyên.')
            if len(cases)+len(additions)>config.max_cases:raise CaseReviewValidationError('Case bổ sung vượt giới hạn của bộ test.')
            attached,oracle_batch=self.oracle.attach(additions,new_scenarios,sources,allow_correction=config.max_repairs>0)
            if len(attached)!=len(additions):raise CaseReviewValidationError('Case bổ sung chưa có đủ oracle có căn cứ.')
            old_ids={case.id for case in cases};old_scenarios={item.id for item in scenarios}
            if any(case.id in old_ids for case in attached) or any(item.id in old_scenarios for item in new_scenarios):
                raise CaseReviewValidationError('Case hoặc scenario bổ sung trùng ID với bộ hiện tại.')
            def signature(case):
                payload=case.model_dump();oracle=payload['oracle'];oracle.pop('case_id',None);oracle.pop('rationale',None)
                for step in oracle['steps']:
                    for assertion in step['assertions']:assertion.pop('evidence',None)
                return json.dumps({key:payload.get(key) for key in ('runner','kind','steps','unit','browser','load','grading','oracle')},sort_keys=True)
            signatures={signature(case) for case in cases}
            for case in attached:
                value=signature(case)
                if value in signatures:raise CaseReviewValidationError('Case bổ sung trùng nội dung với case đã có; cần đổi phạm vi hoặc test data.')
                signatures.add(value)
            cases.extend(attached);scenarios.extend(new_scenarios)
            questions.extend(plan.open_questions+oracle_batch.open_questions);blocking.extend(plan.blocking_questions+oracle_batch.blocking_questions)
        elif action in ('manual_edit','add_case'):
            changed=ExecutableCase.model_validate(request['case_data'])
            if changed.id!=request['case_id']:raise CaseReviewValidationError('Không được đổi case ID khi lưu.')
            criteria=request.get('acceptance_criteria','').strip()
            if criteria:
                from ..domain.contracts import Evidence
                document=next((doc for doc in reversed(sources.documents) if doc.kind=='prd' and criteria in doc.content),None)
                if not document:raise CaseReviewValidationError('Acceptance criteria chưa được lưu thành source.')
                claim=Evidence(source_id=document.id,quote=criteria)
                for step in changed.oracle.steps:
                    for assertion in step.assertions:
                        assertion.evidence=[claim];assertion.expectation_basis='specification'
            if action=='manual_edit':
                if not original or changed.scenario_id!=original.scenario_id or changed.kind!=original.kind or changed.runner!=original.runner:
                    raise CaseReviewValidationError('Sửa tay phải giữ case ID, scenario ID, kind và runner.')
                if request.get('scenario_data'):
                    from ..domain.contracts import PlannedScenario
                    updated=PlannedScenario.model_validate(request['scenario_data'])
                    if criteria:
                        updated.acceptance_criteria=[criteria];updated.evidence=[claim];updated.expectation_basis='specification'
                    if updated.id!=scenario.id or updated.kind!=scenario.kind or updated.runner!=scenario.runner:
                        raise CaseReviewValidationError('Scenario chỉnh tay phải giữ ID, kind và runner.')
                    validate_plan(TestPlan(scenarios=[updated],rationale='Maintainer edit',open_questions=[]),knowledge,sources,config.kinds,config.runners)
                    scenarios=[updated if item.id==scenario.id else item for item in scenarios]
                cases=[changed if case.id==original.id else case for case in cases]
            else:
                from ..domain.contracts import PlannedScenario
                added=PlannedScenario.model_validate(request['scenario_data'])
                if criteria:
                    added.acceptance_criteria=[criteria];added.evidence=[claim];added.expectation_basis='specification'
                if changed.scenario_id!=added.id or changed.kind!=added.kind or changed.runner!=added.runner:
                    raise CaseReviewValidationError('Test case phải khớp scenario về ID, kind và runner.')
                if any(case.id==changed.id for case in cases) or any(item.id==added.id for item in scenarios):
                    raise CaseReviewValidationError('Case ID hoặc scenario ID đã tồn tại.')
                if len(cases)>=config.max_cases:raise CaseReviewValidationError('Đã đạt giới hạn số test case.')
                validate_plan(TestPlan(scenarios=[added],rationale='Maintainer added case',open_questions=[]),knowledge,sources,config.kinds,config.runners)
                cases.append(changed);scenarios.append(added)
        elif action=='delete_case':
            if not original:raise CaseReviewValidationError('Không tìm thấy case cần xóa.')
            cases=[case for case in cases if case.id!=original.id]
            if not any(case.scenario_id==original.scenario_id for case in cases):scenarios=[item for item in scenarios if item.id!=original.scenario_id]
        elif action!='review_blocker':
            if not original or not scenario:raise CaseReviewValidationError('Không tìm thấy case hoặc scenario cần sửa.')
            context={'selected_case':original.model_dump(),'scenario':scenario.model_dump(),'sources':sources.model_dump(),
                'feedback':request['feedback'],'acceptance_criteria':request.get('acceptance_criteria',''),
                'required_case_id':original.id,'required_scenario_id':scenario.id}
            updated=self.agent.propose('review.planning',
                'Chỉnh acceptance criteria của duy nhất scenario đã chọn theo source và feedback. Giữ ID, runner, kind, '
                'priority và toàn bộ reference IDs. Trả một scenario. Không sửa source hoặc requirement khác; '
                'không suy đoán HTTP mapping hoặc exception message trên UI. Với UI error, phân biệt '
                'format/template do client tạo và exception của server. Có thể assert phần text cố định '
                'được source client chứng minh, thay vì yêu cầu một status code hoặc server message không có căn cứ.',context,TestPlan)
            if len(updated.scenarios)!=1:raise CaseReviewValidationError('Bản sửa phải chứa đúng một scenario được chọn.')
            replacement=updated.scenarios[0]
            fields=('id','runner','kind','priority','requirement_ids','feature_ids','risk_ids','operation_ids')
            if any(getattr(replacement,field)!=getattr(scenario,field) for field in fields):
                raise CaseReviewValidationError('Bản sửa không được thay ID, runner hoặc reference IDs của scenario.')
            classify_implementation_expectations([replacement],sources);validate_plan(updated,knowledge,sources,config.kinds,config.runners)
            context['scenario']=replacement.model_dump()
            correction_attempts=2 if config.max_repairs>0 else 1
            for attempt in range(correction_attempts):
                batch=self.agent.propose('review.generation' if not attempt else 'review.generation.correction',
                    'Chỉnh duy nhất test case đã chọn. Giữ case ID, scenario ID, kind, runner, dependencies, cleanup '
                    'và repetitions. Sửa steps/cấu hình theo source; không viết code hay đoán expected result. '
                    'Không sinh lại test case khác. Trả đúng một GeneratedCase.',context,GeneratedBatch)
                good=len(batch.cases)==1
                changed=batch.cases[0] if good else None
                fields=('id','scenario_id','kind','runner','depends_on','cleanup','repetitions')
                good=good and all(getattr(changed,field)==getattr(original,field) for field in fields)
                if good and not generated_shape_feedback(changed):break
                if attempt==correction_attempts-1:raise CaseReviewValidationError('Generator chưa giữ đúng case ID/runner hoặc cấu trúc test case khi chỉnh sửa.')
                context['rejected_batch']=batch.model_dump();context['validation_feedback']='Giữ đúng identity của case được chọn và sửa cấu trúc runner.'
            attached,oracle_batch=self.oracle.attach([changed],[replacement],sources,allow_correction=config.max_repairs>0)
            if len(attached)!=1:raise CaseReviewValidationError('Chưa tạo được oracle có căn cứ cho case được sửa; baseline cũ được giữ nguyên.')
            cases=[attached[0] if case.id==original.id else case for case in cases]
            scenarios=[replacement if item.id==scenario.id else item for item in scenarios]
            questions.extend(updated.open_questions+batch.open_questions+oracle_batch.open_questions)
            blocking.extend(updated.blocking_questions+batch.blocking_questions+oracle_batch.blocking_questions)
        scenario_lookup={item.id:item for item in scenarios}
        for case in cases:
            matched=scenario_lookup.get(case.scenario_id)
            if not matched or any(getattr(case,key)!=getattr(matched,key) for key in ('runner','kind','requirement_ids','feature_ids','risk_ids','operation_ids')):
                raise CaseReviewValidationError('Traceability của test case phải khớp scenario và requirements của bộ test.')
        validation=self.validator.validate(cases,sources)
        coverage=self.coverage.analyze(knowledge,sources,cases,validation,None,config.coverage,runnable_scopes(config.runners))
        self.store.write('review/validation.json',validation.model_dump());self.store.write('review/coverage.json',coverage)
        previous_cases={case['id']:ExecutableCase.model_validate(case).model_dump() for case in previous['cases/cases.json']}
        changes={'previous_baseline':request['baseline_version'],'selected_case_id':request['case_id'],'action':request['action'],
            'changed_case_ids':[case.id for case in cases if case.id in previous_cases and case.model_dump()!=previous_cases[case.id]],
            'added_case_ids':[case.id for case in cases if case.id not in previous_cases],
            'deleted_case_ids':[identifier for identifier in previous_cases if identifier not in {case.id for case in cases}],
            'preserved_case_count':sum(case.model_dump()==previous_cases.get(case.id) for case in cases),
            'previous_blocker_count':len(previous['review_gate.json']['design_blockers'])}
        return self._publish(config,knowledge,sources,scenarios,cases,validation,coverage,None,questions,blocking,{'review_changes.json':changes})

    def _publish(self,config,knowledge,sources,scenarios,cases,validation,coverage,execution,questions,blocking_questions,extra=None):
        review = self.critic.review(knowledge, sources, scenarios, cases, validation, coverage, execution, questions)
        questions = sorted(set(questions))
        blockers = list(blocking_questions)
        if not cases:
            blockers.append("No executable HTTP test cases were generated")
        blockers.extend(issue.message for issue in validation.issues)
        if not coverage["design_satisfied"]:
            blockers.append("Designed coverage thresholds not met")
        missing_scenarios = {s.id for s in scenarios} - {c.scenario_id for c in cases if c.id in validation.valid_case_ids}
        blockers.extend(f"Scenario has no valid oracle-backed test: {identifier}" for identifier in sorted(missing_scenarios))
        if review.recommendation == "needs_changes":
            blockers.append("Test Critic requested changes")
        blockers.extend(issue.message for issue in review.issues if issue.severity == "blocker")
        state = "needs_changes" if blockers else "ready_for_human_review"
        trace = {requirement.id: [case.id for case in cases if requirement.id in case.requirement_ids]
                 for requirement in knowledge.requirements}
        ground_truth = {case.id: case.oracle.model_dump() for case in cases if case.kind == "ai_evaluation"}
        payload = {"knowledge.json": knowledge.model_dump(), "sources.json": sources.model_dump(),
                   "reference_execution.json": execution,
                   "test_plan.json": TestPlan(scenarios=scenarios, rationale="LLM risk-based design", open_questions=questions).model_dump(),
                   "cases/cases.json": [case.model_dump() for case in cases],
                   "ground_truth/oracles.json": ground_truth, "traceability.json": trace,
                   "validation_report.json": validation.model_dump(), "coverage.json": coverage,
                   "critic_review.json": review.model_dump(),
                   "review_gate.json": {"design_blockers": sorted(set(blockers)), "open_questions": questions,
                                        "blocking_questions": sorted(set(blocking_questions)),
                                        "scope": coverage["scope"], "deferred_requirements": coverage["deferred_requirements"]}}
        payload.update(extra or {})
        baseline = self.baselines.publish_draft(config, payload)
        if execution is not None:
            execution["baseline_version"] = baseline.name
        self.store.write("execution_report.json", execution)
        result = {"state": state, "baseline": str(baseline), "case_count": len(cases),
                  "design_blockers": sorted(set(blockers)), "agent_mode": "llm"}
        self.store.write("run_status.json", result)
        self.store.event("workflow", "completed", {"state": state, "cases": len(cases)})
        return result


class CaseReviewValidationError(ValueError):
    code='CASE_REVIEW_VALIDATION'
    def __init__(self,reason):
        self.safe_message='Chưa áp dụng được sửa test case: '+reason
        super().__init__(self.safe_message)
