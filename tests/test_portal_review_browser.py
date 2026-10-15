"""Exercise review rendering with the API payload, including legacy responses."""
import json
from pathlib import Path
import pytest


@pytest.mark.parametrize('with_validation',[True,False])
def test_review_renders_cases_and_approval_gate_with_validation_optional(with_validation):
    playwright=pytest.importorskip('playwright.sync_api')
    html=(Path(__file__).resolve().parents[1]/'app/api/design_portal.html').read_text(encoding='utf-8').replace('start();','')
    case={'id':'TC-1','title':'Kiểm tra sức khỏe','runner':'http','kind':'functional','requirement_ids':['R'],
        'steps':[{'method':'GET','path':'/health','query':[],'body_json':None}], 'depends_on':[],
        'oracle':{'steps':[{'step_index':0,'assertions':[{'kind':'status','pointer':'','expected_json':'200',
            'expectation_basis':'implementation','evidence':[{'source_id':'source','quote':'health'}]}]}]}}
    artifacts={'cases/cases.json':[case],'review_gate.json':{'design_blockers':['Test không hợp lệ'],'open_questions':[]},
        'coverage.json':{'metrics':{}},'critic_review.json':{'issues':[]},'traceability.json':{'R':['TC-1']},
        'sources.json':{'documents':[{'id':'source','path':'app.py'}]}}
    if with_validation:artifacts['validation_report.json']={'issues':[{'case_id':'TC-1','message':'Kết quả mong đợi chưa hợp lệ.'}]}
    data={'baseline':{'state':'draft','source_revision':'base'},'artifacts':artifacts,'execution':{'passed':False,'results':[]}}
    with playwright.sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page();page.route('**/*',lambda route:route.fulfill(status=200,content_type='application/json',body='{}'))
            page.set_content(html);page.evaluate('(data)=>{api=async()=>data;selected="fixture"}',data)
            page.evaluate('()=>{let element=document.getElementById("review");while(element){element.classList.remove("hidden");element=element.parentElement}}')
            page.evaluate('()=>showBaseline("fixture")')
            assert page.locator('#review article.case').count()==1
            assert page.locator('#review table.case-table th').count()==9
            assert 'HTTP' in page.locator('#review tr.case-summary-row').inner_text()
            assert 'TC-1' in page.locator('#review article.case').inner_text()
            assert page.get_by_role('button',name='Duyệt và dùng cho PR').is_disabled()
            assert page.get_by_role('button',name='Từ chối bộ test').count()==1
            if with_validation:assert 'Định nghĩa test cần sửa' in page.locator('#review article.case').inner_text()
        finally:browser.close()


@pytest.mark.parametrize('reference_status',[None,'defect','environment_error','pass'])
def test_design_approval_button_does_not_depend_on_reference_pass(reference_status):
    playwright=pytest.importorskip('playwright.sync_api')
    html=(Path(__file__).resolve().parents[1]/'app/api/design_portal.html').read_text(encoding='utf-8').replace('start();','')
    case={'id':'TC','title':'Kiểm tra API','runner':'http','kind':'functional','requirement_ids':['R'],'depends_on':[],
        'steps':[{'method':'GET','path':'/health','query':[],'body_json':None}],
        'oracle':{'steps':[{'step_index':0,'assertions':[{'kind':'status','pointer':'','expected_json':'200','evidence':[]}]}]}}
    data={'baseline':{'state':'draft','source_revision':'base'},'artifacts':{'cases/cases.json':[case],
        'validation_report.json':{'issues':[],'valid_case_ids':['TC']},'coverage.json':{'metrics':{},'design_satisfied':True},
        'critic_review.json':{'recommendation':'ready_for_human_review','issues':[]},'review_gate.json':{'design_blockers':[]},
        'traceability.json':{},'sources.json':{'documents':[]}},
        'execution':None if reference_status is None else {'passed':reference_status=='pass','results':[{'case_id':'TC','status':reference_status,'steps':[]}]}}
    with playwright.sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page();page.set_content(html)
            page.evaluate('(data)=>{api=async()=>data;selected="fixture";let element=document.getElementById("review");while(element){element.classList.remove("hidden");element=element.parentElement}}',data)
            page.evaluate('()=>showBaseline("fixture")')
            assert page.get_by_role('button',name='Duyệt và dùng cho PR').is_enabled()
        finally:browser.close()


def test_case_review_actions_show_editable_suggestion_and_submit_selected_action():
    playwright=pytest.importorskip('playwright.sync_api')
    html=(Path(__file__).resolve().parents[1]/'app/api/design_portal.html').read_text(encoding='utf-8').replace('start();','')
    case={'id':'TC','title':'Kiểm tra API','runner':'http','kind':'functional','requirement_ids':['R'],'depends_on':[],
        'steps':[{'method':'GET','path':'/health','query':[],'body_json':None}],
        'oracle':{'steps':[{'step_index':0,'assertions':[{'kind':'status','pointer':'','expected_json':'200','evidence':[]}]}]}}
    suggestion='Đối chiếu expected result với source trước khi chỉnh oracle.'
    data={'baseline':{'state':'draft','source_revision':'base'},'can_request_case_review':True,'execution':None,
        'artifacts':{'cases/cases.json':[case],'validation_report.json':{'issues':[],'valid_case_ids':['TC']},
        'coverage.json':{'metrics':{},'design_satisfied':True},'critic_review.json':{'recommendation':'needs_changes','issues':[
            {'severity':'blocker','case_ids':['TC'],'message':'Expected result thiếu căn cứ.','suggested_action':'review_blocker','suggestion':suggestion}]},
        'review_gate.json':{'design_blockers':['Expected result thiếu căn cứ.']},'traceability.json':{},'sources.json':{'documents':[]}}}
    with playwright.sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page();page.set_content(html)
            page.evaluate('(data)=>{api=async()=>data;act=async(path,body)=>{window.submission={path,body}};selected="project";let e=document.getElementById("review");while(e){e.classList.remove("hidden");e=e.parentElement}}',data)
            page.evaluate('()=>showBaseline("baseline")')
            card=page.locator('#review article.case');assert 'Có blocker' in card.locator('.state').inner_text()
            page.get_by_role('button',name='Xem case cần xử lý').click()
            assert page.get_by_label('Lọc trạng thái test case').input_value()=='attention'
            page.get_by_label('Tìm test case').fill('không khớp case nào')
            assert page.locator('#review .case-empty').is_visible()
            page.get_by_label('Tìm test case').fill('TC')
            page.locator('#review tr.case-summary-row').get_by_role('button',name='Xử lý blocker').click()
            assert card.locator('.case-body').evaluate('(element)=>element.open')
            assert card.locator('.case-review').evaluate('(element)=>element.open')
            assert suggestion in card.locator('.review-suggestion').inner_text()
            choice=page.get_by_label('Action cho TC');assert choice.input_value()=='review_blocker'
            for action in ['request_changes','review_blocker','add_acceptance_criteria']:
                choice.select_option(action)
                page.get_by_label('Feedback cho TC').fill('Feedback đã được maintainer chỉnh lại.')
                if action=='add_acceptance_criteria':page.get_by_label('Acceptance criteria cho TC').fill('GET /health trả HTTP 200.')
                card.get_by_role('button',name='Gửi tới TDA').click()
                payload=page.evaluate('window.submission')
                assert payload['path']=='/api/v1/projects/project/baselines/baseline/case-review'
                assert payload['body']['case_id']=='TC' and payload['body']['action']==action
                assert payload['body']['feedback']=='Feedback đã được maintainer chỉnh lại.'
                assert bool(payload['body']['acceptance_criteria'])==(action=='add_acceptance_criteria')
        finally:browser.close()


def test_feedback_keeps_review_open_and_automatically_opens_new_revision():
    playwright=pytest.importorskip('playwright.sync_api')
    html=(Path(__file__).resolve().parents[1]/'app/api/design_portal.html').read_text(encoding='utf-8').replace('start();','')
    case={'id':'TC','scenario_id':'S','title':'Kiểm tra API','runner':'http','kind':'functional','requirement_ids':['R'],'depends_on':[],
        'steps':[{'method':'GET','path':'/health','query':[],'body_json':None}],
        'oracle':{'steps':[{'step_index':0,'assertions':[{'kind':'status','pointer':'','expected_json':'200','evidence':[]}]}]}}
    artifacts={'cases/cases.json':[case],'validation_report.json':{'issues':[],'valid_case_ids':['TC']},'coverage.json':{'metrics':{},'design_satisfied':True},
        'critic_review.json':{'recommendation':'needs_changes','issues':[{'severity':'blocker','case_ids':['TC'],'message':'Oracle cần review.'}]},
        'review_gate.json':{'design_blockers':['Oracle cần review.']},'traceability.json':{},'sources.json':{'documents':[]}}
    old={'baseline':{'state':'draft','source_revision':'base','version':'old'},'artifacts':artifacts,'execution':None,'can_request_case_review':True}
    new=json.loads(json.dumps(old));new['baseline']['version']='new';new['artifacts']['review_changes.json']={'previous_baseline':'old','changed_case_ids':['TC'],'preserved_case_count':0}
    project={'project_id':'p','repository':{'full_name':'owner/repo'},'source_paths':[],'uploaded_document_ids':[],'target_profile':{},'discovered_sources':[],'state':'needs_changes','message':'Review required','current_job_id':'initial','active_baseline':None,'allow_mutations':False}
    response={'project':project,'job':{'job_id':'initial','status':'completed'},'baselines':[{'version':'old','state':'draft'}],'documents':[]}
    with playwright.sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page();page.set_content(html)
            page.evaluate('''(payload)=>{selected='p';window.oldDetail=payload.old;window.newDetail=payload.new;window.projectResponse=payload.project;projectData=payload.project;
                api=async(path,options)=>{if(path.endsWith('/case-review')){projectResponse.job={job_id:'J',status:'queued',review_baseline:'old',review_request:{case_id:'TC'}};projectResponse.project.current_job_id='J';projectResponse.project.state='queued';return {job_id:'J'}}
                  if(path.endsWith('/baselines/old'))return oldDetail;if(path.endsWith('/baselines/new'))return newDetail;
                  if(path==='/api/v1/projects/p')return JSON.parse(JSON.stringify(projectResponse));if(path==='/api/v1/projects')return {projects:[projectResponse.project]};throw Error(path)};
                let e=document.getElementById('review');while(e){e.classList.remove('hidden');e=e.parentElement}}''',{'old':old,'new':new,'project':response})
            page.evaluate('()=>showBaseline("old")')
            page.get_by_role('button',name='Xử lý blocker').click();page.get_by_label('Feedback cho TC').fill('Sửa oracle theo source.')
            page.get_by_role('button',name='Gửi tới TDA').click()
            page.locator('.case-job-progress').wait_for()
            assert 'old' in page.locator('.case-table caption').inner_text()
            assert page.locator('article .case-review').is_visible()
            assert page.locator('[data-tab="baselines"]').get_attribute('aria-selected')=='true'
            page.evaluate('()=>{projectResponse.job.status="completed";projectResponse.job.baseline_version="new";projectResponse.project.state="needs_changes";return loadProject()}')
            assert 'new' in page.locator('.case-table caption').inner_text()
            assert page.locator('.case-job-progress').count()==0
            assert page.get_by_role('button',name='Duyệt và dùng cho PR').is_disabled()
            assert 'Giữ nguyên' in page.locator('#review').text_content()
        finally:browser.close()


def test_manual_editor_and_supplement_submit_structured_revision_without_navigation():
    playwright=pytest.importorskip('playwright.sync_api')
    html=(Path(__file__).resolve().parents[1]/'app/api/design_portal.html').read_text(encoding='utf-8').replace('start();','')
    case={'id':'TC','scenario_id':'S','title':'Health API','runner':'http','kind':'functional','requirement_ids':['R'],'feature_ids':[],'risk_ids':[],'operation_ids':[],'depends_on':[],
        'steps':[{'method':'GET','path':'/health','query':[],'body_json':None}],
        'oracle':{'case_id':'TC','steps':[{'step_index':0,'assertions':[{'kind':'status','pointer':'','expected_json':'200','evidence':[]}]}]}}
    data={'baseline':{'state':'approved','version':'base','source_revision':'base'},'can_request_case_review':True,'artifacts':{
        'cases/cases.json':[case],'test_plan.json':{'scenarios':[{'id':'S','priority':'P0','runner':'http','kind':'functional'}]},
        'validation_report.json':{'valid_case_ids':['TC'],'issues':[]},'coverage.json':{'metrics':{},'design_satisfied':True},
        'critic_review.json':{'recommendation':'ready_for_human_review','issues':[]},'review_gate.json':{'design_blockers':[]},'sources.json':{'documents':[]},'traceability.json':{}}}
    with playwright.sync_playwright() as pw:
        browser=pw.chromium.launch(channel='chrome',headless=True)
        try:
            page=browser.new_page();page.set_content(html)
            page.evaluate('(data)=>{api=async()=>data;act=async(path,body)=>{window.submission={path,body}};selected="p";let e=document.getElementById("review");while(e){e.classList.remove("hidden");e=e.parentElement}}',data)
            page.evaluate('()=>showBaseline("base")')
            page.get_by_role('button',name='Sửa tay',exact=True).click()
            page.get_by_label('Title',exact=True).fill('Changed title')
            page.locator('input[aria-label^="Expected result"]').fill('201')
            page.get_by_role('button',name='Lưu chỉnh sửa').click()
            body=page.evaluate('submission.body')
            assert body['action']=='manual_edit' and body['case_data']['id']=='TC'
            assert body['case_data']['title']=='Changed title'
            assert body['case_data']['oracle']['steps'][0]['assertions'][0]['expected_json']=='201'
            page.get_by_role('button',name='Hủy',exact=True).click()
            page.get_by_role('button',name='Sinh bổ sung',exact=True).click()
            page.locator('.suite-toolbar textarea').fill('Add boundary cases')
            page.get_by_role('button',name='Bổ sung vào bộ test').click()
            assert page.evaluate('submission.body.action')=='supplement'
            assert page.locator('.case-table caption').inner_text().endswith('base')
            page.get_by_role('button',name='Thêm test case',exact=True).click()
            page.get_by_label('Title',exact=True).fill('New case')
            page.get_by_role('button',name='Lưu case mới',exact=True).click()
            body=page.evaluate('submission.body')
            assert body['action']=='add_case' and body['case_id']!='TC'
            assert body['case_data']['oracle']['case_id']==body['case_id']
            assert body['scenario_data']['id']==body['case_data']['scenario_id']
        finally:browser.close()
