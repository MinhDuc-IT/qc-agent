"""Design first workflow. No application execution is part of this workflow."""
import hashlib
import json
from ..domain.workspace import (AutomationBinding, Criterion, DesignBatch, DesignCase,
    DesignReview, DesignStep, DesignStrategy, ExpectedResult, StrategyItem, TestWorkspace,
    WorkspaceFinding, BindingBatch)
from ..domain.contracts import Evidence, SourceBundle
from ..domain.rules import check_evidence, TestValidator, normalize_evidence_quotes
from .ports import AgentFailure


def design_digest(case):
    data=case.model_dump(exclude={'status','author','human_edited'})
    for key in ('inventory_ids','branch_ids','remediation_gap_ids'):
        if not data.get(key):data.pop(key,None)
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def criteria_for(knowledge):
    return [Criterion(id=requirement.id+'-AC-'+hashlib.sha256(text.encode()).hexdigest()[:10],
        requirement_id=requirement.id,text=text) for requirement in knowledge.requirements for text in requirement.acceptance_criteria]


def finding(case_ids,severity,category,message,suggestion=''):
    identity=json.dumps([sorted(case_ids),category,message],ensure_ascii=False)
    return WorkspaceFinding(id='F-'+hashlib.sha256(identity.encode()).hexdigest()[:16],case_ids=case_ids,
        severity=severity,category=category,message=message,suggestion=suggestion)


def normalize_agent_quotes(claims,sources):
    normalize_evidence_quotes(claims,sources)


def normalize_agent_provenance(case,sources):
    """Correct provenance labels only when every exact quote is implementation evidence."""
    documents={document.id:document for document in sources.documents}
    changed=False
    for step in case.steps:
        for expected in step.expected_results:
            normalize_agent_quotes(expected.evidence,sources)
            if expected.origin!='specified' or not expected.evidence:continue
            try:check_evidence(expected.evidence,sources)
            except ValueError:continue
            if all(documents[claim.source_id].kind in ('code','db_schema','existing_tests') for claim in expected.evidence):
                expected.origin='implementation_derived';changed=True
    return changed


def validate_design(workspace):
    from .regression_inventory import mapping_issues
    requirements={item.id for item in workspace.knowledge.requirements}
    criteria={item.id:item.requirement_id for item in workspace.criteria}
    findings=[];seen=set()
    for case in workspace.cases:
        if case.id in seen:raise ValueError('Duplicate design case ID')
        seen.add(case.id)
        if case.status=='retired':continue
        if not case.requirement_ids or not set(case.requirement_ids)<=requirements or not set(case.criterion_ids)<=set(criteria) or any(criteria.get(identifier) not in case.requirement_ids for identifier in case.criterion_ids):
            findings.append(finding([case.id],'blocker','structure','Traceability chưa khớp requirement và acceptance criteria.','Chọn requirement/criteria hợp lệ trong editor.'))
        for index,step in enumerate(case.steps):
            for expected in step.expected_results:
                if expected.origin=='unresolved':
                    findings.append(finding([case.id],'blocker','evidence',f'Bước {index+1}: expected result chưa được xác định.','Bổ sung nguồn hoặc xác nhận acceptance criteria.'));continue
                try:
                    if not expected.evidence:raise ValueError('Missing evidence')
                    check_evidence(expected.evidence,workspace.sources)
                    documents={document.id:document for document in workspace.sources.documents}
                    if expected.origin in ('specified','maintainer_confirmed') and not any(documents[claim.source_id].kind in ('prd','srs','openapi') for claim in expected.evidence):
                        raise ValueError('Specification must use normative sources')
                    if expected.origin=='implementation_derived' and not any(documents[claim.source_id].kind in ('code','db_schema','existing_tests') for claim in expected.evidence):raise ValueError('Implementation expectations need implementation sources')
                except ValueError:
                    findings.append(finding([case.id],'blocker','evidence',f'Bước {index+1}: dẫn chứng expected result chưa hợp lệ.','Chọn trích dẫn từ source hoặc cung cấp acceptance criteria.'))
                if expected.expected_json is not None:
                    try:json.loads(expected.expected_json)
                    except (ValueError,TypeError):findings.append(finding([case.id],'blocker','structure',f'Bước {index+1}: giá trị expected JSON chưa hợp lệ.'))
        if case.questions:
            findings.append(finding([case.id],'blocker','clarity','Case còn câu hỏi cần trả lời: '+'; '.join(case.questions),'Trả lời câu hỏi hoặc chỉnh expected result trước khi duyệt.'))
        for issue in mapping_issues(case,workspace):
            findings.append(finding([case.id],'blocker','structure',issue,'Review inventory/branch mapping và exact source evidence.'))
    signatures={}
    for case in workspace.cases:
        if case.status=='retired':continue
        signature=json.dumps({'kind':case.kind,'preconditions':case.preconditions,'data':[item.model_dump() for item in case.test_data],
            'steps':[{'action':step.action,'expected':[{'description':expected.description,'value':expected.expected_json} for expected in step.expected_results]} for step in case.steps]},sort_keys=True)
        if signature in signatures:
            findings.append(finding([signatures[signature],case.id],'warning','duplicate','Hai case có cùng steps, inputs và expected results.','Gộp phạm vi hoặc ngừng dùng case trùng.'))
        else:signatures[signature]=case.id
    return findings


def coverage_for(workspace,cases=None,legacy=False):
    active=[case for case in (workspace.cases if cases is None else cases) if case.status!='retired']
    # A link with unresolved/invalid design evidence does not establish coverage.
    issues=validate_design(workspace)+workspace.findings
    invalid={identifier for issue in issues if issue.severity=='blocker' for identifier in issue.case_ids}
    global_blocker=any(issue.severity=='blocker' and not issue.case_ids for issue in issues)
    usable=[case for case in active if case.id not in invalid and not global_blocker]
    excluded_requirements=set() if legacy else {req.id for req in workspace.knowledge.requirements if req.execution_scope=='dependency'}
    in_scope=[criterion for criterion in workspace.criteria if criterion.requirement_id not in excluded_requirements]
    mapped={criterion.id:[case.id for case in usable if criterion.id in case.criterion_ids] for criterion in in_scope}
    missing=[criterion.id for criterion in in_scope if not mapped[criterion.id]]
    techniques=[]
    for item in workspace.strategy.items:
        if item.requirement_id in excluded_requirements:continue
        for technique in item.techniques:
            covered=[case.id for case in usable if item.requirement_id in case.requirement_ids and case.technique==technique]
            techniques.append({'requirement_id':item.requirement_id,'technique':technique,'case_ids':covered})
    from .regression_inventory import report_for
    result={'criteria_total':len(mapped),'criteria_covered':sum(bool(value) for value in mapped.values()),
        'out_of_scope_criteria':[criterion.model_dump() for criterion in workspace.criteria if criterion.requirement_id in excluded_requirements],
        'regression_inventory':report_for(workspace,cases,invalid,global_blocker),
        'criterion_mapping':mapped,'missing_criteria':missing,'techniques':techniques,
        'missing_techniques':[item for item in techniques if not item['case_ids']],
        'requirement_mapping':{requirement.id:[case.id for case in usable if requirement.id in case.requirement_ids] for requirement in workspace.knowledge.requirements},
        'limitations':['Coverage phản ánh các criteria và kỹ thuật được lập kế hoạch; không chứng minh toàn bộ hành vi hoặc code coverage.']}
    if legacy:
        result.pop('out_of_scope_criteria');result.pop('regression_inventory')
    return result


def refresh_workspace(workspace):
    deterministic=validate_design(workspace)
    retained=[issue for issue in workspace.findings if issue.id.startswith('AI-') or issue.category=='source_change']
    all_findings=retained+deterministic
    for issue in all_findings:
        if not issue.id:issue.id=finding(issue.case_ids,issue.severity,issue.category,issue.message).id
    workspace.findings=list({issue.id:issue for issue in all_findings}.values())
    blocked={identifier for issue in workspace.findings if issue.severity=='blocker' for identifier in issue.case_ids}
    global_blocker=any(issue.severity=='blocker' and not issue.case_ids for issue in workspace.findings)
    for case in workspace.cases:
        if case.status=='retired':continue
        if case.id in blocked or global_blocker:case.status='needs_clarification'
        elif case.status!='approved':case.status='ready_for_review'
        binding=workspace.bindings.get(case.id)
        if binding and (binding.case_revision!=case.revision or binding.design_sha256!=design_digest(case)):
            binding.state='unbound';binding.reviewer=None
    workspace.coverage=coverage_for(workspace)
    if workspace.design_method=='flow_investigation':
        from .flow_design import report
        workspace.completeness=report(workspace)
        return workspace
    from .completeness import evaluate,scenario_inventory,catalog_digest
    if workspace.scenario_input_sha256!=catalog_digest(workspace):
        workspace.scenarios=scenario_inventory(workspace)
    workspace.completeness=evaluate(workspace)
    for case in workspace.cases:
        if case.id in workspace.completeness['invalid_case_ids'] and case.status not in ('approved','retired'):
            case.status='needs_clarification'
    return workspace


def from_baseline(project_id,baseline,artifacts):
    from ..domain.contracts import KnowledgeModel, ExecutableCase, Parameter
    knowledge=KnowledgeModel.model_validate(artifacts['knowledge.json']);criteria=criteria_for(knowledge)
    scenarios={item['id']:item for item in artifacts['test_plan.json']['scenarios']}
    cases=[];bindings={}
    valid_ids=set(artifacts['validation_report.json'].get('valid_case_ids',[]))
    for payload in artifacts['cases/cases.json']:
        executable=ExecutableCase.model_validate(payload);scenario=scenarios.get(executable.scenario_id,{})
        actions=[]
        if executable.runner=='unit':actions=['Gọi '+executable.unit.function+' trong '+executable.unit.module_path]
        elif executable.runner=='browser':actions=[action.action+' '+(action.path or action.selector or '') for action in executable.browser.actions]
        else:actions=[request.method+' '+request.path for request in executable.steps]
        steps=[]
        data=[]
        if executable.unit:
            data=[Parameter(name='args',value=executable.unit.args_json),Parameter(name='kwargs',value=executable.unit.kwargs_json)]
        elif executable.browser:
            data=[Parameter(name=action.selector or 'input',value=action.value) for action in executable.browser.actions if action.value is not None]
        else:
            for index,request in enumerate(executable.steps):
                prefix=('B'+str(index+1)+'.') if len(executable.steps)>1 else ''
                data.extend(Parameter(name=prefix+parameter.name,value=parameter.value) for parameter in request.query)
                if request.body_json is not None:data.append(Parameter(name=prefix+'body',value=request.body_json))
        for oracle in executable.oracle.steps:
            expected=[ExpectedResult(description=assertion.kind+' '+assertion.pointer+' = '+assertion.expected_json,
                expected_json=assertion.expected_json,assertion_kind=assertion.kind,pointer=assertion.pointer,evidence=assertion.evidence,
                origin='implementation_derived' if assertion.expectation_basis=='implementation' else 'specified') for assertion in oracle.assertions]
            steps.append(DesignStep(action=actions[oracle.step_index] if oracle.step_index<len(actions) else 'Bước '+str(oracle.step_index+1),expected_results=expected))
        case=DesignCase(id=executable.id,title=executable.title,kind=executable.kind,priority=scenario.get('priority','P1'),
            objective=scenario.get('title',executable.title),requirement_ids=executable.requirement_ids,
            criterion_ids=[criterion.id for criterion in criteria if criterion.requirement_id in executable.requirement_ids and criterion.text in scenario.get('acceptance_criteria',[])],
            steps=steps,test_data=data,
            status='approved' if baseline.state=='approved' else 'draft',author='baseline-import')
        cases.append(case)
        bindings[case.id]=AutomationBinding(case_id=case.id,case_revision=case.revision,design_sha256=design_digest(case),executable=executable,
            state='validated' if baseline.state=='approved' and case.id in valid_ids else 'bound',reviewer=baseline.reviewer)
    workspace=TestWorkspace(project_id=project_id,source_revision=baseline.source_revision,source_snapshots=[baseline.source_revision],
        sources=SourceBundle.model_validate(artifacts['sources.json']),knowledge=knowledge,criteria=criteria,
        strategy=DesignStrategy(items=[StrategyItem(requirement_id=item.id,techniques=['happy_path'],kinds=['functional'],rationale='Nhập từ baseline cũ; cần review strategy.') for item in knowledge.requirements]),
        cases=cases,bindings=bindings,generation_job_id=baseline.job_id,
        questions=list(dict.fromkeys(knowledge.open_questions+knowledge.blocking_questions+artifacts['test_plan.json'].get('open_questions',[]))))
    for issue in artifacts.get('critic_review.json',{}).get('issues',[]):
        message=issue['message'].casefold();category='conflict';severity=issue['severity']
        if not issue.get('case_ids') and 'test critic' in message and 'review' in message:category='clarity';severity='warning'
        elif any(token in message for token in ('căn cứ','chứng cứ','không mô tả','chưa đủ','ungrounded')):category='evidence'
        elif any(token in message for token in ('fixture','runtime','executor','harness','executability')):category='structure';severity='warning'
        elif any(token in message for token in ('căn cứ','chứng cứ','nguồn','source','oracle','expected','không mô tả')):category='evidence'
        workspace.findings.append(finding(issue.get('case_ids',[]),severity,category,issue['message'],issue.get('suggestion') or ''))
        workspace.findings[-1].id='AI-'+workspace.findings[-1].id
    return refresh_workspace(workspace)


def binding_design_issues(case,executable):
    from ..domain.assertion_paths import matches_expected
    issues=[]
    if executable.id!=case.id or executable.kind!=case.kind or executable.requirement_ids!=case.requirement_ids:
        issues.append('Binding phải giữ case ID, type và requirement IDs của design.')
    for index,step in enumerate(case.steps):
        for expected in step.expected_results:
            if expected.expected_json is None:continue
            assertions=[assertion for oracle in executable.oracle.steps if oracle.step_index==index or not expected.assertion_kind for assertion in oracle.assertions]
            if not matches_expected(expected,assertions):
                issues.append(f'Binding chưa giữ expected result của bước {index+1}: '+expected.description)
    return issues
