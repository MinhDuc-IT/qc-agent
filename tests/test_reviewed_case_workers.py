import asyncio,json
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_flow_design_app import flow_app
from test_generated_pytest import prepare_pytest
from app.domain.models import WorkerTask,TaskTarget,ProjectDescriptor
from app.infrastructure.test_design.runtime import ManagedBaselineRuntime
from app.infrastructure.test_design.reviewed_baselines import load_reviewed


def enabled(flow_app,tmp_path,cached=False):
    client,store,project,engine,path,headers,w,case,root=prepare_pytest(flow_app,tmp_path)
    factory=engine.agent_factory
    if not cached:
        w.worker_artifacts.clear();w=store.save_workspace(w,w.version,'fixture','no_scripts')
    class NoTDA:
        def __call__(self,*args):pytest.fail('Enable QC must not call TDA')
    engine.agent_factory=NoTDA()
    response=client.post(path+'/workspace/enable-qc',headers=headers,json={'version':w.version,'case_ids':[case.id],'reason':'Reviewed regression intent','acknowledge_gaps':True})
    assert response.status_code==202,response.text
    job=store.claim_job();asyncio.run(engine.execute(job))
    assert store.get_job(job.job_id).status=='completed',store.get_job(job.job_id).error_message
    active=store.get_project(project.project_id)
    baseline=store.get_baseline(project.project_id,active.active_baseline)
    assert baseline.execution_kind=='reviewed_cases'
    task=WorkerTask(run_id='pr-run',capability='unit.run',objective='Execute approved cases',target=TaskTarget(project_id='python'),
        parameters={'managed_project_id':project.project_id,'baseline_version':baseline.version,'installation_id':10,
            'repository':project.repository.full_name,'reviewed_cases':True,'case_ids':[case.id]})
    return store,project,engine,baseline,task,root,factory


def test_description_compiles_on_pr_and_detects_regression_without_changing_oracle(flow_app,tmp_path):
    store,project,engine,baseline,task,root,factory=enabled(flow_app,tmp_path)
    before=(Path(baseline.baseline_dir)/'reviewed_cases.json').read_bytes()
    runtime=ManagedBaselineRuntime(store,engine.settings.agent_qc_tda_dir,engine.settings,case_agent_factory=factory)
    passed=runtime.execute_reviewed(task,ProjectDescriptor(id='python',language='python'),root)
    assert passed.verdict=='pass',passed.summary
    row=passed.summary['results'][0]
    assert row['execution_mode']=='description' and row['expected_results'][0]['status']=='pass'
    runtime.case_agent_factory=lambda _:pytest.fail('Passing reviewed script should be reused from private QC cache')
    (root/'service.py').write_text('def add(a,b): return a+b+1\n')
    failed=runtime.execute_reviewed(task,ProjectDescriptor(id='python',language='python'),root)
    assert failed.verdict=='fail',failed.summary
    assert failed.summary['results'][0]['status']=='defect'
    assert (Path(baseline.baseline_dir)/'reviewed_cases.json').read_bytes()==before


def test_invalid_tda_script_is_discarded_and_qc_builds_from_description(flow_app,tmp_path):
    from app.infrastructure.test_design.case_routing import route_cases
    client,store,project,engine,path,headers,w,case,root=prepare_pytest(flow_app,tmp_path)
    w.worker_artifacts[case.id].validation_issues=['Mocked result does not verify dedupe']
    packets,blocked=route_cases(w,[case.id],w.execution_profiles,engine.settings)
    assert not blocked and packets[0].cached_artifact is None


def test_orchestrator_preserves_existing_tests_and_only_deduplicates_explicit_scenarios(flow_app,tmp_path):
    from app.application.project_onboarding import ManagedBaselineBinding
    from app.domain.models import QCRun,TriggerContext,Trigger,Actor,Installation,Revision,SourceAnalysis,ExecutionPlan
    store,project,engine,baseline,task,root,_=enabled(flow_app,tmp_path)
    packet=load_reviewed(Path(baseline.baseline_dir),baseline.version)[0][0]
    run=QCRun(trigger_context=TriggerContext(trigger=Trigger(event='pull_request',action='opened',delivery_id='reviewed',actor=Actor(id='1',login='owner')),
        installation=Installation(id=10),repository=project.repository,revision=Revision(head_sha='pr',base_sha='base',head_ref='feature',base_ref='main')),
        analysis=SourceAnalysis(projects=[ProjectDescriptor(id='python',language='python')],changed_files=['service.py']),plan=ExecutionPlan(tasks=[
            WorkerTask(run_id='r',capability='unit.run',objective='Run existing tests',target=TaskTarget(project_id='python')),
            WorkerTask(run_id='r',capability='functional.api',objective='Explore change',target=TaskTarget(project_id='python'),scope={'scenarios':[packet.case.title,'Uncovered branch']})]))
    binding=ManagedBaselineBinding(store,SimpleNamespace(materialize=lambda _:None))
    binding.prepare(run);binding.attach(run)
    assert run.analysis.config['reviewed_regression_catalog'][0]['case_id']==packet.case.id
    assert len(run.plan.tasks)==3
    assert run.plan.tasks[0].parameters['test_source']=='existing'
    assert run.plan.tasks[1].scope['scenarios']==['Uncovered branch']
    assert run.plan.tasks[2].parameters['case_ids']==[packet.case.id]


def test_cached_script_needs_no_llm_and_executes_pr_source(flow_app,tmp_path):
    store,project,engine,baseline,task,root,_=enabled(flow_app,tmp_path,cached=True)
    engine.settings.openai_api_key=None
    runtime=ManagedBaselineRuntime(store,engine.settings.agent_qc_tda_dir,engine.settings)
    result=runtime.execute_reviewed(task,ProjectDescriptor(id='python',language='python'),root)
    assert result.verdict=='pass',result.summary
    assert result.summary['results'][0]['execution_mode']=='cached_script'


def test_missing_qc_key_blocks_description_without_using_tda_key(flow_app,tmp_path):
    store,project,engine,baseline,task,root,_=enabled(flow_app,tmp_path)
    engine.settings.openai_api_key=None;engine.settings.tda_openai_api_key='not-the-qc-key'
    result=ManagedBaselineRuntime(store,engine.settings.agent_qc_tda_dir,engine.settings).execute_reviewed(task,ProjectDescriptor(id='python',language='python'),root)
    assert result.verdict=='unknown' and result.summary['results'][0]['status']=='blocked'
    assert 'OPENAI_API_KEY' in result.summary['results'][0]['reason']


def test_reviewed_bundle_rejects_tamper(flow_app,tmp_path):
    _,_,_,baseline,_,_,_=enabled(flow_app,tmp_path)
    file=Path(baseline.baseline_dir)/'reviewed_cases.json';file.write_text('{}')
    with pytest.raises(ValueError):load_reviewed(Path(baseline.baseline_dir),baseline.version)


def test_hercules_builds_exact_oracles_and_requires_all_scenario_evidence(flow_app,tmp_path):
    from tda.domain.case_execution import ReviewedCasePacket
    from tda.application.workspace import design_digest
    from workers.functional_hercules.service import build_feature,reviewed_evidence,reviewed_scenario_id
    store,_,_,baseline,task,root,_=enabled(flow_app,tmp_path)
    packet=load_reviewed(Path(baseline.baseline_dir),baseline.version)[0][0]
    packet.worker='hercules';packet.cached_artifact=None
    task.target.ref='http://test.local';task.parameters={'reviewed_case':packet.model_dump()}
    feature=build_feature(task)
    assert packet.case.steps[0].expected_results[0].description in feature
    assert 'objective should complete' not in feature
    identity=reviewed_scenario_id(packet.case.id,0,0)
    (tmp_path/'results.xml').write_text(f'<testsuite><testcase name="{identity}"/></testsuite>')
    proof=reviewed_evidence(task,tmp_path)
    assert proof['expected_results'][0]['status']=='blocked'  # Completion alone is not an assertion.
    (tmp_path/'results.xml').write_text(f'<testsuite><testcase name="{identity}"><properties><property name="qc_is_assert" value="True"/><property name="qc_is_passed" value="True"/><property name="qc_assert_summary" value="Observed expected result"/></properties></testcase></testsuite>')
    proof=reviewed_evidence(task,tmp_path)
    assert proof['expected_results'][0]['status']=='pass'
    (tmp_path/'results.xml').write_text('<testsuite><testcase name="unrelated"/></testsuite>')
    assert reviewed_evidence(task,tmp_path)['expected_results'][0]['status']=='blocked'


def test_hercules_junit_instrumentation_preserves_real_assertion_flags(monkeypatch):
    import sys,types
    from workers.functional_hercules.reviewed_runner import instrument_junit
    recorded=[]
    class Generator:
        def add_test_case(self,scenario,feature,data,execution_time,cost_metric):recorded.append(data)
    module=types.ModuleType('testzeus_hercules.utils.junit_helper');module.JUnitXMLGenerator=Generator
    monkeypatch.setitem(sys.modules,'testzeus_hercules.utils.junit_helper',module)
    instrument_junit()
    Generator().add_test_case('scenario','feature',{'is_assert':False,'is_passed':True},1,{})
    assert recorded[0]['qc_is_assert'] is False  # Generic completion cannot become assertion PASS.
    Generator().add_test_case('scenario','feature',{'is_assert':True,'is_passed':False,'assert_summary':'Observed mismatch'},1,{})
    assert recorded[1]['qc_is_assert'] is True and recorded[1]['qc_is_passed'] is False
