"""Pin reviewed descriptions and optional automation caches outside PR control."""
import hashlib,json
from uuid import uuid4
from tda.domain.case_execution import ReviewedCasePacket
from tda.application.workspace import coverage_for
from ...domain.test_design import ManagedBaseline


def publish_reviewed(settings,workspace,packets,blocked,actor,reason,acknowledge_gaps):
    if not packets:raise ValueError('Không có case được worker hỗ trợ.')
    if blocked and not acknowledge_gaps:raise ValueError('Có case chưa được worker hỗ trợ; cần xác nhận phạm vi subset.')
    cases=[p.case for p in packets];coverage=coverage_for(workspace,cases)
    if (coverage['missing_criteria'] or coverage['missing_techniques']) and not acknowledge_gaps:
        raise ValueError('Cần xác nhận coverage gap trước khi áp dụng subset.')
    ids=[c.id for c in cases]
    context={k:workspace.model_dump()[k] for k in ('project_id','source_revision','sources','knowledge','criteria','strategy')}
    manifest={'schema_version':'reviewed-cases-1','packets':[p.model_dump() for p in packets],
        'context':context,'reviewer':actor,'reason':reason,'publication_id':uuid4().hex}
    payload=json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    version=hashlib.sha256(payload).hexdigest()[:20]
    directory=settings.agent_qc_tda_dir/'projects'/workspace.project_id/'baselines'/version
    directory.mkdir(parents=True,exist_ok=False);(directory/'reviewed_cases.json').write_bytes(payload)
    for packet in packets:
        workspace.qc_enrollment[packet.case.id]={'status':'awaiting_qc','worker':packet.worker,
            'case_revision':packet.case.revision,'design_sha256':packet.design_sha256,'baseline_version':version,
            'reason':'Chờ QC thực thi trên PR. Preflight tại TDA là tùy chọn.','requirements':packet.requirements}
    for item in blocked:
        workspace.qc_enrollment[item['case_id']]={'status':'unsupported','reason':item['reason'],
            'case_revision':next(c.revision for c in workspace.cases if c.id==item['case_id'])}
    # The new baseline replaces the prior selection; do not display excluded cases as enrolled.
    workspace.qc_enrollment={k:v for k,v in workspace.qc_enrollment.items() if k in ids or k in {b['case_id'] for b in blocked}}
    snapshot=workspace.model_dump();snapshot['cases']=[c.model_dump() for c in cases]
    publication={'version':version,'publication_id':manifest['publication_id'],'workspace_version':workspace.version,
        'source_revision':workspace.source_revision,'reviewer':actor,'reason':reason,
        'selected_case_ids':ids,'executed_case_ids':[], 'scheduled_case_ids':ids,'qc_baseline':version,
        'excluded_cases':[{'case_id':c.id,'reason':'Ngoài phạm vi baseline đã chọn.'} for c in workspace.cases if c.id not in ids],
        'automation_exclusions':blocked,'acknowledged_gaps':acknowledge_gaps,'coverage':coverage,'snapshot':snapshot}
    baseline=ManagedBaseline(project_id=workspace.project_id,version=version,source_revision=workspace.source_revision,
        job_id='design_'+uuid4().hex,baseline_dir=str(directory),state='approved',reviewer=actor,execution_kind='reviewed_cases')
    return publication,baseline


def load_reviewed(directory,version):
    raw=(directory/'reviewed_cases.json').read_bytes()
    if len(raw)>30000000 or hashlib.sha256(raw).hexdigest()[:20]!=version:raise ValueError('Reviewed baseline hash mismatch')
    data=json.loads(raw)
    if data.get('schema_version')!='reviewed-cases-1':raise ValueError('Unknown reviewed baseline schema')
    packets=[ReviewedCasePacket.model_validate(p) for p in data['packets']]
    if not packets or len({p.case.id for p in packets})!=len(packets):raise ValueError('Duplicate/empty reviewed case packets')
    return packets,data['context']
