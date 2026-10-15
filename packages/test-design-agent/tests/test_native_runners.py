import json
import os
import shutil
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from tda.domain.contracts import (Evidence, Assertion, CaseOracle, StepOracle, GeneratedCase,
    ExecutableCase, HttpRequest, UnitCall, BrowserPlan, BrowserAction, LoadPlan, AiRubric, SourceBundle, SourceDocument)
from tda.domain.rules import TestValidator as Validator
from tda.suite import SuiteExecutor


TEXT='Returns JSON result 5; private API returns 403. The calculator shows 5. p95 must be below 5000 ms. The AI answer must contain 5 and score at least 0.8.'


def evidence():return [Evidence(source_id='prd',quote=TEXT)]
def assertion(kind,value,pointer=''):
    return Assertion(kind=kind,pointer=pointer,expected_json=json.dumps(value),evidence=evidence())
def request(path):return HttpRequest(method='GET',path=path,query=[],body_json=None,auth='none')
def executable(identifier,runner,steps,oracles,**kwargs):
    generated=GeneratedCase(id=identifier,scenario_id=identifier,title=identifier,kind=kwargs.pop('kind','functional'),
        runner=runner,steps=steps,cleanup=[],depends_on=[],repetitions=1,notes=[],**kwargs)
    return ExecutableCase(**generated.model_dump(),oracle=CaseOracle(case_id=identifier,
        steps=[StepOracle(step_index=index,assertions=values) for index,values in enumerate(oracles)],rationale='source'),
        requirement_ids=['R'],feature_ids=[],risk_ids=[],operation_ids=[])


@pytest.fixture
def service():
    class Handler(BaseHTTPRequestHandler):
        broken=False
        def do_GET(self):
            if self.path=='/':
                raw=b'<input id="left" value="2"><input id="right" value="3"><button id="add">Add</button><span id="result"></span><script>document.querySelector("#add").onclick=()=>document.querySelector("#result").textContent=Number(document.querySelector("#left").value)+Number(document.querySelector("#right").value)</script>'
                self.send_response(200);self.send_header('Content-Type','text/html')
            else:
                raw=json.dumps({'answer':'5'} if self.path=='/ai' else {'result':6 if self.broken else 5}).encode()
                self.send_response(403 if self.path=='/private' else 200);self.send_header('Content-Type','application/json')
            self.end_headers();self.wfile.write(raw)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}',Handler
    finally:server.shutdown();server.server_close();thread.join()


def native_cases():
    return [
        executable('unit','unit',[],[[assertion('return_equals',5)]],unit=UnitCall(language='python',module_path='calculator.py',function='add',args_json='[2,3]',kwargs_json='{}')),
        executable('browser','browser',[],[[assertion('dom_visible',True,'#left')],[assertion('dom_value_equals','2','#left')],[assertion('dom_value_equals','3','#right')],[assertion('dom_text_equals','5','#result')]],browser=BrowserPlan(actions=[
            BrowserAction(action='goto',path='/'),BrowserAction(action='fill',selector='#left',value='2'),BrowserAction(action='fill',selector='#right',value='3'),BrowserAction(action='click',selector='#add')])),
        executable('load','k6',[request('/sum')],[[assertion('status',200),assertion('json_equals',5,'/result'),assertion('latency_p95_ms',5000)]],kind='performance',load=LoadPlan(vus=2,duration_seconds=1)),
        executable('ai','ai',[request('/ai')],[[assertion('status',200),assertion('ai_score',.8)]],kind='ai_evaluation',grading=AiRubric(rubric='The answer contains 5',response_pointer='/answer',evidence=evidence())),
        executable('security','http',[request('/private')],[[assertion('status',403)]],kind='security'),
        executable('integration','http',[request('/sum'),request('/sum')],[[assertion('status',200)],[assertion('status',200)]],kind='integration'),
        executable('html','http',[request('/')],[[assertion('status',200),assertion('text_contains','id="left"')]])
    ]


def test_native_contracts_validate(tmp_path):
    code='def add(a,b): return a+b'
    sources=SourceBundle(documents=[SourceDocument(id='prd',path='prd.md',kind='prd',sha256='x',content=TEXT),
        SourceDocument(id='code',path='calculator.py',kind='code',sha256='x',content=code)],operations=[])
    report=Validator().validate(native_cases(),sources)
    assert report.issues==[]
    invalid=native_cases()[0];invalid.unit.module_path='../escape.py'
    assert not Validator().validate([invalid],sources).valid_case_ids


def test_python_and_javascript_unit_frameworks_catch_regressions(tmp_path):
    (tmp_path/'calculator.py').write_text('def add(a,b): return a+b\n')
    case=native_cases()[0]
    executor=SuiteExecutor(tmp_path)
    assert executor.execute([case.model_dump()])['passed']
    (tmp_path/'calculator.py').write_text('def add(a,b): return a+b+1\n')
    assert executor.execute([case.model_dump()])['results'][0]['status']=='defect'
    if shutil.which('node'):
        (tmp_path/'calculator.cjs').write_text('exports.add=(a,b)=>a+b;')
        case.unit.language='javascript';case.unit.module_path='calculator.cjs'
        assert executor.execute([case.model_dump()])['passed']


def test_all_five_types_use_real_http_unit_browser_k6_and_ai_routing(tmp_path,service):
    pytest.importorskip('playwright')
    binary=Path(__file__).resolve().parents[3]/'.tools/k6/k6.exe'
    if not binary.exists():pytest.skip('k6 optional executable unavailable')
    (tmp_path/'calculator.py').write_text('def add(a,b): return a+b\n')
    grades=[]
    def judge(rubric,actual):
        grades.append((rubric,actual));return {'score':.95,'reason':'Đạt rubric'}
    cases=[case.model_dump() for case in native_cases()]
    executor=SuiteExecutor(tmp_path,judge=judge,k6_binary=str(binary))
    report=executor.execute(cases,service[0])
    assert report['passed'],report
    assert grades==[('The answer contains 5','5')]
    assert report['runners']==['ai','browser','http','k6','unit']
    service[1].broken=True
    assert executor.execute([native_cases()[2].model_dump()],service[0])['results'][0]['status']=='defect'


def test_ai_without_judge_never_passes(service):
    report=SuiteExecutor().execute([native_cases()[3].model_dump()],service[0])
    assert report['results'][0]['status']=='environment_error'
    assert not report['passed']


def test_native_generation_publish_approve_and_qc_reexecution(tmp_path,service):
    from tda.application.orchestrator import TestDesignOrchestrator
    from tda.domain.contracts import (KnowledgeModel,RequirementNode,PlannedScenario,
        TestPlan,GeneratedBatch,OracleBatch,CriticReview,WorkflowConfig)
    from tda.infrastructure.artifacts import LocalArtifactStore
    from tda.infrastructure.baselines import BaselineRepository
    from tda.pipeline import approve,execute,verify
    pytest.importorskip('playwright')
    binary=Path(__file__).resolve().parents[3]/'.tools/k6/k6.exe'
    if not binary.exists():pytest.skip('k6 unavailable')
    (tmp_path/'calculator.py').write_text('def add(a,b): return a+b\n')
    cases=native_cases()
    scopes={'unit':'unit','browser':'browser','k6':'load','http':'http','ai':'http'}
    for case in cases:case.requirement_ids=['R-'+case.id]
    bundle=SourceBundle(documents=[SourceDocument(id='prd',path='prd.md',kind='prd',sha256='x',content=TEXT),
        SourceDocument(id='code',path='calculator.py',kind='code',sha256='x',content='def add(a,b): return a+b')],operations=[])
    class Reader:
        def collect(self,*args):return bundle
    class Agent:
        calls=[]
        def propose(self,stage,instructions,context,output):
            self.calls.append(stage)
            if stage=='analysis':return KnowledgeModel(requirements=[RequirementNode(id='R-'+case.id,title=case.id,
                acceptance_criteria=[TEXT],execution_scope=scopes[case.runner],scope_reason='source',evidence=evidence()) for case in cases],features=[],risks=[],dependencies=[],open_questions=[])
            if stage=='planning':return TestPlan(scenarios=[PlannedScenario(id=case.id,title=case.id,kind=case.kind,
                runner=case.runner,priority='P0',requirement_ids=case.requirement_ids,feature_ids=[],risk_ids=[],operation_ids=[],
                acceptance_criteria=[TEXT],evidence=evidence()) for case in cases],rationale='sources',open_questions=[])
            if stage.startswith('generation.'):
                return GeneratedBatch(cases=[GeneratedCase.model_validate({key:value for key,value in case.model_dump().items()
                    if key in GeneratedCase.model_fields}) for case in cases if case.kind==stage.split('.')[1]],open_questions=[])
            if stage=='oracle':return OracleBatch(oracles=[case.oracle for case in cases],open_questions=[])
            if stage=='critic':return CriticReview(recommendation='ready_for_human_review',summary='reviewed',issues=[])
            raise AssertionError(stage)
    agent=Agent();store=LocalArtifactStore(tmp_path/'run')
    config=WorkflowConfig(project='fixture',source_revision='base',model='fake',sources=[{'id':'prd','kind':'prd','paths':['prd.md']}],
        runners=['http','unit','browser','k6','ai'],kinds=['functional','integration','security','performance','ai_evaluation'],max_rounds=1)
    judge=lambda *args:{'score':.95,'reason':'Fixture grade'}
    workflow=TestDesignOrchestrator(agent,Reader(),SuiteExecutor(tmp_path,judge=judge,k6_binary=str(binary)),store,BaselineRepository(tmp_path/'baselines'))
    result=workflow.run(config,tmp_path,service[0])
    assert result['state']=='ready_for_human_review',result
    baseline=Path(result['baseline'])
    assert verify(baseline)['runners']==['ai','browser','http','k6','unit']
    approve(baseline,'fixture-reviewer',store.root/'execution_report.json')
    assert execute(baseline,service[0],workspace=tmp_path,judge=judge,k6_binary=str(binary))['passed']
    service[1].broken=True
    report=execute(baseline,service[0],workspace=tmp_path,judge=judge,k6_binary=str(binary))
    assert not report['passed']
    assert any(item['case_id']=='load' and item['status']=='defect' for item in report['results'])
    assert 'repair' not in agent.calls


def test_browser_missing_selector_is_defect_and_oracle_order_is_irrelevant(service):
    pytest.importorskip('playwright')
    case=native_cases()[1]
    case.oracle.steps.reverse()
    assert SuiteExecutor().execute([case.model_dump()],service[0])['passed']
    case.browser.actions[1].selector='#does-not-exist'
    assert SuiteExecutor().execute([case.model_dump()],service[0])['results'][0]['status']=='defect'
