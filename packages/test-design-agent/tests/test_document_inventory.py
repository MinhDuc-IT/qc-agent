from tda.application.document_inventory import index_documents,inventory_requirements,module_documents
from tda.domain.contracts import SourceDocument
from tda.domain.investigation import ModuleScope


def test_long_prd_requirements_are_indexed_after_intro():
    text='# Intro\n'+'Context only.\n'*130+'## Authentication\n| FR-AUTH-01 | Reject locked users | P0 |\n## Recovery\n| BR-01 | Retry preserves identity |\n## Performance\n| NFR-01 | Measure latency; SLO undecided |\n'
    doc=SourceDocument(id='prd',kind='prd',path='uploads/PRD.md',content=text,sha256='a'*64)
    inventory=index_documents([doc]);rows=inventory['requirements']
    assert [r['id'] for r in rows]==['FR-AUTH-01','BR-01','NFR-01']
    assert rows[0]['line']>120
    requirements=inventory_requirements(inventory)
    assert requirements[0].priority=='P0' and requirements[1].category=='business_rule'
    assert requirements[2].category=='non_functional'
    selected=module_documents(inventory,ModuleScope(id='auth',title='Authentication',objective='Reject locked users',entry_paths=['app/auth.py']),max_chars=150)
    assert 'FR-AUTH-01' in selected[0]['content']
    assert sum(len(s['content']) for s in selected)<=150


def test_exact_quote_is_required_to_recover_reference(tmp_path):
    from tda.application.flow_design import FlowDesignAgent
    from tda.infrastructure.repository_tools import RepositoryTools
    from tda.infrastructure.artifacts import LocalArtifactStore
    from tda.domain.contracts import Evidence
    artifacts=LocalArtifactStore(tmp_path/'artifacts')
    (tmp_path/'auth.py').write_text('HEADER = "no-store"\n')
    tools=RepositoryTools(tmp_path,artifacts);source=tools.read_file('auth.py')
    engine=FlowDesignAgent(None,tools,artifacts);engine.module_source_ids={source['source_id']}
    valid=Evidence(source_id='truncated-id',quote='HEADER = "no-store"')
    invalid=Evidence(source_id='invented-id',quote='HEADER = "public"')
    engine.ground_claims([valid,invalid])
    assert valid.source_id==source['source_id']
    assert invalid.source_id=='invented-id'
    (tmp_path/'second.py').write_text('HEADER = "no-store"\n')
    # Both sources must already be in the tool inventory to consider them.
    tools=RepositoryTools(tmp_path,LocalArtifactStore(tmp_path/'more-artifacts'))
    tools.read_file('auth.py');tools.read_file('second.py')
    engine=FlowDesignAgent(None,tools,artifacts)
    ambiguous=Evidence(source_id='bad',quote='HEADER = "no-store"')
    engine.ground_claims([ambiguous]);assert ambiguous.source_id=='bad'


def test_duplicate_requirement_ids_do_not_silently_resolve_conflicts():
    documents=[SourceDocument(id='first',kind='prd',path='first.md',content='| FR-01 | Require two roles |\n',sha256='a'*64),
               SourceDocument(id='second',kind='srs',path='second.md',content='| FR-01 | Require three roles |\n',sha256='b'*64)]
    requirement=inventory_requirements(index_documents(documents))[0]
    assert requirement.clarity=='AMBIGUOUS'
    assert len(requirement.evidence)==2 and len(requirement.acceptance_criteria)==2


def test_tda_timeout_is_configurable_without_hidden_sdk_retries(tmp_path,monkeypatch):
    import openai
    from types import SimpleNamespace
    from tda.infrastructure.openai_agent import OpenAIStructuredAgent
    from tda.infrastructure.artifacts import LocalArtifactStore
    received={}
    def client(**kwargs):received.update(kwargs);return SimpleNamespace()
    monkeypatch.setattr(openai,'OpenAI',client)
    agent=OpenAIStructuredAgent('test-key','test-model',LocalArtifactStore(tmp_path/'artifacts'),timeout_seconds=220)
    assert received['timeout']==220 and received['max_retries']==0 and agent.calls==0
