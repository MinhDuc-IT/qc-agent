"""Dispatch one immutable reviewed case per external task and preserve its case ID."""
from tda.application.worker_routes import route_issues
from ...domain.models import WorkerTask,TaskTarget,ProjectDescriptor


def external_case(plan,case,task,workspace,runtime,auth_token=None):
    issues=route_issues(plan)
    row={'case_id':plan.case_id,'source':'generated','worker_id':plan.worker,'status':'blocked'}
    if issues:return {**row,'reason':'; '.join(issues)}
    if runtime is None:return {**row,'reason':'External worker runtime chưa được cấu hình.'}
    worker=plan.worker;parameters=dict(plan.worker_parameters)
    if worker=='hercules':
        if not task.target.ref:return {**row,'reason':'Hercules cần URL của môi trường test.'}
        import json
        # Preserve approved actions and assertions as data; do not substitute a generic objective.
        feature=['Feature: Approved generated cases',f'  Scenario: {case.id}',
                 '    Given I open the test target '+json.dumps(task.target.ref)]
        feature+=['    When I execute this approved browser action: '+json.dumps(a.model_dump(),ensure_ascii=False) for a in plan.executable.browser.actions]
        feature+=['    Then verify this exact approved assertion: '+json.dumps(a.model_dump(),ensure_ascii=False) for s in plan.executable.oracle.steps for a in s.assertions]
        parameters['feature']='\n'.join(feature)+'\n';capability='functional.e2e';target_type='web_app'
    elif worker=='keploy':
        from ..test_design.execution_profiles import safe_file
        path=safe_file(workspace,parameters['keploy_path'])
        if not path.is_dir() or not any(p.parent.name=='tests' for p in path.rglob('*.yaml')):return {**row,'reason':'Chưa có Keploy recordings cho binding đã review.'}
        import hashlib
        files=[p for p in path.rglob('*') if p.is_file() and p.suffix in ('.yaml','.yml') and 'reports' not in p.relative_to(path).parts]
        actual={str(p.relative_to(path)).replace('\\','/'):hashlib.sha256(safe_file(path,str(p.relative_to(path))).read_bytes()).hexdigest() for p in files}
        if actual!=parameters.pop('recording_sha256'):return {**row,'reason':'Keploy recordings đã thay đổi so với snapshot được review.'}
        capability='integration.service';target_type='repository'
        parameters['generated_case_id']=plan.case_id
    else:
        if task.parameters.get('trigger_type')!='schedule':return {**row,'reason':'Strix chỉ chạy scheduled/nightly; không chạy trên PR.'}
        parameters['trigger_type']='schedule';capability='security.dast';target_type='web_app' if task.target.ref else 'repository'
    child=WorkerTask(run_id=task.run_id,capability=capability,objective=case.title,
        target=TaskTarget(type=target_type,project_id=task.target.project_id,ref=task.target.ref),
        parameters=parameters,scope={'case_ids':[case.id],'scenarios':[case.title]},execution_preference='external_agent',timeout_seconds=task.timeout_seconds)
    available=getattr(runtime.registry,'manifests',None)
    if available is not None:
        candidate=next((m for m in available if worker in (m.agent_id+' '+m.name).lower() and capability in m.capabilities and target_type in m.supported_targets),None)
        if candidate is None:return {**row,'reason':'Không có đúng external worker đã chọn trong registry.'}
        if not candidate.enabled:return {**row,'reason':candidate.blocked_reason or 'External worker disabled'}
        child.worker_id=candidate.agent_id
    manifest=runtime.registry.resolve(child)
    if manifest is None or worker not in (manifest.agent_id+' '+manifest.name).lower():return {**row,'reason':'Không có đúng external worker đã chọn trong registry.'}
    result=runtime.execute(child,ProjectDescriptor(id=task.target.project_id,root='.',language='mixed'),workspace)
    if result is None:return {**row,'reason':'External worker không trả kết quả.'}
    row.update(worker_id=result.worker_id,output=result.output[-6000:])
    if worker=='keploy' and result.execution_status=='completed':
        from .replay_evidence import verify_replay
        row['status'],row['reason']=verify_replay(plan.executable,result.summary.get('observations',[]))
        row['worker_verdict']=result.verdict
    elif worker=='strix':
        row.update(worker_verdict=result.verdict,scan_findings=[f.model_dump() for f in result.findings])
        if result.execution_status!='completed' or result.verdict not in ('pass','fail') or result.summary.get('budget_stopped') or not task.target.ref:
            row['reason']='Scan chưa hoàn tất hoặc thiếu target cho assertion verification.'
        elif any(s.method not in ('GET','HEAD') for s in plan.executable.steps+plan.executable.cleanup):
            row['reason']='Security verification thay đổi dữ liệu cần fixture/reset riêng sau scan.'
        else:
            from tda.runner import HttpSuiteExecutor
            verification=HttpSuiteExecutor(auth_token=auth_token).execute([plan.executable.model_dump()],task.target.ref,False)['results'][0]
            row['status']=verification['status'] if verification['status'] in ('pass','defect') else 'blocked'
            row['assertion_verification']=verification
    elif result.execution_status=='completed' and result.verdict in ('pass','fail'):
        counts=result.summary
        if counts.get('tests')!=1 or counts.get('skipped',0) or counts.get('errors',0) or result.exit_code not in (None,0,1) or (result.verdict=='pass' and result.exit_code not in (None,0)):
            row['reason']='Hercules chưa có đúng một scenario đã chạy đầy đủ.'
        else:row['status']='pass' if result.verdict=='pass' else 'defect'
    return row
