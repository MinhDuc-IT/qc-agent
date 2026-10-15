"""Recover the last complete workspace round without new LLM calls or approvals."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.infrastructure.settings import Settings
from app.infrastructure.persistence.test_design import DesignStore
from app.domain.test_design import DesignJob
from tda.domain.workspace import TestWorkspace
from tda.application.workspace import refresh_workspace


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--job',required=True);parser.add_argument('--expected-version',required=True,type=int);args=parser.parse_args()
    settings=Settings();database=settings.agent_qc_data_dir/'agent-qc.sqlite3'
    with sqlite3.connect(f'file:{database.resolve().as_posix()}?mode=ro',uri=True) as db:
        row=db.execute('SELECT payload FROM design_jobs WHERE job_id=?',(args.job,)).fetchone()
        if not row:raise ValueError('Unknown job')
        job=DesignJob.model_validate_json(row[0])
    directory=Path(job.artifact_dir).resolve();allowed=settings.agent_qc_tda_dir.resolve()/'runs'/job.job_id
    if not directory.is_relative_to(allowed):raise ValueError('Artifact storage boundary mismatch')
    rounds=sorted(directory.glob('rounds/*/workspace.json'),key=lambda path:int(path.parent.name))
    if not rounds:raise ValueError('No completed round to recover')
    workspace=TestWorkspace.model_validate_json(rounds[-1].read_text(encoding='utf-8'))
    workspace.generation_diagnostic={'stage':'workspace.design','error_kind':'validation','schema_errors':[],
        'round':int(rounds[-1].parent.name)+2,'preserved_case_count':len(workspace.cases)}
    workspace.questions.append('Vòng sinh tiếp theo gặp lỗi parse/validation JSON. Draft đã được khôi phục từ vòng hoàn tất, không gọi lại LLM. Có thể Sinh bổ sung để tiếp tục.')
    refresh_workspace(workspace)
    # Use the existing store without constructor startup recovery/reset of running jobs.
    store=object.__new__(DesignStore);store.path=database
    result=store.recover_workspace_draft(job.job_id,workspace,args.expected_version)
    print(json.dumps({'job_id':job.job_id,'workspace_version':result.version,'recovered_cases':len(result.cases),'new_llm_calls':0}))


if __name__=='__main__':main()
