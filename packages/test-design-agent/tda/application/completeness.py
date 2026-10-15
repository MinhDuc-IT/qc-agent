"""Independent evaluator: LLM supplies evidence, Python owns metrics and gate."""
import hashlib
import json
from ..domain.completeness import DesignScenario
from ..domain.rules import check_evidence


def stable_id(prefix, *parts):
    return prefix + hashlib.sha256(json.dumps(parts,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:20]


def input_digest(w):
    data={'requirements':w.knowledge.model_dump(),'criteria':[x.model_dump() for x in w.criteria],
          'scenarios':[x.model_dump() for x in w.scenarios],
          'cases':[x.model_dump(exclude={'status','revision'}) for x in w.cases],
          'sources':[(d.id,d.sha256) for d in w.sources.documents]}
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def catalog_digest(w):
    data={'knowledge':w.knowledge.model_dump(),'criteria':[c.model_dump() for c in w.criteria],
          'sources':[(d.id,d.sha256) for d in w.sources.documents]}
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def scenario_inventory(w):
    """Conservative fallback. Criteria and applicable planned techniques stay separate."""
    result=[]
    for req in w.knowledge.requirements:
        applicable='NOT_APPLICABLE' if req.execution_scope=='dependency' else 'NEEDS_REVIEW' if req.clarity!='CONFIRMED' or not req.acceptance_criteria else 'APPLICABLE'
        criteria=[c for c in w.criteria if c.requirement_id==req.id]
        for c in criteria:
            result.append(DesignScenario(id=stable_id('SC-',req.id,c.id),requirement_id=req.id,
                criterion_ids=[c.id],technique='criterion',description=c.text,applicability=applicable,
                rationale='Acceptance criterion từ nguồn; semantic evaluator kiểm tra hành vi thực sự.',evidence=req.evidence))
        for strategy in w.strategy.items:
            if strategy.requirement_id!=req.id:continue
            for technique in strategy.techniques:
                result.append(DesignScenario(id=stable_id('SC-',req.id,technique),requirement_id=req.id,
                    technique=technique,description=req.title+' · '+technique,
                    applicability=applicable,rationale=strategy.rationale,evidence=req.evidence,
                    kind=strategy.kinds[0]))
    return result


def metric(ids, covered):
    ids=sorted(set(ids));covered=sorted(set(ids)&set(covered))
    return {'numerator':len(covered),'denominator':len(ids),'ratio':len(covered)/len(ids) if ids else None,
            'object_ids':ids,'covered_ids':covered,'uncovered_ids':sorted(set(ids)-set(covered))}


def evaluate(w):
    from .workspace import validate_design
    active=[c for c in w.cases if c.status!='retired'];by_case={c.id:c for c in active}
    requirements={r.id:r for r in w.knowledge.requirements};scenarios={s.id:s for s in w.scenarios}
    gaps=[];invalid=set();unverified=[]
    def gap(kind,req=None,scenario=None,cases=(),description='',severity='high',evidence=(),action=''):
        gaps.append({'id':stable_id('GAP-',kind,req,scenario,sorted(cases),description),'type':kind,
                     'requirement_id':req,'scenario_id':scenario,'case_ids':sorted(cases),
                     'description':description,'severity':severity,'evidence':[e.model_dump() for e in evidence],
                     'recommended_action':action or 'Bổ sung hoặc chỉnh case theo source; đánh giá lại.'})
    for finding in validate_design(w)+w.findings:
        if finding.severity=='blocker':
            invalid.update(finding.case_ids or by_case)
            gap('INVALID_TEST_CASE',cases=finding.case_ids,description=finding.message,evidence=finding.evidence)
        elif finding.category=='duplicate':
            invalid.update(sorted(finding.case_ids)[1:])
            gap('DUPLICATE_TEST_CASE',cases=finding.case_ids,description=finding.message,evidence=finding.evidence)
    for c in active:
        weak=not c.objective.strip() or any(not e.assertion_kind or e.expected_json is None for step in c.steps for e in step.expected_results)
        if weak:
            invalid.add(c.id);gap('WEAK_ORACLE',cases=[c.id],description='Thiếu objective hoặc oracle có thể xác định Pass/Fail.',action='Bổ sung assertion và giá trị kỳ vọng; không lấy số lượng case làm coverage.')
    current=w.semantic_evaluation is not None and w.semantic_input_sha256==input_digest(w)
    mapping={s.id:[] for s in w.scenarios}
    if current:
        for check in w.semantic_evaluation.case_checks:
            try:
                if check.case_id not in by_case or not check.evidence or not check.explanation.strip():raise ValueError('Case check lacks evidence')
                check_evidence(check.evidence,w.sources)
                if check.verdict=='INVALID':
                    invalid.add(check.case_id);gap('INVALID_TEST_CASE',cases=[check.case_id],description=check.explanation,evidence=check.evidence)
                elif check.verdict=='NEEDS_REVIEW':unverified.append(check.case_id+' · '+check.explanation)
            except ValueError:unverified.append(check.case_id+' · Case check chưa đủ bằng chứng.')
        for duplicate in w.semantic_evaluation.duplicate_evidence:
            try:
                if not set(duplicate.case_ids)<=set(by_case) or not duplicate.evidence or not duplicate.explanation.strip():raise ValueError('Duplicate evidence')
                check_evidence(duplicate.evidence,w.sources)
                invalid.update(sorted(set(duplicate.case_ids))[1:])
                gap('DUPLICATE_TEST_CASE',cases=duplicate.case_ids,description=duplicate.explanation,evidence=duplicate.evidence)
            except ValueError:unverified.append('Semantic duplicate chưa đủ bằng chứng.')
        for group in w.semantic_evaluation.duplicate_groups:
            if not any(set(d.case_ids)==set(group) for d in w.semantic_evaluation.duplicate_evidence):
                unverified.append('Duplicate group thiếu explanation/evidence: '+', '.join(group))
        for m in w.semantic_evaluation.mappings:
            s=scenarios.get(m.scenario_id);c=by_case.get(m.case_id)
            try:
                if not s or not c or s.requirement_id not in c.requirement_ids:raise ValueError('ID mapping')
                if not m.evidence or not m.explanation.strip():raise ValueError('Missing evidence')
                check_evidence(m.evidence,w.sources)
                if m.verdict=='NEEDS_REVIEW':unverified.append(m.scenario_id+' / '+m.case_id)
                if m.verdict=='COVERED' and c.id not in invalid:mapping[s.id].append(c.id)
            except ValueError:unverified.append(m.scenario_id+' / '+m.case_id+' · evidence hoặc mapping chưa hợp lệ')
    else:unverified.append('Chưa có independent semantic evaluation cho nội dung hiện tại.')
    if w.scenario_input_sha256!=catalog_digest(w):
        unverified.append('Scenario inventory chưa được phân tích độc lập cho nguồn và catalog hiện tại.')
    unverified.extend(w.scenario_limitations)
    # An exact duplicate is a deterministic quality defect, even without LLM output.
    fingerprints={}
    for c in active:
        fingerprint=json.dumps({'requirements':sorted(c.requirement_ids),'steps':[s.model_dump() for s in c.steps],
                                'data':[d.model_dump() for d in c.test_data],'preconditions':c.preconditions},sort_keys=True)
        if fingerprint in fingerprints:
            invalid.add(c.id);gap('DUPLICATE_TEST_CASE',cases=[fingerprints[fingerprint],c.id],description='Các bước, dữ liệu và oracle trùng nhau.')
        else:fingerprints[fingerprint]=c.id
    # Remove mappings after all quality checks, including duplicates.
    mapping={key:[identifier for identifier in values if identifier not in invalid] for key,values in mapping.items()}
    for req in requirements.values():
        if req.clarity!='CONFIRMED' or not req.acceptance_criteria:
            gap('AMBIGUOUS_REQUIREMENT',req=req.id,description=req.title,severity='critical' if req.priority=='P0' else 'high',evidence=req.evidence,action='Xác nhận requirement hoặc bổ sung acceptance criteria; không tự suy đoán ground truth.')
    applicable=[s for s in w.scenarios if s.applicability=='APPLICABLE']
    covered_s={s.id for s in applicable if mapping[s.id] and requirements[s.requirement_id].clarity=='CONFIRMED'}
    testable=[r for r in requirements.values() if r.execution_scope!='dependency']
    covered_r={r.id for r in testable if r.clarity=='CONFIRMED' and r.acceptance_criteria and any(s.requirement_id==r.id for s in applicable)
               and all(s.id in covered_s for s in applicable if s.requirement_id==r.id)}
    for s in applicable:
        if s.id in covered_s:continue
        kind={'negative':'MISSING_NEGATIVE_CASE','boundary_value':'MISSING_BOUNDARY_CASE'}.get(s.technique,
            'MISSING_INTEGRATION_TEST' if s.kind=='integration' else 'MISSING_NON_FUNCTIONAL_TEST' if s.kind in ('performance','security','ai_evaluation') else 'MISSING_SCENARIO')
        gap(kind,req=s.requirement_id,scenario=s.id,description=s.description,
            severity='critical' if requirements[s.requirement_id].priority=='P0' else 'high',evidence=s.evidence)
    for r in testable:
        if r.id not in covered_r:gap('UNCOVERED_REQUIREMENT',req=r.id,description=r.title,severity='critical' if r.priority=='P0' else 'high',evidence=r.evidence)
    risks=w.knowledge.risks
    covered_risks={r.id for r in risks if any(r.id in s.risk_ids for s in applicable)
                   and all(s.id in covered_s for s in applicable if r.id in s.risk_ids)}
    for risk in risks:
        if risk.id not in covered_risks:
            gap('MISSING_SCENARIO',description='Chưa kiểm chứng risk '+risk.id+': '+risk.description,
                severity=risk.severity,evidence=risk.evidence,action='Lập scenario kiểm chứng risk và sinh case có oracle phù hợp.')
    metrics={'requirement':metric([r.id for r in testable],covered_r),
        'critical_requirement':metric([r.id for r in testable if r.priority=='P0'],covered_r),
        'scenario':metric([s.id for s in applicable],covered_s),'risk':metric([r.id for r in risks],covered_risks),
        'critical_high_risk':metric([r.id for r in risks if r.severity in ('critical','high')],covered_risks),
        'validity':metric(by_case,set(by_case)-invalid)}
    reasons=[]
    for key in ('requirement','critical_requirement','scenario','critical_high_risk','validity'):
        threshold=getattr(w.completeness_policy,key);m=metrics[key]
        if m['ratio'] is not None and m['ratio']<threshold:reasons.append(f'{key}: {m["numerator"]}/{m["denominator"]}, ngưỡng {threshold:.0%}.')
    critical=sum(g['severity']=='critical' for g in gaps)
    if critical>w.completeness_policy.unresolved_critical_gaps:reasons.append(f'Còn {critical} critical gaps.')
    inventory=w.coverage.get('regression_inventory',{})
    uncertain=unverified or any(s.applicability=='NEEDS_REVIEW' for s in w.scenarios) or any(g['type']=='AMBIGUOUS_REQUIREMENT' for g in gaps) or not testable or not applicable or not active
    if inventory.get('missing_catalog') or inventory.get('unknown'):uncertain=True;unverified.append('Inventory còn phần chưa xác định hoặc chưa có trong catalog.')
    gate='NEEDS_REVIEW' if uncertain else 'FAIL' if reasons else 'PASS'
    return {'schema_version':'1.0','input_sha256':input_digest(w),'gate':gate,'reasons':reasons+sorted(set(unverified)),
        'metrics':metrics,'gaps':gaps,'scenario_mapping':mapping,'scenarios':[s.model_dump() for s in w.scenarios],
        'totals':{'requirements':len(requirements),'scenarios':len(w.scenarios),'cases':len(active),'valid_cases':len(set(by_case)-invalid),'invalid_cases':len(invalid)},
        'invalid_case_ids':sorted(invalid),'out_of_scope_requirement_ids':sorted(r.id for r in requirements.values() if r.execution_scope=='dependency'),
        'policy':w.completeness_policy.model_dump(),'supplemented_case_ids':[c.id for c in active if c.remediation_gap_ids],
        'priority_metrics':{priority:metric([r.id for r in testable if r.priority==priority],covered_r) for priority in ('P0','P1','P2')},
        'risk_metrics':{severity:metric([r.id for r in risks if r.severity==severity],covered_risks) for severity in ('critical','high','medium','low')},
        'traceability':[{'scenario_id':s.id,'requirement_id':s.requirement_id,'sources':[e.model_dump() for e in s.evidence],
                        'case_ids':mapping[s.id],'oracle':[{'case_id':c.id,'steps':[step.model_dump() for step in c.steps]} for c in active if c.id in mapping[s.id]]} for s in w.scenarios],
        'limitations':['Completeness là đánh giá test design; chưa chạy test và không chứng minh phần mềm không có bug.',
                      'Scenario inventory dựa trên criteria và test strategy; cần review applicability và các giới hạn static inventory.']}


def markdown(report):
    lines=['# Test Suite Completeness', '', 'Gate: '+report['gate'],'','Đánh giá test design; chưa thực thi test.','','| Metric | Coverage |','|---|---|']
    for name,m in report['metrics'].items():lines.append(f'| {name} | {m["numerator"]}/{m["denominator"]} |')
    lines+=['','## Lý do']+['- '+x for x in report['reasons']]+['','## Gaps']
    for g in report['gaps']:lines+=['',f'- {g["id"]} · {g["type"]} · {g["requirement_id"] or "suite"}: {g["description"]}', '  '+g['recommended_action'], '  Evidence: '+json.dumps(g['evidence'],ensure_ascii=False)]
    lines+=['','## Traceability',json.dumps({'scenarios':report['scenarios'],'scenario_mapping':report['scenario_mapping']},ensure_ascii=False,indent=2)]
    return '\n'.join(lines)+'\n'
