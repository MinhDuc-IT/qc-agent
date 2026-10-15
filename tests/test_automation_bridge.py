import asyncio
import json
from pathlib import Path
import pytest
from test_flow_design_app import flow_app
from tda.domain.automation import AutomationDraft
from tda.domain.contracts import ExecutableCase,UnitCall,CaseOracle,StepOracle,Assertion
from tda.domain.workspace import AssertionMapping


def prepared_case(flow_app,disposition='needs_review',omit_mapping=False):
    client,store,project,engine,source=flow_app
    path='/api/v1/projects/'+project.project_id
    headers={'X-CSRF-Token':'csrf-fixture'}
    client.post(path+'/regenerate',headers=headers,json={});asyncio.run(engine.execute(store.claim_job()))
    w=store.get_workspace(project.project_id);case=w.cases[0]
    response=client.post(path+'/workspace/actions',headers=headers,json={'version':w.version,'action':'approve_case','case_id':case.id,'reason':'Reviewed expected sum'})
    assert response.status_code==200,response.text
    class Agent:
        def propose(self,stage,instructions,context,output):
            assert stage=='automation.prepare'
            c=context['case'];evidence=c['steps'][0]['expected_results'][0]['evidence']
            if disposition=='manual':return AutomationDraft(case_id=c['id'],disposition='manual',reason='Needs human visual judgement')
            executable=ExecutableCase(id=c['id'],scenario_id=c['id'],title=c['title'],kind=c['kind'],runner='unit',steps=[],cleanup=[],depends_on=[],repetitions=1,notes=[],
                unit=UnitCall(language='python',module_path='service.py',function='add',args_json='[2,3]',kwargs_json='{}'),
                oracle=CaseOracle(case_id=c['id'],rationale='Reviewed sum',steps=[StepOracle(step_index=0,assertions=[Assertion(kind='return_equals',pointer='',expected_json='5',evidence=evidence)])]),
                requirement_ids=c['requirement_ids'],feature_ids=[],risk_ids=[],operation_ids=[])
            return AutomationDraft(case_id=c['id'],disposition=disposition,reason='Verify reviewed sum using existing unit runner',executable=executable,
                prerequisites=['Provision test fixture'] if disposition=='needs_setup' else [],
                expected_result_mapping=[] if omit_mapping else [AssertionMapping(design_step=0,expected_result=0,executable_step=0,assertion_indexes=[0],explanation='Return assertion verifies expected sum 5')])
    engine.agent_factory=lambda _:Agent()
    w=store.get_workspace(project.project_id)
    response=client.post(path+'/workspace/jobs',headers=headers,json={'version':w.version,'action':'bind','case_ids':[case.id]})
    assert response.status_code==202,response.text
    job=store.claim_job();asyncio.run(engine.execute(job))
    assert store.get_job(job.job_id).status=='completed',store.get_job(job.job_id).error_message
    return client,store,project,path,headers,store.get_workspace(project.project_id),case


def test_prepare_confirm_activate_baseline_and_qc_detects_regression(flow_app,tmp_path):
    from app.infrastructure.test_design.runtime import ManagedBaselineRuntime
    from app.application.project_onboarding import ManagedBaselineBinding
    from app.domain.models import QCRun,TriggerContext,Trigger,Actor,Installation,Revision,SourceAnalysis,ProjectDescriptor,ExecutionPlan
    from app.infrastructure.settings import Settings
    client,store,project,path,headers,w,case=prepared_case(flow_app)
    assert w.bindings[case.id].state=='bound' and w.bindings[case.id].reviewer is None
    assert store.get_project(project.project_id).active_baseline is None
    blocked=client.post(path+'/workspace/publish',headers=headers,json={'version':w.version,'case_ids':[case.id],'reason':'Try unreviewed','acknowledge_gaps':True,'activate_qc':True})
    assert blocked.status_code==409
    response=client.post(path+'/workspace/actions',headers=headers,json={'version':w.version,'action':'validate_binding','case_id':case.id,'reason':'Assertions match approved expected results'})
    assert response.status_code==200,response.text
    w=store.get_workspace(project.project_id)
    response=client.post(path+'/workspace/publish',headers=headers,json={'version':w.version,'case_ids':[case.id],'reason':'Activate reviewed unit test','acknowledge_gaps':True,'activate_qc':True})
    assert response.status_code==200,response.text
    active=store.get_project(project.project_id)
    assert active.active_baseline
    root=Path(store.get_baseline(project.project_id,active.active_baseline).baseline_dir).parents[3]
    code=tmp_path/'pr';code.mkdir();(code/'service.py').write_text('def add(a,b): return a+b\n')
    context=TriggerContext(trigger=Trigger(event='pull_request',action='opened',delivery_id='fixture',actor=Actor(id='1',login='maintainer')),
        installation=Installation(id=10),repository=project.repository,revision=Revision(head_sha='pr',base_sha='revision1',head_ref='feature',base_ref='main'))
    run=QCRun(trigger_context=context,analysis=SourceAnalysis(projects=[ProjectDescriptor(id='python',language='python')]),plan=ExecutionPlan())
    class Profiles:
        def materialize(self,*args):raise AssertionError('Unit does not need HTTP')
    binding=ManagedBaselineBinding(store,Profiles());binding.prepare(run);binding.attach(run)
    runtime=ManagedBaselineRuntime(store,root,Settings(_env_file=None,agent_qc_test_isolation='local'))
    assert runtime.execute(run.plan.tasks[0],run.analysis.projects[0],code).verdict=='pass'
    (code/'service.py').write_text('def add(a,b): return a+b+1\n')
    assert runtime.execute(run.plan.tasks[0],run.analysis.projects[0],code).verdict=='fail'


@pytest.mark.parametrize('disposition,missing',[('manual',False),('needs_setup',True)])
def test_unrunnable_cases_cannot_activate_qc(flow_app,disposition,missing):
    client,store,project,path,headers,w,case=prepared_case(flow_app,disposition,missing)
    assert w.automation_preparation[case.id]['status']==disposition
    response=client.post(path+'/workspace/publish',headers=headers,json={'version':w.version,'case_ids':[case.id],'reason':'Try activate','acknowledge_gaps':True,'activate_qc':True})
    assert response.status_code==409
    assert store.get_project(project.project_id).active_baseline is None
    if missing:
        response=client.post(path+'/workspace/actions',headers=headers,json={'version':w.version,'action':'validate_binding','case_id':case.id,'reason':'Attempt missing mapping','prerequisites_confirmed':True})
        assert response.status_code==409


def test_saving_design_version_does_not_activate_qc(flow_app):
    client,store,project,path,headers,w,case=prepared_case(flow_app)
    response=client.post(path+'/workspace/publish',headers=headers,json={'version':w.version,'case_ids':[case.id],'reason':'Design snapshot only','acknowledge_gaps':True})
    assert response.status_code==200,response.text
    assert store.get_project(project.project_id).active_baseline is None


def test_case_edit_invalidates_prepared_mapping(flow_app):
    client,store,project,path,headers,w,case=prepared_case(flow_app)
    response=client.post(path+'/workspace/actions',headers=headers,json={'version':w.version,'action':'validate_binding','case_id':case.id,'reason':'Mapping reviewed'})
    assert response.status_code==200
    w=store.get_workspace(project.project_id);edited=w.cases[0].model_dump();edited['objective']='New reviewed objective'
    response=client.post(path+'/workspace/actions',headers=headers,json={'version':w.version,'action':'save_case','case_id':case.id,'case_data':edited,'reason':'Change objective'})
    assert response.status_code==200,response.text
    w=store.get_workspace(project.project_id)
    response=client.post(path+'/workspace/actions',headers=headers,json={'version':w.version,'action':'approve_case','case_id':case.id,'reason':'Reviewed changed objective'})
    assert response.status_code==200,response.text
    w=store.get_workspace(project.project_id)
    response=client.post(path+'/workspace/publish',headers=headers,json={'version':w.version,'case_ids':[case.id],'reason':'Try stale binding','acknowledge_gaps':True,'activate_qc':True})
    assert response.status_code==409


def test_fixtures_require_explicit_confirmation(flow_app):
    client,store,project,path,headers,w,case=prepared_case(flow_app,'needs_setup')
    body={'version':w.version,'action':'validate_binding','case_id':case.id,'reason':'Mapping and fixture reviewed'}
    assert client.post(path+'/workspace/actions',headers=headers,json=body).status_code==409
    body['prerequisites_confirmed']=True
    assert client.post(path+'/workspace/actions',headers=headers,json=body).status_code==200


def test_browser_separates_design_snapshot_from_qc_activation(flow_app):
    playwright=pytest.importorskip('playwright.sync_api')
    from urllib.parse import urlsplit
    client,store,project,path,headers,w,case=prepared_case(flow_app)
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
            page.get_by_role('checkbox',name='Chọn '+case.id,exact=True).check()
            actions=page.locator('nav.design-toolbar').filter(has=page.get_by_role('button',name='Duyệt cases đã chọn',exact=True))
            assert actions.get_by_role('button',name='Chuẩn bị cho QC',exact=True).is_enabled()
            assert actions.get_by_role('button',name='Áp dụng cho QC',exact=True).is_disabled()
            assert actions.get_by_role('button',name='Lưu phiên bản bộ test',exact=True).is_enabled()
            page.get_by_role('button',name='Mở case',exact=True).click()
            panel=page.locator('.design-editor .design-binding');panel.locator('summary').first.click()
            assert 'return_equals' in panel.inner_text()
            assert 'Expected result 1.1' in panel.inner_text()
            panel.get_by_label('Xác nhận assertions kiểm chứng đúng expected results').fill('Reviewed sum 5 mapping')
            panel.get_by_role('button',name='Xác nhận sẵn sàng cho QC',exact=True).click()
            page.wait_for_function("document.querySelector('.design-case-table').textContent.includes('Sẵn sàng cho QC')")
            actions=page.locator('nav.design-toolbar').filter(has=page.get_by_role('button',name='Duyệt cases đã chọn',exact=True))
            assert actions.get_by_role('button',name='Áp dụng cho QC',exact=True).is_enabled()
            actions.get_by_role('button',name='Lưu phiên bản bộ test',exact=True).click()
            assert 'không thay đổi baseline QC' in page.locator('.design-publish').inner_text()
            page.evaluate('''() => {
                const original=window.fetch;
                window.fetch=async (...args)=>{if(String(args[0]).endsWith('/publish'))await new Promise(resolve=>setTimeout(resolve,500));return original(...args)};
            }''')
            form=page.locator('.design-publish')
            form.get_by_role('textbox').fill('Snapshot reviewed cases')
            form.get_by_role('checkbox').check()
            form.get_by_role('button',name='Lưu phiên bản bộ test',exact=True).click()
            page.wait_for_function("document.querySelector('.design-status').textContent.includes('Đang lưu phiên bản')")
            assert form.locator('button[type=submit]').is_disabled()
            page.evaluate('loadProject(true)')
            assert form.is_visible()
            page.wait_for_function("document.querySelector('.design-action-feedback').textContent.includes('Đã lưu phiên bản bộ test')")
            assert store.get_project(project.project_id).active_baseline is None
            assert not errors
        finally:browser.close()
