"""Activate only independently validated automation from an explicit maintainer request."""
from tda.application.workspace import design_digest
from tda.application.worker_artifacts import artifact_issues
from tda.application.automation import preparation_state


def activation_candidates(workspace,case_ids,actor):
    ready=[];blocked=[]
    for case in workspace.cases:
        if case.id not in case_ids:continue
        state=workspace.automation_preparation.get(case.id,{})
        artifact=workspace.worker_artifacts.get(case.id)
        blockers=[f.message for f in workspace.findings if f.severity=='blocker' and (not f.case_ids or case.id in f.case_ids)]
        if artifact and (artifact.preflight.get('status')!='pass' or artifact.validation_issues
            or artifact_issues(case,artifact) or state.get('runtime_blockers')
            or state.get('source_revision')!=workspace.source_revision):
            blockers.append(state.get('reason') or 'Artifact chưa đạt preflight PASS cho source hiện tại.')
        if (case.status=='approved' and artifact and not blockers
            and artifact.case_revision==case.revision and artifact.design_sha256==design_digest(case)
            and state.get('source_revision')==workspace.source_revision
            and state.get('status')=='needs_review' and not state.get('runtime_blockers')
            and not artifact.validation_issues and not artifact_issues(case,artifact)
            and artifact.preflight.get('status')=='pass'):
            artifact.state='validated';artifact.reviewer='system:enable-qc:'+actor
        if case.status=='approved' and not blockers and preparation_state(workspace,case)=='ready':
            ready.append(case.id)
        else:
            blocked.append({'case_id':case.id,'reason':'; '.join(blockers) or state.get('reason') or 'Automation chưa được kiểm chứng.',
                'prerequisites':state.get('prerequisites',[])})
    return ready,blocked
