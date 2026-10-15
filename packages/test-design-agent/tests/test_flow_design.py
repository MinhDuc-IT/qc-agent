import json
from pathlib import Path
import pytest
from tda.domain.contracts import SourceDocument,Evidence,RequirementNode,Parameter
from tda.domain.workspace import DesignCase,DesignStep,ExpectedResult
from tda.domain.investigation import (ProjectSurvey,ModuleScope,InquiryTurn,ToolRequest,ModuleStudy,FlowNode,Behavior,
                                     FlowCaseBatch,ModuleReview,VerifiedLink)
from tda.infrastructure.artifacts import LocalArtifactStore
from tda.infrastructure.repository_tools import RepositoryTools
from tda.application.flow_design import FlowDesignAgent,report
from tda.application.ports import AgentFailure
from tda.application.workspace import refresh_workspace


@pytest.fixture
def project(tmp_path):
    root=tmp_path/'repo';root.mkdir()
    (root/'service.py').write_text('def add(a, b):\n    return a + b\n',encoding='utf-8')
    (root/'.env').write_text('SECRET=must-never-read',encoding='utf-8')
    doc=SourceDocument(id='prd',kind='prd',path='requirements.md',sha256='a'*64,
        content='Sum of 2 and 3 is 5.\n')
    artifacts=LocalArtifactStore(tmp_path/'artifacts')
    return root,doc,artifacts


class FakeInvestigator:
    def __init__(self,fail_review=False):self.turns=0;self.fail_review=fail_review;self.calls=[]
    def propose(self,stage,instructions,context,output):
        self.calls.append(stage)
        normative=Evidence(source_id='prd',quote='Sum of 2 and 3 is 5.')
        if stage=='flow.survey':return ProjectSurvey(modules=[ModuleScope(id='SUM',title='Addition',objective='Verify addition',entry_paths=['service.py'])])
        if stage=='flow.investigate':
            self.turns+=1
            if self.turns%3==1:return InquiryTurn(notes='Find add',requests=[ToolRequest(name='search',query='def add')])
            if self.turns%3==2:
                assert context['recent_tool_results'][0]['result']['matches']
                return InquiryTurn(notes='Read add implementation',requests=[ToolRequest(name='read_file',path='service.py')])
            result=context['recent_tool_results'][0]['result'];code=Evidence(source_id=result['source_id'],quote='return a + b')
            return InquiryTurn(study=ModuleStudy(module_id='SUM',summary='API logic adds operands',
                requirements=[RequirementNode(id='SUM-R1',title='Addition',acceptance_criteria=['Sum of 2 and 3 is 5.'],evidence=[normative])],
                flow=[FlowNode(role='service',description='Add operands',evidence=[code])],
                behaviors=[Behavior(id='SUM-B1',description='Sum of 2 and 3 is 5.',requirement_ids=['SUM-R1'],
                    technique='happy_path',basis='specified',expected_behavior='Sum of 2 and 3 is 5.',evidence=[normative,code])]))
        if stage=='flow.write':
            case=DesignCase(id='SUM-T1',title='Add 2 and 3',objective='Verify sum',requirement_ids=['SUM-R1'],
                criterion_ids=[context['criteria'][0]['id']],test_data=[Parameter(name='a',value='2'),Parameter(name='b',value='3')],
                steps=[DesignStep(action='Call add(2,3)',expected_results=[ExpectedResult(description='Return 5',origin='specified',evidence=[normative])])])
            return FlowCaseBatch(cases=[case],behavior_links=[{'behavior_id':'SUM-B1','case_ids':['SUM-T1']}])
        if stage=='flow.review':
            if self.fail_review:raise AgentFailure('Budget exhausted',stage=stage,error_kind='budget')
            return ModuleReview(links=[VerifiedLink(behavior_id='SUM-B1',case_id='SUM-T1',step_indexes=[0],
                criterion_ids=[context['criteria'][0]['id']],verdict='verified',explanation='Inputs 2 and 3 and observable output 5',evidence=[normative])])
        raise AssertionError(stage)


def test_agent_searches_then_reads_flow_and_independently_verifies_cases(project):
    root,doc,artifacts=project;tools=RepositoryTools(root,artifacts,[doc]);agent=FakeInvestigator()
    w=FlowDesignAgent(agent,tools,artifacts).run('p','sha')
    assert agent.calls==['flow.survey','flow.investigate','flow.investigate','flow.investigate','flow.write','flow.review']
    assert w.design_method=='flow_investigation' and len(w.cases)==1
    assert w.completeness['gate']=='PASS'
    assert w.completeness['metrics']['scenario']['numerator']==1
    assert w.completeness['metrics']['requirement']['numerator']==1
    assert (artifacts.root/'tools/002.json').exists()
    assert report(w)==report(w)


def test_evidence_must_be_in_observed_excerpt_not_just_existing_file(project):
    root,doc,artifacts=project;tools=RepositoryTools(root,artifacts,[doc]);source=tools.snapshot('service.py')
    with pytest.raises(ValueError):tools.check_evidence([Evidence(source_id=source.id,quote='return a + b')])
    tools.read_file('service.py',1,1)
    with pytest.raises(ValueError):tools.check_evidence([Evidence(source_id=source.id,quote='return a + b')])
    tools.read_file('service.py',2,2);tools.check_evidence([Evidence(source_id=source.id,quote='return a + b')])


@pytest.mark.parametrize('path',['../outside.py','.env','/etc/passwd','.git/config','C:/secret.py'])
def test_repository_tools_deny_secret_and_escaping_paths(project,path):
    root,doc,artifacts=project;tools=RepositoryTools(root,artifacts,[doc])
    assert not tools.allowed(path)
    assert '.env' not in tools.list_files()['files']
    with pytest.raises(ValueError):tools.snapshot(path)


def test_budget_exhaustion_preserves_cases_but_never_claims_verified_coverage(project):
    root,doc,artifacts=project;w=FlowDesignAgent(FakeInvestigator(True),RepositoryTools(root,artifacts,[doc]),artifacts).run('p','sha')
    assert len(w.cases)==1 and w.generation_diagnostic['error_kind']=='budget'
    assert w.flow_modules[0]['status']=='partial'
    assert w.completeness['metrics']['scenario']['numerator']==0
    assert w.completeness['gate']=='NEEDS_REVIEW'


def test_editing_case_invalidates_previous_independent_review(project):
    root,doc,artifacts=project;w=FlowDesignAgent(FakeInvestigator(),RepositoryTools(root,artifacts,[doc]),artifacts).run('p','sha')
    w.cases[0].steps[0].expected_results[0].description='Return 999'
    refresh_workspace(w)
    assert w.completeness['metrics']['scenario']['numerator']==0


def test_supplement_does_not_overwrite_human_case(project,tmp_path):
    root,doc,artifacts=project;w=FlowDesignAgent(FakeInvestigator(),RepositoryTools(root,artifacts,[doc]),artifacts).run('p','sha')
    case=w.cases[0];case.title='Human title';case.human_edited=True;case.author='maintainer'
    original=case.model_dump(exclude={'status'})
    store=LocalArtifactStore(tmp_path/'supplement');agent=FakeInvestigator()
    updated=FlowDesignAgent(agent,RepositoryTools(root,store,[doc]),store,max_rounds=1).run('p','sha',w,action='supplement',module_ids=['SUM'])
    assert updated.cases[0].model_dump(exclude={'status'})==original
    assert 'flow.survey' not in agent.calls


def test_search_accepts_standard_dot_root(project):
    root,doc,artifacts=project;tools=RepositoryTools(root,artifacts,[doc])
    assert tools.list_files('.')==tools.list_files('')
    assert tools.search('def add','.')['matches'][0]['path']=='service.py'


def test_expected_result_accepts_normative_evidence_with_exact_code_support(project):
    root,doc,artifacts=project;w=FlowDesignAgent(FakeInvestigator(),RepositoryTools(root,artifacts,[doc]),artifacts).run('p','sha')
    code=next(d for d in w.sources.documents if d.kind=='code')
    expected=w.cases[0].steps[0].expected_results[0]
    expected.evidence.append(Evidence(source_id=code.id,quote='return a + b'))
    refresh_workspace(w)
    assert w.cases[0].status=='ready_for_review'
    expected.evidence=[expected.evidence[-1]]
    refresh_workspace(w)
    assert w.cases[0].status=='needs_clarification'


def test_llm_cannot_approve_its_own_generated_case(project):
    class Agent(FakeInvestigator):
        def propose(self,stage,instructions,context,output):
            result=super().propose(stage,instructions,context,output)
            if stage=='flow.write':result.cases[0].status='approved';result.cases[0].revision=99
            return result
    root,doc,artifacts=project
    w=FlowDesignAgent(Agent(),RepositoryTools(root,artifacts,[doc]),artifacts).run('p','sha')
    assert w.cases[0].status=='ready_for_review' and w.cases[0].revision==1


def test_study_recovers_unique_source_id_and_reads_missing_range(project):
    root,doc,artifacts=project
    (root/'service.py').write_text('# padding\n'*130+'def add(a, b):\n    return a + b\n',encoding='utf-8')
    tools=RepositoryTools(root,artifacts,[doc])
    actual=tools.snapshot('service.py')
    tools.read_file(doc.path,1,120)
    fake=FakeInvestigator();fake.turns=2
    study=fake.propose('flow.investigate','',{'recent_tool_results':[{'result':{'source_id':actual.id}}]},InquiryTurn).study
    for node in study.flow:
        for claim in node.evidence:
            claim.source_id='mistyped-source'
            claim.quote='return a + b'
    behavior=study.behaviors[0]
    behavior.basis='specified'
    behavior.evidence=[Evidence(source_id='mistyped-source',quote='return a + b')]
    engine=FlowDesignAgent(fake,tools,artifacts)
    engine.ground_study(study)
    assert behavior.basis=='implementation_derived'
    assert behavior.evidence[0].source_id==actual.id
    assert tools.calls>=1
    tools.check_evidence(behavior.evidence)
    behavior.evidence[0].quote='return invented_result'
    with pytest.raises(ValueError):engine.ground_study(study)


def test_turn_limit_in_one_module_does_not_skip_remaining_modules(project):
    root,doc,artifacts=project
    class Agent(FakeInvestigator):
        def propose(self,stage,instructions,context,output):
            if stage=='flow.survey':
                return ProjectSurvey(modules=[ModuleScope(id='BROKEN',title='Broken',objective='Unavailable flow',entry_paths=['service.py']),ModuleScope(id='SUM',title='Addition',objective='Addition',entry_paths=['service.py'])])
            if stage=='flow.investigate' and context['module']['id']=='BROKEN':
                return InquiryTurn(notes='Need more reading',requests=[ToolRequest(name='search',query='missing')])
            return super().propose(stage,instructions,context,output)
    w=FlowDesignAgent(Agent(),RepositoryTools(root,artifacts,[doc]),artifacts,max_turns=3,max_rounds=1).run('p','sha')
    assert len(w.cases)==1
    assert {m['scope']['id']:m['status'] for m in w.flow_modules}=={'BROKEN':'partial','SUM':'reviewed'}
    assert w.generation_diagnostic['error_kind']=='turn_limit'


def test_new_semantic_duplicate_is_withheld_as_edit_proposal(project):
    from tda.domain.workspace import WorkspaceFinding
    root,doc,artifacts=project
    class Agent(FakeInvestigator):
        def propose(self,stage,instructions,context,output):
            result=super().propose(stage,instructions,context,output)
            if stage=='flow.write':
                duplicate=result.cases[0].model_copy(deep=True)
                duplicate.id='SUM-T2';duplicate.title='Same addition with different wording'
                result.cases.append(duplicate)
            if stage=='flow.review':
                from tda.domain.investigation import DuplicateGroup
                result.duplicate_groups=[DuplicateGroup(canonical_case_id='SUM-T1',duplicate_case_ids=['SUM-T2'],explanation='Same inputs and outcome',evidence=[Evidence(source_id='prd',quote='Sum of 2 and 3 is 5.')])]
                result.findings.append(WorkspaceFinding(case_ids=['SUM-T1','SUM-T2'],severity='warning',category='duplicate',
                    message='Same inputs and outcome',suggestion='Keep one case',evidence=[Evidence(source_id='prd',quote='Sum of 2 and 3 is 5.')]))
            return result
    w=FlowDesignAgent(Agent(),RepositoryTools(root,artifacts,[doc]),artifacts,max_rounds=1).run('p','sha')
    assert [c.id for c in w.cases]==['SUM-T1']
    assert len(w.proposals)==1 and w.proposals[0].id=='SUM-T1'
    assert w.proposals[0].revision==2
    assert not any(l['case_id']=='SUM-T2' for m in w.flow_modules for l in m['links'])


def test_module_context_does_not_include_previously_read_unrelated_code(project):
    root,doc,artifacts=project
    (root/'unrelated.py').write_text('unrelated_secret_feature = 123\n')
    tools=RepositoryTools(root,artifacts,[doc]);tools.read_file('unrelated.py',1,120)
    class Agent(FakeInvestigator):
        def propose(self,stage,instructions,context,output):
            if stage=='flow.investigate':
                assert all(s['path']!='unrelated.py' for s in context['observed_sources'])
                assert all(s['path']!='unrelated.py' for s in context['known_sources'])
            return super().propose(stage,instructions,context,output)
    w=FlowDesignAgent(Agent(),tools,artifacts,max_rounds=1).run('p','sha')
    assert len(w.cases)==1


def test_all_modules_receive_first_pass_before_second_generation_round(project):
    root,doc,artifacts=project
    class Agent(FakeInvestigator):
        max_calls=20
        def __init__(self):super().__init__();self.order=[];self.current=None;self.turns_by_module={}
        def propose(self,stage,instructions,context,output):
            if stage=='flow.survey':
                self.calls.append(stage)
                return ProjectSurvey(modules=[ModuleScope(id=id,title=id,objective='Addition',entry_paths=['service.py']) for id in ['FIRST','SECOND']])
            module=context['module']['id'] if stage=='flow.investigate' else context['study']['module_id']
            if stage=='flow.investigate':self.turns=self.turns_by_module.get(module,0)
            result=super().propose(stage,instructions,context,output)
            if stage=='flow.investigate':self.turns_by_module[module]=self.turns
            self.order.append((module,stage))
            data=result.model_dump()
            def replace(value):
                if isinstance(value,str):return value.replace('SUM',module)
                if isinstance(value,list):return [replace(v) for v in value]
                if isinstance(value,dict):return {k:replace(v) for k,v in value.items()}
                return value
            result=output.model_validate(replace(data))
            if stage=='flow.review':result.links=[]
            return result
    agent=Agent()
    w=FlowDesignAgent(agent,RepositoryTools(root,artifacts,[doc]),artifacts,max_rounds=2).run('p','sha')
    assert [module for module,stage in agent.order if stage=='flow.write']==['FIRST','SECOND','FIRST','SECOND']
    assert len(w.cases)==2 and len(w.proposals)==2


def test_duplicate_review_preserves_preexisting_human_case(project,tmp_path):
    from tda.domain.investigation import DuplicateGroup
    root,doc,artifacts=project
    w=FlowDesignAgent(FakeInvestigator(),RepositoryTools(root,artifacts,[doc]),artifacts,max_rounds=1).run('p','sha')
    w.cases[0].human_edited=True;w.cases[0].status='approved';w.cases[0].title='Maintainer title'
    original=w.cases[0].model_dump()
    class Agent(FakeInvestigator):
        def propose(self,stage,instructions,context,output):
            result=super().propose(stage,instructions,context,output)
            if stage=='flow.write':result.cases[0].id='SUM-T2'
            if stage=='flow.review':
                result.duplicate_groups=[DuplicateGroup(canonical_case_id='SUM-T2',duplicate_case_ids=['SUM-T1'],explanation='Same scenario',evidence=[Evidence(source_id='prd',quote='Sum of 2 and 3 is 5.')])]
            return result
    new_artifacts=LocalArtifactStore(tmp_path/'supplemented')
    updated=FlowDesignAgent(Agent(),RepositoryTools(root,new_artifacts,[doc]),new_artifacts,max_rounds=1).run('p','sha',w,action='supplement')
    assert len(updated.cases)==1 and updated.cases[0].model_dump()==original
    assert updated.proposals[0].id=='SUM-T1'
