import asyncio
import shutil
from types import SimpleNamespace
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.infrastructure.persistence.sqlite import SqliteRunStore
from app.infrastructure.persistence.test_design import DesignStore
from app.infrastructure.settings import Settings
from app.infrastructure.test_design.engine import ManagedDesignEngine
from app.application.project_onboarding import ProjectOnboarding
from app.api.design_portal import create_design_router
from test_flow_design import FakeInvestigator


@pytest.fixture
def flow_app(tmp_path,monkeypatch):
    store=DesignStore(SqliteRunStore(tmp_path/'state').path)
    repository={'id':20,'full_name':'owner/project','default_branch':'main'}
    project=ProjectOnboarding(store).register_repository(10,repository)
    doc=store.add_document(project.project_id,'requirements.md','prd','Sum of 2 and 3 is 5.\n')
    project.uploaded_document_ids=[doc['id']];store.save_project(project)
    # Fixture agent's evidence ID is deliberately stable across user-upload naming.
    identity=SimpleNamespace(login='maintainer',csrf='csrf-fixture',user_id=9)
    monkeypatch.setattr(store,'session',lambda _:identity)
    class Github:
        async def repository_permission(self,*args):return 'write'
        async def describe_repository(self,*args):return {'sha':'revision1','repository':repository}
    source_root=tmp_path/'source';source_root.mkdir();(source_root/'service.py').write_text('def add(a, b):\n    return a + b\n')
    class Source:
        prepared=0;cleaned=0
        async def prepare(self,run):
            self.prepared+=1;path=tmp_path/run.run_id;shutil.copytree(source_root,path);return path
        def cleanup(self,path):self.cleaned+=1;shutil.rmtree(path)
    source=Source();github=Github()
    settings=Settings(_env_file=None,agent_qc_tda_dir=tmp_path/'design',agent_qc_public_url='http://flow.local',agent_qc_tda_max_rounds=1)
    app=FastAPI();app.include_router(create_design_router(settings,store,ProjectOnboarding(store),github,SimpleNamespace(callback_url='http://flow.local/auth/github/callback')))
    class Agent(FakeInvestigator):
        def propose(self,stage,instructions,context,output):
            result=super().propose(stage,instructions,context,output)
            def rewrite(value):
                if isinstance(value,dict):
                    if value.get('source_id')=='prd':value['source_id']=doc['id']
                    for v in value.values():rewrite(v)
                elif isinstance(value,list):
                    for v in value:rewrite(v)
            data=result.model_dump();rewrite(data);return output.model_validate(data)
    engine=ManagedDesignEngine(store,github,source,settings,agent_factory=lambda _:Agent())
    with TestClient(app) as client:yield client,store,project,engine,source


def test_install_does_not_generate_and_explicit_job_uses_new_tools(flow_app):
    client,store,project,engine,source=flow_app
    assert project.state=='not_started' and store.claim_job() is None
    path='/api/v1/projects/'+project.project_id
    response=client.post(path+'/regenerate',headers={'X-CSRF-Token':'csrf-fixture'},json={})
    assert response.status_code==202,response.text
    job=store.claim_job();asyncio.run(engine.execute(job))
    saved=store.get_job(job.job_id);assert saved.status=='completed',saved.error_message
    w=store.get_workspace(project.project_id)
    assert w.design_method=='flow_investigation' and len(w.cases)==1
    assert w.completeness['gate']=='PASS'
    assert source.prepared==source.cleaned==1
    assert client.get(path+'/workspace').json()['workspace']['flow_modules'][0]['study']['flow']
    assert client.get(path+'/workspace/completeness?format=markdown').status_code==200
    assert store.get_project(project.project_id).active_baseline is None


def test_new_flow_requests_have_csrf_and_module_validation(flow_app):
    client,store,project,engine,source=flow_app;path='/api/v1/projects/'+project.project_id
    assert client.post(path+'/regenerate',json={}).status_code==403
    client.post(path+'/regenerate',headers={'X-CSRF-Token':'csrf-fixture'},json={})
    asyncio.run(engine.execute(store.claim_job()));w=store.get_workspace(project.project_id)
    body={'version':w.version,'action':'review','module_ids':['UNKNOWN']}
    assert client.post(path+'/workspace/jobs',headers={'X-CSRF-Token':'csrf-fixture'},json=body).status_code==409
    body['module_ids']=['SUM']
    response=client.post(path+'/workspace/jobs',headers={'X-CSRF-Token':'csrf-fixture'},json=body)
    assert response.status_code==202,response.text
    asyncio.run(engine.execute(store.claim_job()))
    assert store.get_workspace(project.project_id).version==w.version+1


def test_project_progress_exposes_module_and_current_call_budget(flow_app):
    from pathlib import Path
    import json
    client,store,project,engine,source=flow_app
    client.post('/api/v1/projects/'+project.project_id+'/regenerate',headers={'X-CSRF-Token':'csrf-fixture'},json={})
    job=store.claim_job();asyncio.run(engine.execute(job))
    directory=Path(store.get_job(job.job_id).artifact_dir)
    directory.joinpath('generation_progress.json').write_text(json.dumps({'module_title':'Addition','modules_total':2,'modules_with_cases':1,'calls_used':4,'calls_limit':24}),encoding='utf-8')
    directory.joinpath('events.jsonl').write_text(json.dumps({'stage':'flow.review','status':'llm_started','details':{'call':5}})+'\n',encoding='utf-8')
    progress=client.get('/api/v1/projects/'+project.project_id).json()['progress']
    assert progress['module_title']=='Addition' and progress['calls_used']==5
    assert progress['calls_remaining']==19


def test_browser_shows_flow_sources_and_case_table(flow_app):
    playwright=pytest.importorskip('playwright.sync_api')
    from urllib.parse import urlsplit
    client,store,project,engine,source=flow_app
    client.post('/api/v1/projects/'+project.project_id+'/regenerate',headers={'X-CSRF-Token':'csrf-fixture'},json={})
    asyncio.run(engine.execute(store.claim_job()))
    with playwright.sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            def route(request):
                incoming=request.request;url=urlsplit(incoming.url)
                response=client.request(incoming.method,url.path+('?' +url.query if url.query else ''),content=incoming.post_data,
                    headers={k:v for k,v in incoming.headers.items() if k.lower() in ('content-type','x-csrf-token')})
                request.fulfill(status=response.status_code,body=response.content,headers={'Content-Type':response.headers.get('content-type','application/json')})
            page.route('http://flow.local/**',route);page.goto('http://flow.local/app')
            page.get_by_role('button',name='Chọn repository').click();page.locator('#projects button').first.click()
            page.locator('.design-case-table').wait_for()
            assert page.locator('.design-case-table').inner_text().find('Add 2 and 3')>=0
            assert 'Modules' in page.locator('#review').inner_text()
            settings_tab=page.locator('.repo-tab[data-tab="settings"]')
            settings_tab.click()
            workspace=client.get('/api/v1/projects/'+project.project_id+'/workspace').json()
            workspace['workspace']['version']+=1
            page.route('**/workspace',lambda route:route.fulfill(json=workspace))
            page.evaluate('loadProject(true)')
            assert settings_tab.get_attribute('aria-selected')=='true'
            assert not page.locator('#review').is_visible()
            # A delayed background response must respect the user's latest tab.
            page.evaluate('''async () => {
                setTab('baselines');
                const originalFetch=window.fetch;
                window.fetch=async (...args) => {
                    await new Promise(resolve => setTimeout(resolve,100));
                    return originalFetch(...args);
                };
                const pending=loadProject(true);
                setTab('settings');
                try { await pending; } finally { window.fetch=originalFetch; }
            }''')
            assert settings_tab.get_attribute('aria-selected')=='true'
            assert not errors
        finally:browser.close()


def test_incomplete_generation_is_partial_not_completed(flow_app):
    from test_flow_design import FakeInvestigator
    client,store,project,engine,source=flow_app
    original_factory=engine.agent_factory
    def failing_reviewer(artifacts):
        agent=original_factory(artifacts);agent.fail_review=True;return agent
    engine.agent_factory=failing_reviewer
    client.post('/api/v1/projects/'+project.project_id+'/regenerate',headers={'X-CSRF-Token':'csrf-fixture'},json={})
    job=store.claim_job()
    asyncio.run(engine.execute(job))
    saved=store.get_job(job.job_id)
    assert saved.status=='partial',saved.error_message
    assert store.get_workspace(project.project_id) is not None
    assert store.get_project(project.project_id).state=='needs_changes'
