import json
from pathlib import Path
import pytest
from test_workflow import run_workflow,ScriptedAgent
from tda.application.orchestrator import TestDesignOrchestrator as Workflow,CaseReviewValidationError
from tda.domain.contracts import TestPlan as Plan,PlannedScenario,GeneratedCase,GeneratedBatch,OracleBatch,CaseOracle,CriticReview,WorkflowConfig
from tda.infrastructure.sources import LocalSourceReader
from tda.infrastructure.artifacts import LocalArtifactStore
from tda.infrastructure.baselines import BaselineRepository


@pytest.mark.parametrize('action',['request_changes','review_blocker'])
def test_targeted_review_preserves_other_cases_ids_and_coverage(tmp_path,action):
    initial,_=run_workflow(tmp_path,ScriptedAgent(kinds=['functional','integration']))
    old=Path(initial['baseline']);names=['knowledge.json','sources.json','test_plan.json','cases/cases.json','review_gate.json']
    payload={name:json.loads((old/name).read_text(encoding='utf-8')) for name in names}
    target=payload['cases/cases.json'][0];unaffected=payload['cases/cases.json'][1]
    calls=[]
    class Agent:
        def propose(self,stage,instructions,context,output):
            calls.append(stage)
            if stage=='review.planning':return Plan(scenarios=[PlannedScenario.model_validate(context['scenario'])],rationale='fix selected',open_questions=[])
            if stage=='review.generation':
                case=GeneratedCase.model_validate({k:v for k,v in context['selected_case'].items() if k in GeneratedCase.model_fields});case.title='Case đã được chỉnh sửa'
                return GeneratedBatch(cases=[case],open_questions=[])
            if stage=='oracle':return OracleBatch(oracles=[CaseOracle.model_validate(target['oracle'])],open_questions=[])
            if stage=='critic':return CriticReview(recommendation='ready_for_human_review',summary='review targeted revision',issues=[])
            raise AssertionError(stage)
    config=WorkflowConfig(project='demo',source_revision='revision-1',model='fake',sources=[{'id':'prd','kind':'prd','paths':['prd.md']}],reference_execution=False)
    workflow=Workflow(Agent(),LocalSourceReader(),None,LocalArtifactStore(tmp_path/'revision-run'),BaselineRepository(tmp_path/'revision-baselines'))
    result=workflow.run(config,tmp_path,case_review={'case_id':target['id'],'action':action,'feedback':'Fix only this case','baseline_version':old.name,'artifacts':payload})
    new=Path(result['baseline']);cases=json.loads((new/'cases/cases.json').read_text(encoding='utf-8'))
    assert len(cases)==2 and cases[1]==unaffected
    assert cases[0]['id']==target['id']
    changes=json.loads((new/'review_changes.json').read_text())
    assert changes['preserved_case_count']==(2 if action=='review_blocker' else 1)
    assert changes['changed_case_ids']==([target['id']] if action=='request_changes' else [])
    assert json.loads((new/'coverage.json').read_text())['design_satisfied']
    if action=='review_blocker':assert calls==['critic']


def test_targeted_review_refuses_changed_source_instead_of_regenerating_suite(tmp_path):
    initial,_=run_workflow(tmp_path,ScriptedAgent(kinds=['functional']))
    old=Path(initial['baseline']);payload={name:json.loads((old/name).read_text(encoding='utf-8')) for name in ['knowledge.json','sources.json','test_plan.json','cases/cases.json','review_gate.json']}
    (tmp_path/'prd.md').write_text('Changed source')
    config=WorkflowConfig(project='demo',source_revision='revision-1',model='fake',sources=[{'id':'prd','kind':'prd','paths':['prd.md']}])
    workflow=Workflow(ScriptedAgent(),LocalSourceReader(),None,LocalArtifactStore(tmp_path/'revision-run'),BaselineRepository(tmp_path/'revision-baselines'))
    with pytest.raises(CaseReviewValidationError):workflow.run(config,tmp_path,case_review={'case_id':payload['cases/cases.json'][0]['id'],'action':'request_changes','feedback':'fix','baseline_version':old.name,'artifacts':payload})


@pytest.mark.parametrize('action',['manual_edit','add_case','delete_case','supplement'])
def test_suite_revision_preserves_original_and_records_add_edit_delete(tmp_path,action):
    from test_workflow import scenario, generated, oracle
    initial,_=run_workflow(tmp_path,ScriptedAgent(kinds=['functional','integration']))
    old=Path(initial['baseline']);payload={name:json.loads((old/name).read_text(encoding='utf-8')) for name in ['knowledge.json','sources.json','test_plan.json','cases/cases.json','review_gate.json']}
    before=(old/'cases/cases.json').read_bytes();target=payload['cases/cases.json'][0]
    changed=json.loads(json.dumps(target));changed['title']='Updated manually'
    new_plan=scenario('functional','supplemental');new_case=generated();new_case.id='new-case';new_case.scenario_id=new_plan.id
    new_case.steps[0].query=[{'name':'a','value':'2'}]
    from tda.domain.contracts import ExecutableCase,Parameter
    new_case.steps[0].query=[Parameter(name='a',value='2')]
    content=ExecutableCase(**new_case.model_dump(),oracle=oracle(new_case),requirement_ids=['R1'],feature_ids=['F1'],risk_ids=['K1'],operation_ids=[])
    class Agent:
        def propose(self,stage,instructions,context,output):
            if stage=='planning':
                assert len(context['existing_scenarios'])==2
                assert 'boundary' in context['coverage_gaps']['requested_additions']
                return Plan(scenarios=[new_plan],rationale='add boundary',open_questions=[])
            if stage=='generation.functional':return GeneratedBatch(cases=[new_case],open_questions=[])
            if stage=='oracle':return OracleBatch(oracles=[oracle(new_case)],open_questions=[])
            assert stage=='critic'
            return CriticReview(recommendation='ready_for_human_review',summary='review',issues=[])
    request={'case_id':target['id'],'action':action,'feedback':'Add boundary','baseline_version':old.name,'artifacts':payload}
    if action=='manual_edit':request['case_data']=changed
    if action=='add_case':request.update(case_id=new_case.id,case_data=content.model_dump(),scenario_data=new_plan.model_dump())
    config=WorkflowConfig(project='demo',source_revision='revision-1',model='fake',sources=[{'id':'prd','kind':'prd','paths':['prd.md']}],reference_execution=False)
    workflow=Workflow(Agent(),LocalSourceReader(),None,LocalArtifactStore(tmp_path/'edit-run'),BaselineRepository(tmp_path/'edit-baselines'))
    result=workflow.run(config,tmp_path,case_review=request)
    new=Path(result['baseline']);cases=json.loads((new/'cases/cases.json').read_text());changes=json.loads((new/'review_changes.json').read_text())
    assert (old/'cases/cases.json').read_bytes()==before
    assert cases[-1 if action=='delete_case' else 1]==payload['cases/cases.json'][1]
    assert len(cases)==(1 if action=='delete_case' else 2 if action=='manual_edit' else 3)
    assert changes['added_case_ids']==(['new-case'] if action in ('add_case','supplement') else [])
    assert changes['deleted_case_ids']==([target['id']] if action=='delete_case' else [])
    assert changes['changed_case_ids']==([target['id']] if action=='manual_edit' else [])


@pytest.mark.parametrize('action',['manual_edit','delete_case'])
def test_manual_invalid_or_deleted_only_case_remains_blocked(tmp_path,action):
    initial,_=run_workflow(tmp_path,ScriptedAgent(kinds=['functional']))
    old=Path(initial['baseline']);payload={name:json.loads((old/name).read_text(encoding='utf-8')) for name in ['knowledge.json','sources.json','test_plan.json','cases/cases.json','review_gate.json']}
    case=json.loads(json.dumps(payload['cases/cases.json'][0]));case['oracle']['steps'][0]['assertions'][0]['expected_json']='"not-an-http-status"'
    class Agent:
        def propose(self,stage,instructions,context,output):
            assert stage=='critic'
            return CriticReview(recommendation='ready_for_human_review',summary='review',issues=[])
    workflow=Workflow(Agent(),LocalSourceReader(),None,LocalArtifactStore(tmp_path/'invalid-run'),BaselineRepository(tmp_path/'invalid-baselines'))
    config=WorkflowConfig(project='demo',source_revision='revision-1',model='fake',sources=[{'id':'prd','kind':'prd','paths':['prd.md']}],reference_execution=False)
    result=workflow.run(config,tmp_path,case_review={'case_id':case['id'],'case_data':case,'action':action,'feedback':'update','baseline_version':old.name,'artifacts':payload})
    gate=json.loads((Path(result['baseline'])/'review_gate.json').read_text())
    assert gate['design_blockers']
