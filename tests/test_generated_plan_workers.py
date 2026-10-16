"""Exercise the QC bridge itself; external transports and AI grades remain deterministic."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.infrastructure.settings import Settings
from app.domain.models import WorkerTask,TaskTarget,WorkerResult
from app.infrastructure.execution.generated_plans import run_plans
from app.infrastructure.execution.generated_external import external_case

spec=importlib.util.spec_from_file_location('native_fixtures',Path(__file__).resolve().parents[1]/'packages/test-design-agent/tests/test_native_runners.py')
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
service=fixtures.service


def test_qc_bridge_http_browser_k6_and_deterministic_ai(tmp_path,service):
    from tda.domain.workspace import AutomationBinding
    binary=Path('.tools/k6/k6.exe').resolve()
    if not binary.exists():binary=Path('C:/Program Files/k6/k6.exe')
    assert binary.exists(),'k6 is required for this integration check'
    cases=fixtures.native_cases()[1:]
    # This check exercises deterministic AI assertions, not an extra provider call.
    ai=next(c for c in cases if c.runner=='ai');ai.grading=None
    ai.oracle.steps[0].assertions=[fixtures.assertion('json_equals','5','/answer')]
    plans=[AutomationBinding(case_id=c.id,case_revision=1,design_sha256='fixture',executable=c) for c in cases]
    settings=Settings(_env_file=None,agent_qc_k6_binary=binary)
    registered=SimpleNamespace(installation_id=10,repository_id=20,allow_mutations=False)
    task=WorkerTask(run_id='bridge',capability='baseline.http',objective='Reviewed suite',target=TaskTarget(project_id='project',type='http_service',ref=service[0]))
    result=run_plans(plans,tmp_path,settings,task,registered,tmp_path)
    assert result.verdict=='pass',result.model_dump()
    assert {r['worker_id'] for r in result.summary['results']}=={'playwright-generated','k6-generated','http-generated','ai-evaluation-generated'}
    service[1].broken=True
    result=run_plans(plans,tmp_path,settings,task,registered,tmp_path)
    assert result.verdict=='fail',result.model_dump()
    assert any(r['status']=='defect' and r['case_id']=='load' for r in result.summary['results'])


def test_hercules_receives_exact_reviewed_assertions(tmp_path):
    from tda.domain.workspace import AutomationBinding
    case=fixtures.native_cases()[1]
    plan=AutomationBinding(case_id=case.id,case_revision=1,design_sha256='fixture',executable=case,worker='hercules')
    task=WorkerTask(run_id='external',capability='baseline.http',objective='Approved',target=TaskTarget(project_id='project',type='http_service',ref='http://127.0.0.1:1234'))
    received=[]
    class Runtime:
        registry=SimpleNamespace(resolve=lambda task:SimpleNamespace(agent_id='hercules',name='Hercules'))
        def execute(self,child,project,workspace):
            received.append(child)
            return WorkerResult(task_id=child.task_id,run_id=child.run_id,capability=child.capability,worker_kind='external_agent',worker_id='hercules',execution_status='completed',verdict='pass',summary={'tests':1,'failures':0,'errors':0,'skipped':0})
    row=external_case(plan,case,task,tmp_path,Runtime())
    assert row['status']=='pass' and row['case_id']==case.id
    assert 'dom_text_equals' in received[0].parameters['feature']
    assert '#result' in received[0].parameters['feature']
    assert 'objective should complete' not in received[0].parameters['feature']


def test_keploy_requires_recordings_and_strix_never_runs_on_pr(tmp_path):
    from tda.domain.workspace import AutomationBinding
    from tda.application.worker_routes import route_issues
    cases=fixtures.native_cases()
    case=next(c for c in cases if c.kind=='integration')
    plan=AutomationBinding(case_id=case.id,case_revision=1,design_sha256='fixture',executable=case,worker='keploy',worker_parameters={'keploy_path':'keploy','sut_command':'python app.py','recording_sha256':{'test-set-0/tests/test-1.yaml':'0'*64}})
    assert not route_issues(plan)
    task=WorkerTask(run_id='external',capability='baseline.http',objective='Approved',target=TaskTarget(project_id='project'),parameters={'trigger_type':'pull_request'})
    runtime=SimpleNamespace(execute=lambda *a:pytest.fail('Worker must not execute without prerequisites'))
    assert external_case(plan,case,task,tmp_path,runtime)['status']=='blocked'
    case=next(c for c in cases if c.kind=='security')
    plan=AutomationBinding(case_id=case.id,case_revision=1,design_sha256='fixture',executable=case,worker='strix',worker_parameters={'max_budget_usd':1})
    assert 'scheduled' in external_case(plan,case,task,tmp_path,runtime)['reason']


def test_keploy_actual_observations_verify_approved_oracle_and_ignore_old_reports(tmp_path):
    import yaml
    from workers.integration_keploy.service import parse
    from workers.common import AgentRequest,SourceRef,PreparedCommand
    from app.domain.models import ProjectDescriptor
    from app.infrastructure.execution.replay_evidence import verify_replay
    root=tmp_path/'keploy';reports=root/'reports/test-run-0';reports.mkdir(parents=True)
    report=reports/'test-set-0.yaml'
    payload={'tests':[{'test_case_id':'test-1','status':'PASSED','req':{'method':'GET','url':'http://localhost:8000/sum','body':'','header':{'Authorization':'Bearer hidden'}},
        'result':{'status_code':{'actual':200},'body_result':[{'actual':'{"result":5}'}]}}]}
    report.write_text(yaml.safe_dump(payload),encoding='utf-8')
    task=WorkerTask(run_id='replay',capability='integration.service',objective='Integration',target=TaskTarget(project_id='project'))
    request=AgentRequest(task=task,project=ProjectDescriptor(id='project',language='python'),source=SourceRef(type='local_path',ref=str(tmp_path)))
    prepared=PreparedCommand(argv=[],cwd=tmp_path,metadata={'test_path':str(root),'before_reports':{}})
    result=parse(request,0,'',prepared)
    assert 'Authorization' not in str(result.summary)
    case=fixtures.executable('replay','http',[fixtures.request('/sum')],[[fixtures.assertion('status',200),fixtures.assertion('json_equals',5,'/result')]],kind='integration')
    observations=result.summary['observations']
    assert verify_replay(case,observations)[0]=='pass'
    observations[0]['body']='{"result":6}'
    assert verify_replay(case,observations)[0]=='defect'
    assert verify_replay(case,[])[0]=='blocked'
    import hashlib
    prepared.metadata['before_reports']={str(report):hashlib.sha256(report.read_bytes()).hexdigest()}
    assert parse(request,0,'',prepared).summary['observations']==[]


def test_strix_scan_does_not_substitute_for_http_assertions(tmp_path,service):
    from tda.domain.workspace import AutomationBinding
    case=next(c for c in fixtures.native_cases() if c.kind=='security')
    plan=AutomationBinding(case_id=case.id,case_revision=1,design_sha256='fixture',executable=case,worker='strix',worker_parameters={'max_budget_usd':1})
    task=WorkerTask(run_id='nightly',capability='baseline.http',objective='Security',target=TaskTarget(project_id='project',type='http_service',ref=service[0]),parameters={'trigger_type':'schedule'})
    class Runtime:
        registry=SimpleNamespace(resolve=lambda task:SimpleNamespace(agent_id='strix-nightly',name='Strix'))
        def execute(self,child,project,workspace):
            assert child.parameters['trigger_type']=='schedule'
            return WorkerResult(task_id=child.task_id,run_id=child.run_id,capability=child.capability,worker_kind='external_agent',worker_id='strix-nightly',execution_status='completed',verdict='pass')
    result=external_case(plan,case,task,tmp_path,Runtime())
    assert result['status']=='pass' and result['assertion_verification']['steps'][0]['status_code']==403
    case.oracle.steps[0].assertions[0].expected_json='200'
    assert external_case(plan,case,task,tmp_path,Runtime())['status']=='defect'
