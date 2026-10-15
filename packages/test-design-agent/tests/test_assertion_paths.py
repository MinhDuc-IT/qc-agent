import json
import pytest
from tda.domain.assertion_paths import json_pointer
from tda.domain.contracts import Evidence,ExecutableCase,SourceDocument,SourceBundle
from tda.domain.workspace import DesignCase,AutomationBinding
from tda.application.automation import normalize_binding,mapping_issues
from tda.application.workspace import binding_design_issues
from tda.domain.rules import TestValidator as Validator


@pytest.mark.parametrize('before,after',[('$',''),('$.detail','/detail'),('$.data[0].id','/data/0/id'),('/a~1b/~0key','/a~1b/~0key')])
def test_json_path_to_json_pointer(before,after):assert json_pointer(before)==after


@pytest.mark.parametrize('pointer',['$..detail','$.data[*]','$.data[?(@.id)]','/bad~2key'])
def test_ambiguous_json_paths_are_not_guessed(pointer):
    with pytest.raises(ValueError):json_pointer(pointer)


def legacy_binding(pointer='$.detail',expected_json='{"detail":"Invalid token"}'):
    claim={'source_id':'code','quote':'raise HTTPException(status_code=400, detail="Invalid token")'}
    case=DesignCase(id='C',title='Invalid reset token',kind='functional',status='approved',requirement_ids=['R'],
        steps=[{'action':'POST reset password with invalid token','expected_results':[{'description':'HTTP 400 with Invalid token',
            'origin':'implementation_derived','evidence':[claim],'assertion_kind':'json_equals','pointer':pointer,'expected_json':expected_json}]}])
    executable=ExecutableCase(id='C',scenario_id='S',title=case.title,kind=case.kind,runner='http',
        steps=[{'method':'POST','path':'/reset-password/','query':[],'body_json':'{"token":"invalid"}','auth':'none'}],
        cleanup=[],depends_on=[],repetitions=1,notes=[],requirement_ids=['R'],feature_ids=[],risk_ids=[],operation_ids=[],
        oracle={'case_id':'C','rationale':'Source exception','steps':[{'step_index':0,'assertions':[
            {'kind':'status','pointer':'$.status','expected_json':'400','expectation_basis':'implementation','evidence':[claim]},
            {'kind':'json_equals','pointer':'$.detail','expected_json':'"Invalid token"','expectation_basis':'implementation','evidence':[claim]}]}]})
    binding=AutomationBinding(case_id='C',case_revision=1,design_sha256='hash',executable=executable,
        expected_result_mapping=[{'design_step':0,'expected_result':0,'executable_step':0,'assertion_indexes':[0],'explanation':'Expected response'}])
    sources=SourceBundle(documents=[SourceDocument(id='code',kind='code',path='main.py',sha256='x',content=claim['quote'])],operations=[])
    return case,binding,sources


def test_legacy_wrapped_body_is_preserved_and_mapping_is_repaired():
    case,binding,sources=legacy_binding();original=case.model_dump()
    assert mapping_issues(case,binding)
    notes=normalize_binding(case,binding)
    assert notes and case.model_dump()==original
    assertions=binding.executable.oracle.steps[0].assertions
    assert assertions[0].pointer==''
    assert any(a.kind=='json_equals' and a.pointer=='' and json.loads(a.expected_json)=={'detail':'Invalid token'} for a in assertions)
    assert not Validator().validate([binding.executable],sources).issues
    assert not binding_design_issues(case,binding.executable)
    assert not mapping_issues(case,binding)
    assert binding.state=='bound' and binding.reviewer is None
    count=len(assertions);normalize_binding(case,binding)
    assert len(assertions)==count


def test_field_expected_value_stays_on_field_and_is_not_weakened_to_status():
    case,binding,sources=legacy_binding(expected_json='"Invalid token"')
    normalize_binding(case,binding)
    assert not mapping_issues(case,binding)
    assert binding.executable.oracle.steps[0].assertions[1].pointer=='/detail'
    binding.expected_result_mapping[0].assertion_indexes=[0]
    assert mapping_issues(case,binding)


def test_wrong_expected_value_is_still_rejected():
    case,binding,sources=legacy_binding(pointer='$')
    normalize_binding(case,binding)
    for assertion in binding.executable.oracle.steps[0].assertions:
        if assertion.kind=='json_equals':assertion.expected_json='{"detail":"Wrong error"}'
    assert mapping_issues(case,binding)
    assert binding_design_issues(case,binding.executable)


def test_normalized_binding_executor_detects_wrong_response_body():
    from tda.runner import HttpSuiteExecutor
    case,binding,sources=legacy_binding();normalize_binding(case,binding)
    class Executor(HttpSuiteExecutor):
        response={'detail':'Invalid token'}
        def _request(self,spec):return 400,self.response,1
    executor=Executor(auth_token=None)
    assert executor.execute([binding.executable.model_dump()],'http://fixture.local',allow_mutations=True)['passed']
    executor.response={'detail':'Wrong error'}
    result=executor.execute([binding.executable.model_dump()],'http://fixture.local',allow_mutations=True)
    assert not result['passed'] and result['results'][0]['status']=='defect'
