"""Import one preflight-checked proposal for UI review; never approve or publish it."""
import argparse
import sqlite3
from pathlib import Path
from app.infrastructure.settings import Settings
from app.infrastructure.persistence.design_workspace import WorkspaceStoreMixin
from tda.domain.workspace import TestWorkspace
from tda.application.workspace import design_digest
from tda.application.worker_artifacts import artifact_issues


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot',type=Path)
    parser.add_argument('case_id')
    args=parser.parse_args()
    snapshot=TestWorkspace.model_validate_json(args.snapshot.read_text(encoding='utf-8'))
    proposal=snapshot.worker_artifacts[args.case_id]
    if proposal.state!='proposed' or proposal.reviewer or proposal.preflight.get('status') not in ('pass','defect') or proposal.validation_issues:
        raise ValueError('Only unapproved, preflight-checked proposals may be imported')
    # Use workspace persistence without DesignStore startup recovery, which resets running jobs.
    class Store(WorkspaceStoreMixin):
        def connect(self):return sqlite3.connect('file:'+str((Settings().agent_qc_data_dir/'agent-qc.sqlite3').resolve())+'?mode=rw',uri=True,timeout=30)
    store=Store();current=store.get_workspace(snapshot.project_id)
    if current is None:raise ValueError('Workspace unavailable')
    def mutate(workspace):
        case=next(c for c in workspace.cases if c.id==args.case_id)
        if workspace.source_revision!=snapshot.source_revision or case.status!='approved' or case.revision!=proposal.case_revision or design_digest(case)!=proposal.design_sha256:
            raise ValueError('Source/case has changed since smoke; regenerate instead')
        issues=artifact_issues(case,proposal)
        if issues:raise ValueError('; '.join(issues))
        existing=workspace.worker_artifacts.get(case.id)
        if existing and existing.state=='validated':raise ValueError('Do not replace an artifact already approved by a maintainer')
        workspace.bindings.pop(case.id,None)
        workspace.worker_artifacts[case.id]=proposal.model_copy(deep=True)
        workspace.automation_preparation[case.id]=dict(snapshot.automation_preparation[case.id])
        if proposal.profile not in workspace.execution_profiles:workspace.execution_profiles.append(proposal.profile.model_copy(deep=True))
        workspace.last_changes={'automation_prepared':[case.id],'smoke_snapshot':str(args.snapshot),'added':[],'changed':[],'retired':[],'proposed':[]}
    result=store.mutate_workspace(snapshot.project_id,current.version,'system:artifact-smoke','import_prepared_artifact',mutate)
    print('Workspace',result.version,'case',args.case_id,'needs_review; baseline unchanged')


if __name__=='__main__':main()
