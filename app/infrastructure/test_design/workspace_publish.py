"""Publish approved design and optionally activate its validated automation subset."""
import hashlib
import json
from uuid import uuid4
from pathlib import Path
from ...domain.test_design import ManagedBaseline


def publish_workspace(settings,workspace,case_ids,actor,reason,acknowledge_gaps,activate_qc=False):
    from tda.application.workspace import refresh_workspace,coverage_for,design_digest
    from tda.domain.contracts import WorkflowConfig,TestPlan,PlannedScenario
    from tda.domain.rules import TestValidator
    from tda.application.coverage import CoverageAnalyzer
    from tda.infrastructure.baselines import BaselineRepository
    from tda.pipeline import approve
    refresh_workspace(workspace)
    selected=set(case_ids)
    if len(selected)!=len(case_ids):raise ValueError('Danh sách publish trùng case ID.')
    cases=[case for case in workspace.cases if case.id in selected]
    if len(cases)!=len(selected) or any(case.status!='approved' for case in cases):raise ValueError('Chỉ publish cases đã được duyệt.')
    if not cases:raise ValueError('Chọn ít nhất một case.')
    if activate_qc:
        from tda.application.automation import preparation_state
        if any(preparation_state(workspace,c)!='ready' for c in cases):
            raise ValueError('Chỉ áp dụng cho QC các cases có executable và mapping đã xác nhận cho revision hiện tại. Cases cần điều kiện hoặc thủ công phải nằm ngoài phạm vi áp dụng.')
    if any(issue.severity=='blocker' and (not issue.case_ids or set(issue.case_ids)&selected) for issue in workspace.findings):raise ValueError('Case được chọn còn blocker.')
    coverage=coverage_for(workspace,cases)
    if (coverage['missing_criteria'] or coverage['missing_techniques']) and not acknowledge_gaps:raise ValueError('Còn coverage gap. Xác nhận phạm vi publish và ghi lý do để tiếp tục.')
    executable=[];binding_exclusions=[]
    for case in cases:
        binding=workspace.bindings.get(case.id)
        if activate_qc and binding and binding.state=='validated' and binding.reviewer and binding.case_revision==case.revision and binding.design_sha256==design_digest(case):
            executable.append(binding.executable)
        else:binding_exclusions.append({'case_id':case.id,'reason':'Chưa có automation binding được xác nhận cho revision này.'})
    snapshot=workspace.model_dump()
    snapshot['cases']=[case.model_dump() for case in cases]
    publication={'workspace_version':workspace.version,'source_revision':workspace.source_revision,'reviewer':actor,'reason':reason,'publication_id':uuid4().hex,
        'selected_case_ids':case_ids,'executed_case_ids':[case.id for case in executable],
        'excluded_cases':[{'case_id':case.id,'status':case.status,'reason':'Không thuộc phạm vi publish đã chọn.'} for case in workspace.cases if case.id not in selected],
        'automation_exclusions':binding_exclusions,'acknowledged_gaps':bool(acknowledge_gaps),'coverage':coverage,'snapshot':snapshot}
    publication['version']=hashlib.sha256(json.dumps(publication,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
    if not executable:return publication,None
    validation=TestValidator().validate(executable,workspace.sources)
    if validation.issues:raise ValueError('Automation subset còn lỗi validation; chưa cập nhật QC baseline.')
    lookup={case.id:case for case in cases};scenarios=[]
    for case in executable:
        design=lookup[case.id];evidence=[claim for step in design.steps for expected in step.expected_results for claim in expected.evidence]
        scenarios.append(PlannedScenario(id=case.scenario_id,title=design.title,kind=case.kind,runner=case.runner,priority=design.priority,
            requirement_ids=case.requirement_ids,feature_ids=case.feature_ids,risk_ids=case.risk_ids,operation_ids=case.operation_ids,
            acceptance_criteria=[expected.description for step in design.steps for expected in step.expected_results],evidence=evidence))
    config=WorkflowConfig(project=workspace.project_id,source_revision=workspace.source_revision,model='reviewed-workspace',
        sources=[{'id':'snapshot','kind':'prd','paths':['sources.json']}],kinds=['functional','integration','security','performance','ai_evaluation'],
        runners=['http','unit','browser','k6','ai'],reference_execution=False)
    raw_coverage=CoverageAnalyzer().analyze(workspace.knowledge,workspace.sources,executable,validation,None,config.coverage,{'http','unit','browser','load'})
    payload={'knowledge.json':workspace.knowledge.model_dump(),'sources.json':workspace.sources.model_dump(),
        'test_plan.json':TestPlan(scenarios=scenarios,rationale='Maintainer-reviewed automation subset',open_questions=[]).model_dump(),
        'cases/cases.json':[case.model_dump() for case in executable],
        'ground_truth/oracles.json':{case.id:case.oracle.model_dump() for case in executable if case.kind=='ai_evaluation'},
        'traceability.json':{requirement.id:[case.id for case in executable if requirement.id in case.requirement_ids] for requirement in workspace.knowledge.requirements},
        'validation_report.json':validation.model_dump(),'coverage.json':raw_coverage,
        'critic_review.json':{'recommendation':'ready_for_human_review','summary':'Design và automation mapping được maintainer review theo từng case.','issues':[]},
        'review_gate.json':{'design_blockers':[],'blocking_questions':[],'open_questions':[],
            'scope':'published_subset','deferred_requirements':raw_coverage.get('deferred_requirements',[])},
        'workspace_publication.json':publication,'test_workspace.json':snapshot}
    directory=BaselineRepository(settings.agent_qc_tda_dir/'projects'/workspace.project_id/'baselines').publish_draft(config,payload,
        approval_policy='workspace-reviewed-subset-v1')
    approve(directory,actor)
    publication['qc_baseline']=directory.name
    baseline=ManagedBaseline(project_id=workspace.project_id,version=directory.name,source_revision=workspace.source_revision,
        job_id='design_'+uuid4().hex,baseline_dir=str(directory),state='approved',reviewer=actor)
    return publication,baseline
