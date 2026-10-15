import json
from pathlib import Path
from types import SimpleNamespace

from app.application.project_onboarding import ManagedBaselineBinding,ProjectOnboarding
from app.domain.models import Actor,ExecutionPlan,Installation,QCRun,Revision,SourceAnalysis,ProjectDescriptor,Trigger,TriggerContext
from app.domain.test_design import ManagedBaseline
from app.infrastructure.persistence.sqlite import SqliteRunStore
from app.infrastructure.persistence.test_design import DesignStore
from app.infrastructure.settings import Settings
from app.infrastructure.test_design.runtime import ManagedBaselineRuntime
from tda.domain.contracts import (WorkflowConfig,SourceBundle,SourceDocument,Evidence,Assertion,GeneratedCase,
    UnitCall,ExecutableCase,CaseOracle,StepOracle)
from tda.domain.rules import TestValidator as Validator
from tda.infrastructure.baselines import BaselineRepository
from tda.pipeline import execute,approve,dump


def test_qc_binds_unit_baseline_without_http_target_and_detects_changed_code(tmp_path):
    store=DesignStore(SqliteRunStore(tmp_path/'db').path)
    project=ProjectOnboarding(store).register_repository(1,{'id':10,'name':'demo','full_name':'owner/demo','default_branch':'main'})
    root=tmp_path/'design';workspace=tmp_path/'code';workspace.mkdir()
    source='def add(a,b): return a+b\n';(workspace/'calc.py').write_text(source)
    sources=SourceBundle(documents=[SourceDocument(id='code',kind='code',path='calc.py',sha256='x',content=source)],operations=[])
    generated=GeneratedCase(id='U',scenario_id='S',title='sum',kind='functional',runner='unit',steps=[],cleanup=[],depends_on=[],repetitions=1,notes=[],
        unit=UnitCall(language='python',module_path='calc.py',function='add',args_json='[2,3]',kwargs_json='{}'))
    case=ExecutableCase(**generated.model_dump(),oracle=CaseOracle(case_id='U',rationale='code',steps=[StepOracle(step_index=0,assertions=[
        Assertion(kind='return_equals',pointer='',expected_json='5',expectation_basis='implementation',evidence=[Evidence(source_id='code',quote=source)])])]),
        requirement_ids=['R'],feature_ids=[],risk_ids=[],operation_ids=[])
    config=WorkflowConfig(project='demo',source_revision='base',model='fake',sources=[{'id':'code','kind':'code','paths':['calc.py']}],runners=['unit'])
    baseline=BaselineRepository(root/'projects'/project.project_id/'baselines').publish_draft(config,{
        'cases/cases.json':[case.model_dump()],'sources.json':sources.model_dump(),'knowledge.json':{},'traceability.json':{'R':['U']},
        'test_plan.json':{},'validation_report.json':Validator().validate([case],sources).model_dump(),
        'coverage.json':{'design_satisfied':True},'critic_review.json':{'recommendation':'ready_for_human_review','issues':[]},
        'review_gate.json':{'design_blockers':[]},'ground_truth/oracles.json':{}})
    report=tmp_path/'reference.json';dump(report,execute(baseline,None,workspace=workspace));approve(baseline,'reviewer',report)
    store.save_baseline(ManagedBaseline(project_id=project.project_id,version=baseline.name,job_id='fixture',source_revision='base',
        baseline_dir=str(baseline),state='approved',report_path=str(report)))
    project.active_baseline=baseline.name;project.target_profile={'kind':'irrelevant'};store.save_project(project)
    context=TriggerContext(trigger=Trigger(event='pull_request',action='opened',delivery_id='fixture',actor=Actor(id='1',login='reviewer')),
        repository=project.repository,installation=Installation(id=1),revision=Revision(head_sha='changed',base_sha='base',head_ref='feature',base_ref='main'))
    run=QCRun(trigger_context=context,analysis=SourceAnalysis(projects=[ProjectDescriptor(id='python',language='python')]),plan=ExecutionPlan())
    class Profiles:
        def materialize(self,*args):raise AssertionError('Unit-only suite must not provision HTTP')
    binding=ManagedBaselineBinding(store,Profiles());binding.prepare(run);binding.attach(run)
    task=run.plan.tasks[0];assert task.capability=='baseline.repository';assert task.target.type=='repository'
    runtime=ManagedBaselineRuntime(store,root,Settings(_env_file=None,agent_qc_test_isolation='local'))
    assert runtime.execute(task,run.analysis.projects[0],workspace).verdict=='pass'
    (workspace/'calc.py').write_text('def add(a,b): return a+b+1\n')
    assert runtime.execute(task,run.analysis.projects[0],workspace).verdict=='fail'
