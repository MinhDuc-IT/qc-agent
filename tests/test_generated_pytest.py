import asyncio
import json
from pathlib import Path
import pytest
from test_flow_design_app import flow_app
from tda.domain.worker_artifacts import PytestDraft,ArtifactReview


def prepare_pytest(flow_app,tmp_path,code=None):
    client,store,project,engine,source=flow_app
    root=tmp_path/'source';(root/'pyproject.toml').write_text('[project]\nname="sample"\nversion="0.1"\nrequires-python=">=3.11"\n')
    (root/'tests').mkdir();(root/'tests/conftest.py').write_text('import pytest\n@pytest.fixture\ndef operands():\n    return (2,3)\n')
    engine.settings.agent_qc_test_isolation='local'
    path='/api/v1/projects/'+project.project_id;headers={'X-CSRF-Token':'csrf-fixture'}
    client.post(path+'/regenerate',headers=headers,json={});asyncio.run(engine.execute(store.claim_job()))
    w=store.get_workspace(project.project_id);case=w.cases[0]
    assert client.post(path+'/workspace/actions',headers=headers,json={'version':w.version,'action':'approve_case','case_id':case.id,'reason':'Reviewed sum'}).status_code==200
    mapping=[{'design_step':0,'expected_result':0,'assertion_lines':[3],'explanation':'Assert sum matches reviewed expected value 5'}]
    class Agent:
        calls=0;max_calls=24
        def propose(self,stage,instructions,context,output):
            self.calls+=1
            if stage=='artifacts.write':return PytestDraft(disposition='needs_review',reason='Reuse operands fixture',code=code or 'def test_case(operands):\n    from service import add\n    assert add(*operands) == 5\n',mapping=mapping)
            if stage=='artifacts.review':
                import ast
                assertion=next(n for n in ast.walk(ast.parse(context['code'])) if isinstance(n,ast.Assert))
                return ArtifactReview(mapping=[{'expected_key':'step:0/expected:0','assertion_sources':[ast.get_source_segment(context['code'],assertion)],'explanation':'Exact reviewed assertion'}],issues=[])
            raise AssertionError(stage)
    engine.agent_factory=lambda _:Agent()
    w=store.get_workspace(project.project_id)
    response=client.post(path+'/workspace/jobs',headers=headers,json={'version':w.version,'action':'bind','case_ids':[case.id]})
    assert response.status_code==202,response.text
    job=store.claim_job();asyncio.run(engine.execute(job))
    assert store.get_job(job.job_id).status=='completed',store.get_job(job.job_id).error_message
    return client,store,project,engine,path,headers,store.get_workspace(project.project_id),case,root


def test_generated_pytest_preflight_activation_and_pr_regression(flow_app,tmp_path):
    from app.application.project_onboarding import ManagedBaselineBinding
    from app.domain.models import QCRun,TriggerContext,Trigger,Actor,Installation,Revision,SourceAnalysis,ProjectDescriptor,ExecutionPlan
    from app.infrastructure.test_design.runtime import ManagedBaselineRuntime
    client,store,project,engine,path,headers,w,case,root=prepare_pytest(flow_app,tmp_path)
    artifact=w.worker_artifacts[case.id]
    assert artifact.preflight['status']=='pass',artifact.preflight
    assert artifact.state=='proposed' and artifact.reviewer is None
    assert not (root/'tests/.qc_generated').exists()
    response=client.post(path+'/workspace/actions',headers=headers,json={'version':w.version,'action':'validate_artifact','case_id':case.id,'reason':'Reviewed pytest code, fixtures and expected 5'})
    assert response.status_code==200,response.text
    w=store.get_workspace(project.project_id)
    response=client.post(path+'/workspace/publish',headers=headers,json={'version':w.version,'case_ids':[case.id],'activate_qc':True,'acknowledge_gaps':True,'reason':'Apply reviewed pytest subset'})
    assert response.status_code==200,response.text
    active=store.get_project(project.project_id);baseline=store.get_baseline(project.project_id,active.active_baseline)
    assert baseline.execution_kind=='worker_artifacts'
    context=TriggerContext(trigger=Trigger(event='pull_request',action='opened',delivery_id='generated',actor=Actor(id='1',login='maintainer')),
        installation=Installation(id=10),repository=project.repository,revision=Revision(head_sha='pr',base_sha='revision1',head_ref='feature',base_ref='main'))
    run=QCRun(trigger_context=context,analysis=SourceAnalysis(projects=[ProjectDescriptor(id='python',language='python')]),plan=ExecutionPlan())
    class Profiles:
        def materialize(self,*args):raise AssertionError('Pytest fixture owns services')
    binding=ManagedBaselineBinding(store,Profiles());binding.prepare(run);binding.attach(run)
    task=run.plan.tasks[0];assert task.capability=='unit.run' and task.parameters['generated_artifact']
    runtime=ManagedBaselineRuntime(store,engine.settings.agent_qc_tda_dir,engine.settings)
    result=runtime.execute_generated(task,run.analysis.projects[0],root)
    assert result.verdict=='pass',result.output
    assert result.worker_kind=='tool' and result.worker_id=='python-pytest-generated'
    (root/'service.py').write_text('def add(a,b): return a+b+1\n')
    result=runtime.execute_generated(task,run.analysis.projects[0],root)
    assert result.verdict=='fail',result.output
    assert result.summary['results'][0]['case_id']==case.id
    assert result.summary['results'][0]['comparison']=='new_failure'
    assert not (root/'tests/.qc_generated').exists()
    manifest=Path(baseline.baseline_dir)/'worker_artifacts.json';manifest.write_text('{}')
    assert runtime.execute_generated(task,run.analysis.projects[0],root).verdict=='unknown'


def test_bad_generated_assertion_blocks_confirmation(flow_app,tmp_path):
    client,store,project,engine,path,headers,w,case,root=prepare_pytest(flow_app,tmp_path,code='def test_case():\n    value=5\n    assert True\n')
    assert w.worker_artifacts[case.id].validation_issues
    response=client.post(path+'/workspace/actions',headers=headers,json={'version':w.version,'action':'validate_artifact','case_id':case.id,'reason':'Try invalid artifact'})
    assert response.status_code==409


def test_profile_discovers_nested_fastapi_requirements_without_repo_name(tmp_path):
    from app.infrastructure.test_design.execution_profiles import discover_profiles
    from tda.infrastructure.repository_tools import RepositoryTools
    from tda.infrastructure.artifacts import LocalArtifactStore
    root=tmp_path/'repo';(root/'service/tests').mkdir(parents=True);(root/'service/app').mkdir()
    (root/'service/pyproject.toml').write_text('[project]\nrequires-python=">=3.14,<4"\ndependencies=["psycopg", "sqlmodel"]\n')
    (root/'service/tests/conftest.py').write_text('import pytest\n# db engine fixture\n')
    (root/'service/app/config.py').write_text('from pydantic_settings import BaseSettings\nclass Settings(BaseSettings):\n    DATABASE_URL: str\n    SECRET_KEY: str\n')
    (root/'service/alembic.ini').write_text('[alembic]\n')
    profiles=discover_profiles(RepositoryTools(root,LocalArtifactStore(tmp_path/'artifacts')))
    assert len(profiles)==1
    p=profiles[0];assert p.root=='service' and p.python_version=='3.14' and p.services==['postgres'] and p.migrations
    assert 'DATABASE_URL' in p.required_environment


def test_generated_worker_reports_fixture_failure_as_blocked(flow_app,tmp_path):
    client,store,project,engine,path,headers,w,case,root=prepare_pytest(flow_app,tmp_path,
        code='def test_case(missing_fixture):\n    value=missing_fixture\n    assert value == 5\n')
    artifact=w.worker_artifacts[case.id]
    assert artifact.preflight['status']=='blocked',artifact.preflight
    assert w.automation_preparation[case.id]['status']=='needs_setup'
    response=client.post(path+'/workspace/actions',headers=headers,json={'version':w.version,'action':'validate_artifact','case_id':case.id,'reason':'Try bypass fixture error'})
    assert response.status_code==409


def test_asyncpg_dataclass_config_and_database_safeguards_are_discovered(tmp_path):
    from app.infrastructure.test_design.execution_profiles import discover_profiles
    from tda.infrastructure.repository_tools import RepositoryTools
    from tda.infrastructure.artifacts import LocalArtifactStore
    (tmp_path/'svc/tests').mkdir(parents=True);(tmp_path/'svc/app/repositories').mkdir(parents=True)
    (tmp_path/'svc/pyproject.toml').write_text('[project]\nrequires-python=">=3.11"\ndependencies=["asyncpg"]\n')
    (tmp_path/'svc/tests/conftest.py').write_text('from app.db import engine\nfrom app.repositories.postgres import require_test_database\n')
    (tmp_path/'svc/app/config.py').write_text('import os\nenv=os.getenv("DEMO_ENVIRONMENT")\nsecret=os.getenv("DEMO_TOKEN_SECRET")\n')
    (tmp_path/'svc/app/repositories/postgres.py').write_text('import os\ndef require_test_database():\n if os.environ.get("DEMO_ALLOW_DATABASE_CLEAR") != "test-only": raise RuntimeError()\n')
    profile=discover_profiles(RepositoryTools(tmp_path,LocalArtifactStore(tmp_path/'artifacts')))[0]
    assert profile.services==['postgres']
    assert profile.test_environment=={'DEMO_ENVIRONMENT':'test','DEMO_TOKEN_SECRET':'${QC_SECRET}','DEMO_ALLOW_DATABASE_CLEAR':'test-only'}


def test_normalize_reserved_test_entry_point():
    from tda.application.worker_artifacts import normalize_test_name
    code = "def test_login(client):\n    assert client is not None\n"
    assert normalize_test_name(code) == "def test_case(client):\n    assert client is not None\n"
    recursive = "def test_login():\n    return test_login()\n"
    assert normalize_test_name(recursive) == recursive


def test_stable_mapping_resolves_wrong_indices_without_changing_oracle(flow_app,tmp_path):
    from tda.application.worker_artifacts import resolve_mapping,artifact_issues
    from tda.domain.worker_artifacts import ArtifactMapping
    _,_,_,_,_,_,w,case,_=prepare_pytest(flow_app,tmp_path)
    binding=w.worker_artifacts[case.id]
    mapping=ArtifactMapping(expected_key='step:0/expected:0',design_step=1,expected_result=1,
        assertion_lines=[99],assertion_sources=['assert add(*operands) == 5'],explanation='Exact reviewed assertion')
    binding.mapping=resolve_mapping(case,binding.artifact.content,[mapping])
    assert (binding.mapping[0].design_step,binding.mapping[0].expected_result)==(0,0)
    assert binding.mapping[0].assertion_lines==[3]
    assert not artifact_issues(case,binding)
    mapping.expected_key='step:9/expected:9'
    with pytest.raises(ValueError,match='expected_key'):resolve_mapping(case,binding.artifact.content,[mapping])
    mapping.expected_key='step:0/expected:0';mapping.assertion_sources=['assert add(*operands) == 4']
    with pytest.raises(ValueError,match='Assertion source'):resolve_mapping(case,binding.artifact.content,[mapping])


def test_python_profile_does_not_capture_ui_load_or_ai_cases(flow_app,tmp_path):
    from app.infrastructure.test_design.execution_profiles import pytest_case_ids
    _,_,_,_,_,_,w,case,_=prepare_pytest(flow_app,tmp_path)
    assert pytest_case_ids(w,[case.id],w.execution_profiles)==[case.id]
    for kind in ('performance','ai_evaluation'):
        changed=w.model_copy(deep=True);changed.cases[0].kind=kind
        assert pytest_case_ids(changed,[case.id],w.execution_profiles)==[]
    changed=w.model_copy(deep=True)
    changed.flow_modules[0]['scope']['entry_paths']=['frontend/src/login.tsx']
    cited={e.source_id for s in changed.cases[0].steps for r in s.expected_results for e in r.evidence}
    for document in changed.sources.documents:
        if document.id in cited:document.kind='code';document.path='frontend/src/login.tsx'
    assert pytest_case_ids(changed,[case.id],w.execution_profiles)==[]


def test_api_case_evidence_wins_over_mixed_module_paths(flow_app,tmp_path):
    from app.infrastructure.test_design.execution_profiles import pytest_case_ids
    _,_,_,_,_,_,w,case,_=prepare_pytest(flow_app,tmp_path)
    cited={e.source_id for s in w.cases[0].steps for r in s.expected_results for e in r.evidence}
    for document in w.sources.documents:
        if document.id in cited:document.kind='code';document.path='service.py'
    w.flow_modules[0]['scope']['entry_paths']=['service.py','frontend/src/settings.tsx']
    assert pytest_case_ids(w,[case.id],w.execution_profiles)==[case.id]


def test_mixed_bundle_validates_both_binding_types(flow_app,tmp_path):
    from tda.application.workspace import design_digest
    from tda.domain.workspace import AutomationBinding,AssertionMapping
    from tda.domain.contracts import ExecutableCase,UnitCall,CaseOracle,StepOracle,Assertion
    from app.infrastructure.test_design.artifact_baselines import publish_artifacts,load_bundle
    _,_,_,engine,_,_,w,case,_=prepare_pytest(flow_app,tmp_path)
    artifact=w.worker_artifacts[case.id];artifact.state='validated';artifact.reviewer='maintainer'
    other=w.cases[0].model_copy(deep=True);other.id='other_sum';w.cases.append(other)
    executable=ExecutableCase(id=other.id,scenario_id=other.id,title=other.title,kind=other.kind,runner='unit',steps=[],cleanup=[],depends_on=[],repetitions=1,notes=[],
        unit=UnitCall(language='python',module_path='service.py',function='add',args_json='[2,3]',kwargs_json='{}'),
        oracle=CaseOracle(case_id=other.id,rationale='Reviewed sum',steps=[StepOracle(step_index=0,assertions=[Assertion(kind='return_equals',pointer='',expected_json='5',evidence=other.steps[0].expected_results[0].evidence)])]),
        requirement_ids=other.requirement_ids,feature_ids=[],risk_ids=[],operation_ids=[])
    w.bindings[other.id]=AutomationBinding(case_id=other.id,case_revision=other.revision,design_sha256=design_digest(other),state='validated',reviewer='maintainer',executable=executable,
        expected_result_mapping=[AssertionMapping(design_step=0,expected_result=0,executable_step=0,assertion_indexes=[0],explanation='Return matches approved sum')])
    _,baseline=publish_artifacts(engine.settings,w,[case.id,other.id],'maintainer','Mixed suite',True)
    native,plans=load_bundle(baseline.baseline_dir,baseline.version)
    assert [b.case_id for b in native]==[case.id]
    assert [b.case_id for b in plans]==[other.id]


def test_pytest_exit_failure_cannot_be_reported_as_ready(tmp_path):
    from app.infrastructure.execution.adapters.generated_pytest import GeneratedPytestAdapter
    from app.domain.models import WorkerTask,TaskTarget
    (tmp_path/'.qc-generated-results.xml').write_text('<testsuite tests="1"><testcase classname="test_abc" name="test_case"/></testsuite>')
    task=WorkerTask(run_id='protocol',capability='unit.run',objective='Generated case',target=TaskTarget(project_id='project'),
        parameters={'generated_cases':[{'case_id':'case','path':'tests/.qc_generated/test_abc.py'}]})
    result=GeneratedPytestAdapter().parse_result(task,1,'',tmp_path)
    assert result.verdict=='unknown' and result.summary['results'][0]['status']=='blocked'


def test_browser_confirms_three_artifact_mappings_in_one_action(flow_app,tmp_path):
    playwright=pytest.importorskip('playwright.sync_api')
    from urllib.parse import urlsplit
    from tda.application.workspace import design_digest
    from tda.application.worker_artifacts import artifact_path
    client,store,project,engine,path,headers,w,case,root=prepare_pytest(flow_app,tmp_path)
    ids=[case.id]
    for number in (2,3):
        copy=w.cases[0].model_copy(deep=True);copy.id='SUM-T'+str(number);w.cases.append(copy);ids.append(copy.id)
        binding=w.worker_artifacts[case.id].model_copy(deep=True);binding.case_id=copy.id;binding.design_sha256=design_digest(copy)
        binding.artifact.path=artifact_path(copy.id);binding.test_selector=binding.artifact.path+'::test_case';w.worker_artifacts[copy.id]=binding
        w.automation_preparation[copy.id]={**w.automation_preparation[case.id],'design_sha256':binding.design_sha256}
    store.save_workspace(w,w.version,'fixture','bulk-artifact-fixture')
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
            page.get_by_role('button',name='Chọn repository').click();page.locator('#projects button').first.click();page.locator('.design-case-table').wait_for()
            page.get_by_text('Thao tác kỹ thuật / phiên bản',exact=True).click()
            confirm=page.get_by_role('button',name='Xác nhận mappings đã chọn',exact=True)
            assert confirm.is_disabled()
            for identifier in ids:page.get_by_role('checkbox',name='Chọn '+identifier,exact=True).check()
            assert confirm.is_enabled()
            confirm.click();panel=page.locator('.design-bulk-mapping')
            panel.get_by_label('Lý do xác nhận mapping cho các case đã chọn').fill('Reviewed generated code, fixture and all expected sums')
            panel.locator('input[type=checkbox]').check();panel.get_by_role('button',name='Xác nhận 3 mappings',exact=True).click()
            page.wait_for_function("[...document.querySelectorAll('.design-case-table tbody tr')].every(row=>row.textContent.includes('Sẵn sàng cho QC'))")
            assert page.get_by_role('button',name='Áp dụng cho QC',exact=True).is_enabled()
            result=store.get_workspace(project.project_id)
            assert all(result.worker_artifacts[cid].state=='validated' for cid in ids)
            assert store.get_project(project.project_id).active_baseline is None
            assert not errors
        finally:browser.close()
