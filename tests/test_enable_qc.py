import asyncio
from test_flow_design_app import flow_app
from test_generated_pytest import prepare_pytest


def test_enable_qc_prepares_confirms_and_activates_without_mapping_click(flow_app,tmp_path):
    client,store,project,engine,path,headers,w,case,root=prepare_pytest(flow_app,tmp_path)
    before=[c.model_dump() for c in w.cases]
    response=client.post(path+'/workspace/enable-qc',headers=headers,json={
        'version':w.version,'action':'enable_qc','case_ids':[case.id],
        'reason':'Use reviewed regression case for PR','acknowledge_gaps':True})
    assert response.status_code==202,response.text
    job=store.claim_job();asyncio.run(engine.execute(job))
    done=store.get_job(job.job_id)
    assert done.status=='completed',done.error_message
    current=store.get_workspace(project.project_id)
    assert [c.model_dump() for c in current.cases]==before
    assert current.worker_artifacts[case.id].state=='validated'
    assert current.worker_artifacts[case.id].reviewer
    active=store.get_project(project.project_id)
    assert active.active_baseline==done.baseline_version
    assert active.current_job_id==job.job_id
    assert active.state=='active'


def test_enable_qc_never_activates_failed_preflight_or_changes_approved_case(flow_app,tmp_path,monkeypatch):
    from app.infrastructure.test_design import pytest_builder
    from app.domain.models import WorkerResult
    client,store,project,engine,path,headers,w,case,root=prepare_pytest(flow_app,tmp_path)
    artifact=w.worker_artifacts[case.id]
    artifact.preflight={'status':'blocked','output':'Docker environment failed: daemon unavailable'}
    w=store.save_workspace(w,w.version,'test','blocked')
    def blocked(*args,**kwargs):
        return WorkerResult(task_id='preflight',run_id='prepare',capability='unit.run',worker_id='python-pytest-generated',worker_kind='tool',execution_status='failed',
            verdict='unknown',summary={'results':[{'case_id':case.id,'status':'blocked'}]},output='Docker environment failed: daemon unavailable')
    monkeypatch.setattr(pytest_builder,'run_artifacts',blocked)
    class NoRewrite:
        calls=0;max_calls=24
        def propose(self,*args):raise AssertionError('Infrastructure retry must reuse reviewed script')
    engine.agent_factory=lambda _:NoRewrite()
    response=client.post(path+'/workspace/enable-qc',headers=headers,json={
        'version':w.version,'action':'enable_qc','case_ids':[case.id],'reason':'Test blocked activation','acknowledge_gaps':True})
    assert response.status_code==202,response.text
    job=store.claim_job();asyncio.run(engine.execute(job))
    assert store.get_job(job.job_id).status=='partial'
    assert not store.get_project(project.project_id).active_baseline
    assert store.get_workspace(project.project_id).cases==w.cases


def test_enable_qc_publishes_only_passing_subset_with_explicit_consent(flow_app,tmp_path):
    from tda.domain.worker_artifacts import PytestDraft
    client,store,project,engine,path,headers,w,case,root=prepare_pytest(flow_app,tmp_path)
    other=w.cases[0].model_copy(deep=True);other.id='BLOCKED';w.cases.append(other)
    w.flow_modules[0]['case_ids'].append(other.id)
    w=store.save_workspace(w,w.version,'test','add_blocked')
    class MissingCapability:
        calls=0;max_calls=24
        def propose(self,*args):
            self.calls+=1
            return PytestDraft(disposition='needs_setup',reason='Live runner unavailable',prerequisites=['Live runner fixture'])
    engine.agent_factory=lambda _:MissingCapability()
    response=client.post(path+'/workspace/enable-qc',headers=headers,json={
        'version':w.version,'case_ids':[case.id,other.id],'reason':'Apply supported subset','acknowledge_gaps':True})
    assert response.status_code==202,response.text
    job=store.claim_job();asyncio.run(engine.execute(job))
    done=store.get_job(job.job_id)
    assert done.status=='partial' and done.baseline_version,done.error_message
    publications=store.workspace_publications(project.project_id)
    assert publications[0]['executed_case_ids']==[case.id]
    assert publications[0]['automation_exclusions'][0]['case_id']==other.id


def test_enable_qc_requires_approved_cases_and_explicit_endpoint(flow_app,tmp_path):
    client,store,project,engine,path,headers,w,case,root=prepare_pytest(flow_app,tmp_path)
    response=client.post(path+'/workspace/jobs',headers=headers,json={'version':w.version,'action':'enable_qc','case_ids':[case.id]})
    assert response.status_code==409
    response=client.post(path+'/workspace/enable-qc',headers=headers,json={'version':w.version,'action':'enable_qc','case_ids':[case.id]})
    assert response.status_code==422


def test_browser_primary_enable_action_preserves_selection_and_queues_job(flow_app,tmp_path):
    import pytest
    from urllib.parse import urlsplit
    playwright=pytest.importorskip('playwright.sync_api')
    client,store,project,engine,path,headers,w,case,root=prepare_pytest(flow_app,tmp_path)
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
            page.get_by_role('checkbox',name='Chọn '+case.id,exact=True).check()
            assert not page.get_by_role('button',name='Chuẩn bị cho QC',exact=True).is_visible()
            page.get_by_role('button',name='Bật cho QC',exact=True).click()
            panel=page.locator('.design-publish');panel.locator('input[type=checkbox]').check()
            panel.get_by_role('button',name='Bật cho QC',exact=True).click()
            page.wait_for_function("document.querySelector('.design-status .spinner') !== null")
            assert page.get_by_role('checkbox',name='Chọn '+case.id,exact=True).is_checked()
            job=store.get_job(store.get_project(project.project_id).current_job_id)
            assert job.workspace_request['action']=='enable_qc'
            assert job.workspace_request['case_ids']==[case.id]
            assert not errors
        finally:browser.close()
