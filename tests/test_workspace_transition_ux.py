import asyncio
import pytest
from test_flow_design_app import flow_app


@pytest.mark.parametrize('fail_second',[False,True])
def test_bulk_approval_keeps_table_and_view_during_polling(flow_app,fail_second):
    playwright=pytest.importorskip('playwright.sync_api')
    from urllib.parse import urlsplit
    client,store,project,engine,source=flow_app
    path='/api/v1/projects/'+project.project_id
    client.post(path+'/regenerate',headers={'X-CSRF-Token':'csrf-fixture'},json={})
    asyncio.run(engine.execute(store.claim_job()))
    w=store.get_workspace(project.project_id)
    extra=w.cases[0].model_copy(deep=True);extra.id='SUM-T2';extra.title='Another sum case';w.cases.append(extra)
    store.save_workspace(w,w.version,'fixture','add_second_case')
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
            page.get_by_role('searchbox',name='Tìm test case').fill('SUM')
            page.get_by_label('Review status',exact=True).select_option('ready_for_review')
            page.get_by_role('checkbox',name='Chọn SUM-T1',exact=True).check()
            page.get_by_role('checkbox',name='Chọn SUM-T2',exact=True).check()
            page.get_by_role('button',name='Mở case',exact=True).first.click()
            page.locator('#review > details').filter(has=page.get_by_text('Requirements / Acceptance criteria',exact=True)).locator('summary').click()
            page.evaluate('''(failSecond) => {
                window.__table=document.querySelector('.design-case-table');window.__actions=0;
                const original=window.fetch;
                window.fetch=async (...args)=>{if(String(args[0]).endsWith('/actions')){window.__actions++;await new Promise(resolve=>setTimeout(resolve,600));if(failSecond&&window.__actions===2)return new Response(JSON.stringify({detail:'Simulated failure'}),{status:409,headers:{'Content-Type':'application/json'}});}return original(...args)};
                window.__poll=setInterval(()=>loadProject(),50);
            }''',fail_second)
            page.get_by_role('button',name='Duyệt cases đã chọn',exact=True).click()
            page.wait_for_function("document.querySelector('.design-status').textContent.includes('1/2')")
            assert page.locator('#review').get_attribute('aria-busy')=='true'
            assert page.locator('.design-status .spinner').count()==1
            assert page.evaluate("document.querySelector('.design-case-table')===window.__table")
            page.wait_for_function("document.querySelector('.design-status').textContent.includes('2/2')")
            assert page.evaluate("document.querySelector('.design-case-table')===window.__table")
            assert page.get_by_role('button',name='Đang duyệt 2/2…',exact=True).is_disabled()
            page.wait_for_function("document.querySelector('#review').getAttribute('aria-busy')==='false'")
            if fail_second:
                assert '1/2' in page.locator('.design-action-feedback').inner_text()
                assert 'Simulated failure' in page.locator('.design-action-feedback').inner_text()
            else:assert 'Đã duyệt 2/2' in page.locator('.design-action-feedback').inner_text()
            page.evaluate('clearInterval(window.__poll)')
            assert page.evaluate('window.__actions')==2
            assert page.get_by_role('searchbox',name='Tìm test case').input_value()=='SUM'
            assert page.get_by_label('Review status',exact=True).input_value()=='ready_for_review'
            assert page.locator('.design-editor').count()==1
            assert page.locator('#review > details').filter(has=page.get_by_text('Requirements / Acceptance criteria',exact=True)).get_attribute('open') is not None
            assert page.get_by_role('checkbox',name='Chọn SUM-T1',exact=True,include_hidden=True).is_checked()
            assert page.get_by_role('checkbox',name='Chọn SUM-T2',exact=True,include_hidden=True).is_checked()
            assert not errors
        finally:browser.close()
