import copy
from urllib.parse import urlsplit
import pytest
from test_flow_design_app import flow_app
from test_generated_pytest import prepare_pytest


def test_filters_paging_selection_and_publish_scope(flow_app,tmp_path):
    playwright=pytest.importorskip('playwright.sync_api')
    client,store,project,engine,path,headers,w,case,root=prepare_pytest(flow_app,tmp_path)
    w.document_inventory={'requirements':[{'id':'REQ-01','text':'Existing flow','path':'PRD.md','line':150,'heading':'Flow'},{'id':'REQ-02','text':'Missing recovery','path':'PRD.md','line':250,'heading':'Recovery'}],
        'traceability':[{'id':'REQ-01','path':'PRD.md','analyzed':True,'case_ids':[case.id]},{'id':'REQ-02','path':'PRD.md','analyzed':False,'case_ids':[]}]}
    for number in range(1,61):
        item=case.model_copy(deep=True);item.id=f'ITEM-{number:03d}';item.title=f'Item scenario {number}';item.priority='P1' if number%2 else 'P0';item.kind='integration' if number%2 else 'functional'
        w.cases.append(item)
    module=copy.deepcopy(w.flow_modules[0]);module['scope']['id']='items';module['scope']['title']='Items';module['case_ids']=[c.id for c in w.cases if c.id.startswith('ITEM-')];w.flow_modules.append(module)
    store.save_workspace(w,w.version,'fixture','ux-filter-fixture')
    with playwright.sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            def route(request):
                incoming=request.request;url=urlsplit(incoming.url)
                response=client.request(incoming.method,url.path+('?' +url.query if url.query else ''),content=incoming.post_data,headers={k:v for k,v in incoming.headers.items() if k.lower() in ('content-type','x-csrf-token')})
                request.fulfill(status=response.status_code,body=response.content,headers={'Content-Type':response.headers.get('content-type','application/json')})
            page.route('http://flow.local/**',route);page.goto('http://flow.local/app');page.get_by_role('button',name='Chọn repository').click()
            page.get_by_label('Tìm repository',exact=True).fill('no-matching-repo')
            assert page.locator('#projects button:visible').count()==0
            page.get_by_label('Tìm repository',exact=True).fill('')
            page.locator('#projects button').first.click();page.locator('.design-case-table').wait_for()
            from pathlib import Path
            previews=Path('.data/local-stack');previews.mkdir(parents=True,exist_ok=True)
            page.screenshot(path=str(previews/'workspace-ux-desktop.png'))
            visible=page.locator('.design-case-table tbody tr:visible')
            assert visible.count()==50
            page.get_by_role('button',name='Tiếp →',exact=True).click();assert visible.count()==11
            page.get_by_label('Module',exact=True).select_option('items');assert visible.count()==50
            page.get_by_label('Type',exact=True).select_option('integration');assert visible.count()==30
            page.get_by_label('Priority',exact=True).select_option('P0');assert visible.count()==0
            page.get_by_label('Priority',exact=True).select_option('P1');assert visible.count()==30
            page.get_by_label('Chọn tất cả cases đang hiển thị',exact=True).check()
            assert '30 đã chọn' in page.locator('.workspace-list-info').inner_text()
            page.get_by_label('Type',exact=True).select_option('functional');assert '30 ngoài trang' in page.locator('.workspace-list-info').inner_text()
            page.get_by_role('button',name='Bỏ chọn tất cả',exact=True).click()
            page.get_by_role('button',name='Xóa bộ lọc',exact=True).click()
            page.get_by_role('button',name='1 Đã duyệt',exact=False).click()
            assert visible.count()==1
            page.get_by_label('Chọn '+case.id,exact=True).check()
            page.get_by_text('Thao tác kỹ thuật / phiên bản',exact=True).click()
            page.get_by_role('button',name='Lưu phiên bản bộ test',exact=True).click()
            assert '1 cases sẽ có trong phiên bản này' in page.locator('.design-publish').inner_text()
            page.locator('.design-publish').get_by_role('button',name='Hủy',exact=True).click()
            page.get_by_role('button',name='Xác nhận mappings đã chọn',exact=True).click()
            mapping=page.locator('.design-bulk-mapping')
            mapping.get_by_label('Lý do xác nhận mapping cho các case đã chọn').fill('Reviewed expected sum and artifact')
            mapping.locator('input[type=checkbox]').check()
            mapping.get_by_role('button',name='Xác nhận 1 mappings',exact=True).click()
            page.get_by_role('button',name='Áp dụng cho QC',exact=True).click()
            assert 'THAY THẾ' in page.locator('.design-publish').inner_text()
            assert 'không tự gộp cases cũ' in page.locator('.design-publish').inner_text()
            page.locator('.design-publish').get_by_role('button',name='Hủy',exact=True).click()
            page.set_viewport_size({'width':390,'height':844})
            assert page.get_by_label('Module',exact=True).is_visible()
            page.screenshot(path=str(previews/'workspace-ux-mobile.png'))
            page.emulate_media(color_scheme='dark');page.reload()
            page.get_by_role('button',name='Chọn repository').click();page.locator('#projects button').first.click()
            page.locator('.design-case-table').wait_for()
            assert page.locator('.workspace-filter-panel').get_attribute('open') is None
            assert page.locator('html').get_attribute('data-theme')=='dark'
            page.screenshot(path=str(previews/'workspace-ux-mobile-dark.png'))
            document_panel=page.locator('details').filter(has=page.locator(':scope > summary').filter(has_text='Đối chiếu PRD / SRS')).first
            document_panel.locator(':scope > summary').click()
            page.get_by_label('Trạng thái phân tích tài liệu',exact=True).select_option('unanalyzed')
            assert 'REQ-02' in document_panel.inner_text() and 'REQ-01 · Có case' not in document_panel.inner_text()
            assert not errors
            assert store.get_project(project.project_id).active_baseline is None
        finally:browser.close()
