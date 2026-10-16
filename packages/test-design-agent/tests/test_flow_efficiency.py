from types import SimpleNamespace
import pytest
from tda.application.flow_design import FlowDesignAgent
from tda.application.flow_context import retain_items,compact_sources
from tda.application.ports import AgentFailure
from tda.domain.investigation import ModuleScope,ModuleStudy,FlowNode,Behavior,ModuleReview
from tda.domain.contracts import Evidence,RequirementNode
from tda.infrastructure.artifacts import LocalArtifactStore
from tda.infrastructure.repository_tools import RepositoryTools


def test_repair_keeps_valid_siblings_and_merges_only_corrected_item(tmp_path):
    (tmp_path/'service.py').write_text('def add(a,b):\n return a+b\n')
    artifacts=LocalArtifactStore(tmp_path/'artifacts');tools=RepositoryTools(tmp_path,artifacts)
    source=tools.read_file('service.py');e=Evidence(source_id=source['source_id'],quote='return a+b')
    scope=ModuleScope(id='SUM',title='Sum',objective='Addition',entry_paths=['service.py'])
    engine=FlowDesignAgent(None,tools,artifacts)
    requirement=RequirementNode(id='SUM-R1',title='Addition',expectation_basis='implementation',acceptance_criteria=['Return sum'],evidence=[e])
    good=Behavior(id='SUM-B1',description='Happy path',requirement_ids=['SUM-R1'],technique='happy_path',basis='implementation_derived',expected_behavior='Return sum',evidence=[e])
    bad=good.model_copy(deep=True);bad.id='SUM-B2';bad.evidence[0].quote='return unsupported'
    study=ModuleStudy(module_id='SUM',summary='Sum',requirements=[requirement],flow=[FlowNode(role='service',description='Add',evidence=[e])],behaviors=[good,bad])
    checkpoint,issues=retain_items(engine,scope,study)
    assert [b['id'] for b in checkpoint['behaviors']]==['SUM-B1']
    assert len(issues)==1 and issues[0]['id']=='SUM-B2' and issues[0]['item']['evidence'][0]['quote']=='return unsupported'
    bad.evidence=[e]
    patch=ModuleStudy(module_id='SUM',summary='Sum',requirements=[],flow=[],behaviors=[bad])
    updated,issues=retain_items(engine,scope,patch,checkpoint)
    assert not issues and [b['id'] for b in updated['behaviors']]==['SUM-B1','SUM-B2']
    assert updated['requirements']==checkpoint['requirements'] and updated['flow']==checkpoint['flow']


def test_review_batches_are_cached_and_invalidated_by_changed_case(tmp_path):
    artifacts=LocalArtifactStore(tmp_path/'artifacts');tools=RepositoryTools(tmp_path,artifacts)
    class Agent:
        calls=0;max_calls=12
        def propose(self,stage,instructions,context,model):
            self.calls+=1;assert len(context['cases'])<=4
            return ModuleReview()
    agent=Agent();engine=FlowDesignAgent(agent,tools,artifacts)
    cases=[{'id':str(i),'objective':'Check sum','kind':'functional','test_data':[],'steps':[]} for i in range(9)]
    context={'scope':{'id':'SUM'},'study':{},'cases':cases}
    engine.propose('flow.review','Review',context,ModuleReview);assert agent.calls==3
    engine.propose('flow.review','Review',context,ModuleReview);assert agent.calls==3
    cases[-1]['objective']='Changed expectation'
    engine.propose('flow.review','Review',context,ModuleReview);assert agent.calls==4


def test_context_limit_blocks_provider_call_without_spending_call_budget(tmp_path):
    from tda.infrastructure.openai_agent import OpenAIStructuredAgent
    received=[]
    client=SimpleNamespace(responses=SimpleNamespace(parse=lambda **kwargs:received.append(kwargs)))
    agent=OpenAIStructuredAgent('test','model',LocalArtifactStore(tmp_path/'artifacts'),client=client,max_input_chars=100)
    with pytest.raises(AgentFailure) as failure:agent.propose('flow.investigate','Read',{'code':'x'*200},ModuleStudy)
    assert failure.value.error_kind=='context_budget' and agent.calls==0 and not received


def test_source_excerpts_have_a_shared_size_bound_and_deduplicate():
    sources=[{'source_id':'code','excerpts':['same','same','same with context','z'*20000]},
             {'source_id':'other','excerpts':['w'*20000]}]
    result=compact_sources(sources,max_chars=3000)
    assert sum(len(text) for source in result for text in source['excerpts'])<=3000
    assert result[0]['excerpts'].count('same')==0
