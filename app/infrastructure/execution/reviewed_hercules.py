"""Pass descriptions to Hercules and require evidence for every approved expectation."""
from ...domain.models import WorkerTask,TaskTarget,ProjectDescriptor


def run_hercules_case(packet,task,workspace,runtime):
    row={'case_id':packet.case.id,'case_revision':packet.case.revision,'design_sha256':packet.design_sha256,
        'source':'tda_baseline','worker_id':'hercules','status':'blocked',
        'expected_results':[{'expected_key':f'step:{i}/expected:{j}','status':'blocked'} for i,s in enumerate(packet.case.steps) for j,_ in enumerate(s.expected_results)]}
    if runtime is None:return {**row,'reason':'Hercules runtime chưa được cấu hình.'}
    if not task.target.ref:return {**row,'reason':'Worker thiếu URL môi trường test của PR.'}
    target_type='web_app' if packet.worker_capability=='functional.e2e' else 'http_service'
    candidate=next((m for m in runtime.registry.manifests if m.enabled and 'hercules' in (m.agent_id+' '+m.name).lower()
        and packet.worker_capability in m.capabilities and target_type in m.supported_targets),None)
    if not candidate:return {**row,'reason':'Không có Hercules worker phù hợp đang bật.'}
    child=WorkerTask(run_id=task.run_id,capability=packet.worker_capability,objective=packet.case.title,worker_id=candidate.agent_id,
        target=TaskTarget(type=target_type,project_id=task.target.project_id,ref=task.target.ref),
        scope={'case_ids':[packet.case.id]},parameters={'reviewed_case':packet.model_dump()},
        execution_preference='external_agent',timeout_seconds=task.timeout_seconds)
    result=runtime.execute(child,ProjectDescriptor(id=task.target.project_id,language='mixed'),workspace)
    if result is None:return {**row,'reason':'Worker không trả kết quả.'}
    proof=result.summary.get('case_evidence',{})
    expected={f'step:{i}/expected:{j}' for i,s in enumerate(packet.case.steps) for j,_ in enumerate(s.expected_results)}
    evidence=proof.get('expected_results',[])
    if (proof.get('design_sha256')!=packet.design_sha256 or len(evidence)!=len(expected)
        or {e.get('expected_key') for e in evidence}!=expected
        or any(e.get('status') not in ('pass','defect','blocked') or not e.get('execution_report') for e in evidence)
        or result.execution_status!='completed'):
        return {**row,'reason':'Worker thiếu bằng chứng cho từng expected result đã duyệt.','output':result.output[-6000:]}
    status='blocked' if any(e['status']=='blocked' for e in evidence) else 'defect' if any(e['status']=='defect' for e in evidence) else 'pass'
    if result.exit_code not in (None,0,1) or (status=='pass' and (result.verdict!='pass' or result.exit_code not in (None,0))):status='blocked'
    return {**row,'status':status,'expected_results':evidence,'output':result.output[-6000:],
        'reason':'Hercules chưa có assertion evidence đầy đủ cho case.' if status=='blocked' else ''}
