import json
from pathlib import Path
import pytest

from tda.application.design_validation import normalize_string_oracles
from tda.application.stages import OracleEngine,SpecializedGenerator
from tda.domain.contracts import (GeneratedCase,GeneratedBatch,OracleBatch,CaseOracle,StepOracle,
    SourceBundle,SourceDocument,PlannedScenario)
from tda.domain.contracts import KnowledgeModel,RequirementNode
from tda.domain.rules import TestValidator as Validator
from test_native_runners import native_cases,evidence,TEXT


def sources():
    return SourceBundle(documents=[SourceDocument(id='prd',path='prd.md',kind='prd',sha256='x',content=TEXT)],operations=[])


def planned(case):
    return PlannedScenario(id=case.scenario_id,title=case.title,runner=case.runner,kind=case.kind,priority='P0',
        requirement_ids=['R'],feature_ids=[],risk_ids=[],operation_ids=[],acceptance_criteria=[TEXT],evidence=evidence())


def generated(case):return GeneratedCase.model_validate({key:value for key,value in case.model_dump().items() if key in GeneratedCase.model_fields})


@pytest.mark.parametrize('literal',['5','','ValueError','Agent-QC Calculator','{"detail":','null','true'])
def test_string_normalization_keeps_literal_text_and_is_idempotent(literal):
    oracle=native_cases()[1].oracle
    assertion=oracle.steps[1].assertions[0];assertion.expected_json=literal
    original_evidence=assertion.evidence.copy()
    normalize_string_oracles([oracle])
    assert json.loads(assertion.expected_json)==literal
    assert assertion.evidence==original_evidence
    encoded=assertion.expected_json;normalize_string_oracles([oracle])
    assert assertion.expected_json==encoded


@pytest.mark.parametrize('weaken',[False,True])
def test_browser_oracle_correction_can_reindex_but_cannot_weaken(weaken):
    case=native_cases()[1]
    original=CaseOracle(case_id=case.id,rationale='bad indexing',steps=[StepOracle(step_index=0,
        assertions=[assertion.model_copy(deep=True) for step in case.oracle.steps for assertion in step.assertions])])
    corrected=case.oracle.model_copy(deep=True)
    if weaken:corrected.steps[-1].assertions[0].expected_json='"6"'
    class Agent:
        calls=[]
        def propose(self,stage,instructions,context,output):
            self.calls.append(stage)
            if stage=='oracle':return OracleBatch(oracles=[original],open_questions=[])
            assert stage=='oracle.correction'
            assert 'execution' not in context
            assert context['validation_feedback']
            return OracleBatch(oracles=[corrected],open_questions=[])
    agent=Agent();cases,batch=OracleEngine(agent).attach([generated(case)],[planned(case)],sources())
    assert agent.calls==['oracle','oracle.correction']
    report=Validator().validate(cases,sources())
    assert bool(report.valid_case_ids)==(not weaken)
    if weaken:assert cases[0].oracle==original


def test_empty_k6_requests_are_corrected_before_oracle_generation():
    case=native_cases()[2];broken=generated(case);broken.steps=[]
    class Agent:
        calls=[]
        def propose(self,stage,instructions,context,output):
            self.calls.append(stage)
            if stage=='generation.performance':return GeneratedBatch(cases=[broken],open_questions=[])
            assert stage=='generation.performance.correction'
            assert context['validation_feedback'][case.id]
            return GeneratedBatch(cases=[generated(case)],open_questions=[])
    knowledge=KnowledgeModel(requirements=[RequirementNode(id='R',title='Load',execution_scope='load',scope_reason='k6 source',
        acceptance_criteria=[TEXT],evidence=evidence())],features=[],risks=[],dependencies=[],open_questions=[])
    agent=Agent();batch=SpecializedGenerator(agent,'performance').generate([planned(case)],knowledge,sources(),[])
    assert batch.cases[0].steps==case.steps
    assert agent.calls==['generation.performance','generation.performance.correction']


def test_oracle_correction_is_bounded_and_can_be_disabled():
    case=native_cases()[1];bad=case.oracle.model_copy(deep=True);bad.steps=bad.steps[:1]
    class Agent:
        calls=[]
        def propose(self,stage,*args):self.calls.append(stage);return OracleBatch(oracles=[bad],open_questions=[])
    agent=Agent();OracleEngine(agent).attach([generated(case)],[planned(case)],sources(),allow_correction=False)
    assert agent.calls==['oracle']


def test_invalid_json_object_is_not_normalized_into_a_different_expectation():
    oracle=native_cases()[2].oracle
    assertion=oracle.steps[0].assertions[1];assertion.expected_json='{"detail":'
    normalize_string_oracles([oracle])
    assert assertion.expected_json=='{"detail":'


def test_browser_input_completion_preserves_existing_values():
    from tda.application.design_validation import preserves_browser_inputs,generated_shape_feedback
    case=native_cases()[1];old=case.browser.model_copy(deep=True)
    case.browser.actions[1].value=None
    assert generated_shape_feedback(case)
    assert preserves_browser_inputs(case.browser,old)
    changed=old.model_copy(deep=True);changed.actions[2].value='9'
    assert not preserves_browser_inputs(case.browser,changed)


@pytest.mark.parametrize('corrected',[True,False])
def test_generator_corrects_wrong_kind_and_historical_scenario_without_relaxing_validation(corrected):
    from tda.application.stages import GenerationValidationError
    target=native_cases()[6];wrong=native_cases()[5]
    knowledge=KnowledgeModel(requirements=[RequirementNode(id='R',title='HTML',acceptance_criteria=[TEXT],evidence=evidence())],
        features=[],risks=[],dependencies=[],open_questions=[])
    class Agent:
        calls=[]
        def propose(self,stage,instructions,context,output):
            self.calls.append(stage)
            if stage.endswith('identity_correction'):
                assert context['validation_feedback']
                assert [scenario['id'] for scenario in context['scenarios']]==[target.scenario_id]
            return GeneratedBatch(cases=[generated(target if corrected and stage.endswith('identity_correction') else wrong)],open_questions=[])
    agent=Agent()
    if corrected:
        result=SpecializedGenerator(agent,'functional').generate([planned(target)],knowledge,sources(),[])
        assert result.cases[0].kind=='functional' and result.cases[0].scenario_id==target.scenario_id
    else:
        with pytest.raises(GenerationValidationError):SpecializedGenerator(agent,'functional').generate([planned(target)],knowledge,sources(),[])
    assert agent.calls==['generation.functional','generation.functional.identity_correction']
