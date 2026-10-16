"""Immutable server-side artifact bundles, separate from PR-controlled files."""
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from tda.domain.worker_artifacts import WorkerArtifactBinding
from tda.domain.workspace import DesignCase
from tda.application.worker_artifacts import artifact_issues
from tda.application.automation import preparation_state
from tda.application.workspace import coverage_for
from tda.application.workspace import binding_design_issues,design_digest
from tda.application.automation import mapping_issues
from tda.domain.workspace import AutomationBinding
from tda.domain.rules import TestValidator
from ...domain.test_design import ManagedBaseline


def canonical(data):return json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode('utf-8')


def publish_artifacts(settings,workspace,case_ids,actor,reason,acknowledge_gaps):
    cases=[c for c in workspace.cases if c.id in set(case_ids)]
    if len(cases)!=len(case_ids) or not cases or any(preparation_state(workspace,c)!='ready' for c in cases):raise ValueError('Chỉ áp dụng artifacts đã được xác nhận và khớp revision.')
    for case in cases:
        artifact=workspace.worker_artifacts.get(case.id)
        binding=workspace.bindings.get(case.id)
        if not artifact and not binding:raise ValueError('Case chưa có automation binding.')
        issues=artifact_issues(case,artifact)+artifact.validation_issues if artifact else validate_plan(case,binding,workspace.sources)
        if issues:raise ValueError('; '.join(issues))
    coverage=coverage_for(workspace,cases)
    if (coverage['missing_criteria'] or coverage['missing_techniques']) and not acknowledge_gaps:raise ValueError('Xác nhận phạm vi và coverage gaps trước khi áp dụng subset.')
    snapshot=workspace.model_dump();snapshot['cases']=[c.model_dump() for c in cases]
    publication={'version':uuid4().hex[:20],'publication_id':uuid4().hex,'workspace_version':workspace.version,'source_revision':workspace.source_revision,
        'selected_case_ids':case_ids,'executed_case_ids':case_ids,'excluded_cases':[{'case_id':c.id,'status':c.status,'reason':'Ngoài phạm vi áp dụng.'} for c in workspace.cases if c.id not in case_ids],
        'automation_exclusions':[],'coverage':coverage,'snapshot':snapshot,'reviewer':actor,'reason':reason,'acknowledged_gaps':acknowledge_gaps}
    manifest={'schema_version':'worker-artifacts-1','source_revision':workspace.source_revision,'cases':[c.model_dump() for c in cases],
        'bindings':[workspace.worker_artifacts[c.id].model_dump() for c in cases if c.id in workspace.worker_artifacts],
        'plans':[workspace.bindings[c.id].model_dump() for c in cases if c.id not in workspace.worker_artifacts],
        'sources':workspace.sources.model_dump(),'publication_id':publication['publication_id']}
    auto_ids={p['case_id'] for p in manifest['plans'] if p.get('worker','auto')=='auto'}
    if any(set(p['executable']['depends_on'])-auto_ids for p in manifest['plans']):raise ValueError('Dependency phải thuộc cùng nhóm runner plans đã chọn.')
    version=hashlib.sha256(canonical(manifest)).hexdigest()[:20]
    directory=settings.agent_qc_tda_dir/'projects'/workspace.project_id/'baselines'/version
    directory.mkdir(parents=True,exist_ok=False);(directory/'worker_artifacts.json').write_bytes(canonical(manifest))
    publication['qc_baseline']=version
    baseline=ManagedBaseline(project_id=workspace.project_id,version=version,job_id='design_'+uuid4().hex,source_revision=workspace.source_revision,
        baseline_dir=str(directory),state='approved',reviewer=actor,execution_kind='worker_artifacts')
    return publication,baseline


def validate_plan(case,binding,sources):
    from tda.application.worker_routes import route_issues
    if binding.state!='validated' or not binding.reviewer or binding.case_revision!=case.revision or binding.design_sha256!=design_digest(case):return ['Plan chưa được xác nhận hoặc không khớp revision.']
    return route_issues(binding)+binding.validation_issues+binding_design_issues(case,binding.executable)+mapping_issues(case,binding)+[i.message for i in TestValidator().validate([binding.executable],sources).issues]


def load_bundle(directory,version):
    payload=(Path(directory)/'worker_artifacts.json').read_bytes()
    if len(payload)>10000000 or hashlib.sha256(payload).hexdigest()[:20]!=version:raise ValueError('Artifact baseline hash mismatch')
    data=json.loads(payload)
    if data.get('schema_version')!='worker-artifacts-1':raise ValueError('Unsupported artifact contract')
    cases={c.id:c for c in [DesignCase.model_validate(value) for value in data['cases']]}
    bindings=[WorkerArtifactBinding.model_validate(value) for value in data['bindings']]
    plans=[AutomationBinding.model_validate(value) for value in data.get('plans',[])]
    identifiers=[b.case_id for b in bindings+plans]
    if set(cases)!=set(identifiers) or len(identifiers)!=len(set(identifiers)):raise ValueError('Artifact inventory mismatch')
    for binding in bindings:
        case=cases.get(binding.case_id)
        if not case or case.status!='approved' or binding.state!='validated' or not binding.reviewer or binding.validation_issues or artifact_issues(case,binding):raise ValueError('Unapproved/stale artifact in baseline')
    if plans:
        from tda.domain.contracts import SourceBundle
        sources=SourceBundle.model_validate(data['sources'])
        for plan in plans:
            case=cases[plan.case_id]
            if case.status!='approved' or validate_plan(case,plan,sources):raise ValueError('Unapproved/stale plan in baseline')
        selected={p.case_id for p in plans if p.worker=='auto'}
        if any(set(p.executable.depends_on)-selected for p in plans):raise ValueError('Plan dependency outside approved baseline')
    return bindings,plans


def load_artifacts(directory,version):
    return load_bundle(directory,version)[0]
