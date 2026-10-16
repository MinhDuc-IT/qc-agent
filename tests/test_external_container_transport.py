from types import SimpleNamespace
from app.domain.models import ExternalAgentManifest,WorkerTask,TaskTarget,ProjectDescriptor,WorkerResult
from app.infrastructure.external_agents.http import HttpExternalAgentTransport


def test_container_address_mapping_preserves_approved_assertions(monkeypatch,tmp_path):
    original='http://127.0.0.1:8080'
    task=WorkerTask(run_id='bridge',capability='functional.e2e',objective='Approved UI',
        target=TaskTarget(type='web_app',project_id='project',ref=original),
        parameters={'feature':f'Feature: Test\n  Given I open "{original}/"\n  Then the configured public URL is "{original}"'})
    manifest=ExternalAgentManifest(agent_id='hercules',name='Hercules',version='1',capabilities=['functional.e2e'],supported_targets=['web_app'],
        endpoint='http://127.0.0.1:8101',source_root='/workspaces',target_host_override='host.docker.internal')
    payloads=[]
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,path,json):
            payloads.append(json)
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'run_id':'remote'})
        def get(self,path):
            result=WorkerResult(task_id=task.task_id,run_id=task.run_id,capability=task.capability,worker_kind='external_agent',execution_status='completed',verdict='pass')
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'status':'completed','result':result.model_dump()})
    monkeypatch.setattr('app.infrastructure.external_agents.http.httpx.Client',Client)
    HttpExternalAgentTransport(0).execute(manifest,task,ProjectDescriptor(id='project',language='python'),tmp_path/'checkout')
    wire=payloads[0]
    assert wire['source']['ref']=='/workspaces/checkout'
    assert wire['task']['target']['ref']=='http://host.docker.internal:8080'
    assert 'Given I open "http://host.docker.internal:8080/"' in wire['task']['parameters']['feature']
    assert f'Then the configured public URL is "{original}"' in wire['task']['parameters']['feature']
    assert task.target.ref==original and task.parameters['feature'].count(original)==2


def test_disabled_worker_preserves_blocker_and_never_falls_back(tmp_path):
    from app.infrastructure.external_agents.registry import ExternalAgentRegistry
    from app.infrastructure.external_agents.runtime import ExternalAgentRuntime
    from app.infrastructure.execution.hybrid import HybridWorkerRuntime
    manifest=ExternalAgentManifest(agent_id='keploy',name='Keploy',version='3.6.126',capabilities=['integration.service'],supported_targets=['repository'],
        endpoint='http://127.0.0.1:8102',enabled=False,blocked_reason='Dedicated eBPF worker VM required')
    task=WorkerTask(run_id='blocked',capability='integration.service',objective='Integration',target=TaskTarget(project_id='project'))
    class Tools:
        def execute(self,*args):raise AssertionError('Maintainer-disabled worker cannot silently fall back')
    result=HybridWorkerRuntime(ExternalAgentRuntime(ExternalAgentRegistry([manifest])),Tools()).execute(task,ProjectDescriptor(id='project',language='python'),tmp_path)
    assert result.verdict=='unknown' and result.summary['configuration_blocked']
    assert 'eBPF' in result.output
